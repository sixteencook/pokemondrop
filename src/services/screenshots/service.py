"""ScreenshotService — consommateur indépendant de l'Event Bus.

Contrat de découplage, non négociable :

    Le handler d'événement ENFILE puis rend la main immédiatement.
    Le moteur de surveillance n'attend JAMAIS Playwright.

Chaîne complète :
    moteur ──CHANGE_DETECTED──▶ [enregistreur SQLite]
                              ▶ [ScreenshotService] enfile (instantané)
                              ▶ [WebSocket] le dashboard voit l'alerte
                              ▶ [Notifications] diffère si capture en cours
    workers ──▶ capture ──▶ SCREENSHOT_COMPLETED
                              ▶ [enregistreur] chemin en base
                              ▶ [WebSocket] miniature au dashboard
                              ▶ [Notifications] Telegram sendPhoto (ou repli texte)

Si Playwright plante, n'est pas installé, ou dépasse son délai, l'événement
SCREENSHOT_COMPLETED est publié avec `path=None` : l'alerte Telegram part
en texte seul et la surveillance continue, imperturbable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.config import ScreenshotSettings
from src.core.events import SCREENSHOT_PENDING_KEY, Event, EventBus, EventType
from src.models import ChangeEvent, ProductConfig
from src.monitors import MonitorRegistry
from src.services.screenshots import storage
from src.services.screenshots.browser import BrowserPool, BrowserUnavailable
from src.services.screenshots.capture import CaptureRequest, PageCapturer
from src.services.screenshots.policy import is_screenshot_worthy
from src.utils.logger import get_logger

log = get_logger("screenshots")

#: Réexport : la clé de payload est définie avec le contrat du bus
#: (src/core/events.py) car elle est partagée avec les notifications.
PENDING_FLAG = SCREENSHOT_PENDING_KEY


@dataclass
class CaptureJob:
    """Une capture à réaliser, avec le contexte nécessaire à la suite.

    `group_key` mutualise les changements issus d'un MÊME check : un
    passage qui détecte à la fois « prix détecté » et « précommande
    ouverte » ne déclenche qu'une seule capture, partagée par les deux
    alertes (même page, même instant, même visuel).
    """

    product: ProductConfig
    change: ChangeEvent
    payload: dict[str, Any]
    group_key: str = ""


class ScreenshotService:
    """File d'attente asynchrone + pool de workers bornés."""

    def __init__(
        self,
        settings: ScreenshotSettings,
        bus: EventBus,
        registry: Optional[MonitorRegistry] = None,
        capturer: Optional[PageCapturer] = None,
        pool: Optional[BrowserPool] = None,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._registry = registry
        self._pool = pool or BrowserPool(settings)
        self._capturer = capturer or PageCapturer(settings)
        self._queue: asyncio.Queue[CaptureJob] = asyncio.Queue(
            maxsize=settings.queue_size
        )
        self._workers: list[asyncio.Task] = []
        self._unavailable_reason: Optional[str] = None
        #: group_key → alertes partageant la même capture.
        self._groups: dict[str, list[CaptureJob]] = {}

    # ------------------------------------------------------------------ #
    # Cycle de vie                                                        #
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return self._settings.enabled and self._unavailable_reason is None

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def attach_to(self, bus: EventBus) -> None:
        """S'abonne AVANT les notifications (l'ordre du bus fait foi)."""
        bus.subscribe(self._on_change_detected, {EventType.CHANGE_DETECTED})

    async def start(self) -> None:
        """Démarre les workers (le navigateur, lui, démarre au premier job)."""
        if not self._settings.enabled:
            log.ok("Captures d'écran désactivées (SCREENSHOTS_ENABLED=false).")
            return
        self._settings.directory.mkdir(parents=True, exist_ok=True)
        purged = storage.purge_older_than(
            self._settings.directory, self._settings.retention_days
        )
        if purged:
            log.ok("Rétention des captures appliquée (%d dossier(s)).", purged)
        for index in range(self._settings.max_concurrent):
            self._workers.append(
                asyncio.create_task(self._worker(index + 1), name=f"screenshot-{index + 1}")
            )
        log.ok(
            "Service de captures prêt — %d worker(s), file de %d, dossier %s",
            self._settings.max_concurrent, self._settings.queue_size,
            self._settings.directory,
        )

    async def stop(self) -> None:
        """Arrête les workers puis ferme proprement Chromium."""
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        await self._pool.close()

    # ------------------------------------------------------------------ #
    # Réception des événements (jamais bloquante)                         #
    # ------------------------------------------------------------------ #

    async def _on_change_detected(self, event: Event) -> None:
        change = event.payload.get("change")
        product = event.payload.get("product")
        if not isinstance(change, ChangeEvent) or not isinstance(product, ProductConfig):
            return
        if not self.enabled or not self._workers:
            return
        if not product.url.strip():
            return
        if not is_screenshot_worthy(change):
            return  # check de routine : aucune capture

        # Clé de mutualisation : même produit + même instant de vérification.
        snapshot = event.payload.get("snapshot")
        group_key = f"{product.uuid or product.key}:{getattr(snapshot, 'checked_at', '')}"
        job = CaptureJob(
            product=product, change=change, payload=event.payload, group_key=group_key
        )

        existing = self._groups.get(group_key)
        if existing is not None:
            # Une capture est déjà programmée pour ce check : on s'y rattache.
            existing.append(job)
            event.payload[PENDING_FLAG] = True
            log.check(
                "Capture mutualisée : %s (%s)", product.name, change.change_type.value
            )
            return

        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            # On ne marque PAS l'attente : la notification part en texte.
            log.error(
                "File de captures saturée (%d) — capture abandonnée pour %s.",
                self._settings.queue_size, product.name,
            )
            return

        self._groups[group_key] = [job]
        # Signale aux notifications d'attendre la capture pour un seul
        # message Telegram enrichi (photo + légende).
        event.payload[PENDING_FLAG] = True
        log.check("Capture programmée : %s (%s)", product.name, change.change_type.value)

    # ------------------------------------------------------------------ #
    # Workers                                                             #
    # ------------------------------------------------------------------ #

    async def _worker(self, number: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — un worker ne meurt jamais
                log.error("Worker capture #%d : erreur inattendue — %s", number, exc)
                await self._publish_result(job, None)
            finally:
                self._queue.task_done()

    async def _process(self, job: CaptureJob) -> None:
        relative = storage.build_relative_path(
            job.product.site, job.product.name,
            extension=self._settings.image_format,
        )
        target = storage.prepare_target(self._settings.directory, relative)
        request = CaptureRequest(
            url=job.product.url,
            target=target,
            full_page=self._settings.full_page,
            cookie_selectors=self._cookie_selectors(job.product.site),
        )

        last_error: Optional[str] = None
        for attempt in range(1, self._settings.max_attempts + 1):
            try:
                async with self._pool.page() as page:
                    result = await asyncio.wait_for(
                        self._capturer.capture(page, request),
                        # Marge sur le timeout Playwright : filet anti-blocage.
                        timeout=(self._settings.timeout_ms / 1000) + 15,
                    )
                if result.success:
                    log.ok(
                        "Capture réussie : %s (%.0f Ko, %d ms, cookies %s)",
                        relative, result.size_bytes / 1024, result.duration_ms,
                        "fermés" if result.cookie_banner_closed else "non détectés",
                    )
                    await self._publish_result(job, relative)
                    return
                last_error = result.error or "capture vide"
            except BrowserUnavailable as exc:
                # Inutile de réessayer : on désactive le service et on prévient.
                self._unavailable_reason = str(exc)
                log.error("Captures désactivées — %s", exc)
                await self._publish_result(job, None)
                return
            except asyncio.TimeoutError:
                last_error = f"délai dépassé ({self._settings.timeout_ms} ms)"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{exc.__class__.__name__}: {exc}"

            if attempt < self._settings.max_attempts:
                log.check(
                    "Capture en échec (%s), tentative %d/%d — %s",
                    job.product.name, attempt + 1, self._settings.max_attempts, last_error,
                )
                await asyncio.sleep(2 * attempt)

        log.error(
            "Capture abandonnée après %d tentative(s) pour %s — %s",
            self._settings.max_attempts, job.product.name, last_error,
        )
        _remove_partial(target)
        await self._publish_result(job, None)

    def _cookie_selectors(self, site: str) -> tuple[str, ...]:
        """Sélecteurs cookies fournis par le plugin du site, s'il en déclare."""
        if self._registry is None:
            return ()
        try:
            return tuple(self._registry.get(site).cookie_selectors)
        except Exception:  # noqa: BLE001 — site sans plugin
            return ()

    async def _publish_result(self, job: CaptureJob, relative: Optional[Path]) -> None:
        """Republie sur le bus pour CHAQUE alerte partageant cette capture.

        La base, le WebSocket et Telegram prennent alors le relais. Le chemin
        est normalisé en style POSIX (« 2026-08-21/x.png ») : une base créée
        sous Windows reste exploitable sur Railway/Linux.
        """
        path = relative.as_posix() if relative else None
        # Retirer le groupe avant publication : tout changement arrivant
        # après ce point ouvrira une nouvelle capture.
        jobs = self._groups.pop(job.group_key, None) or [job]
        for member in jobs:
            payload = dict(member.payload)
            payload.pop(PENDING_FLAG, None)
            payload["screenshot_path"] = path
            await self._bus.publish(Event(EventType.SCREENSHOT_COMPLETED, payload))


def _remove_partial(target: Path) -> None:
    """Supprime un fichier partiel laissé par une capture ratée."""
    try:
        if target.exists():
            target.unlink()
    except OSError:
        pass
