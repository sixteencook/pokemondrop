"""Tests du WebSocket temps réel.

Le TestClient exécute l'application dans un thread avec sa propre event
loop : on y injecte les événements via run_coroutine_threadsafe sur le bus.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.core.events import Event, EventType
from src.models import Availability, ChangeEvent, ChangeType, ProductSnapshot
from src.utils.logger import get_logger
from src.web.app import create_app
from tests.helpers import make_product
from tests.test_api import make_settings

WS_URL = "/api/v1/ws"


@pytest.fixture()
def app_and_client(tmp_path):
    app = create_app(settings=make_settings(tmp_path), config_path=None, run_engine=False)
    with TestClient(app) as client:
        yield app, client


def _login_token(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login",
                           json={"username": "rayan", "password": "s3cret!"})
    assert response.status_code == 200
    return client.cookies.get("dm_token")


def _publish(app, event: Event) -> None:
    ctx = app.state.ctx
    asyncio.run_coroutine_threadsafe(ctx.bus.publish(event), ctx.loop).result(timeout=5)


def _receive_until(ws, msg_type: str, attempts: int = 10) -> dict:
    """Ignore les messages intercalés (logs…) jusqu'au type attendu."""
    for _ in range(attempts):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Message « {msg_type} » jamais reçu")


def test_ws_rejects_unauthenticated(app_and_client):
    _, client = app_and_client
    with client.websocket_connect(WS_URL) as ws:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 4401


def test_ws_hello_and_ping(app_and_client):
    _, client = app_and_client
    token = _login_token(client)
    with client.websocket_connect(f"{WS_URL}?token={token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["payload"]["user"] == "rayan"

        ws.send_text("ping")
        assert _receive_until(ws, "pong")


def test_ws_receives_check_events(app_and_client):
    app, client = app_and_client
    token = _login_token(client)
    product = make_product(uuid="u1")
    snapshot = ProductSnapshot(availability=Availability.UNAVAILABLE, page_exists=True)

    with client.websocket_connect(f"{WS_URL}?token={token}") as ws:
        ws.receive_json()  # hello
        _publish(app, Event(EventType.CHECK_COMPLETED, {
            "product": product, "snapshot": snapshot,
            "response_time_ms": 123, "changes": 0,
        }))
        msg = _receive_until(ws, "check")
        assert msg["payload"]["product"]["uuid"] == "u1"
        assert msg["payload"]["status"] == "ok"
        assert msg["payload"]["response_time_ms"] == 123


def test_ws_change_produces_timeline_then_alert(app_and_client):
    app, client = app_and_client
    token = _login_token(client)
    product = make_product(uuid="u1")
    snapshot = ProductSnapshot(availability=Availability.PREORDER,
                               price="119,99 €", page_exists=True)
    change = ChangeEvent(product=product, change_type=ChangeType.PREORDER_OPENED,
                         old_value="unavailable", new_value="preorder",
                         snapshot=snapshot)

    with client.websocket_connect(f"{WS_URL}?token={token}") as ws:
        ws.receive_json()  # hello
        _publish(app, Event(EventType.CHANGE_DETECTED,
                            {"product": product, "change": change, "snapshot": snapshot}))
        timeline_msg = _receive_until(ws, "timeline")
        assert timeline_msg["payload"]["label"] == "Précommande ouverte"
        alert_msg = _receive_until(ws, "alert")
        assert alert_msg["payload"]["change_type"] == "preorder_opened"
        # L'alerte a été persistée AVANT diffusion : alert_id présent.
        assert alert_msg["payload"]["alert_id"] is not None


def test_ws_broadcasts_logs(app_and_client):
    app, client = app_and_client
    token = _login_token(client)
    with client.websocket_connect(f"{WS_URL}?token={token}") as ws:
        ws.receive_json()  # hello
        get_logger("engine").ok("ligne de test websocket")
        msg = _receive_until(ws, "log")
        assert "ligne de test websocket" in msg["payload"]["message"]
        assert msg["payload"]["level"] == "INFO"
