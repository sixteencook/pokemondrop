"""Routes /api/v1/catalog : produits canoniques, offres, fusions."""

import pytest
from fastapi.testclient import TestClient

from src.web.app import create_app
from tests.test_api import make_settings

EAN = "4006381333931"


@pytest.fixture()
def authed(tmp_path):
    app = create_app(settings=make_settings(tmp_path), config_path=None,
                     run_engine=False)
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/login",
                           json={"username": "rayan", "password": "s3cret!"}
                           ).status_code == 200
        yield client


def add(client: TestClient, **overrides) -> dict:
    body = {
        "url": "https://micromania.fr/p/upc.html",
        "title": "Pokémon 30 Ans Ultra Premium Collection",
        "site": "micromania",
        "price": "189,99 €",
        "ean": EAN,
    }
    body.update(overrides)
    response = client.post("/api/v1/catalog/products", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_requires_authentication(tmp_path):
    app = create_app(settings=make_settings(tmp_path), config_path=None,
                     run_engine=False)
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/catalog/products").status_code == 401


def test_manual_add_creates_product_with_one_offer(authed):
    product = add(authed)
    assert product["ean"] == EAN
    assert len(product["offers"]) == 1
    assert product["offers"][0]["site"] == "micromania"
    # Le produit canonique ne porte AUCUNE URL.
    assert "url" not in product


def test_two_merchants_same_ean_give_one_product(authed):
    first = add(authed)
    second = add(
        authed, site="fnac", url="https://fnac.com/a/upc",
        title="POKEMON 30 ANS UPC", price="190,00 €",
    )
    assert second["uuid"] == first["uuid"]
    assert len(second["offers"]) == 2
    assert {offer["site"] for offer in second["offers"]} == {"micromania", "fnac"}

    listing = authed.get("/api/v1/catalog/products").json()
    assert listing["total"] == 1     # une seule ligne dans le dashboard


def test_product_detail_and_offer_history(authed):
    product = add(authed)
    detail = authed.get(f"/api/v1/catalog/products/{product['uuid']}").json()
    assert detail["name"] == product["name"]

    offer_uuid = detail["offers"][0]["uuid"]
    history = authed.get(f"/api/v1/catalog/offers/{offer_uuid}/history").json()
    assert len(history) >= 1
    assert history[0]["status"] == "active"


def test_search_filters_products(authed):
    add(authed)
    add(authed, site="fnac", url="https://fnac.com/p/manette",
        title="Manette PS5", ean=None)
    assert authed.get("/api/v1/catalog/products?search=manette").json()["total"] == 1


def test_status_reports_thresholds_and_methods(authed):
    body = authed.get("/api/v1/catalog/status").json()
    assert body["merge_threshold"] == 90
    assert body["methods"][0].startswith("ean")
    assert body["enabled"] is True


def test_low_confidence_creates_a_suggestion(authed):
    """Même nom, aucun identifiant : proposé, jamais fusionné en silence."""
    add(authed, ean=None)
    add(authed, ean=None, site="fnac", url="https://fnac.com/p/upc")

    suggestions = authed.get("/api/v1/catalog/suggestions").json()
    assert len(suggestions) == 1
    assert suggestions[0]["score"] == 70
    assert suggestions[0]["method"] == "name_only"


def test_accepting_a_suggestion_merges_the_offers(authed):
    add(authed, ean=None)
    add(authed, ean=None, site="fnac", url="https://fnac.com/p/upc")
    suggestion = authed.get("/api/v1/catalog/suggestions").json()[0]

    merged = authed.post(
        f"/api/v1/catalog/suggestions/{suggestion['id']}/accept"
    ).json()
    assert len(merged["offers"]) == 2
    assert authed.get("/api/v1/catalog/suggestions").json() == []


def test_merged_shell_disappears_from_the_catalogue(authed):
    """Après fusion, plus AUCUN doublon visible — mais rien n'est perdu."""
    add(authed, ean=None)
    add(authed, ean=None, site="fnac", url="https://fnac.com/p/upc")
    suggestion = authed.get("/api/v1/catalog/suggestions").json()[0]
    authed.post(f"/api/v1/catalog/suggestions/{suggestion['id']}/accept")

    assert authed.get("/api/v1/catalog/products").json()["total"] == 1
    # La fiche vidée reste consultable si on la demande explicitement.
    assert authed.get(
        "/api/v1/catalog/products?include_empty=true"
    ).json()["total"] == 2


def test_rejecting_a_suggestion_keeps_products_separate(authed):
    add(authed, ean=None)
    add(authed, ean=None, site="fnac", url="https://fnac.com/p/upc")
    suggestion = authed.get("/api/v1/catalog/suggestions").json()[0]

    assert authed.post(
        f"/api/v1/catalog/suggestions/{suggestion['id']}/reject"
    ).status_code == 204
    assert authed.get("/api/v1/catalog/suggestions").json() == []
    assert authed.get("/api/v1/catalog/products").json()["total"] == 2


def test_cross_site_search_is_refused_when_disabled(authed):
    product = add(authed)
    response = authed.post(f"/api/v1/catalog/products/{product['uuid']}/find-offers")
    assert response.status_code == 409
    assert "cross_site_search" in response.json()["detail"]


def test_unknown_product_returns_404(authed):
    assert authed.get("/api/v1/catalog/products/inconnu").status_code == 404
    assert authed.get("/api/v1/catalog/offers/inconnu/history").status_code == 404


def test_openapi_documents_catalog_routes(authed):
    paths = authed.get("/api/openapi.json").json()["paths"]
    for route in (
        "/api/v1/catalog/products",
        "/api/v1/catalog/products/{product_uuid}",
        "/api/v1/catalog/suggestions",
        "/api/v1/catalog/products/{product_uuid}/find-offers",
    ):
        assert route in paths


def test_existing_routes_are_untouched(authed):
    """Garde-fou anti-régression : l'API existante répond toujours."""
    for route in ("/api/v1/products", "/api/v1/alerts", "/api/v1/discoveries",
                  "/api/v1/stats/overview", "/api/v1/monitors"):
        assert authed.get(route).status_code == 200
