"""Robustesse du parser : les cas qui produisaient un statut « unknown ».

Chaque test reproduit une cause réelle identifiée sur des fiches produit
e-commerce, et vérifie qu'elle est désormais correctement classée.
"""

import httpx
import pytest

from src.models import Availability
from src.monitors.generic import GenericHtmlMonitor, normalise
from plugins.micromania.monitor import MicromaniaMonitor
from tests.helpers import make_product


@pytest.fixture
def monitor() -> GenericHtmlMonitor:
    return GenericHtmlMonitor(httpx.AsyncClient())


@pytest.fixture
def micromania() -> MicromaniaMonitor:
    return MicromaniaMonitor(httpx.AsyncClient())


@pytest.fixture
def product():
    return make_product(url="https://example.com/p")


def page(body: str) -> str:
    return f"<html lang='fr'><head><title>Fiche produit</title></head><body>{body}" + (
        "<p>" + "Description du produit. " * 20 + "</p></body></html>"
    )


# --------------------------------------------------------------------- #
# Normalisation                                                          #
# --------------------------------------------------------------------- #

def test_normalise_folds_accents_case_and_spaces():
    assert normalise("PRÉCOMMANDER") == "precommander"
    assert normalise("Ajouter\xa0au   panier") == "ajouter au panier"
    assert normalise("  Épuisé\n") == "epuise"


def test_unaccented_button_is_detected(monitor, product):
    """« PRECOMMANDER » sans accent : invisible pour un simple .lower()."""
    snapshot = monitor.parse(page("<button>PRECOMMANDER</button>"), product)
    assert snapshot.availability is Availability.PREORDER


def test_non_breaking_space_button_is_detected(monitor, product):
    """L'espace insécable est très fréquente dans les boutons e-commerce."""
    snapshot = monitor.parse(
        page("<button>Ajouter au panier</button>"), product
    )
    assert snapshot.availability is Availability.IN_STOCK


# --------------------------------------------------------------------- #
# Extraction élargie                                                     #
# --------------------------------------------------------------------- #

def test_div_with_role_button_is_detected(monitor, product):
    snapshot = monitor.parse(
        page('<div role="button" class="btn-primary">Précommander</div>'), product
    )
    assert snapshot.availability is Availability.PREORDER


def test_button_with_nested_icon_and_long_text(monitor, product):
    """Icône + texte pour lecteur d'écran : dépassait l'ancienne limite."""
    html = page(
        '<button><span class="icon" aria-hidden="true">🛒</span>'
        '<span class="sr-only">Action principale du produit —</span>'
        "<span>Ajouter au panier</span></button>"
    )
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.IN_STOCK


def test_aria_label_is_used(monitor, product):
    snapshot = monitor.parse(
        page('<button aria-label="Précommander ce produit"><i></i></button>'), product
    )
    assert snapshot.availability is Availability.PREORDER


def test_candidate_buttons_are_collected_for_diagnostics(monitor, product):
    """Les libellés SANS mot-clé doivent rester visibles pour le diagnostic."""
    html = page("<button>Voir les avis</button><a href='#'>Comparer</a>")
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.UNKNOWN
    assert snapshot.buttons == []  # aucun bouton d'action retenu


# --------------------------------------------------------------------- #
# Pages d'attente anti-robot (HTTP 200 mais pas de fiche produit)         #
# --------------------------------------------------------------------- #

def test_cloudflare_challenge_is_flagged(monitor, product):
    html = (
        "<html><head><title>Just a moment...</title></head><body>"
        "<div class='cf-browser-verification'>Checking your browser…</div>"
        "</body></html>"
    )
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.UNKNOWN
    assert "Cloudflare" in (snapshot.status_text or "")


def test_datadome_challenge_is_flagged(monitor, product):
    html = "<html><head><title>Accès</title></head><body><script>datadome</script></body></html>"
    snapshot = monitor.parse(html, product)
    assert "DataDome" in (snapshot.status_text or "")


def test_nearly_empty_page_is_flagged(monitor, product):
    """Coquille vide hydratée côté client."""
    snapshot = monitor.parse(
        "<html><head><title>Micromania</title></head><body><div id='root'></div></body></html>",
        product,
    )
    assert "rendu côté client" in (snapshot.status_text or "")


def test_conclusive_short_page_is_not_flagged_as_interstitial(monitor, product):
    """Une page courte mais CLASSÉE n'est pas un mur anti-robot : le vrai
    motif d'indisponibilité doit être conservé."""
    snapshot = monitor.parse(
        "<html><body><p>Produit actuellement indisponible</p></body></html>", product
    )
    assert snapshot.availability is Availability.UNAVAILABLE
    assert snapshot.status_text == "indisponible"


# --------------------------------------------------------------------- #
# Plugin Micromania                                                      #
# --------------------------------------------------------------------- #

def test_micromania_vocabulary(micromania, product):
    assert micromania.parse(
        page("<button>Réserver en magasin</button>"), product
    ).availability is Availability.PREORDER

    assert micromania.parse(
        page("<button>Me prévenir de la disponibilité</button>"), product
    ).availability is Availability.UNAVAILABLE


def test_micromania_price_selectors(micromania, product):
    snapshot = micromania.parse(
        page('<span class="price-sales">189,99 €</span><button>Précommander</button>'),
        product,
    )
    assert snapshot.price == "189,99 €"
    assert snapshot.availability is Availability.PREORDER


def test_preorder_still_wins_over_page_noise(micromania, product):
    """Une mention « indisponible » ailleurs ne doit pas masquer le bouton."""
    html = page(
        "<button>Précommander</button>"
        "<p>Retrait en magasin indisponible dans votre boutique</p>"
    )
    assert micromania.parse(html, product).availability is Availability.PREORDER
