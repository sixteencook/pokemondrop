"""Schémas du catalogue produit (contrat public de l'API v1)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from src.intelligence.entities import (
    CanonicalProduct,
    MatchSuggestion,
    Offer,
    OfferSnapshotEntry,
    OfferStatus,
)
from src.models import Priority


class OfferOut(BaseModel):
    """Offre d'un marchand pour un produit."""

    uuid: str
    product_uuid: str
    site: str
    url: str
    price: Optional[str]
    currency: str
    availability: Optional[str] = Field(
        None, description="preorder, in_stock, unavailable…"
    )
    status: OfferStatus
    monitored_uuid: Optional[str] = Field(
        None, description="Produit surveillé correspondant, s'il existe"
    )
    first_seen_at: datetime
    last_checked_at: Optional[datetime]
    last_changed_at: Optional[datetime]

    @classmethod
    def from_domain(cls, offer: Offer) -> "OfferOut":
        return cls(
            uuid=offer.uuid, product_uuid=offer.product_uuid, site=offer.site,
            url=offer.url, price=offer.price, currency=offer.currency,
            availability=offer.availability, status=offer.status,
            monitored_uuid=offer.monitored_uuid,
            first_seen_at=offer.first_seen_at,
            last_checked_at=offer.last_checked_at,
            last_changed_at=offer.last_changed_at,
        )


class CatalogProductOut(BaseModel):
    """Produit canonique et toutes ses offres. Aucune URL sur le produit."""

    uuid: str
    name: str
    brand: Optional[str]
    collection: Optional[str]
    edition: Optional[str]
    category: Optional[str]
    release_date: Optional[str]
    image_url: Optional[str]
    ean: Optional[str]
    upc: Optional[str]
    isbn: Optional[str]
    mpn: Optional[str]
    manufacturer_sku: Optional[str]
    manufacturer_ref: Optional[str]
    tags: list[str]
    priority: Priority
    created_at: datetime
    updated_at: datetime
    offers: list[OfferOut] = Field(default_factory=list)
    best_offer_site: Optional[str] = Field(
        None, description="Marchand où le produit est le plus rapidement obtenable"
    )

    @classmethod
    def from_domain(
        cls, product: CanonicalProduct, offers: list[Offer] | None = None
    ) -> "CatalogProductOut":
        offers = offers or []
        return cls(
            uuid=product.uuid, name=product.name,
            brand=product.attributes.brand,
            collection=product.attributes.collection,
            edition=product.attributes.edition,
            category=product.attributes.category,
            release_date=product.attributes.release_date,
            image_url=product.attributes.image_url,
            ean=product.identifiers.ean, upc=product.identifiers.upc,
            isbn=product.identifiers.isbn, mpn=product.identifiers.mpn,
            manufacturer_sku=product.identifiers.manufacturer_sku,
            manufacturer_ref=product.identifiers.manufacturer_ref,
            tags=list(product.tags), priority=product.priority,
            created_at=product.created_at, updated_at=product.updated_at,
            offers=[OfferOut.from_domain(offer) for offer in offers],
            best_offer_site=_best_site(offers),
        )


#: Ordre de préférence : ce que l'on peut obtenir le plus vite.
_AVAILABILITY_RANK = {"in_stock": 0, "preorder": 1, "unknown": 2, "unavailable": 3}


def _best_site(offers: list[Offer]) -> Optional[str]:
    candidates = [
        offer for offer in offers
        if offer.availability in ("in_stock", "preorder")
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda offer: _AVAILABILITY_RANK.get(offer.availability or "unknown", 9),
    ).site


class OfferHistoryOut(BaseModel):
    id: int
    price: Optional[str]
    availability: Optional[str]
    status: OfferStatus
    recorded_at: datetime

    @classmethod
    def from_domain(cls, entry: OfferSnapshotEntry) -> "OfferHistoryOut":
        return cls(
            id=entry.id, price=entry.price, availability=entry.availability,
            status=entry.status, recorded_at=entry.recorded_at,
        )


class MatchSuggestionOut(BaseModel):
    """Rapprochement en attente de validation manuelle."""

    id: int
    product_uuid: str
    product_name: Optional[str]
    candidate_uuid: str
    candidate_name: Optional[str]
    score: int
    method: str
    reason: str
    created_at: datetime

    @classmethod
    def from_domain(
        cls,
        suggestion: MatchSuggestion,
        product_name: Optional[str],
        candidate_name: Optional[str],
    ) -> "MatchSuggestionOut":
        return cls(
            id=suggestion.id, product_uuid=suggestion.product_uuid,
            product_name=product_name, candidate_uuid=suggestion.candidate_uuid,
            candidate_name=candidate_name, score=suggestion.score,
            method=suggestion.method, reason=suggestion.reason,
            created_at=suggestion.created_at,
        )


class CatalogStatusOut(BaseModel):
    """État de la couche d'intelligence produit."""

    enabled: bool
    merge_threshold: int = Field(description="Score minimal de fusion automatique")
    suggestion_floor: int = Field(description="Score minimal pour proposer une fusion")
    cross_site_search: bool
    products: int
    offers: int
    pending_suggestions: int
    methods: list[str] = Field(description="Méthodes de corrélation, par confiance")
    search_capable_sites: list[str]


class ManualProductIn(BaseModel):
    """Ajout manuel d'une fiche marchande au catalogue."""

    url: str = Field(min_length=8, max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    site: str = Field(min_length=1, max_length=50)
    price: Optional[str] = None
    ean: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
