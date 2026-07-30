"""Diffusion des alertes vers tous les canaux de notification actifs.

Le manager est un ABONNÉ de l'event bus. Il écoute deux événements :

  CHANGE_DETECTED       → envoi immédiat, SAUF si une capture d'écran a été
                          mise en file (drapeau `screenshot_pending`) : on
                          patiente alors pour n'envoyer qu'un seul message
                          Telegram enrichi (photo + légende).
  SCREENSHOT_COMPLETED  → envoi de l'alerte différée, avec la capture si
                          elle a abouti, en texte seul sinon.

Une alerte n'est donc jamais perdue, même si Playwright échoue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.core.events import SCREENSHOT_PENDING_KEY, Event, EventBus, EventType
from src.models import ChangeEvent
from src.notifications.base import BaseNotifier
from src.utils.logger import get_logger

log = get_logger("notifications")


class NotificationManager:
    """Fan-out vers les canaux enregistrés."""

    def __init__(self, screenshots_dir: Optional[Path] = None) -> None:
        self._notifiers: list[BaseNotifier] = []
        self._bus: EventBus | None = None
        self._screenshots_dir = screenshots_dir

    def register(self, notifier: BaseNotifier) -> None:
        self._notifiers.append(notifier)
        log.ok("Canal de notification actif : %s", notifier.channel_name)

    @property
    def has_channels(self) -> bool:
        return bool(self._notifiers)

    def attach_to(self, bus: EventBus) -> None:
        """S'abonne EN DERNIER : l'alerte est d'abord persistée (alert_id posé),
        la capture mise en file et le dashboard prévenu."""
        self._bus = bus
        bus.subscribe(self._on_change_detected, {EventType.CHANGE_DETECTED})
        bus.subscribe(self._on_screenshot_completed, {EventType.SCREENSHOT_COMPLETED})

    # ------------------------------------------------------------------ #
    # Réception                                                           #
    # ------------------------------------------------------------------ #

    async def _on_change_detected(self, event: Event) -> None:
        if event.payload.get(SCREENSHOT_PENDING_KEY):
            # Une capture est en cours : l'envoi aura lieu à sa complétion.
            return
        await self._handle(event, screenshot=None)

    async def _on_screenshot_completed(self, event: Event) -> None:
        await self._handle(event, screenshot=self._resolve(event.payload))

    def _resolve(self, payload: dict) -> Optional[Path]:
        """Chemin absolu de la capture (le payload ne porte que le relatif)."""
        relative = payload.get("screenshot_path")
        if not relative or self._screenshots_dir is None:
            return None
        candidate = self._screenshots_dir / relative
        return candidate if candidate.is_file() else None

    async def _handle(self, event: Event, screenshot: Optional[Path]) -> None:
        change = event.payload.get("change")
        if not isinstance(change, ChangeEvent):
            return
        delivered = await self.dispatch(change, screenshot)
        if self._bus is not None and change.is_alert_worthy:
            result_type = (
                EventType.NOTIFICATION_SENT if delivered else EventType.NOTIFICATION_FAILED
            )
            payload = dict(event.payload)
            payload.pop(SCREENSHOT_PENDING_KEY, None)
            await self._bus.publish(Event(result_type, payload))

    # ------------------------------------------------------------------ #
    # Diffusion                                                           #
    # ------------------------------------------------------------------ #

    async def dispatch(
        self, event: ChangeEvent, screenshot: Optional[Path] = None
    ) -> bool:
        """Envoie l'événement sur chaque canal ; les échecs sont loggés, pas
        bloquants. Retourne True si au moins un canal a délivré l'alerte."""
        if not event.is_alert_worthy:
            return False
        delivered = False
        for notifier in self._notifiers:
            success = await notifier.send(event, screenshot)
            if success:
                delivered = True
                log.alert(
                    "Alerte envoyée (%s%s) — %s : %s",
                    notifier.channel_name,
                    " + capture" if screenshot else "",
                    event.product.name,
                    event.change_type.value,
                )
            else:
                log.error(
                    "Échec d'envoi (%s) — %s : %s",
                    notifier.channel_name,
                    event.product.name,
                    event.change_type.value,
                )
        return delivered
