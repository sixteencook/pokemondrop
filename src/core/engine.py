"""Moteur d'orchestration : une tâche asyncio par produit surveillé.

Le moteur ne reçoit plus une liste figée : il interroge un « provider »
(la base de données) et RÉCONCILIE en continu ses boucles de surveillance
avec la configuration courante. Ajouter, modifier, activer ou supprimer
un produit depuis le futur dashboard prend effet en quelques secondes,
sans redémarrage.

Chaque produit tourne dans sa propre boucle indépendante :
  fetch → parse → diff avec l'état mémorisé → publication d'événements
  → persistance → sleep.

Le moteur ne connaît AUCUN consommateur : il publie sur l'EventBus.

Robustesse :
  - timeout et erreurs réseau gérés par tentatives avec backoff exponentiel ;
  - un jitter aléatoire est ajouté au délai pour ne pas marteler le site ;
  - au premier passage d'un produit, l'état est enregistré sans alerte.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable

from src.core.detector import detect_changes
from src.core.events import Event, EventBus, EventType
from src.models import GlobalSettings, ProductConfig, ProductSnapshot
from src.monitors import FetchError, MonitorRegistry, UnknownSiteError
from src.repositories import SnapshotRepository
from src.utils.logger import get_logger

log = get_logger("engine")

#: Fournit la liste courante des produits (typiquement ProductRepository.list_all).
ProductProvider = Callable[[], Awaitable[list[ProductConfig]]]


class MonitorEngine:
    """Supervise les boucles de surveillance et les réconcilie avec la config."""

    def __init__(
        self,
        registry: MonitorRegistry,
        bus: EventBus,
        snapshots: SnapshotRepository,
        settings: GlobalSettings,
        product_provider: ProductProvider,
        reload_interval: float = 5.0,
    ) -> None:
        self._registry = registry
        self._bus = bus
        self._snapshots = snapshots
        self._settings = settings
        self._provider = product_provider
        self._reload_interval = reload_interval
        self._stopping = asyncio.Event()
        #: uuid (ou clé) → (config au lancement, tâche de surveillance)
        self._watchers: dict[str, tuple[ProductConfig, asyncio.Task]] = {}
        self._skipped_reported: set[str] = set()

    def stop(self) -> None:
        self._stopping.set()

    @property
    def active_count(self) -> int:
        return len(self._watchers)

    @property
    def active_products(self) -> list[ProductConfig]:
        return [product for product, _ in self._watchers.values()]

    async def check_now(self, product: ProductConfig) -> "ProductSnapshot | None":
        """Vérification immédiate à la demande (bouton « Vérifier maintenant »).

        Indépendante de la boucle périodique du produit ; publie les mêmes
        événements (check, changements, alertes) que le cycle normal.
        Retourne le snapshot obtenu, ou None en cas d'échec réseau.
        """
        return await self._check_once(product)

    async def run(self) -> None:
        """Boucle superviseur : réconcilie les watchers avec la configuration."""
        await self._bus.publish(Event(EventType.ENGINE_STARTED))
        log.ok("Moteur démarré — rechargement de la configuration toutes les %.0f s.",
               self._reload_interval)
        try:
            while not self._stopping.is_set():
                try:
                    await self._reconcile()
                except Exception as exc:  # noqa: BLE001 — le moteur ne meurt jamais
                    log.error("Rechargement de la configuration impossible : %s", exc)
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self._reload_interval
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            for _, task in self._watchers.values():
                task.cancel()
            if self._watchers:
                await asyncio.gather(
                    *(task for _, task in self._watchers.values()),
                    return_exceptions=True,
                )
            self._watchers.clear()
            await self._bus.publish(Event(EventType.ENGINE_STOPPED))

    # ------------------------------------------------------------------ #
    # Réconciliation                                                      #
    # ------------------------------------------------------------------ #

    def _product_key(self, product: ProductConfig) -> str:
        return product.uuid or product.key

    async def _reconcile(self) -> None:
        products = await self._provider()
        desired: dict[str, ProductConfig] = {}
        for product in products:
            if product.is_monitorable:
                desired[self._product_key(product)] = product
                self._skipped_reported.discard(self._product_key(product))
            else:
                self._report_skipped_once(product)

        # Arrêt des watchers supprimés ou dont la configuration a changé.
        for key in list(self._watchers):
            current, task = self._watchers[key]
            if key not in desired:
                task.cancel()
                del self._watchers[key]
                log.ok("Surveillance arrêtée : %s", current.name)
            elif desired[key] != current:
                task.cancel()
                del self._watchers[key]
                log.ok("Configuration modifiée, redémarrage : %s", desired[key].name)

        # Démarrage des nouveaux watchers.
        for key, product in desired.items():
            if key in self._watchers:
                continue
            try:
                self._registry.get(product.site)
            except UnknownSiteError as exc:
                if key not in self._skipped_reported:
                    self._skipped_reported.add(key)
                    log.error("Produit « %s » non surveillable : %s", product.name, exc)
                continue
            task = asyncio.create_task(self._watch(product), name=key)
            self._watchers[key] = (product, task)

    def _report_skipped_once(self, product: ProductConfig) -> None:
        key = self._product_key(product)
        if key in self._skipped_reported:
            return
        self._skipped_reported.add(key)
        reason = "désactivé" if not product.enabled else "URL vide (page pas encore publiée)"
        log.ok("Ignoré : %s — %s", product.name, reason)

    # ------------------------------------------------------------------ #
    # Boucle par produit                                                  #
    # ------------------------------------------------------------------ #

    async def _watch(self, product: ProductConfig) -> None:
        monitor = self._registry.get(product.site)
        log.ok("%s lancé — %s (toutes les %d s)",
               monitor.display_name or product.site, product.name, product.check_interval)

        # Démarrage étalé pour éviter une rafale de requêtes simultanées.
        await asyncio.sleep(random.uniform(0, 2))

        while not self._stopping.is_set():
            try:
                await self._check_once(product)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — une boucle ne meurt jamais
                log.error("Erreur inattendue (%s) : %s", product.name, exc)
            # Jitter ±10 % pour lisser la charge côté site.
            delay = product.check_interval * random.uniform(0.9, 1.1)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue  # délai écoulé → nouveau check

    async def _check_once(self, product: ProductConfig) -> "ProductSnapshot | None":
        monitor = self._registry.get(product.site)
        log.check("Vérification : %s (%s)", product.name, product.site)

        snapshot = None
        started = time.perf_counter()
        backoff = self._settings.retry_backoff
        for attempt in range(1, self._settings.max_retries + 1):
            try:
                snapshot = await monitor.check(product)
                break
            except FetchError as exc:
                if attempt == self._settings.max_retries:
                    log.error(
                        "Erreur réseau (%s) — abandon après %d tentatives : %s",
                        product.name, attempt, exc,
                    )
                    await self._bus.publish(
                        Event(
                            EventType.CHECK_FAILED,
                            {"product": product, "error": str(exc), "attempts": attempt},
                        )
                    )
                    return None  # on garde l'ancien état, prochain cycle
                log.check(
                    "Erreur réseau (%s), nouvelle tentative %d/%d dans %d s : %s",
                    product.name, attempt + 1, self._settings.max_retries, backoff, exc,
                )
                await asyncio.sleep(backoff)
                backoff *= 2

        if snapshot is None:
            return None

        response_time_ms = int((time.perf_counter() - started) * 1000)
        key = self._product_key(product)
        previous = await self._snapshots.load(key)
        events = detect_changes(product, previous, snapshot)

        await self._bus.publish(
            Event(
                EventType.CHECK_COMPLETED,
                {
                    "product": product,
                    "snapshot": snapshot,
                    "response_time_ms": response_time_ms,
                    "changes": len(events),
                },
            )
        )

        if previous is None:
            log.ok("Baseline enregistrée : %s (statut : %s) — aucune alerte au premier passage",
                   product.name, snapshot.availability.value)
            await self._bus.publish(
                Event(
                    EventType.BASELINE_RECORDED,
                    {"product": product, "snapshot": snapshot},
                )
            )
        elif not events:
            log.check("Aucun changement : %s", product.name)
        else:
            for change in events:
                log.alert("%s — %s : %s → %s",
                          product.name, change.change_type.value,
                          change.old_value or "—", change.new_value or "—")
                await self._bus.publish(
                    Event(
                        EventType.CHANGE_DETECTED,
                        {"product": product, "change": change, "snapshot": snapshot},
                    )
                )

        await self._snapshots.save(key, snapshot)
        return snapshot
