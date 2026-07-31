"""Entités du Product Intelligence Engine.

Renversement de perspective : le logiciel ne raisonne plus en URL mais en
PRODUITS.

    CanonicalProduct   le produit réel, indépendant de tout marchand.
                       Il ne possède AUCUNE URL.
    Offer              la proposition d'un marchand pour ce produit :
                       une URL, un prix, une disponibilité, un historique.

Un produit possède plusieurs offres. Une offre n'est jamais supprimée :
elle change d'état, pour que l'historique reste complet.

Couche strictement additive : la surveillance existante continue de
travailler sur ses propres enregistrements (src/models/product.py), qu'une
offre référence via `monitored_uuid`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Optional

from src.intelligence.identity import ProductIdentity
from src.models import Priority


def _empty_identity() -> ProductIdentity:
    return ProductIdentity()


class OfferStatus(str, Enum):
    """Cycle de vie d'une offre — aucune suppression, jamais."""

    ACTIVE = "active"          # fiche en ligne et surveillée
    INACTIVE = "inactive"      # fiche en ligne mais produit retiré de la vente
    NOT_FOUND = "not_found"    # URL en 404
    REMOVED = "removed"        # fiche disparue du site
    ARCHIVED = "archived"      # conservée pour l'historique uniquement


@dataclass(frozen=True)
class ProductIdentifiers:
    """Identifiants normalisés d'un produit.

    Ce sont eux qui portent la confiance du matching : un EAN identique ne
    laisse aucun doute, un nom approchant en laisse beaucoup.
    """

    ean: Optional[str] = None
    upc: Optional[str] = None
    isbn: Optional[str] = None
    mpn: Optional[str] = None                 # Manufacturer Part Number
    manufacturer_sku: Optional[str] = None
    manufacturer_ref: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.ean, self.upc, self.isbn, self.mpn,
             self.manufacturer_sku, self.manufacturer_ref)
        )

    def merged_with(self, other: "ProductIdentifiers") -> "ProductIdentifiers":
        """Complète les trous sans jamais écraser une valeur déjà connue."""
        return ProductIdentifiers(
            ean=self.ean or other.ean,
            upc=self.upc or other.upc,
            isbn=self.isbn or other.isbn,
            mpn=self.mpn or other.mpn,
            manufacturer_sku=self.manufacturer_sku or other.manufacturer_sku,
            manufacturer_ref=self.manufacturer_ref or other.manufacturer_ref,
        )


@dataclass(frozen=True)
class ProductAttributes:
    """Caractéristiques descriptives, utiles au matching de second niveau."""

    brand: Optional[str] = None
    collection: Optional[str] = None
    edition: Optional[str] = None
    category: Optional[str] = None
    release_date: Optional[str] = None        # ISO (AAAA-MM-JJ)
    image_url: Optional[str] = None

    def merged_with(self, other: "ProductAttributes") -> "ProductAttributes":
        return ProductAttributes(
            brand=self.brand or other.brand,
            collection=self.collection or other.collection,
            edition=self.edition or other.edition,
            category=self.category or other.category,
            release_date=self.release_date or other.release_date,
            image_url=self.image_url or other.image_url,
        )


@dataclass(frozen=True)
class ProductDraft:
    """Produit candidat, tel que reconstitué depuis une fiche marchande.

    C'est l'entrée du moteur de corrélation : il décide si ce brouillon
    représente un produit déjà connu, ou un produit inédit.

    `identity` porte le profil complet (v2) : mêmes informations que
    `identifiers`/`attributes`, plus les clés additionnelles (ASIN, modèle,
    fabricant), les alias et la confiance de chaque champ.
    """

    name: str
    identifiers: ProductIdentifiers = field(default_factory=ProductIdentifiers)
    attributes: ProductAttributes = field(default_factory=ProductAttributes)
    tags: tuple[str, ...] = ()
    priority: Priority = Priority.NORMAL
    identity: "ProductIdentity" = field(default_factory=lambda: _empty_identity())


@dataclass(frozen=True)
class CanonicalProduct:
    """Le produit réel, indépendant des marchands. AUCUNE URL ici."""

    uuid: str
    name: str
    name_key: str                              # nom normalisé, clé de matching
    identifiers: ProductIdentifiers
    attributes: ProductAttributes
    tags: tuple[str, ...]
    priority: Priority
    created_at: datetime
    updated_at: datetime
    identity: "ProductIdentity" = field(default_factory=lambda: _empty_identity())

    def enriched_with(self, draft: ProductDraft) -> "CanonicalProduct":
        """Absorbe les informations d'un brouillon sans rien écraser."""
        return replace(
            self,
            identifiers=self.identifiers.merged_with(draft.identifiers),
            attributes=self.attributes.merged_with(draft.attributes),
            tags=tuple(dict.fromkeys((*self.tags, *draft.tags))),
        )


@dataclass(frozen=True)
class Offer:
    """Proposition d'un marchand pour un produit donné."""

    uuid: str
    product_uuid: str
    site: str
    url: str
    canonical_url: str
    price: Optional[str]
    currency: str
    availability: Optional[str]
    status: OfferStatus
    monitored_uuid: Optional[str]              # produit surveillé correspondant
    discovery_fingerprint: Optional[str]
    first_seen_at: datetime
    last_checked_at: Optional[datetime]
    last_changed_at: Optional[datetime]


@dataclass(frozen=True)
class OfferSnapshotEntry:
    """Point d'historique d'une offre (prix / disponibilité / statut)."""

    id: int
    offer_uuid: str
    price: Optional[str]
    availability: Optional[str]
    status: OfferStatus
    recorded_at: datetime


@dataclass(frozen=True)
class MatchSuggestion:
    """Rapprochement jugé trop incertain pour être appliqué automatiquement."""

    id: int
    product_uuid: str
    candidate_uuid: str
    score: int
    method: str
    reason: str
    status: str                                # pending | accepted | rejected
    created_at: datetime
