"""État métier d'une offre — le seul objet que le moteur compare.

POURQUOI CE MODÈLE EXISTE
-------------------------
Le projet a longtemps surveillé du HTML : une liste de libellés de boutons
et un hash de texte. Deux conséquences, toutes deux observées :

  * **faux positifs** — Amazon change un bandeau Prime, une mention de
    livraison ou l'ordre d'un carrousel, le hash bouge, une alerte part
    alors que rien n'a changé pour l'acheteur ;
  * **oscillation** — une lecture partielle rend l'analyse inconclusive,
    l'état retombe à « inconnu », puis remonte au cycle suivant : deux
    alertes pour un produit parfaitement immobile.

Le HTML n'est qu'une **source de données**. Ce que l'on surveille, c'est
un état métier : *que peut faire l'acheteur, à quel prix, chez qui ?*

Chaque plugin a donc une seule obligation : traduire sa page en un
`OfferState`. Le cœur ne compare plus jamais autre chose.

RÈGLE FONDATRICE
----------------
Mieux vaut manquer un événement que d'en inventer un. Tout ce qui n'est
pas une **action d'achat principale** est du bruit : liste d'envies,
adresse de livraison, Prime, financement, garantie, partage, carrousels,
produits sponsorisés, questions/réponses, navigation, pied de page.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from src.models.product import Availability


class PurchaseAction(str, Enum):
    """L'action d'achat principale proposée par une fiche produit.

    Une fiche en propose **une seule** à un instant donné. Tout le reste
    des boutons de la page est ignoré par construction.
    """

    ADD_TO_CART = "add_to_cart"
    BUY_NOW = "buy_now"
    PREORDER = "preorder"
    REQUEST_INVITE = "request_invite"
    #: « Prévenez-moi » : le produit existe mais ne peut pas être commandé.
    NOTIFY_ME = "notify_me"
    COMING_SOON = "coming_soon"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    CURRENTLY_UNAVAILABLE = "currently_unavailable"
    #: Fiche encore en ligne, mais le produit n'est plus commercialisé.
    DISCONTINUED = "discontinued"
    #: Achetable, mais pas vendu par le marchand lui-même.
    THIRD_PARTY_ONLY = "third_party_only"
    #: Aucune action principale identifiée : on ne conclut pas.
    NONE = "none"


class SellerType(str, Enum):
    """Qui vend, du point de vue de l'acheteur."""

    #: Le marchand lui-même (Amazon.fr sur Amazon, Micromania sur Micromania).
    OFFICIAL = "official"
    #: Un revendeur tiers hébergé par la place de marché.
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"


ACTION_LABELS: dict[PurchaseAction, str] = {
    PurchaseAction.ADD_TO_CART: "Ajouter au panier",
    PurchaseAction.BUY_NOW: "Acheter maintenant",
    PurchaseAction.PREORDER: "Précommander",
    PurchaseAction.REQUEST_INVITE: "Demander une invitation",
    PurchaseAction.NOTIFY_ME: "Prévenez-moi",
    PurchaseAction.COMING_SOON: "Bientôt disponible",
    PurchaseAction.TEMPORARILY_UNAVAILABLE: "Temporairement indisponible",
    PurchaseAction.CURRENTLY_UNAVAILABLE: "Actuellement indisponible",
    PurchaseAction.DISCONTINUED: "Plus disponible",
    PurchaseAction.THIRD_PARTY_ONLY: "Revendeur tiers uniquement",
    PurchaseAction.NONE: "Aucune action identifiée",
}

#: Traduction vers le vocabulaire de disponibilité du cœur.
#:
#: `REQUEST_INVITE` vaut PREORDER : une demande d'invitation signifie que
#: le drop est lancé et que l'acheteur doit agir maintenant. Le libellé
#: exact reste disponible dans `action` et `native_state`.
ACTION_TO_AVAILABILITY: dict[PurchaseAction, Availability] = {
    PurchaseAction.ADD_TO_CART: Availability.IN_STOCK,
    PurchaseAction.BUY_NOW: Availability.IN_STOCK,
    PurchaseAction.THIRD_PARTY_ONLY: Availability.IN_STOCK,
    PurchaseAction.PREORDER: Availability.PREORDER,
    PurchaseAction.REQUEST_INVITE: Availability.PREORDER,
    PurchaseAction.NOTIFY_ME: Availability.UNAVAILABLE,
    PurchaseAction.COMING_SOON: Availability.UNAVAILABLE,
    PurchaseAction.TEMPORARILY_UNAVAILABLE: Availability.UNAVAILABLE,
    PurchaseAction.CURRENTLY_UNAVAILABLE: Availability.UNAVAILABLE,
    PurchaseAction.DISCONTINUED: Availability.UNAVAILABLE,
    PurchaseAction.NONE: Availability.UNKNOWN,
}

#: Actions qui permettent réellement d'agir. Sert aux règles qui n'ont de
#: sens que sur une offre vivante (changement de vendeur, par exemple).
ACTIONABLE: frozenset[PurchaseAction] = frozenset({
    PurchaseAction.ADD_TO_CART,
    PurchaseAction.BUY_NOW,
    PurchaseAction.PREORDER,
    PurchaseAction.REQUEST_INVITE,
    PurchaseAction.THIRD_PARTY_ONLY,
})

#: Version de la logique de périmètre et de résolution d'action.
#:
#: Elle entre dans le hash métier : la faire évoluer invalide
#: **volontairement** tous les états mémorisés, pour qu'un changement de
#: règles ne soit jamais confondu avec un changement du produit. Le premier
#: cycle qui suit rejoue une baseline silencieuse.
PRODUCT_SCOPE_VERSION = "2026.08-1"


@dataclass(frozen=True)
class OfferState:
    """Ce qui est vrai pour l'acheteur, indépendamment du HTML.

    Deux `OfferState` égaux décrivent la même situation commerciale, même
    si les deux pages dont ils sont issus n'ont pas un octet en commun.
    """

    action: PurchaseAction = PurchaseAction.NONE
    #: État natif du marchand, plus riche que la disponibilité générique
    #: (`invitation`, `third_party_only`, `coming_soon`…). Il entre dans le
    #: hash : c'est lui qui porte la nuance métier.
    native_state: str = ""
    has_buy_box: bool = False
    seller_type: SellerType = SellerType.UNKNOWN
    seller_name: Optional[str] = None
    price: Optional[str] = None
    currency: Optional[str] = None
    #: Identifiant du produit chez le marchand (ASIN, SKU, référence).
    identifier: Optional[str] = None
    scope_version: str = PRODUCT_SCOPE_VERSION

    @property
    def availability(self) -> Availability:
        return ACTION_TO_AVAILABILITY[self.action]

    @property
    def label(self) -> str:
        return ACTION_LABELS[self.action]

    @property
    def conclusive(self) -> bool:
        """L'analyse a-t-elle abouti à une action identifiée ?"""
        return self.action is not PurchaseAction.NONE

    @property
    def actionable(self) -> bool:
        return self.action in ACTIONABLE

    @property
    def sold_by_official(self) -> bool:
        return self.seller_type is SellerType.OFFICIAL

    def business_hash(self) -> str:
        """Empreinte du seul état métier.

        Ce qui n'y figure pas y est absent **volontairement** : libellés de
        boutons, textes de la page, liste d'envies, adresse de livraison,
        bandeaux Prime, promotions, carrousels, DOM. Amazon peut refondre
        son interface sans que cette empreinte bouge d'un caractère.

        Cas particulier du vendeur : seul le *type* entre dans l'empreinte
        pour un revendeur tiers. Une place de marché fait tourner ses
        revendeurs en permanence ; y réagir produirait un flot d'alertes
        sans intérêt. Le nom n'est retenu que pour le vendeur officiel,
        où il porte une information réelle.
        """
        seller = (
            (self.seller_name or "").strip().lower()
            if self.seller_type is SellerType.OFFICIAL else ""
        )
        payload = "|".join([
            self.availability.value,
            self.native_state,
            "buybox" if self.has_buy_box else "",
            self.seller_type.value,
            seller,
            self.price or "",
            self.currency or "",
            self.identifier or "",
            self.scope_version,
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def describe(self) -> str:
        """Résumé d'une ligne, lisible dans une alerte ou la timeline."""
        parts = [self.label]
        if self.price:
            parts.append(self.price)
        if self.seller_name:
            parts.append(f"vendu par {self.seller_name}")
        return " · ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "native_state": self.native_state,
            "has_buy_box": self.has_buy_box,
            "seller_type": self.seller_type.value,
            "seller_name": self.seller_name,
            "price": self.price,
            "currency": self.currency,
            "identifier": self.identifier,
            "scope_version": self.scope_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OfferState":
        """Relecture tolérante : un état mémorisé par une version antérieure
        ne doit jamais empêcher le démarrage."""
        try:
            action = PurchaseAction(data.get("action", "none"))
        except ValueError:
            action = PurchaseAction.NONE
        try:
            seller_type = SellerType(data.get("seller_type", "unknown"))
        except ValueError:
            seller_type = SellerType.UNKNOWN
        return cls(
            action=action,
            native_state=str(data.get("native_state") or ""),
            has_buy_box=bool(data.get("has_buy_box", False)),
            seller_type=seller_type,
            seller_name=data.get("seller_name"),
            price=data.get("price"),
            currency=data.get("currency"),
            identifier=data.get("identifier"),
            scope_version=str(data.get("scope_version") or ""),
        )
