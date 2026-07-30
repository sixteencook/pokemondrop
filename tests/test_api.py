"""Tests d'intégration de l'API v1 (FastAPI TestClient, base SQLite jetable).

Le moteur n'est pas démarré (run_engine=False) : on teste le contrat de
l'API — auth, CRUD, pagination, enveloppes — pas la surveillance HTTP.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.config.settings import AppSettings, ScreenshotSettings
from src.web.app import create_app


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        telegram_bot_token="",
        telegram_chat_ids=(),
        log_level="ERROR",
        data_dir=tmp_path,
        log_dir=tmp_path / "logs",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
        dashboard_username="rayan",
        dashboard_password="s3cret!",
        secret_key="clef-de-test",
        token_ttl_hours=1,
        # Captures désactivées : les tests d'API ne lancent pas Chromium.
        screenshots=ScreenshotSettings(
            enabled=False, directory=tmp_path / "screenshots", retention_days=0
        ),
    )


@pytest.fixture()
def client(tmp_path):
    app = create_app(settings=make_settings(tmp_path), config_path=None, run_engine=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def authed(client):
    response = client.post("/api/v1/auth/login",
                           json={"username": "rayan", "password": "s3cret!"})
    assert response.status_code == 200
    return client


PRODUCT = {
    "name": "Pokémon 30 Ans ETB",
    "site": "micromania",
    "url": "",
    "check_interval": 60,
    "enabled": False,
    "priority": "high",
    "tags": ["Pokemon", "ETB", "pokemon"],
    "group": "pokemon-30-etb",
}


def test_health_is_public(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_protected_routes_require_auth(client):
    assert client.get("/api/v1/products").status_code == 401
    assert client.get("/api/v1/alerts").status_code == 401
    assert client.get("/api/v1/stats/overview").status_code == 401


def test_login_rejects_bad_credentials(client):
    response = client.post("/api/v1/auth/login",
                           json={"username": "rayan", "password": "faux"})
    assert response.status_code == 401


def test_login_me_logout_flow(authed):
    assert authed.get("/api/v1/auth/me").json() == {"username": "rayan"}
    assert authed.post("/api/v1/auth/logout").status_code == 204


def test_product_crud_flow(authed):
    created = authed.post("/api/v1/products", json=PRODUCT)
    assert created.status_code == 201
    body = created.json()
    assert body["uuid"]
    assert body["tags"] == ["pokemon", "etb"]  # normalisés, dédoublonnés
    assert body["monitorable"] is False  # URL vide

    uuid = body["uuid"]
    detail = authed.get(f"/api/v1/products/{uuid}")
    assert detail.status_code == 200

    patched = authed.patch(f"/api/v1/products/{uuid}",
                           json={"url": "https://example.com/p", "enabled": True})
    assert patched.status_code == 200
    assert patched.json()["monitorable"] is True

    assert authed.delete(f"/api/v1/products/{uuid}").status_code == 204
    assert authed.get(f"/api/v1/products/{uuid}").status_code == 404


def test_product_unknown_site_rejected(authed):
    response = authed.post("/api/v1/products", json={**PRODUCT, "site": "inconnu"})
    assert response.status_code == 422


def test_product_interval_too_short_rejected(authed):
    response = authed.post("/api/v1/products", json={**PRODUCT, "check_interval": 3})
    assert response.status_code == 422


def test_products_pagination_envelope(authed):
    for i in range(3):
        authed.post("/api/v1/products", json={**PRODUCT, "name": f"Produit {i}"})
    response = authed.get("/api/v1/products?page=1&page_size=2&sort=name&order=asc")
    body = response.json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["name"] == "Produit 0"


def test_check_now_requires_url(authed):
    uuid = authed.post("/api/v1/products", json=PRODUCT).json()["uuid"]
    response = authed.post(f"/api/v1/products/{uuid}/check")
    assert response.status_code == 409  # URL vide → conflit explicite


def test_stats_overview_shape(authed):
    body = authed.get("/api/v1/stats/overview").json()
    assert body["products_total"] == 0
    assert body["monitor_active"] is False
    assert "uptime_seconds" in body


def test_monitors_lists_plugins(authed):
    body = authed.get("/api/v1/monitors").json()
    sites = {entry["site"] for entry in body}
    assert "micromania" in sites


def test_logs_endpoint_envelope(authed):
    response = authed.get("/api/v1/logs?page_size=10")
    body = response.json()
    assert {"items", "total", "page", "page_size", "pages"} <= set(body)


def test_settings_masks_secrets(authed):
    body = authed.get("/api/v1/settings").json()
    assert body["telegram"]["configured"] is False
    assert body["auth_configured"] is True
    assert body["database"] == "sqlite"


def test_openapi_document_available(authed):
    response = authed.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/products" in paths
    assert "/api/v1/products/{uuid}/check" in paths
    assert "/api/v1/alerts/{alert_id}/screenshot" in paths


def test_settings_exposes_screenshot_config(authed):
    body = authed.get("/api/v1/settings").json()["screenshots"]
    assert body["enabled"] is False
    assert body["image_format"] == "png"
    assert body["pending"] == 0


def test_screenshot_endpoint_404_when_absent(authed):
    assert authed.get("/api/v1/alerts/999/screenshot").status_code == 404


def test_screenshot_endpoint_serves_image(authed, tmp_path):
    """Alerte avec capture : l'image est servie, et téléchargeable."""
    import asyncio

    ctx = authed.app.state.ctx
    alert_id = asyncio.run_coroutine_threadsafe(
        ctx.alerts.add("u1", "preorder_opened", "unavailable", "preorder",
                       "189,99 €", "https://example.com/p"),
        ctx.loop,
    ).result(timeout=5)

    capture = tmp_path / "screenshots" / "2026-08-21" / "shot.png"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    asyncio.run_coroutine_threadsafe(
        ctx.alerts.set_screenshot(alert_id, "2026-08-21/shot.png"), ctx.loop
    ).result(timeout=5)

    response = authed.get(f"/api/v1/alerts/{alert_id}/screenshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")

    download = authed.get(f"/api/v1/alerts/{alert_id}/screenshot?download=true")
    assert "attachment" in download.headers["content-disposition"]


def test_alert_list_exposes_screenshot_url(authed):
    import asyncio

    ctx = authed.app.state.ctx
    alert_id = asyncio.run_coroutine_threadsafe(
        ctx.alerts.add("u1", "back_in_stock", None, "in_stock", None, "https://x.fr"),
        ctx.loop,
    ).result(timeout=5)
    asyncio.run_coroutine_threadsafe(
        ctx.alerts.set_screenshot(alert_id, "2026-08-21/shot.png"), ctx.loop
    ).result(timeout=5)

    alert = next(
        item for item in authed.get("/api/v1/alerts").json()["items"]
        if item["id"] == alert_id
    )
    assert alert["screenshot_url"] == f"/api/v1/alerts/{alert_id}/screenshot"
