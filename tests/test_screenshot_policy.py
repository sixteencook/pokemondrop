"""Politique de déclenchement : seuls les événements importants sont capturés."""

import pytest

from src.models import Availability, ChangeEvent, ChangeType, ProductSnapshot
from src.services.screenshots import is_screenshot_worthy
from tests.helpers import make_product


def change(change_type: ChangeType, old=None, new=None) -> ChangeEvent:
    return ChangeEvent(
        product=make_product(uuid="u1"),
        change_type=change_type,
        old_value=old,
        new_value=new,
        snapshot=ProductSnapshot(availability=Availability.PREORDER, page_exists=True),
    )


@pytest.mark.parametrize("change_type", [
    ChangeType.PREORDER_OPENED,
    ChangeType.BACK_IN_STOCK,
    ChangeType.PRODUCT_APPEARED,
    ChangeType.PRICE_APPEARED,
    ChangeType.STATUS_CHANGED,
])
def test_important_changes_are_captured(change_type):
    assert is_screenshot_worthy(change(change_type))


@pytest.mark.parametrize("change_type", [
    ChangeType.PAGE_CHANGED,
    ChangeType.PRICE_CHANGED,
])
def test_routine_changes_are_not_captured(change_type):
    assert not is_screenshot_worthy(change(change_type))


def test_buy_button_appearance_is_captured():
    """Le bouton d'achat apparaît alors que le statut global n'a pas bougé."""
    assert is_screenshot_worthy(
        change(ChangeType.BUTTON_CHANGED, old="M'alerter", new="Précommander")
    )
    assert is_screenshot_worthy(
        change(ChangeType.BUTTON_CHANGED, old="(aucun)", new="Ajouter au panier")
    )


def test_unrelated_button_change_is_not_captured():
    assert not is_screenshot_worthy(
        change(ChangeType.BUTTON_CHANGED, old="M'alerter", new="Prévenez-moi")
    )


def test_buy_button_already_present_is_not_recaptured():
    """Le bouton existait déjà : ce n'est pas une apparition."""
    assert not is_screenshot_worthy(
        change(ChangeType.BUTTON_CHANGED,
               old="Précommander maintenant", new="Précommander")
    )
