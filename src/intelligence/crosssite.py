"""Cross Site Intelligence — recherche multi-clés et relance persistante.

Deux idées portent cette couche :

  1. TOUTES LES CLÉS SERVENT. Une information découverte chez un marchand
     (un UPC lu chez Amazon) devient immédiatement une clé de recherche
     chez tous les autres. Le moteur construit la liste ordonnée des
     recherches possibles et laisse chaque plugin choisir sa méthode.

  2. LES ÉCHECS SE MÉMORISENT. Si Micromania ne connaît pas encore cet UPC
     aujourd'hui, l'échec est enregistré avec une heure de relance. Le
     moteur y revient tout seul, sans repartir de zéro — c'est ainsi qu'une
     fiche publiée quelques heures après les autres est repérée très vite.

Le moteur ne connaît aucun marchand : il ne manipule que des identités,
des clés, des candidats et des scores.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Sequence

from src.discovery.loader import DiscoveryRegistry
from src.intelligence.candidates import OfferCandidate
from src.intelligence.identity import ProductIdentity
from src.intelligence.keys import SearchKey, build_search_keys
from src.repositories.search_attempts import (
    STATUS_ERROR,
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    STATUS_UNSUPPORTED,
    SearchAttemptRepository,
    next_retry,
)
from src.utils.logger import get_logger

log = get_logger("intelligence.crosssite")


@dataclass(frozen=True)
class CrossSiteSettings:
    """Réglages de la recherche inter-sites et de sa relance."""

    enabled: bool = False
    max_sites: int = 8
    max_keys_per_site: int = 6
    #: Confiance à partir de laquelle un plugin peut cesser de chercher.
    stop_confidence: int = 90
    site_timeout: float = 30.0
    retry_base_seconds: int = 1800          # 30 minutes
    retry_multiplier: float = 1.5
    retry_cap_seconds: int = 21600          # 6 heures
    retry_interval: int = 300               # cadence de la boucle de relance
    retry_batch: int = 25

    @classmethod
    def from_config(cls, raw: dict | None) -> "CrossSiteSettings":
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("cross_site_search", False)),
            max_sites=max(1, int(raw.get("cross_site_max_sites", 8))),
            max_keys_per_site=max(1, int(raw.get("max_keys_per_site", 6))),
            stop_confidence=max(1, min(100, int(raw.get("stop_confidence", 90)))),
            site_timeout=float(raw.get("site_timeout", 30)),
            retry_base_seconds=max(60, int(raw.get("retry_base_seconds", 1800))),
            retry_multiplier=max(1.0, float(raw.get("retry_multiplier", 1.5))),
            retry_cap_seconds=max(60, int(raw.get("retry_cap_seconds", 21600))),
            retry_interval=max(30, int(raw.get("retry_interval", 300))),
            retry_batch=max(1, int(raw.get("retry_batch", 25))),
        )


@dataclass
class SiteSearchOutcome:
    """Ce qu'un marchand a répondu, clé par clé."""

    site: str
    candidates: list[OfferCandidate] = field(default_factory=list)
    tried_keys: list[SearchKey] = field(default_factory=list)
    error: str = ""
    unsupported: bool = False


@dataclass
class CrossSiteReport:
    """Bilan d'une campagne de recherche, exposé par l'API."""

    product_uuid: str
    sites_queried: int = 0
    keys_tried: int = 0
    candidates_found: int = 0
    offers_created: int = 0
    retries_scheduled: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.sites_queried} site(s), {self.keys_tried} clé(s) essayée(s), "
            f"{self.candidates_found} candidat(s), "
            f"{self.offers_created} offre(s) créée(s), "
            f"{self.retries_scheduled} relance(s) programmée(s)"
        )


class CrossSiteIntelligence:
    """Interroge les marchands avec toutes les clés, et retente les échecs."""

    def __init__(
        self,
        settings: CrossSiteSettings,
        registry: DiscoveryRegistry,
        attempts: SearchAttemptRepository,
        context_factory,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._attempts = attempts
        self._context_factory = context_factory
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ #
    # Capacités                                                           #
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return self._settings.enabled and bool(self.capable_sites)

    @property
    def capable_sites(self) -> list[str]:
        """Marchands dont le plugin sait répondre à une recherche."""
        return [
            plugin.site_name for plugin in self._registry.all()
            if callable(getattr(plugin, "search", None))
        ]

    def stop(self) -> None:
        self._stopping.set()

    # ------------------------------------------------------------------ #
    # Campagne de recherche                                               #
    # ------------------------------------------------------------------ #

    async def search_everywhere(
        self,
        product_uuid: str,
        identity: ProductIdentity,
        exclude_sites: Sequence[str] = (),
        only_sites: Optional[Sequence[str]] = None,
        only_keys: Optional[Sequence[SearchKey]] = None,
    ) -> tuple[list[OfferCandidate], CrossSiteReport]:
        """Cherche le produit chez tous les marchands, avec toutes les clés."""
        report = CrossSiteReport(product_uuid=product_uuid)
        keys = list(only_keys) if only_keys else build_search_keys(identity)
        if not keys:
            log.check("Aucune clé exploitable pour le produit %s.", product_uuid[:8])
            return [], report

        excluded = {site.lower() for site in exclude_sites}
        wanted = {site.lower() for site in only_sites} if only_sites else None
        plugins = [
            plugin for plugin in self._registry.all()
            if callable(getattr(plugin, "search", None))
            and plugin.site_name.lower() not in excluded
            and (wanted is None or plugin.site_name.lower() in wanted)
        ][: self._settings.max_sites]

        if not plugins:
            return [], report

        log.check(
            "Recherche inter-sites : %d clé(s) × %d site(s) pour %s",
            len(keys), len(plugins), product_uuid[:8],
        )
        outcomes = await asyncio.gather(*(
            self._search_site(plugin, product_uuid, identity, keys)
            for plugin in plugins
        ))

        candidates: list[OfferCandidate] = []
        for outcome in outcomes:
            report.sites_queried += 1
            report.keys_tried += len(outcome.tried_keys)
            if outcome.error:
                report.errors.append(f"{outcome.site} : {outcome.error}")
            candidates.extend(outcome.candidates)

        report.candidates_found = len(candidates)
        report.retries_scheduled = sum(
            1 for outcome in outcomes if not outcome.candidates and not outcome.unsupported
        )
        return candidates, report

    async def _search_site(
        self,
        plugin,
        product_uuid: str,
        identity: ProductIdentity,
        keys: Sequence[SearchKey],
    ) -> SiteSearchOutcome:
        """Interroge un marchand, clé par clé, jusqu'à confiance suffisante."""
        outcome = SiteSearchOutcome(site=plugin.site_name)
        options = getattr(plugin, "options", {}) or {}
        context = self._context_factory(options)

        for key in list(keys)[: self._settings.max_keys_per_site]:
            if self._stopping.is_set():
                break
            outcome.tried_keys.append(key)
            try:
                found = await asyncio.wait_for(
                    plugin.search(identity, context, key),
                    timeout=self._settings.site_timeout,
                )
            except NotImplementedError:
                outcome.unsupported = True
                await self._record(product_uuid, plugin.site_name, key,
                                   STATUS_UNSUPPORTED, reason="clé non exploitable")
                continue
            except asyncio.TimeoutError:
                outcome.error = "délai dépassé"
                await self._record(product_uuid, plugin.site_name, key,
                                   STATUS_ERROR, reason="délai dépassé")
                break
            except Exception as exc:  # noqa: BLE001 — isolation par site
                outcome.error = str(exc)
                log.error("Recherche %s (%s) en échec : %s",
                          plugin.site_name, key, exc)
                await self._record(product_uuid, plugin.site_name, key,
                                   STATUS_ERROR, reason=str(exc)[:200])
                break

            results = [
                candidate.with_site(plugin.site_name)
                for candidate in (found or ())
            ]
            if not results:
                await self._record(product_uuid, plugin.site_name, key,
                                   STATUS_NOT_FOUND,
                                   reason="aucun résultat pour cette clé")
                continue

            best = max(results, key=lambda candidate: candidate.confidence)
            outcome.candidates.extend(results)
            await self._record(
                product_uuid, plugin.site_name, key, STATUS_FOUND,
                confidence=best.confidence, matched_fields=best.matched_fields,
                reason=best.reason, found_url=best.url,
            )
            log.ok(
                "Trouvé chez %s via %s — confiance %d (%s)",
                plugin.site_name, key, best.confidence,
                ", ".join(best.matched_fields) or "aucun champ",
            )
            if best.confidence >= self._settings.stop_confidence:
                break   # inutile d'essayer les clés moins sûres

        return outcome

    async def _record(
        self,
        product_uuid: str,
        site: str,
        key: SearchKey,
        status: str,
        confidence: int = 0,
        matched_fields: tuple[str, ...] = (),
        reason: str = "",
        found_url: Optional[str] = None,
    ) -> None:
        """Journalise la tentative et programme sa relance si besoin."""
        existing = None
        if status != STATUS_FOUND:
            for attempt in await self._attempts.for_product(product_uuid):
                if (attempt.site == site and attempt.key_kind == key.kind
                        and attempt.key_value == key.value):
                    existing = attempt
                    break

        retry_at = None
        if status in (STATUS_NOT_FOUND, STATUS_ERROR):
            retry_at = next_retry(
                (existing.attempts + 1) if existing else 1,
                self._settings.retry_base_seconds,
                self._settings.retry_multiplier,
                self._settings.retry_cap_seconds,
            )

        await self._attempts.record(
            product_uuid=product_uuid, site=site, key_kind=key.kind,
            key_value=key.value, status=status, confidence=confidence,
            matched_fields=matched_fields, reason=reason, found_url=found_url,
            next_retry_at=retry_at,
        )

    # ------------------------------------------------------------------ #
    # Boucle de relance                                                   #
    # ------------------------------------------------------------------ #

    async def run_retry_loop(self, on_retry) -> None:
        """Reprend périodiquement les recherches restées infructueuses.

        `on_retry(product_uuid, site, key)` est fourni par le moteur
        d'intelligence : il connaît l'identité à jour et sait ingérer un
        résultat. Cette classe, elle, ne s'occupe que du « quand ».
        """
        if not self.enabled:
            log.ok("Relance des recherches inactive (recherche inter-sites désactivée).")
            return

        log.ok(
            "Relance des recherches active — vérification toutes les %d s, "
            "premier réessai après %d min.",
            self._settings.retry_interval, self._settings.retry_base_seconds // 60,
        )
        while not self._stopping.is_set():
            try:
                due = await self._attempts.due_for_retry(self._settings.retry_batch)
                if due:
                    log.check("Relance de %d recherche(s) en attente.", len(due))
                for attempt in due:
                    if self._stopping.is_set():
                        break
                    await on_retry(attempt)
            except Exception as exc:  # noqa: BLE001 — la boucle ne meurt jamais
                log.error("Boucle de relance : %s", exc)

            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.retry_interval
                )
            except asyncio.TimeoutError:
                continue
