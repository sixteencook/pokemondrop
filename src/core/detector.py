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
                describe_buttons(old.buttons),
                describe_buttons(new.buttons),
            )

    # --- Modification significative de la page (filet de sécurité) -----------
    if (
        not events
        and old.content_hash
        and new.content_hash
        and old.content_hash != new.content_hash
    ):
        # Le hash est un OUTIL INTERNE : il ne doit jamais apparaître dans
        # une alerte ni dans le dashboard. On décrit le changement en clair.
        add(
            ChangeType.PAGE_CHANGED,
            describe_state(old),
            describe_state(new),
        )

    return events


#: Nombre de libellés affichés avant abréviation.
MAX_DISPLAYED_BUTTONS = 3
MAX_LABEL_LENGTH = 60


def describe_buttons(buttons: list[str]) -> str:
    """Libellés lisibles par un humain, jamais un identifiant technique."""
    if not buttons:
        return "aucun bouton"
    shown = [_shorten(label) for label in buttons[:MAX_DISPLAYED_BUTTONS]]
    extra = len(buttons) - len(shown)
    rendered = " | ".join(f"« {label} »" for label in shown)
    return f"{rendered} (+{extra})" if extra > 0 else rendered


def describe_state(snapshot: ProductSnapshot) -> str:
    """Résumé compréhensible d'un état de page, sans aucun hash."""
    parts: list[str] = [snapshot.availability.value]
    if snapshot.price:
        parts.append(snapshot.price)
    if snapshot.buttons:
        parts.append(describe_buttons(snapshot.buttons))
    elif snapshot.status_text:
        parts.append(_shorten(snapshot.status_text))
    return " · ".join(parts)


def _shorten(text: str) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= MAX_LABEL_LENGTH:
        return collapsed
    return collapsed[: MAX_LABEL_LENGTH - 1] + "…"
