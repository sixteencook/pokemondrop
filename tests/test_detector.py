"""Tests du détecteur de changements (logique anti-spam incluse)."""

from src.core.detector import detect_changes
from src.models import Availability, ChangeType, ProductConfig, ProductSnapshot


def make_product() -> ProductConfig:
    return ProductConfig(
        name="Pokémon 30 Ans ETB",
        site="micromania",
        url="https://example.com/produit",
        check_interval=60,
        enabled=True,
    )


def snap(**kwargs) -> ProductSnapshot:
    defaults = dict(page_exists=True, availability=Availability.UNAVAILABLE)
    defaults.update(kwargs)
    return ProductSnapshot(**defaults)


def test_first_run_produces_no_events():
    """Premier lancement : baseline silencieuse, aucune alerte."""
    events = detect_changes(make_product(), None, snap(availability=Availability.PREORDER))
    assert events == []


def test_no_change_produces_no_events():
    old = snap(price="119,99 €", content_hash="abc")
    new = snap(price="119,99 €", content_hash="abc")
    assert detect_changes(make_product(), old, new) == []


def test_preorder_opened():
    old = snap(availability=Availability.UNAVAILABLE)
    new = snap(availability=Availability.PREORDER, buttons=["Précommander"])
    events = detect_changes(make_product(), old, new)
    types = [e.change_type for e in events]
    assert ChangeType.PREORDER_OPENED in types
    # Le changement de bouton lié à la dispo n'est pas doublonné.
    assert ChangeType.BUTTON_CHANGED not in types


def test_back_in_stock():
    old = snap(availability=Availability.UNAVAILABLE)
    new = snap(availability=Availability.IN_STOCK)
    events = detect_changes(make_product(), old, new)
    assert [e.change_type for e in events] == [ChangeType.BACK_IN_STOCK]


def test_product_page_appears():
    old = ProductSnapshot(page_exists=False)
    new = snap(availability=Availability.UNKNOWN)
    events = detect_changes(make_product(), old, new)
    assert ChangeType.PRODUCT_APPEARED in [e.change_type for e in events]


def test_price_appears_then_changes():
    old = snap()
    new = snap(price="119,99 €")
    events = detect_changes(make_product(), old, new)
    assert [e.change_type for e in events] == [ChangeType.PRICE_APPEARED]

    newer = snap(price="129,99 €")
    events = detect_changes(make_product(), new, newer)
    assert [e.change_type for e in events] == [ChangeType.PRICE_CHANGED]


def test_button_change_without_availability_change():
    old = snap(buttons=["M'alerter"])
    new = snap(buttons=["Prévenez-moi"])
    events = detect_changes(make_product(), old, new)
    assert [e.change_type for e in events] == [ChangeType.BUTTON_CHANGED]


def test_page_hash_change_is_not_alert_worthy():
    old = snap(content_hash="aaa")
    new = snap(content_hash="bbb")
    events = detect_changes(make_product(), old, new)
    assert [e.change_type for e in events] == [ChangeType.PAGE_CHANGED]
    assert not events[0].is_alert_worthy
