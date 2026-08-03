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
from src.models import (
    ChangeEvent,
    ChangeType,
    EventKind,
    EventScope,
    ProductConfig,
    ProductSnapshot,
)
from src.repositories import (
    AlertRepository,
    CheckRepository,
    EngineEventRepository,
    TimelineRepository,
)
from src.utils.logger import get_logger

log = get_logger("recorder")

#: Libellés métier affichés dans la timeline du dashboard.
#:
#: Chacun répond à « qu'est-ce qui a changé pour l'acheteur ? ». Aucun ne
#: décrit le HTML : « Bouton modifié » et « Page modifiée » ne sont plus
#: produits — ils ne subsistent que pour relire les lignes anciennes.
_TIMELINE_LABELS: dict[ChangeType, str] = {
    ChangeType.PRODUCT_APPEARED: "Produit découvert",
    ChangeType.PRODUCT_DELISTED: "Fiche retirée",
    ChangeType.PRICE_APPEARED: "Prix détecté",
    ChangeType.PRICE_CHANGED: "Prix modifié",
    ChangeType.PREORDER_OPENED: "Précommande ouverte",
    ChangeType.INVITATION_OPENED: "Invitation ouverte",
    ChangeType.BACK_IN_STOCK: "Retour en stock",
    ChangeType.WENT_OUT_OF_STOCK: "Rupture de stock",
    ChangeType.STATUS_CHANGED: "Disponibilité modifiée",
    # Hérités, jamais réémis.
    ChangeType.BUTTON_CHANGED: "Bouton modifié (événement retiré)",
    ChangeType.PAGE_CHANGED: "Page modifiée (événement retiré)",
}

class _NullEngineEvents:
    """Repository d'événements techniques inactif — n'écrit rien."""

    async def add(self, **_kwargs: object) -> None:
        return None


#: Événements qui n'alimentent QUE l'historique technique : ils ne
#: concernent pas un produit surveillé et n'ont donc ni check, ni alerte.
_ENGINE_ONLY_EVENTS = frozenset({
    EventType.DISCOVERY_SCAN_COMPLETED,
    EventType.CATALOG_PRODUCT_CREATED,
    EventType.CATALOG_OFFER_LINKED,
    EventType.CATALOG_MATCH_PENDING,
})

BASELINE_EVENT_TYPE = "baseline"
DISCOVERY_EVENT_TYPE = "discovered"
UNSTABLE_EVENT_TYPE = "unstable"


def timeline_label(change: ChangeEvent) -> str:
    """Libellé métier d'un changement.

    Les événements de vendeur nomment le marchand : « Amazon devient
    vendeur » est immédiatement compréhensible, « seller_became_official »
    ne l'est pas.

    Partagé par l'enregistreur (table timeline) et le WebSocket.
    """
    merchant = (change.product.site or "le marchand").capitalize()
    if change.change_type is ChangeType.SELLER_BECAME_OFFICIAL:
        return f"{merchant} devient vendeur"
    if change.change_type is ChangeType.SELLER_LEFT_BUYBOX:
        return f"{merchant} quitte la Buy Box"
    return _TIMELINE_LABELS.get(change.change_type, change.change_type.value)


class EventRecorder:
    def __init__(
        self,
        checks: CheckRepository,
        timeline: TimelineRepository,
        alerts: AlertRepository,
        engine_events: Optional[EngineEventRepository] = None,
    ) -> None:
        self._checks = checks
        self._timeline = timeline
        self._alerts = alerts
        #: Facultatif : sans lui, l'enregistreur fonctionne exactement comme
        #: avant (utile aux tests et aux montages partiels).
        self._engine_events = engine_events or _NullEngineEvents()

    def attach_to(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event, {
            EventType.CHECK_COMPLETED,
            EventType.CHECK_FAILED,
            EventType.BASELINE_RECORDED,
            EventType.CHECK_UNSTABLE,
            EventType.CHANGE_DETECTED,
            EventType.SCREENSHOT_COMPLETED,
            EventType.NOTIFICATION_SENT,
            EventType.NEW_PRODUCT_DISCOVERED,
            EventType.DISCOVERY_SCAN_COMPLETED,
            EventType.CATALOG_PRODUCT_CREATED,
            EventType.CATALOG_OFFER_LINKED,
            EventType.CATALOG_MATCH_PENDING,
        })

    async def _on_event(self, event: Event) -> None:
        if event.type in _ENGINE_ONLY_EVENTS:
            await self._record_engine_event(event)
            return
        if event.type is EventType.NEW_PRODUCT_DISCOVERED:
            await self._record_discovery(event)
            return

        product: Optional[ProductConfig] = event.payload.get("product")
        if product is None or not product.uuid:
            return  # produit non persisté (tests, CLI hors base) : rien à écrire

        if event.type is EventType.CHECK_COMPLETED:
            snapshot: ProductSnapshot = event.payload["snapshot"]
            # `observed` est la lecture réelle ; `snapshot` l'état affiché.
            # C'est la lecture réelle qui intéresse l'observabilité.
            observed: ProductSnapshot = event.payload.get("observed") or snapshot
            diagnostics = observed.diagnostics
            await self._checks.add(
                product.uuid,
                status="ok",
                availability=observed.availability.value,
                response_time_ms=event.payload.get("response_time_ms"),
                fetch_source=diagnostics.fetch_source.value,
                http_status=diagnostics.http_status,
                confidence=diagnostics.confidence,
            )
            await self._record_check_incidents(product, observed)
        elif event.type is EventType.CHECK_FAILED:
            await self._checks.add(
                product.uuid, status="error", error=event.payload.get("error"),
            )
            await self._engine_events.add(
                scope=EventScope.ENGINE, source=product.site,
                kind=EventKind.NETWORK_ERROR, product_uuid=product.uuid,
                detail=str(event.payload.get("error") or "")[:300],
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
        elif event.type is EventType.CHECK_UNSTABLE:
            # Trace visible dans la timeline, mais AUCUNE alerte : deux
            # lectures consécutives se sont contredites.
            await self._timeline.add(
                product.uuid,
                event_type=UNSTABLE_EVENT_TYPE,
                label="État instable — aucune notification",
                new_value=event.payload.get("observed"),
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
                    evidence_path=event.payload.get("evidence_path"),
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
            duration = event.payload.get("duration_ms")
            if duration is not None:
                await self._engine_events.add(
                    scope=EventScope.ENGINE, source=product.site,
                    kind=EventKind.SCREENSHOT, product_uuid=product.uuid,
                    detail=path or "capture échouée", duration_ms=duration,
                )
        elif event.type is EventType.NOTIFICATION_SENT:
            alert_id = event.payload.get("alert_id")
            if alert_id is not None:
                await self._alerts.mark_notified(alert_id)

    async def _record_engine_event(self, event: Event) -> None:
        """Consigne la vie de la Découverte et du Product Intelligence.

        Ces moteurs publiaient déjà tout ce qu'il faut : rien n'a été
        ajouté de leur côté. L'enregistreur se contente de traduire leurs
        événements métier en événements techniques datés et mesurés.
        """
        payload = event.payload
        duration = payload.get("duration_ms")

        if event.type is EventType.DISCOVERY_SCAN_COMPLETED:
            await self._engine_events.add(
                scope=EventScope.DISCOVERY, source="discovery",
                kind=EventKind.DISCOVERY_SCAN,
                detail=str(payload.get("summary") or ""), duration_ms=duration,
            )
            return

        product = payload.get("product")
        offer = payload.get("offer")
        site = getattr(offer, "site", None) or "intelligence"
        name = getattr(product, "name", "") or ""

        if event.type is EventType.CATALOG_PRODUCT_CREATED:
            kind, detail = EventKind.CATALOG_CREATED, name
        elif event.type is EventType.CATALOG_OFFER_LINKED:
            kind = EventKind.CATALOG_MERGED
            detail = str(payload.get("summary") or name)
        else:  # CATALOG_MATCH_PENDING
            kind = EventKind.CATALOG_PENDING
            detail = (
                f"score {payload.get('score')} "
                f"({payload.get('method') or 'méthode inconnue'})"
            )

        await self._engine_events.add(
            scope=EventScope.INTELLIGENCE, source=site, kind=kind,
            detail=detail, duration_ms=duration,
            product_uuid=getattr(offer, "monitored_uuid", None),
        )

    async def _record_check_incidents(
        self, product: ProductConfig, observed: ProductSnapshot
    ) -> None:
        """Consigne les seuls incidents techniques d'une vérification.

        Un cycle nominal n'écrit RIEN ici : la table reste petite et les
        agrégations de la page Santé restent rapides. Tout est déduit de
        ce que le plugin a déjà calculé — aucune analyse supplémentaire.
        """
        diagnostics = observed.diagnostics

        if diagnostics.blocked:
            await self._engine_events.add(
                scope=EventScope.PLUGIN, source=product.site,
                kind=EventKind.BLOCKED, product_uuid=product.uuid,
                detail=diagnostics.blocked_reason or "page non exploitable",
            )
        elif not observed.conclusive:
            await self._engine_events.add(
                scope=EventScope.PLUGIN, source=product.site,
                kind=EventKind.UNKNOWN_STATE, product_uuid=product.uuid,
                detail=diagnostics.blocked_reason or "état indéterminé",
            )

        if diagnostics.blocked_reason == "confiance insuffisante":
            await self._engine_events.add(
                scope=EventScope.PLUGIN, source=product.site,
                kind=EventKind.LOW_CONFIDENCE, product_uuid=product.uuid,
                detail=f"confiance {diagnostics.confidence}",
            )
        elif diagnostics.blocked_reason == "contexte de livraison incorrect":
            await self._engine_events.add(
                scope=EventScope.PLUGIN, source=product.site,
                kind=EventKind.LOCALE_MISMATCH, product_uuid=product.uuid,
                detail=diagnostics.blocked_reason,
            )

        if diagnostics.browser_fallback:
            await self._engine_events.add(
                scope=EventScope.PLUGIN, source=product.site,
                kind=EventKind.BROWSER_FALLBACK, product_uuid=product.uuid,
                detail="analyse infructueuse ou accès refusé en HTTP",
            )

        status = diagnostics.http_status
        if status is not None and status >= 400:
            kind = (
                EventKind.PAGE_MISSING if status == 404 else EventKind.HTTP_ERROR
            )
            await self._engine_events.add(
                scope=EventScope.PLUGIN, source=product.site, kind=kind,
                product_uuid=product.uuid, detail=f"HTTP {status}",
            )

    async def _record_discovery(self, event: Event) -> None:
        """Ouvre la timeline d'un produit créé par la découverte automatique."""
        product_uuid = event.payload.get("product_uuid")
        discovery = event.payload.get("discovery")
        if not product_uuid or discovery is None:
            return  # fiche en attente de validation : pas encore de produit
        await self._timeline.add(
            product_uuid,
            event_type=DISCOVERY_EVENT_TYPE,
            label="Produit découvert automatiquement",
            new_value=event.payload.get("site_label") or discovery.site,
            price=discovery.price,
        )
