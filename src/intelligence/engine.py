"""Product Intelligence Engine — corrélation, enrichissement, offres.

Point d'entrée unique : `ingest()`. Toutes les sources de découverte
(catégorie, sitemap, recherche, RSS, EAN, manuelle) y aboutissent et
publient les mêmes événements — le cœur ne connaît que ces événements.

Déroulé d'une ingestion :

    fiche marchande
      ├─ brouillon produit      identifiants + attributs extraits
      ├─ présélection SQL       candidats partageant un identifiant/nom
      ├─ moteur de corrélation  meilleure méthode, score de confiance
      ├─ décision
      │    score ≥ seuil        → rattachement au produit connu (enrichi)
      │    score ≥ plancher     → nouveau produit + suggestion de fusion
      │    sinon                → nouveau produit
      ├─ offre créée ou mise à jour (jamais supprimée)
      └─ événements publiés sur le bus

Couche additive : la surveillance existante n'est pas modifiée. Une offre
référence le produit surveillé via `monitored_uuid`, et le champ `group`
de ce dernier reçoit l'UUID du produit canonique — c'est ainsi que le
regroupement devient automatique.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from src.core.events import Event, EventBus, EventType
from src.discovery.contracts import DiscoveredProduct
from src.discovery.fingerprint import canonical_url
from src.intelligence.config import IntelligenceSettings
from src.intelligence.entities import (
    CanonicalProduct,
    Offer,
    ProductAttributes,
    ProductDraft,
    ProductIdentifiers,
)
from src.intelligence.matching import MatchingEngine, MatchResult
from src.intelligence.search import CrossSiteSearchCoordinator, SearchQuery
from src.models import Priority
from src.utils.logger import get_logger

if TYPE_CHECKING:  # import différé : évite le cycle repositories ↔ intelligence
    from src.repositories import CatalogRepository, OfferRepository, ProductRepository

log = get_logger("intelligence")


@dataclass(frozen=True)
class IngestOutcome:
    """Résultat d'une ingestion, exploitable par l'API et les tests."""

    product: CanonicalProduct
    offer: Offer
    created_product: bool
    created_offer: bool
    match: Optional[MatchResult] = None
    suggestion_id: Optional[int] = None

    @property
    def summary(self) -> str:
        if self.match and not self.created_product:
            return (
                f"rattaché à « {self.product.name} » "
                f"({self.match.method}, confiance {self.match.score})"
            )
        if self.suggestion_id is not None:
            return (
                f"nouveau produit — fusion possible proposée "
                f"(confiance {self.match.score if self.match else 0})"
            )
        return "nouveau produit"


class ProductIntelligenceEngine:
    """Transforme des fiches marchandes en produits et offres corrélés."""

    def __init__(
        self,
        settings: IntelligenceSettings,
        catalog: "CatalogRepository",
        offers: "OfferRepository",
        monitored: "ProductRepository",
        bus: EventBus,
        matching: Optional[MatchingEngine] = None,
        search: Optional[CrossSiteSearchCoordinator] = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._offers = offers
        self._monitored = monitored
        self._bus = bus
        self._matching = matching or MatchingEngine()
        self._search = search

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def methods(self) -> list[str]:
        return self._matching.methods

    # ------------------------------------------------------------------ #
    # Ingestion                                                           #
    # ------------------------------------------------------------------ #

    async def ingest(
        self,
        discovered: DiscoveredProduct,
        identifiers: Optional[ProductIdentifiers] = None,
        attributes: Optional[ProductAttributes] = None,
        monitored_uuid: Optional[str] = None,
        fingerprint: Optional[str] = None,
        source: str = "discovery",
    ) -> IngestOutcome:
        """Rattache une fiche marchande au bon produit, ou en crée un."""
        draft = self._to_draft(discovered, identifiers, attributes)
        candidates = await self._catalog.candidates_for(draft)
        match = await self._matching.match(draft, candidates)

        product, created_product, suggestion_id = await self._resolve(draft, match)

        offer, created_offer = await self._offers.upsert(
            product_uuid=product.uuid,
            site=discovered.site,
            url=discovered.url,
            canonical_url=canonical_url(discovered.url),
            price=discovered.price,
            monitored_uuid=monitored_uuid,
            discovery_fingerprint=fingerprint,
        )

        # Le regroupement du produit surveillé devient automatique.
        if monitored_uuid:
            await self._monitored.update(monitored_uuid, group=product.uuid)

        outcome = IngestOutcome(
            product=product, offer=offer, created_product=created_product,
            created_offer=created_offer, match=match, suggestion_id=suggestion_id,
        )

        if created_product:
            await self._bus.publish(Event(EventType.CATALOG_PRODUCT_CREATED, {
                "product": product, "offer": offer, "source": source,
            }))
        if created_offer:
            await self._bus.publish(Event(EventType.CATALOG_OFFER_LINKED, {
                "product": product, "offer": offer, "match": match,
                "source": source, "summary": outcome.summary,
            }))

        log.check(
            "Intelligence — %s (%s) : %s",
            discovered.title, discovered.site, outcome.summary,
        )
        return outcome

    async def _resolve(
        self, draft: ProductDraft, match: Optional[MatchResult]
    ) -> tuple[CanonicalProduct, bool, Optional[int]]:
        """Applique le seuil de confiance : fusion, suggestion, ou création."""
        if match and match.score >= self._settings.merge_threshold:
            enriched = await self._catalog.enrich(match.product.uuid, draft)
            return (enriched or match.product), False, None

        product = await self._catalog.create(draft)

        if match and match.score >= self._settings.suggestion_floor:
            suggestion_id = await self._catalog.add_suggestion(
                product_uuid=product.uuid, candidate_uuid=match.product.uuid,
                score=match.score, method=match.method, reason=match.reason,
            )
            log.ok(
                "Rapprochement à valider : « %s » ↔ « %s » (%s, confiance %d < %d)",
                product.name, match.product.name, match.method,
                match.score, self._settings.merge_threshold,
            )
            await self._bus.publish(Event(EventType.CATALOG_MATCH_PENDING, {
                "product": product, "candidate": match.product,
                "score": match.score, "method": match.method,
                "reason": match.reason, "suggestion_id": suggestion_id,
            }))
            return product, True, suggestion_id

        return product, True, None

    def _to_draft(
        self,
        discovered: DiscoveredProduct,
        identifiers: Optional[ProductIdentifiers],
        attributes: Optional[ProductAttributes],
    ) -> ProductDraft:
        from src.intelligence.identifiers import normalise_code, normalise_ean

        # Tout ce que le plugin a su extraire de la fiche enrichit le produit :
        # identifiants forts d'abord, attributs descriptifs ensuite.
        base_ids = (identifiers or ProductIdentifiers()).merged_with(
            ProductIdentifiers(
                ean=normalise_ean(discovered.ean),
                manufacturer_sku=normalise_code(discovered.sku),
                mpn=normalise_code(discovered.mpn),
            )
        )
        base_attrs = (attributes or ProductAttributes()).merged_with(
            ProductAttributes(
                brand=discovered.brand,
                release_date=discovered.release_date,
                image_url=discovered.image_url,
            )
        )
        return ProductDraft(
            name=discovered.title,
            identifiers=base_ids,
            attributes=base_attrs,
            tags=discovered.tags,
            priority=Priority.NORMAL,
        )

    # ------------------------------------------------------------------ #
    # Fusion manuelle                                                     #
    # ------------------------------------------------------------------ #

    async def merge(self, source_uuid: str, target_uuid: str) -> Optional[CanonicalProduct]:
        """Fusionne deux fiches : les offres de `source` rejoignent `target`.

        Le produit source est conservé (aucune donnée perdue) mais se
        retrouve sans offre ; ses informations enrichissent la cible.
        """
        source = await self._catalog.get(source_uuid)
        target = await self._catalog.get(target_uuid)
        if source is None or target is None:
            return None

        await self._catalog.enrich(target_uuid, ProductDraft(
            name=target.name, identifiers=source.identifiers,
            attributes=source.attributes, tags=source.tags,
        ))
        moved = await self._offers.reassign(source_uuid, target_uuid)

        for offer in await self._offers.for_product(target_uuid):
            if offer.monitored_uuid:
                await self._monitored.update(offer.monitored_uuid, group=target_uuid)

        log.ok("Fusion : « %s » → « %s » (%d offre(s) déplacée(s)).",
               source.name, target.name, moved)
        return await self._catalog.get(target_uuid)

    # ------------------------------------------------------------------ #
    # Recherche inter-sites                                               #
    # ------------------------------------------------------------------ #

    async def find_across_sites(self, product_uuid: str) -> list[Offer]:
        """Cherche le produit chez les autres marchands et crée les offres.

        Ne fait rien si la recherche inter-sites est désactivée ou si aucun
        plugin ne sait chercher — sans jamais lever.
        """
        if not self._settings.cross_site_search or self._search is None:
            return []

        product = await self._catalog.get(product_uuid)
        if product is None:
            return []

        known_sites = {offer.site for offer in await self._offers.for_product(product_uuid)}
        results = await self._search.search(
            SearchQuery.from_product(product), exclude_sites=tuple(known_sites)
        )

        created: list[Offer] = []
        for result in results:
            for found in result.products:
                outcome = await self.ingest(found, source="cross_site_search")
                if outcome.created_offer:
                    created.append(outcome.offer)

        if created:
            log.ok(
                "Recherche inter-sites : %d nouvelle(s) offre(s) pour « %s ».",
                len(created), product.name,
            )
        return created
