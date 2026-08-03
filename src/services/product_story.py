"""L'histoire d'un produit canonique — monitoring, découverte, intelligence.

POURQUOI CE SERVICE
-------------------
Jusqu'ici chaque étage racontait sa propre version : la timeline montrait
les changements d'un *produit surveillé*, la découverte listait des fiches,
l'intelligence des offres. Aucun endroit ne disait simplement :

    02 août   Amazon        Produit découvert
    03 août   Amazon        Invitation ouverte
    05 août   Micromania    Nouvelle fiche
    07 août   Micromania    Précommande ouverte
    12 août   Amazon        Retour en stock

C'est pourtant la seule vue qui répond à « que s'est-il passé pour ce
produit ? » — et à « quel marchand publie ses fiches le plus tôt ? ».

Le produit canonique est le bon pivot : il n'a pas d'URL, il porte des
offres, et chaque offre relie un marchand à un produit surveillé.

COÛT
----
Aucune écriture. Trois lectures groupées (offres, timelines, événements
techniques) puis une fusion en mémoire sur quelques centaines de lignes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.models import ChangeType
from src.repositories import (
    AlertRepository,
    CatalogRepository,
    DiscoveryRepository,
    EngineEventRepository,
    OfferRepository,
    TimelineRepository,
)
from src.repositories.search_attempts import SearchAttemptRepository

#: Natures d'événements techniques dignes de figurer dans l'histoire d'un
#: produit. Les incidents (403, bascules) restent dans la page Santé : ils
#: décrivent le moteur, pas le produit.
_STORY_KINDS: frozenset[str] = frozenset({
    "catalog_created", "catalog_merged", "catalog_pending",
    "discovery_found", "discovery_imported",
})

#: Événements métier comptés dans les métriques produit.
_PRICE_DROP_TYPES = frozenset({ChangeType.PRICE_CHANGED.value})


@dataclass(frozen=True)
class StoryEntry:
    """Une ligne de l'histoire : quand, chez qui, quoi."""

    at: datetime
    site: str
    label: str
    detail: str = ""
    #: monitoring | discovery | intelligence
    origin: str = "monitoring"

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "site": self.site,
            "label": self.label,
            "detail": self.detail,
            "origin": self.origin,
        }


class ProductStoryService:
    """Assemble la timeline, la propagation et les métriques d'un produit."""

    def __init__(
        self,
        catalog: CatalogRepository,
        offers: OfferRepository,
        timeline: TimelineRepository,
        alerts: AlertRepository,
        discoveries: DiscoveryRepository,
        events: EngineEventRepository,
        attempts: Optional[SearchAttemptRepository] = None,
    ) -> None:
        self._catalog = catalog
        self._offers = offers
        self._timeline = timeline
        self._alerts = alerts
        self._discoveries = discoveries
        self._events = events
        self._attempts = attempts

    # ------------------------------------------------------------------ #
    # Histoire complète                                                   #
    # ------------------------------------------------------------------ #

    async def story(self, product_uuid: str) -> Optional[dict[str, Any]]:
        """Tout ce qu'un produit canonique a vécu, dans l'ordre."""
        product = await self._catalog.get(product_uuid)
        if product is None:
            return None

        offers = await self._offers.for_product(product_uuid)
        monitored = [offer.monitored_uuid for offer in offers if offer.monitored_uuid]
        site_of = {
            offer.monitored_uuid: offer.site
            for offer in offers if offer.monitored_uuid
        }

        entries = [
            *self._offer_entries(offers),
            *await self._monitoring_entries(monitored, site_of),
            *await self._engine_entries(monitored, site_of),
        ]
        entries.sort(key=lambda entry: entry.at)

        return {
            "uuid": product_uuid,
            "name": product.name,
            "brand": product.attributes.brand,
            "timeline": [entry.as_dict() for entry in entries],
            "propagation": self._propagation(offers),
            "metrics": await self._metrics(offers, monitored),
            "identity": self._identity(product),
            "searches": await self._searches(product_uuid),
        }

    def _offer_entries(self, offers) -> list[StoryEntry]:
        """L'apparition d'une fiche chez un marchand est un événement."""
        return [
            StoryEntry(
                at=offer.first_seen_at,
                site=offer.site,
                label="Nouvelle fiche",
                detail=offer.url,
                origin="discovery",
            )
            for offer in offers
        ]

    async def _monitoring_entries(
        self, monitored: list[str], site_of: dict[str, str]
    ) -> list[StoryEntry]:
        return [
            StoryEntry(
                at=entry.created_at,
                site=site_of.get(entry.product_uuid, "—"),
                label=entry.label,
                detail=_transition(entry),
                origin="monitoring",
            )
            for entry in await self._timeline.for_products(monitored)
        ]

    async def _engine_entries(
        self, monitored: list[str], site_of: dict[str, str]
    ) -> list[StoryEntry]:
        return [
            StoryEntry(
                at=event.created_at,
                site=site_of.get(event.product_uuid or "", event.source),
                label=event.label,
                detail=event.detail,
                origin="intelligence",
            )
            for event in await self._events.for_products(monitored)
            if event.kind in _STORY_KINDS
        ]

    # ------------------------------------------------------------------ #
    # Propagation entre marchands                                         #
    # ------------------------------------------------------------------ #

    def _propagation(self, offers) -> list[dict[str, Any]]:
        """Qui a publié la fiche, et dans quel ordre.

        C'est la réponse à « quel marchand faut-il surveiller en priorité ? » :
        celui qui apparaît systématiquement en tête publie le plus tôt.
        """
        ordered = sorted(offers, key=lambda offer: offer.first_seen_at)
        if not ordered:
            return []

        first = ordered[0].first_seen_at
        return [
            {
                "site": offer.site,
                "first_seen_at": offer.first_seen_at,
                "rank": index + 1,
                "delay_hours": round(
                    (offer.first_seen_at - first).total_seconds() / 3600, 1
                ),
                "url": offer.url,
                "price": offer.price,
                "availability": offer.availability,
            }
            for index, offer in enumerate(ordered)
        ]

    # ------------------------------------------------------------------ #
    # Métriques métier                                                    #
    # ------------------------------------------------------------------ #

    async def _metrics(self, offers, monitored: list[str]) -> dict[str, Any]:
        """Toutes déduites d'événements existants — rien n'est mesuré en plus."""
        ordered = sorted(offers, key=lambda offer: offer.first_seen_at)
        by_type = await self._timeline.count_by_type(monitored)
        alerts = await self._alerts.count_for_products(monitored)
        screenshots = await self._alerts.count_screenshots(monitored)

        return {
            "merchants": len(offers),
            "first_merchant": ordered[0].site if ordered else None,
            "first_seen_at": ordered[0].first_seen_at if ordered else None,
            "last_merchant": ordered[-1].site if ordered else None,
            "last_merchant_at": ordered[-1].first_seen_at if ordered else None,
            "changes": sum(by_type.values()),
            "notifications": alerts,
            "screenshots": screenshots,
            "price_changes": sum(
                total for event_type, total in by_type.items()
                if event_type in _PRICE_DROP_TYPES
            ),
            "back_in_stock": by_type.get(ChangeType.BACK_IN_STOCK.value, 0),
            "out_of_stock": by_type.get(ChangeType.WENT_OUT_OF_STOCK.value, 0),
            "preorders": by_type.get(ChangeType.PREORDER_OPENED.value, 0),
            "invitations": by_type.get(ChangeType.INVITATION_OPENED.value, 0),
        }

    # ------------------------------------------------------------------ #
    # Identité et recherches inter-sites                                  #
    # ------------------------------------------------------------------ #

    def _identity(self, product) -> dict[str, Any]:
        """Les clés fortes connues — ce qui permet (ou non) une fusion sûre.

        Les clés historiques vivent dans `identifiers`, les clés v2 (ASIN,
        modèle, fabricant) dans le profil `identity`. On expose les deux
        sous une forme unique, sans dupliquer l'information.
        """
        known = {
            key: value
            for key, value in (
                ("ean", product.identifiers.ean),
                ("upc", product.identifiers.upc),
                ("isbn", product.identifiers.isbn),
                ("mpn", product.identifiers.mpn),
                ("sku", product.identifiers.manufacturer_sku),
                ("ref", product.identifiers.manufacturer_ref),
            )
            if value
        }
        for key in ("asin", "model_number", "manufacturer", "brand"):
            value = getattr(product.identity, key, None)
            if value:
                known.setdefault(key, value)
        return known

    async def _searches(self, product_uuid: str) -> list[dict[str, Any]]:
        """Recherches inter-sites : ce qui a été tenté, et ce qui reste à faire."""
        if self._attempts is None:
            return []
        return [
            {
                "site": attempt.site,
                "key_kind": attempt.key_kind,
                "key_value": attempt.key_value,
                "status": attempt.status,
                "attempts": attempt.attempts,
                "confidence": attempt.confidence,
                "reason": attempt.reason,
                "found_url": attempt.found_url,
                "last_attempt_at": attempt.last_attempt_at,
                "next_retry_at": attempt.next_retry_at,
            }
            for attempt in await self._attempts.for_product(product_uuid)
        ]


def _transition(entry) -> str:
    """« indisponible → en stock · 189,99 € », ou rien."""
    parts: list[str] = []
    if entry.old_value or entry.new_value:
        parts.append(f"{entry.old_value or '—'} → {entry.new_value or '—'}")
    if entry.price:
        parts.append(entry.price)
    return " · ".join(parts)
