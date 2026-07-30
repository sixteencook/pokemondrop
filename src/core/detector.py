"""Comparaison de deux snapshots et production des événements de changement.

C'est ici que se joue la logique anti-spam :
  - premier lancement (old is None) → aucun événement, on enregistre la baseline ;
  - on ne signale que les transitions, jamais un état stable.
"""

from __future__ import annotations

from typing import Optional

from src.models import (
    Availability,
    ChangeEvent,
    ChangeType,
    ProductConfig,
    ProductSnapshot,
)


def detect_changes(
    product: ProductConfig,
    old: Optional[ProductSnapshot],
    new: ProductSnapshot,
) -> list[ChangeEvent]:
    """Retourne la liste des changements significatifs entre old et new."""
    if old is None:
        # Premier lancement : baseline silencieuse, aucune alerte.
        return []

    events: list[ChangeEvent] = []

    def add(change_type: ChangeType, old_value: Optional[str], new_value: Optional[str]) -> None:
        events.append(
            ChangeEvent(
                product=product,
                change_type=change_type,
                old_value=old_value,
                new_value=new_value,
                snapshot=new,
            )
        )

    # --- Apparition de la fiche produit ----------------------------------
    if not old.page_exists and new.page_exists:
        add(ChangeType.PRODUCT_APPEARED, None, "Fiche produit en ligne")

    # --- Prix --------------------------------------------------------------
    if new.price and not old.price:
        add(ChangeType.PRICE_APPEARED, None, new.price)
    elif new.price and old.price and new.price != old.price:
        add(ChangeType.PRICE_CHANGED, old.price, new.price)

    # --- Disponibilité -------------------------------------------------------
    if new.availability != old.availability:
        if new.availability is Availability.PREORDER:
            add(ChangeType.PREORDER_OPENED, old.availability.value, new.availability.value)
        elif new.availability is Availability.IN_STOCK:
            add(ChangeType.BACK_IN_STOCK, old.availability.value, new.availability.value)
        else:
            add(ChangeType.STATUS_CHANGED, old.availability.value, new.availability.value)

    # --- Boutons --------------------------------------------------------------
    if old.page_exists and new.page_exists and old.buttons != new.buttons:
        # Ne pas doublonner : un changement de dispo implique souvent un
        # changement de bouton — on ne l'ajoute que s'il est seul.
        if new.availability == old.availability:
            add(
                ChangeType.BUTTON_CHANGED,
                " | ".join(old.buttons) or "(aucun)",
                " | ".join(new.buttons) or "(aucun)",
            )

    # --- Modification significative de la page (filet de sécurité) -----------
    if (
        not events
        and old.content_hash
        and new.content_hash
        and old.content_hash != new.content_hash
    ):
        add(ChangeType.PAGE_CHANGED, old.content_hash, new.content_hash)

    return events
