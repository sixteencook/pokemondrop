"""Discovery Engine — exploration périodique et import automatique.

Cycle, identique pour tous les sites :

    pour chaque plugin de découverte activé
      ├─ scan()                        le plugin explore comme il l'entend
      ├─ fingerprint de chaque fiche   identité stable, anti-doublon
      ├─ règles d'inclusion/exclusion  entièrement configurables
      ├─ décision selon le mode        auto | review | rules
      ├─ import éventuel               produit créé → surveillé sous 5 s
      └─ publication sur l'Event Bus   Telegram, WebSocket, timeline

Le moteur ne connaît AUCUN site : il ne manipule que des DiscoveryPlugin,
des DiscoveredProduct et un RuleSet. Ajouter un site = déposer un dossier
dans plugins/, sans toucher à ce fichier.

Isolation : un plugin qui échoue est journalisé et ignoré ; les autres
sites, et toute la surveillance existante, continuent normalement.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from src.core.events import Event, EventBus, EventType
from src.discovery.config import ApprovalMode, DiscoverySettings
from src.discovery.contracts import DiscoveredProduct, ScanResult
from src.discovery.fingerprint import compute, product_slug
from src.discovery.loader import DiscoveryRegistry
from src.discovery.rules import RuleMatch
from src.models import DiscoveryRecord, DiscoveryStatus, ProductConfig
from src.utils.logger import get_logger

if TYPE_CHECKING:  # import différé : évite le cycle repositories ↔ discovery
    from src.repositories import DiscoveryRepository, ProductRepository

log = get_logger("discovery")


@dataclass
class ScanReport:
    """Bilan d'un balayage, exposé par l'API et journalisé."""

    sites_scanned: int = 0
    products_seen: int = 0
    new_products: int = 0
    imported: int = 0
    pending: int = 0
    excluded: int = 0
    gone: int = 0
    errors: list[str] = field(default_factory=list)
    #: Durée du balayage complet — suivie sur la page Santé.
    duration_ms: int = 0

    def summary(self) -> str:
        return (
            f"{self.sites_scanned} site(s), {self.products_seen} fiche(s) vues, "
            f"{self.new_products} nouvelle(s) → {self.imported} importée(s), "
            f"{self.pending} en attente, {self.excluded} exclue(s), "
            f"{self.gone} disparue(s)"
        )


class DiscoveryEngine:
    """Orchestre les plugins de découverte et l'import automatique."""

    def __init__(
        self,
        settings: DiscoverySettings,
        registry: DiscoveryRegistry,
        discoveries: "DiscoveryRepository",
        products: "ProductRepository",
        bus: EventBus,
        context_factory,
        intelligence=None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._discoveries = discoveries
        self._products = products
        self._bus = bus
        self._context_factory = context_factory
        self._intelligence = intelligence
        self._stopping = asyncio.Event()
        self._scanning = asyncio.Lock()
        self._last_report: Optional[ScanReport] = None

    # ------------------------------------------------------------------ #
    # Cycle de vie                                                        #
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return self._settings.enabled and len(self._registry) > 0

    @property
    def last_report(self) -> Optional[ScanReport]:
        return self._last_report

    @property
    def sites(self) -> list[str]:
        return self._registry.sites

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """Boucle périodique. Ne meurt jamais sur une erreur de plugin."""
        if not self.enabled:
            reason = (
                "désactivée dans config/discovery.yaml"
                if not self._settings.enabled
                else "aucun plugin de découverte chargé"
            )
            log.ok("Découverte inactive — %s.", reason)
            return

        log.ok(
            "Découverte active — mode « %s », %d site(s), balayage toutes les %d s.",
            self._settings.mode.value, len(self._registry), self._settings.scan_interval,
        )
        while not self._stopping.is_set():
            try:
                await self.scan_all()
            except Exception as exc:  # noqa: BLE001 — le moteur survit à tout
                log.error("Balayage de découverte en échec : %s", exc)
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.scan_interval
                )
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------ #
    # Balayage                                                            #
    # ------------------------------------------------------------------ #

    async def scan_all(self) -> ScanReport:
        """Balaye tous les sites activés. Utilisable à la demande via l'API."""
        if self._scanning.locked():
            log.check("Balayage déjà en cours — demande ignorée.")
            return self._last_report or ScanReport()

        async with self._scanning:
            started = time.perf_counter()
            report = ScanReport()
            for plugin in self._registry.all():
                site_config = self._settings.for_site(plugin.site_name)
                if not site_config.enabled:
                    continue
                try:
                    await self._scan_site(plugin, site_config, report)
                    report.sites_scanned += 1
                except Exception as exc:  # noqa: BLE001 — isolation par site
                    message = f"{plugin.site_name} : {exc}"
                    report.errors.append(message)
                    log.error("Découverte — échec du site %s : %s",
                              plugin.site_name, exc)

            report.duration_ms = int((time.perf_counter() - started) * 1000)
            self._last_report = report
            log.ok("Balayage terminé — %s", report.summary())
            await self._bus.publish(Event(
                EventType.DISCOVERY_SCAN_COMPLETED,
                {"report": report, "summary": report.summary(),
                 "duration_ms": report.duration_ms},
            ))
            return report

    async def _scan_site(self, plugin, site_config, report: ScanReport) -> None:
        context = self._context_factory(site_config.options)
        result: ScanResult = await plugin.scan(context)

        log.check(
            "Découverte %s : %d fiche(s) remontée(s) (%d source(s), balayage %s).",
            plugin.site_name, len(result.products), result.sources_scanned,
            "complet" if result.complete else "partiel",
        )

        seen: set[str] = set()
        new_this_scan = 0

        for raw in result.products:
            product = raw.with_site(plugin.site_name)
            report.products_seen += 1

            info = compute(product.site, product.url, product.sku, product.ean)
            seen.add(info.value)

            record, is_new = await self._discoveries.record_sighting(
                fingerprint=info.value,
                site=product.site,
                url=product.url,
                canonical_url=info.canonical,
                title=product.title,
                image_url=product.image_url,
                price=product.price,
                sku=product.sku,
                ean=product.ean,
                source=product.source,
            )
            if not is_new:
                continue

            report.new_products += 1
            new_this_scan += 1
            await self._decide(product, record, plugin, report)
            await self._feed_intelligence(product, info.value)

            if new_this_scan >= self._settings.max_new_per_scan:
                log.check(
                    "Plafond de %d nouveauté(s) atteint pour %s — suite au prochain "
                    "balayage.", self._settings.max_new_per_scan, plugin.site_name,
                )
                break

        # Fiches disparues : uniquement après un balayage EXHAUSTIF.
        if result.complete and seen:
            report.gone += await self._discoveries.mark_missing(plugin.site_name, seen)

    async def _feed_intelligence(
        self, product: DiscoveredProduct, fingerprint: str
    ) -> None:
        """Transmet la fiche au Product Intelligence Engine, s'il est actif.

        Isolé : une erreur de corrélation ne doit jamais compromettre la
        découverte elle-même.
        """
        if self._intelligence is None or not self._intelligence.enabled:
            return
        try:
            await self._intelligence.ingest(
                product, fingerprint=fingerprint, source="discovery"
            )
        except Exception as exc:  # noqa: BLE001 — la découverte prime
            log.error("Corrélation impossible pour « %s » : %s", product.title, exc)

    # ------------------------------------------------------------------ #
    # Décision                                                            #
    # ------------------------------------------------------------------ #

    async def _decide(
        self,
        product: DiscoveredProduct,
        record: DiscoveryRecord,
        plugin,
        report: ScanReport,
    ) -> None:
        """Applique règles et mode d'approbation à une fiche inédite."""
        match: RuleMatch = self._settings.rules.evaluate(product)

        # L'exclusion prime, quel que soit le mode.
        if match.excluded:
            report.excluded += 1
            await self._discoveries.set_status(
                record.fingerprint, DiscoveryStatus.IGNORED, match.reason
            )
            log.check("Découverte écartée — %s (%s)", product.title, match.reason)
            return

        should_import = (
            self._settings.mode is ApprovalMode.AUTO
            or (self._settings.mode is ApprovalMode.RULES and match.accepted)
        )

        if should_import:
            uuid = await self.import_product(record, match.matched)
            report.imported += 1
            status, reason = DiscoveryStatus.IMPORTED, (
                match.reason or "import automatique"
            )
        else:
            uuid = None
            report.pending += 1
            status, reason = DiscoveryStatus.PENDING, (
                match.reason or "en attente de validation"
            )
            await self._discoveries.set_status(record.fingerprint, status, reason)

        log.ok(
            "🆕 %s — %s (%s) : %s",
            plugin.display_name, product.title, product.url,
            "importé et surveillé" if should_import else "en attente de validation",
        )

        await self._bus.publish(Event(EventType.NEW_PRODUCT_DISCOVERED, {
            "discovery": product,
            "fingerprint": record.fingerprint,
            "site_label": plugin.display_name,
            "status": status.value,
            "reason": reason,
            "product_uuid": uuid,
            "imported": should_import,
        }))

    async def import_product(
        self, record: DiscoveryRecord, matched_rules: tuple[str, ...] = ()
    ) -> str:
        """Crée le produit surveillé à partir d'une fiche découverte.

        Le moteur de surveillance relit la base toutes les 5 s : la
        surveillance démarre donc seule, sans redémarrage.
        """
        defaults = self._settings.defaults
        tags = tuple(dict.fromkeys((*defaults.tags, *matched_rules, record.site)))

        created = await self._products.create(ProductConfig(
            name=record.title[:200],
            site=record.site,
            url=record.url,
            check_interval=defaults.check_interval,
            enabled=defaults.enabled,
            group=product_slug(record.url) or None,
            priority=defaults.priority,
            tags=tags,
        ))
        await self._discoveries.set_status(
            record.fingerprint, DiscoveryStatus.IMPORTED,
            "import automatique", created.uuid,
        )
        log.ok("Surveillance créée automatiquement : %s (%s)",
               created.name, created.uuid)

        # Rattache l'offre au produit surveillé : le champ `group` du produit
        # devient alors l'UUID du produit canonique (regroupement automatique).
        if self._intelligence is not None and self._intelligence.enabled:
            try:
                await self._intelligence.ingest(
                    DiscoveredProduct(
                        url=record.url, title=record.title, site=record.site,
                        image_url=record.image_url, price=record.price,
                        sku=record.sku, ean=record.ean, source=record.source,
                    ),
                    monitored_uuid=created.uuid,
                    fingerprint=record.fingerprint,
                    source="import",
                )
            except Exception as exc:  # noqa: BLE001 — l'import prime
                log.error("Rattachement au catalogue impossible : %s", exc)

        return created.uuid
