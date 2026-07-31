"""Périmètre d'analyse : les carrousels ne doivent jamais fausser le statut.

Cas réel observé sur une fiche Micromania : 145 boutons candidats sur la
page, dont tout le carrousel « produits similaires » avec ses propres
boutons « Ajouter au panier ». Sans restriction du périmètre :
  - le produit passait pour disponible à cause d'un AUTRE produit ;
  - le hash changeait à chaque rotation du carrousel, déclenchant des
    alertes « Bouton modifié » parasites.
"""

import httpx
import pytest

from src.models import Availability
from src.monitors.generic import MAX_ACTION_BUTTONS, GenericHtmlMonitor
from tests.helpers import make_product


@pytest.fixture
def monitor() -> GenericHtmlMonitor:
    return GenericHtmlMonitor(httpx.AsyncClient())


@pytest.fixture
def product():
    return make_product(url="https://example.com/p")


FILLER = "<p>" + "Description détaillée du produit. " * 20 + "</p>"


def page_with_carousel(main_button: str) -> str:
    return f"""
    <html lang="fr"><head><title>Fiche</title></head><body>
      <header><a href="/">Accueil</a></header>
      <main class="product-detail">
        <h1>Produit principal</h1>
        <span class="price">189,99 €</span>
        <button>{main_button}</button>
        {FILLER}
      </main>
      <section class="carousel recommended-products">
        <h2>Produits similaires</h2>
        <button>Ajouter au panier</button>
        <span class="price">59,99 €</span>
        <button>Ajouter au panier</button>
        <span class="price">799,99 €</span>
      </section>
      <footer><button>Ajouter au panier</button></footer>
    </body></html>
    """


def test_carousel_does_not_make_product_look_available(monitor, product):
    """Le produit est indisponible ; seul le carrousel propose « panier »."""
    snapshot = monitor.parse(page_with_carousel("Produit indisponible"), product)
    assert snapshot.availability is Availability.UNAVAILABLE
    assert not any("panier" in button.lower() for button in snapshot.buttons)


def test_optional_scope_selector_can_be_enabled_by_a_plugin(monitor, product):
    """La restriction est désactivée par défaut, mais reste disponible."""

    class ScopedMonitor(GenericHtmlMonitor):
        site_name = "scoped"
        product_scope_selectors = "#achat"

    html = f"""
    <html><body>
      <div id="achat"><button>Précommander</button>{FILLER}</div>
      <div id="autre"><button>Ajouter au panier</button>{FILLER}</div>
    </body></html>
    """
    snapshot = ScopedMonitor(httpx.AsyncClient()).parse(html, product)
    assert snapshot.availability is Availability.PREORDER
    assert snapshot.buttons == ["Précommander"]


def test_main_product_button_is_still_detected(monitor, product):
    snapshot = monitor.parse(page_with_carousel("Précommander"), product)
    assert snapshot.availability is Availability.PREORDER
    assert snapshot.buttons == ["Précommander"]


def test_price_comes_from_the_product_not_the_carousel(monitor, product):
    snapshot = monitor.parse(page_with_carousel("Précommander"), product)
    assert snapshot.price == "189,99 €"


def test_hash_is_stable_when_carousel_changes(monitor, product):
    """Une rotation du carrousel ne doit plus déclencher d'alerte."""
    first = monitor.parse(page_with_carousel("Précommander"), product)
    rotated = page_with_carousel("Précommander").replace(
        "799,99 €", "469,99 €"
    ).replace("Produits similaires", "Vous aimerez aussi")
    second = monitor.parse(rotated, product)
    assert first.content_hash == second.content_hash


def test_retained_buttons_are_capped(monitor, product):
    many = "".join(f"<button>Précommander {index}</button>" for index in range(30))
    html = f"<html><body><main class='product-detail'>{many}{FILLER}</main></body></html>"
    snapshot = monitor.parse(html, product)
    assert len(snapshot.buttons) <= MAX_ACTION_BUTTONS


def test_falls_back_to_whole_page_without_product_container(monitor, product):
    """Sans conteneur identifiable, on analyse tout : mieux que rien."""
    html = f"<html><body><div><button>Précommander</button>{FILLER}</div></body></html>"
    assert monitor.parse(html, product).availability is Availability.PREORDER


def test_tiny_scope_falls_back_to_whole_page(monitor, product):
    """Un <main> quasi vide ne doit pas masquer le contenu réel."""
    html = (
        "<html><body><main></main>"
        f"<div class='legacy'><button>Précommander</button>{FILLER}</div>"
        "</body></html>"
    )
    assert monitor.parse(html, product).availability is Availability.PREORDER


def test_noise_is_removed_even_without_product_container(monitor, product):
    """Cas réel : aucun conteneur exploitable, mais des carrousels partout.

    Le nettoyage doit s'appliquer quand même, sinon le carrousel continue
    de polluer le statut et le hash.
    """
    html = f"""
    <html><body>
      <nav><button>Ajouter au panier</button></nav>
      <div class="legacy-layout">
        <button>Produit indisponible</button>
        {FILLER}
      </div>
      <div class="carousel"><button>Ajouter au panier</button></div>
      <footer><button>Ajouter au panier</button></footer>
    </body></html>
    """
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.UNAVAILABLE
    assert not any("panier" in button.lower() for button in snapshot.buttons)


def test_telegram_truncates_long_transitions():
    from src.notifications.telegram import VALUE_PREVIEW_LIMIT, _short

    assert _short(None) == "—"
    assert _short("Précommander") == "Précommander"
    long_value = " | ".join(f"Bouton numéro {index}" for index in range(50))
    assert len(_short(long_value)) == VALUE_PREVIEW_LIMIT
    assert _short(long_value).endswith("…")
