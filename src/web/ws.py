"""Temps réel : WebSocket unique multiplexé (/api/v1/ws).

Tous les messages partagent la même enveloppe :

    {"type": "...", "payload": {...}, "ts": "2026-07-30T14:03:12+00:00"}

Types émis par le serveur :
    hello         connexion acceptée (utilisateur, watchers actifs)
    check         un check vient de se terminer (ok ou erreur)
    timeline      nouvel événement de timeline (baseline, changement…)
    alert         changement notifiable détecté (toast + refresh alertes)
    screenshot    capture terminée (alert_id, path ou null) → miniature
    discovery     fiche produit inédite trouvée par la découverte
    discovery_scan bilan d'un balayage de découverte
    catalog       produit canonique créé, ou offre rattachée
    alert_status  résultat de l'envoi Telegram (alert_id, delivered)
    engine        moteur démarré / arrêté
    log           nouvelle ligne de log
    ping / pong   heartbeat (le client peut aussi envoyer "ping")

Authentification : cookie httpOnly de session, ou `?token=<jwt>` pour les
clients hors navigateur. Socket refusé → fermeture code 4401.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from src.core.events import Event, EventBus, EventType
from src.models import ChangeEvent, ProductConfig, ProductSnapshot
from src.services.recorder import BASELINE_EVENT_TYPE, timeline_label
from src.utils.logger import CHECK_LEVEL, _ConsoleFormatter, get_logger
from src.web.security import COOKIE_NAME, decode_token, effective_secret

log = get_logger("ws")  # les logs « ws » ne sont pas rediffusés (anti-boucle)

HEARTBEAT_SECONDS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def message(msg_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": msg_type, "payload": payload, "ts": _now()}


def _product_payload(product: ProductConfig) -> dict[str, Any]:
    return {"uuid": product.uuid, "name": product.name, "site": product.site,
            "url": product.url}


def _snapshot_payload(snapshot: ProductSnapshot) -> dict[str, Any]:
    return {
        "availability": snapshot.availability.value,
        "price": snapshot.price,
        "page_exists": snapshot.page_exists,
        "checked_at": snapshot.checked_at,
    }


class WsHub:
    """Registre des sockets connectés + diffusion."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def register(self, websocket: WebSocket) -> None:
        self._clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        """Envoie à tous les clients ; un socket mort est simplement retiré."""
        for websocket in list(self._clients):
            try:
                if websocket.application_state is WebSocketState.CONNECTED:
                    await websocket.send_json(msg)
                else:
                    self._clients.discard(websocket)
            except Exception:  # noqa: BLE001 — client parti, rien de grave
                self._clients.discard(websocket)


class EventBroadcaster:
    """Abonné du bus : traduit les événements moteur en messages WebSocket."""

    def __init__(self, hub: WsHub) -> None:
        self._hub = hub

    def attach_to(self, bus: EventBus) -> None:
        """À abonner APRÈS l'EventRecorder (pour disposer d'alert_id) et
        AVANT les notifications (le dashboard n'attend pas Telegram)."""
        bus.subscribe(self._on_event, {
            EventType.ENGINE_STARTED,
            EventType.ENGINE_STOPPED,
            EventType.CHECK_COMPLETED,
            EventType.CHECK_FAILED,
            EventType.BASELINE_RECORDED,
            EventType.CHANGE_DETECTED,
            EventType.SCREENSHOT_COMPLETED,
            EventType.NOTIFICATION_SENT,
            EventType.NOTIFICATION_FAILED,
            EventType.NEW_PRODUCT_DISCOVERED,
            EventType.DISCOVERY_SCAN_COMPLETED,
            EventType.CATALOG_PRODUCT_CREATED,
            EventType.CATALOG_OFFER_LINKED,
            EventType.CATALOG_MATCH_PENDING,
        })

    async def _on_event(self, event: Event) -> None:
        for msg in self._translate(event):
            await self._hub.broadcast(msg)

    def _translate(self, event: Event) -> list[dict[str, Any]]:
        payload = event.payload
        product: Optional[ProductConfig] = payload.get("product")

        if event.type in (EventType.ENGINE_STARTED, EventType.ENGINE_STOPPED):
            return [message("engine", {"running": event.type is EventType.ENGINE_STARTED})]

        if event.type in (
            EventType.CATALOG_PRODUCT_CREATED,
            EventType.CATALOG_OFFER_LINKED,
            EventType.CATALOG_MATCH_PENDING,
        ):
            catalog_product = payload.get("product")
            offer = payload.get("offer")
            return [message("catalog", {
                "kind": event.type.value,
                "product_uuid": getattr(catalog_product, "uuid", None),
                "product_name": getattr(catalog_product, "name", None),
                "site": getattr(offer, "site", None),
                "score": payload.get("score"),
                "summary": payload.get("summary", ""),
            })]

        if event.type is EventType.DISCOVERY_SCAN_COMPLETED:
            return [message("discovery_scan", {"summary": payload.get("summary", "")})]

        if event.type is EventType.NEW_PRODUCT_DISCOVERED:
            discovery = payload.get("discovery")
            if discovery is None:
                return []
            return [message("discovery", {
                "fingerprint": payload.get("fingerprint"),
                "site": discovery.site,
                "site_label": payload.get("site_label", discovery.site),
                "title": discovery.title,
                "url": discovery.url,
                "image_url": discovery.image_url,
                "price": discovery.price,
                "status": payload.get("status"),
                "imported": bool(payload.get("imported")),
                "product_uuid": payload.get("product_uuid"),
            })]

        if product is None:
            return []

        if event.type is EventType.CHECK_COMPLETED:
            snapshot: ProductSnapshot = payload["snapshot"]
            return [message("check", {
                "product": _product_payload(product),
                "status": "ok",
                **_snapshot_payload(snapshot),
                "response_time_ms": payload.get("response_time_ms"),
                "changes": payload.get("changes", 0),
            })]

        if event.type is EventType.CHECK_FAILED:
            return [message("check", {
                "product": _product_payload(product),
                "status": "error",
                "error": payload.get("error"),
            })]

        if event.type is EventType.BASELINE_RECORDED:
            snapshot = payload["snapshot"]
            return [message("timeline", {
                "product": _product_payload(product),
                "event_type": BASELINE_EVENT_TYPE,
                "label": "Surveillance démarrée — état initial enregistré",
                "new_value": snapshot.availability.value,
                "price": snapshot.price,
            })]

        if event.type is EventType.CHANGE_DETECTED:
            change: ChangeEvent = payload["change"]
            timeline_msg = message("timeline", {
                "product": _product_payload(product),
                "event_type": change.change_type.value,
                "label": timeline_label(change),
                "old_value": change.old_value,
                "new_value": change.new_value,
                "price": change.snapshot.price,
            })
            if not change.is_alert_worthy:
                return [timeline_msg]
            return [timeline_msg, message("alert", {
                "product": _product_payload(product),
                "alert_id": payload.get("alert_id"),
                "change_type": change.change_type.value,
                "label": timeline_label(change),
                "old_value": change.old_value,
                "new_value": change.new_value,
                "price": change.snapshot.price,
            })]

        if event.type is EventType.SCREENSHOT_COMPLETED:
            return [message("screenshot", {
                "product": _product_payload(product),
                "alert_id": payload.get("alert_id"),
                "path": payload.get("screenshot_path"),
                "success": payload.get("screenshot_path") is not None,
            })]

        if event.type in (EventType.NOTIFICATION_SENT, EventType.NOTIFICATION_FAILED):
            return [message("alert_status", {
                "alert_id": payload.get("alert_id"),
                "delivered": event.type is EventType.NOTIFICATION_SENT,
            })]

        return []


class WsLogHandler(logging.Handler):
    """Handler de logging → WebSocket, via une file drainée dans l'event loop.

    `emit()` peut être appelé depuis n'importe quel thread : le passage de
    frontière se fait par call_soon_threadsafe. File bornée : si le dashboard
    ne suit pas, les lignes excédentaires sont silencieusement abandonnées
    (les fichiers logs/ restent complets).
    """

    def __init__(self, hub: WsHub, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(level=CHECK_LEVEL)
        self._hub = hub
        self._loop = loop
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("ws"):
            return  # anti-boucle : ne pas rediffuser les logs du hub lui-même
        entry = {
            "time": logging.Formatter().formatTime(record, "%H:%M:%S"),
            "level": _ConsoleFormatter._LABELS.get(record.levelno, record.levelname),
            "logger": record.name,
            "message": record.getMessage(),
        }
        try:
            self._loop.call_soon_threadsafe(self._enqueue, entry)
        except RuntimeError:
            pass  # loop fermée (arrêt du serveur)

    def _enqueue(self, entry: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            pass

    async def pump(self) -> None:
        """Tâche de fond : diffuse les lignes de log aux clients connectés."""
        while True:
            entry = await self._queue.get()
            if self._hub.client_count:
                await self._hub.broadcast(message("log", entry))


async def _heartbeat(websocket: WebSocket) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        await websocket.send_json(message("ping", {}))


def register_websocket(app: FastAPI) -> None:
    """Déclare la route WebSocket sur l'application."""

    @app.websocket("/api/v1/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        ctx = websocket.app.state.ctx
        await websocket.accept()

        token = websocket.cookies.get(COOKIE_NAME) or websocket.query_params.get("token")
        username = (
            decode_token(token, effective_secret(ctx.settings.secret_key))
            if token else None
        )
        if not ctx.settings.auth_configured or username is None:
            await websocket.close(code=4401, reason="Authentification requise")
            return

        hub: WsHub = ctx.hub
        # hello d'abord, enregistrement ensuite : garantit que « hello » est
        # toujours le premier message reçu (avant tout broadcast).
        await websocket.send_json(message("hello", {
            "user": username,
            "watchers_active": ctx.engine.active_count,
        }))
        hub.register(websocket)
        log.check("Client WebSocket connecté (%d au total)", hub.client_count)

        heartbeat_task = asyncio.create_task(_heartbeat(websocket))
        try:
            while True:
                raw = await websocket.receive_text()
                if raw.strip().lower() in ('"ping"', "ping", '{"type": "ping"}',
                                           '{"type":"ping"}'):
                    await websocket.send_json(message("pong", {}))
        except WebSocketDisconnect:
            pass
        finally:
            heartbeat_task.cancel()
            hub.disconnect(websocket)
            log.check("Client WebSocket déconnecté (%d restant(s))", hub.client_count)
