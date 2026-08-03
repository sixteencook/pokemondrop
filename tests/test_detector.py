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
    assert [e.change_type for e in events] == [ChangeType.PREORDER_OPENED]


def test_an_unknown_state_never_produces_an_event():
    """Une lecture non concluante ne peut ni annoncer, ni effacer.

    C'est la règle qui supprime l'oscillation « invitation → inconnu →
    invitation » et ses deux alertes pour un produit immobile.
    """
    known = snap(availability=Availability.PREORDER)
    unknown = snap(availability=Availability.UNKNOWN)

    assert detect_changes(make_product(), known, unknown) == []
    assert detect_changes(make_product(), unknown, known) == []


def test_back_in_stock():
    old = snap(availability=Availability.UNAVAILABLE)
    new = snap(availability=Availability.IN_STOCK)
    events = detect_changes(make_product(), old, new)
    assert [e.change_type for e in events] == [ChangeType.BACK_IN_STOCK]


def test_product_page_appears():
    old = ProductSnapshot(page_exists=False, availability=Availability.NOT_LISTED)
    new = snap(availability=Availability.PREORDER)
    events = detect_changes(make_product(), old, new)
    # Un seul événement : la mise en ligne absorbe le changement d'état.
    assert [e.change_type for e in events] == [ChangeType.PRODUCT_APPEARED]


def test_product_page_disappears():
    old = snap(availability=Availability.IN_STOCK)
    new = ProductSnapshot(page_exists=False, availability=Availability.NOT_LISTED)
    events = detect_changes(make_product(), old, new)
    assert [e.change_type for e in events] == [ChangeType.PRODUCT_DELISTED]


def test_price_appears_then_changes():
    old = snap()
    new = snap(price="119,99 €")
    events = detect_changes(make_product(), old, new)
    assert [e.change_type for e in events] == [ChangeType.PRICE_APPEARED]

    newer = snap(price="129,99 €")
    events = detect_changes(make_product(), new, newer)
    assert [e.change_type for e in events] == [ChangeType.PRICE_CHANGED]


def test_a_button_label_change_alone_produces_nothing():
    """Le moteur ne surveille plus des boutons, mais un état métier."""
    old = snap(buttons=["M'alerter"])
    new = snap(buttons=["Prévenez-moi"])
    assert detect_changes(make_product(), old, new) == []


def test_a_hash_change_alone_produces_nothing():
    """Le hash ne déclenche plus rien : il ne sert qu'à l'archivage."""
    old = snap(content_hash="aaa")
    new = snap(content_hash="bbb")
    assert detect_changes(make_product(), old, new) == []
