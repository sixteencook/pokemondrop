"""Comparaison de deux états métier et production des événements.

C'est ici que se joue l'anti-spam. Quatre règles, dans cet ordre :

  1. **Baseline silencieuse** — au premier passage, on enregistre, on
     n'alerte pas.
  2. **Une lecture non concluante n'est jamais un événement.** Ni comme
     ancien état, ni comme nouveau. Une page d'interception, une confiance
     insuffisante ou un contexte de localisation incorrect ne prouvent
     rien : ils ne peuvent donc rien annoncer. C'est cette règle qui
     supprime l'oscillation « invitation → inconnu → invitation », qui
     produisait deux alertes pour un produit parfaitement immobile.
  3. **Seul l'état métier est comparé.** Ni libellés de boutons, ni texte
     de page, ni hash de HTML. Si le marchand refond son interface sans
     changer l'offre, rien ne part.
  4. **Un changement, un événement.** Une transition de disponibilité
     absorbe l'apparition de prix qui l'accompagne : passer de
     « indisponible » à « en stock » n'envoie pas deux messages.

Les types `BUTTON_CHANGED` et `PAGE_CHANGED` ne sont plus jamais émis.
Ils décrivaient le HTML, pas le produit.
"""

from __future__ import annotations

from typing import Optional

from src.models import (
    Availability,
    ChangeEvent,
    ChangeType,
    OfferState,
    ProductConfig,
    ProductSnapshot,
    PurchaseAction,
    SellerType,
)

#: Transitions de disponibilité qui portent un nom métier.
_AVAILABILITY_EVENTS: dict[Availability, ChangeType] = {
    Availability.IN_STOCK: ChangeType.BACK_IN_STOCK,
    Availability.PREORDER: ChangeType.PREORDER_OPENED,
    Availability.UNAVAILABLE: ChangeType.WENT_OUT_OF_STOCK,
    Availability.NOT_LISTED: ChangeType.PRODUCT_DELISTED,
}


def detect_changes(
    product: ProductConfig,
    old: Optional[ProductSnapshot],
    new: ProductSnapshot,
) -> list[ChangeEvent]:
    """Retourne les changements métier entre `old` et `new`."""
    if old is None:
        return []                       # baseline silencieuse

    # Règle 2 : on ne conclut rien à partir d'une lecture qui n'a rien
    # conclu, et on ne compare rien à un passé qui n'avait rien conclu.
    if not old.conclusive or not new.conclusive:
        return []

    events: list[ChangeEvent] = []

    def add(change_type: ChangeType, old_value: Optional[str],
            new_value: Optional[str]) -> None:
        events.append(ChangeEvent(
            product=product,
            change_type=change_type,
            old_value=old_value,
            new_value=new_value,
            snapshot=new,
        ))

    before = old.offer or OfferState()
    after = new.offer or OfferState()

    # --- Apparition de la fiche ------------------------------------------
    # Elle absorbe l'événement de disponibilité qui l'accompagne : une
    # fiche qui apparaît change forcément d'état, ce n'est pas une seconde
    # nouvelle.
    appeared = not old.page_exists and new.page_exists
    if appeared:
        add(ChangeType.PRODUCT_APPEARED, "Fiche absente", _describe(new))

    # --- Disponibilité ----------------------------------------------------
    availability_changed = appeared or _add_availability_event(add, old, new, after)

    # --- Vendeur ----------------------------------------------------------
    _add_seller_events(add, before, after)

    # --- Prix -------------------------------------------------------------
    # Règle 4 : le prix qui apparaît EN MÊME TEMPS qu'un retour en stock
    # fait partie du même événement — il figure déjà dans l'alerte.
    if not availability_changed:
        _add_price_event(add, old, new)

    return events


def _add_availability_event(
    add, old: ProductSnapshot, new: ProductSnapshot, after: OfferState
) -> bool:
    """Événement de disponibilité, nommé selon ce qui s'ouvre réellement."""
    if new.availability is old.availability:
        return False

    change_type = _AVAILABILITY_EVENTS.get(
        new.availability, ChangeType.STATUS_CHANGED
    )
    # Une invitation n'est pas une précommande ordinaire : c'est le signal
    # le plus attendu du projet, il mérite son propre événement.
    if (
        change_type is ChangeType.PREORDER_OPENED
        and after.action is PurchaseAction.REQUEST_INVITE
    ):
        change_type = ChangeType.INVITATION_OPENED

    add(change_type, _describe(old), _describe(new))
    return True


def _add_seller_events(add, before: OfferState, after: OfferState) -> None:
    """Entrée et sortie du vendeur officiel — un vrai signal d'achat.

    Deux garde-fous :
      * la comparaison n'a de sens que si les deux états sont actionnables
        (comparer le vendeur d'une fiche en rupture n'a pas de sens) ;
      * un vendeur simplement **inconnu** ne déclenche rien : l'absence
        d'information n'est pas un changement.
    """
    if not (before.actionable and after.actionable):
        return
    if SellerType.UNKNOWN in (before.seller_type, after.seller_type):
        return
    if before.seller_type is after.seller_type:
        return

    if after.seller_type is SellerType.OFFICIAL:
        add(ChangeType.SELLER_BECAME_OFFICIAL,
            before.seller_name or "revendeur tiers",
            after.seller_name or "vendeur officiel")
    else:
        add(ChangeType.SELLER_LEFT_BUYBOX,
            before.seller_name or "vendeur officiel",
            after.seller_name or "revendeur tiers")


def _add_price_event(add, old: ProductSnapshot, new: ProductSnapshot) -> None:
    if new.price and not old.price:
        add(ChangeType.PRICE_APPEARED, None, new.price)
    elif new.price and old.price and new.price != old.price:
        add(ChangeType.PRICE_CHANGED, old.price, new.price)


def _describe(snapshot: ProductSnapshot) -> str:
    """Résumé métier d'un état, sans aucun hash ni libellé de bouton."""
    if snapshot.offer is not None and snapshot.offer.conclusive:
        return snapshot.offer.describe()
    parts: list[str] = [snapshot.availability.value]
    if snapshot.price:
        parts.append(snapshot.price)
    return " · ".join(parts)


#: Conservé : le résumé d'état sert aussi aux notifications et à la timeline.
describe_state = _describe


def event_signature(events: list[ChangeEvent]) -> tuple[tuple[str, str], ...]:
    """Signature comparable d'une série d'événements.

    Sert à la confirmation : un changement n'est notifié que si une seconde
    lecture produit **exactement les mêmes** événements métier. Comparer
    des signatures plutôt que des hachages de page évite qu'une variation
    cosmétique entre deux lectures fasse échouer la confirmation.
    """
    return tuple(
        sorted(
            (event.change_type.value, event.new_value or "")
            for event in events
        )
    )
