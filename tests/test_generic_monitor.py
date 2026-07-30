"""Tests de l'analyse HTML générique (aucun HTML spécifique à un site réel)."""

import httpx
import pytest

from src.models import Availability, ProductConfig
from src.monitors.generic import GenericHtmlMonitor


@pytest.fixture
def monitor() -> GenericHtmlMonitor:
    return GenericHtmlMonitor(httpx.AsyncClient())


@pytest.fixture
def product() -> ProductConfig:
    return ProductConfig(
        name="Produit test", site="generic", url="https://example.com",
        check_interval=60, enabled=True,
    )


def test_detects_preorder_button(monitor, product):
    html = "<html><body><h1>Produit</h1><button>Précommander</button></body></html>"
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.PREORDER
    assert "Précommander" in snapshot.buttons


def test_detects_add_to_cart(monitor, product):
    html = '<html><body><button class="btn">Ajouter au panier</button></body></html>'
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.IN_STOCK


def test_detects_unavailable(monitor, product):
    html = "<html><body><p>Produit actuellement indisponible</p></body></html>"
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.UNAVAILABLE
    assert snapshot.status_text == "indisponible"


def test_extracts_price(monitor, product):
    html = '<html><body><span class="product-price">119,99 €</span></body></html>'
    snapshot = monitor.parse(html, product)
    assert snapshot.price == "119,99 €"


def test_hash_stable_for_same_signals(monitor, product):
    html = "<html><body><button>Précommander</button><span class='price'>99,99 €</span></body></html>"
    a = monitor.parse(html, product)
    b = monitor.parse(html + "<!-- bannière du jour -->", product)
    assert a.content_hash == b.content_hash


def test_preorder_wins_over_unavailable(monitor, product):
    """Priorité : le bouton Précommander l'emporte sur une mention 'indisponible' ailleurs."""
    html = (
        "<html><body><button>Précommander</button>"
        "<p>Livraison indisponible en point relais</p></body></html>"
    )
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.PREORDER
