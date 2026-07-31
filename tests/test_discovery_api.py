"""Routes /api/v1/discoveries : liste, validation, refus, blocage."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.models import DiscoveryStatus
from src.web.app import create_app
from tests.test_api import make_settings


@pytest.fixture()
def authed(tmp_path):
    app = create_app(settings=make_settings(tmp_path), config_path=None,
                     run_engine=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/login",
                               json={"username": "rayan", "password": "s3cret!"})
        assert response.status_code == 200
        yield client


def seed(client: TestClient, title: str = "Pokémon 30 Ans UPC",
         url: str = "https://boutique.test/p/upc") -> str:
    """Insère une fiche découverte directement par le repository."""
    ctx = client.app.state.ctx
    record, _ = asyncio.run_coroutine_threadsafe(
        ctx.discoveries.record_sighting(
            fingerprint=f"fp-{abs(hash(url)) % 10**12:012d}",
            site="micromania", url=url, canonical_url=url, title=title,
            image_url="https://boutique.test/img.png", price="189,99 €",
            source="sitemap",
        ),
        ctx.loop,
    ).result(timeout=5)
    return record.fingerprint


def test_requires_authentication(tmp_path):
    app = create_app(settings=make_settings(tmp_path), config_path=None,
                     run_engine=False)
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/discoveries").status_code == 401


def test_list_is_paginated_and_typed(authed):
    seed(authed)
    body = authed.get("/api/v1/discoveries").json()
    assert {"items", "total", "page", "page_size", "pages"} <= set(body)
    item = body["items"][0]
    assert item["title"] == "Pokémon 30 Ans UPC"
    assert item["status"] == "pending"
    assert item["image_url"].startswith("https://")


def test_filter_by_status(authed):
    seed(authed)
    assert authed.get("/api/v1/discoveries?status=pending").json()["total"] == 1
    assert authed.get("/api/v1/discoveries?status=imported").json()["total"] == 0


def test_search_by_title(authed):
    seed(authed, title="Pokémon 30 Ans UPC", url="https://b.test/p/a")
    seed(authed, title="Manette Pro", url="https://b.test/p/b")
    assert authed.get("/api/v1/discoveries?search=manette").json()["total"] == 1


def test_approve_creates_and_monitors_the_product(authed):
    fingerprint = seed(authed)
    response = authed.post(f"/api/v1/discoveries/{fingerprint}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "imported"
    assert body["product_uuid"]

    product = authed.get(f"/api/v1/products/{body['product_uuid']}").json()
    assert product["name"] == "Pokémon 30 Ans UPC"
    assert product["monitorable"] is True   # surveillé sans redémarrage


def test_approve_twice_is_rejected(authed):
    fingerprint = seed(authed)
    authed.post(f"/api/v1/discoveries/{fingerprint}/approve")
    assert authed.post(f"/api/v1/discoveries/{fingerprint}/approve").status_code == 409


def test_ignore_and_block(authed):
    first = seed(authed, url="https://b.test/p/a")
    second = seed(authed, url="https://b.test/p/b")

    assert authed.post(f"/api/v1/discoveries/{first}/ignore").json()["status"] == "ignored"
    assert authed.post(f"/api/v1/discoveries/{second}/block").json()["status"] == "blocked"


def test_unknown_fingerprint_returns_404(authed):
    assert authed.post("/api/v1/discoveries/inconnu/approve").status_code == 404
    assert authed.post("/api/v1/discoveries/inconnu/ignore").status_code == 404


def test_status_endpoint_reports_configuration(authed):
    seed(authed)
    body = authed.get("/api/v1/discoveries/status").json()
    assert body["mode"] in ("auto", "review", "rules")
    assert body["counts"]["pending"] == 1
    assert "micromania" in body["sites"]


def test_scan_is_rejected_when_discovery_disabled(authed):
    """config/discovery.yaml est livré désactivé : le refus doit être explicite."""
    response = authed.post("/api/v1/discoveries/scan")
    assert response.status_code == 409
    assert "discovery.yaml" in response.json()["detail"]


def test_deleting_product_detaches_the_discovery(authed):
    fingerprint = seed(authed)
    uuid = authed.post(f"/api/v1/discoveries/{fingerprint}/approve").json()["product_uuid"]
    authed.delete(f"/api/v1/products/{uuid}")

    record = next(
        item for item in authed.get("/api/v1/discoveries").json()["items"]
        if item["fingerprint"] == fingerprint
    )
    assert record["status"] == DiscoveryStatus.IGNORED.value
    assert record["product_uuid"] is None


def test_openapi_documents_discovery_routes(authed):
    paths = authed.get("/api/openapi.json").json()["paths"]
    assert "/api/v1/discoveries" in paths
    assert "/api/v1/discoveries/{fingerprint}/approve" in paths
    assert "/api/v1/discoveries/scan" in paths
