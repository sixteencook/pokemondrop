"""Event bus interne du Drop Monitor.

Le moteur de surveillance ne connaît aucun consommateur : il publie des
événements typés sur ce bus. Les abonnés (notifications Telegram, futur
broadcaster WebSocket, future écriture SQLite, futur service de captures
Playwright) s'enregistrent indépendamment.

Garanties :
  - un abonné qui lève une exception n'affecte ni les autres abonnés,
    ni le moteur (isolation totale) ;
  - les abonnés sont exécutés dans l'ORDRE D'ABONNEMENT : l'enregistreur
    en base est abonné avant les notifications, si bien qu'une alerte est
    toujours persistée avant d'être envoyée ;
  - un abonné peut filtrer les types d'événements qui l'intéressent ;
  - tous les événements portent un horodatage UTC ISO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from src.utils.logger import get_logger

log = get_logger("events")


class EventType(str, Enum):
    """Tous les événements pouvant transiter sur le bus."""

    ENGINE_STARTED = "engine_started"
    ENGINE_STOPPED = "engine_stopped"
    CHECK_COMPLETED = "check_completed"      # un check a abouti (avec ou sans changement)
    CHECK_FAILED = "check_failed"            # échec réseau après épuisement des retries
    BASELINE_RECORDED = "baseline_recorded"  # premier passage d'un produit (timeline)
    CHANGE_DETECTED = "change_detected"      # un changement significatif (→ alerte)
    NEW_PRODUCT_DISCOVERED = "new_product_discovered"   # fiche inédite trouvée
    DISCOVERY_SCAN_COMPLETED = "discovery_scan_completed"
    CATALOG_PRODUCT_CREATED = "catalog_product_created"  # produit canonique inédit
    CATALOG_OFFER_LINKED = "catalog_offer_linked"        # offre rattachée à un produit
    CATALOG_MATCH_PENDING = "catalog_match_pending"      # fusion à valider
    SCREENSHOT_COMPLETED = "screenshot_completed"  # capture terminée (réussie ou non)
    NOTIFICATION_SENT = "notification_sent"
    NOTIFICATION_FAILED = "notification_failed"


@dataclass(frozen=True)
class Event:
    """Un événement du bus. Le payload contient les objets métier concernés
    (ProductConfig, ProductSnapshot, ChangeEvent, response_time_ms, error…)."""

    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


#: Signature d'un abonné : coroutine recevant l'événement.
EventHandler = Callable[[Event], Awaitable[None]]

#: Clé de payload posée par le service de captures sur CHANGE_DETECTED pour
#: signaler aux notifications qu'une capture est en cours : elles patientent
#: alors l'événement SCREENSHOT_COMPLETED afin de n'envoyer qu'un seul
#: message enrichi (photo + légende).
SCREENSHOT_PENDING_KEY = "screenshot_pending"


class EventBus:
    """Bus de publication/abonnement asynchrone en mémoire."""

    def __init__(self) -> None:
        self._subscribers: list[tuple[EventHandler, Optional[frozenset[EventType]]]] = []

    def subscribe(
        self,
        handler: EventHandler,
        event_types: Optional[set[EventType]] = None,
    ) -> None:
        """Enregistre un abonné.

        Args:
            handler: coroutine appelée pour chaque événement.
            event_types: si fourni, l'abonné ne reçoit que ces types ;
                sinon il reçoit tout.
        """
        types = frozenset(event_types) if event_types else None
        self._subscribers.append((handler, types))

    async def publish(self, event: Event) -> None:
        """Diffuse l'événement aux abonnés concernés, dans l'ordre d'abonnement.

        Toute exception est loggée et n'interrompt jamais ni les autres
        abonnés ni l'appelant.
        """
        for handler, types in self._subscribers:
            if types is not None and event.type not in types:
                continue
            try:
                await handler(event)
            except Exception as exc:  # noqa: BLE001 — isolation volontaire
                log.error(
                    "Abonné %s en échec sur %s : %s",
                    getattr(handler, "__qualname__", repr(handler)),
                    event.type.value,
                    exc,
                )
