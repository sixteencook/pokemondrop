"""Enregistreur : persiste les événements du bus en base de données.

Abonné de l'EventBus, il alimente :
  - la table checks     (chaque vérification, ok ou erreur → stats/graphiques) ;
  - la table timeline   (l'historique complet de chaque produit) ;
  - la table alerts     (les changements notifiables, marqués notified
                         lorsque l'envoi a réussi).

Le moteur et les notifications ignorent totalement son existence.
"""

from __future__ import annotations

from typing import Optional

from src.core.events import Event, EventBus, EventType
from src.models import Availability, ChangeEvent, ChangeType, ProductConfig, ProductSnapshot
from src.repositories import AlertRepository, CheckRepository, TimelineRepository
from src.utils.logger import get_logger

log = get_logger("recorder")

#: Libellés lisibles affichés dans la timeline du dashboard.
_TIMELINE_LABELS: dict[ChangeType, str] = {
    ChangeType.PRODUCT_APPEARED: "Fiche produit détectée",
    ChangeType.PRICE_APPEARED: "Prix détecté",
    ChangeType.PRICE_CHANGED: "Prix modifié",
    ChangeType.PREORDER_OPENED: "Précommande ouverte",
    ChangeType.BACK_IN_STOCK: "Retour en stock",
    ChangeType.BUTTON_CHANGED: "Bouton modifié",
    ChangeType.STATUS_CHANGED: "Statut modifié",
    ChangeType.PAGE_CHANGED: "Page modifiée",
}

BASELINE_EVENT_TYPE = "baseline"


def timeline_label(change: ChangeEvent) -> str:
    """Libellé affiné : une transition vers « indisponible » est une rupture.

    Partagé par l'enregistreur (table timeline) et le WebSocket.
    """
    if (
        change.change_type is ChangeType.STATUS_CHANGED
        and change.new_value == Availability.UNAVAILABLE.value
    ):
        return "Rupture de stock"
    return _TIMELINE_LABELS.get(change.change_type, change.change_type.value)


class EventRecorder:
    def __init__(
        self,
        checks: CheckRepository,
        timeline: TimelineRepository,
        alerts: AlertRepository,
    ) -> None:
        self._checks = checks
        self._timeline = timeline
        self._alerts = alerts

    def attach_to(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event, {
            EventType.CHECK_COMPLETED,
            EventType.CHECK_FAILED,
            EventType.BASELINE_RECORDED,
            EventType.CHANGE_DETECTED,
            EventType.SCREENSHOT_COMPLETED,
            EventType.NOTIFICATION_SENT,
        })

    async def _on_event(self, event: Event) -> None:
        product: Optional[ProductConfig] = event.payload.get("product")
        if product is None or not product.uuid:
            return  # produit non persisté (tests, CLI hors base) : rien à écrire

        if event.type is EventType.CHECK_COMPLETED:
            snapshot: ProductSnapshot = event.payload["snapshot"]
            await self._checks.add(
                product.uuid,
                status="ok",
                availability=snapshot.availability.value,
                response_time_ms=event.payload.get("response_time_ms"),
            )
        elif event.type is EventType.CHECK_FAILED:
            await self._checks.add(
                product.uuid, status="error", error=event.payload.get("error"),
            )
        elif event.type is EventType.BASELINE_RECORDED:
            snapshot = event.payload["snapshot"]
            await self._timeline.add(
                product.uuid,
                event_type=BASELINE_EVENT_TYPE,
                label="Surveillance démarrée — état initial enregistré",
                new_value=snapshot.availability.value,
                price=snapshot.price,
            )
        elif event.type is EventType.CHANGE_DETECTED:
            change: ChangeEvent = event.payload["change"]
            await self._timeline.add(
                product.uuid,
                event_type=change.change_type.value,
                label=timeline_label(change),
                old_value=change.old_value,
                new_value=change.new_value,
                price=change.snapshot.price,
            )
            if change.is_alert_worthy:
                alert_id = await self._alerts.add(
                    product.uuid,
                    change_type=change.change_type.value,
                    old_value=change.old_value,
                    new_value=change.new_value,
                    price=change.snapshot.price,
                    url=product.url,
                )
                # L'id est reporté dans le payload pour que NOTIFICATION_SENT
                # (publié par le NotificationManager) puisse le retrouver.
                event.payload["alert_id"] = alert_id
        elif event.type is EventType.SCREENSHOT_COMPLETED:
            alert_id = event.payload.get("alert_id")
            path = event.payload.get("screenshot_path")
            if alert_id is not None and path:
                # Seul le CHEMIN est stocké ; le fichier reste sur le disque.
                await self._alerts.set_screenshot(alert_id, path)
        elif event.type is EventType.NOTIFICATION_SENT:
            alert_id = event.payload.get("alert_id")
            if alert_id is not None:
                await self._alerts.mark_notified(alert_id)
