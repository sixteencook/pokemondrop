"""Politique de déclenchement des captures.

Une capture coûte cher (navigateur, mémoire, temps) : elle n'est réalisée
que pour les événements porteurs de sens. Les checks de routine et les
simples variations de page n'en déclenchent JAMAIS.

Sont capturés :
  - apparition de la fiche produit ;
  - apparition d'un prix ;
  - ouverture des précommandes ;
  - retour en stock ;
  - changement de statut du produit ;
  - apparition d'un bouton d'achat (« Précommander », « Ajouter au panier »)
    même lorsque le statut global, lui, n'a pas changé.

Pour étendre la politique : ajouter un type à IMPORTANT_CHANGE_TYPES
(src/models/product.py) ou enrichir _BUY_BUTTON_HINTS ci-dessous.
"""

from __future__ import annotations

from src.models import ChangeEvent, ChangeType

#: Fragments de texte trahissant l'apparition d'un bouton d'achat.
_BUY_BUTTON_HINTS: tuple[str, ...] = (
    "précommander", "precommander", "pré-commander", "preorder", "pre-order",
    "réserver", "reserver", "ajouter au panier", "add to cart", "acheter",
)


def is_screenshot_worthy(change: ChangeEvent) -> bool:
    """True si ce changement mérite une preuve visuelle."""
    if change.is_important:
        return True
    if change.change_type is ChangeType.BUTTON_CHANGED:
        return _mentions_buy_button(change.new_value) and not _mentions_buy_button(
            change.old_value
        )
    return False


def _mentions_buy_button(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return any(hint in lowered for hint in _BUY_BUTTON_HINTS)
