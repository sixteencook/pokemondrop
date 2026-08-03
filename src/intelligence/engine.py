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

import time
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
from src.intelligence.crosssite import CrossSiteIntelligence, CrossSiteReport
from src.intelligence.identity import ProductIdentity
from src.intelligence.matching import MatchingEngine, MatchResult
from src.intelligence.search import CrossSiteSearchCoordinator
from src.intelligence.strategies import IdentityContext, IdentityStrategyRegistry
from src.models import Priority
from src.utils.logger import get_logger

if TYPE_CHECKING:  # import différé : évite le cycle repositories ↔ intelligence
    from src.repositories import CatalogRepository, OfferRepository, ProductRepository
    from src.repositories.search_attempts import SearchAttemptRepository

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
        crosssite: Optional["CrossSiteIntelligence"] = None,
        strategies: Optional["IdentityStrategyRegistry"] = None,
        attempts: Optional["SearchAttemptRepository"] = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._offers = offers
        self._monitored = monitored
        self._bus = bus
        self._matching = matching or MatchingEngine()
        self._search = search
        self._crosssite = crosssite
        self._strategies = strategies
        self._attempts = attempts

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
        html: Optional[str] = None,
        identity: Optional[ProductIdentity] = None,
    ) -> IngestOutcome:
        """Rattache une fiche marchande au bon produit, ou en crée un.

        `html` permet aux stratégies d'identité (données structurées, et
        demain OCR ou vision) d'extraire davantage de clés.
        """
        started = time.perf_counter()
        draft = self._to_draft(discovered, identifiers, attributes)
        draft = await self._enrich_identity(draft, discovered, html, identity)
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

        duration_ms = int((time.perf_counter() - started) * 1000)
        if created_product:
            await self._bus.publish(Event(EventType.CATALOG_PRODUCT_CREATED, {
                "product": product, "offer": offer, "source": source,
                "duration_ms": duration_ms,
            }))
        if created_offer:
            await self._bus.publish(Event(EventType.CATALOG_OFFER_LINKED, {
                "product": product, "offer": offer, "match": match,
                "source": source, "summary": outcome.summary,
                "duration_ms": duration_ms,
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

    async def _enrich_identity(
        self,
        draft: ProductDraft,
        discovered: DiscoveredProduct,
        html: Optional[str],
        provided: Optional[ProductIdentity],
    ) -> ProductDraft:
        """Construit le profil d'identité et le fait passer par les stratégies.

        Toute information extraite ici devient une clé de recherche pour
        TOUS les autres marchands.
        """
        from dataclasses import replace as _replace

        identity = provided or ProductIdentity()
        site = discovered.site or "inconnu"

        # Ce que le brouillon sait déjà, converti en champs d'identité.
        for name, value, confidence in (
            ("ean", draft.identifiers.ean, 100),
            ("upc", draft.identifiers.upc, 100),
            ("isbn", draft.identifiers.isbn, 100),
            ("mpn", draft.identifiers.mpn, 95),
            ("sku", draft.identifiers.manufacturer_sku, 92),
            ("manufacturer_part_number", draft.identifiers.manufacturer_ref, 90),
            ("brand", draft.attributes.brand, 90),
            ("collection", draft.attributes.collection, 80),
            ("edition", draft.attributes.edition, 80),
            ("release_date", draft.attributes.release_date, 90),
            ("primary_image", draft.attributes.image_url, 85),
            ("canonical_name", draft.name, 70),
        ):
            identity = identity.with_field(name, value, confidence, site)
        identity = identity.with_alias(draft.name)

        if self._strategies is not None and len(self._strategies):
            identity = await self._strategies.enrich(identity, IdentityContext(
                site=site, url=discovered.url, title=discovered.title,
                html=html, image_url=discovered.image_url,
            ))

        return _replace(draft, identity=identity)

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

    async def find_across_sites(
        self, product_uuid: str, only_sites: Optional[list[str]] = None
    ) -> tuple[list[Offer], Optional[CrossSiteReport]]:
        """Cherche le produit partout, avec TOUTES les clés de son identité.

        Chaque information découverte en chemin enrichit le produit et
        devient une clé pour les recherches suivantes. Les échecs sont
        mémorisés et reprogrammés — jamais perdus.
        """
        if self._crosssite is None or not self._crosssite.enabled:
            return [], None

        product = await self._catalog.get(product_uuid)
        if product is None:
            return [], None

        known_sites = {
            offer.site for offer in await self._offers.for_product(product_uuid)
        }
        candidates, report = await self._crosssite.search_everywhere(
            product_uuid, product.identity,
            exclude_sites=tuple(known_sites), only_sites=only_sites,
        )

        created = await self._ingest_candidates(candidates, product)
        report.offers_created = len(created)

        if created:
            log.ok(
                "Recherche inter-sites : %d nouvelle(s) offre(s) pour « %s » — %s",
                len(created), product.name, report.summary(),
            )
        return created, report

    async def _ingest_candidates(self, candidates, product=None) -> list[Offer]:
        """Ingère les candidats trouvés ; leurs indices enrichissent l'identité.

        Un candidat obtenu EN CHERCHANT ce produit précis n'a pas à repasser
        par une corrélation à l'aveugle : on sait déjà à qui il appartient.
        Au-dessus du seuil de confiance, l'offre lui est donc rattachée
        directement — sinon on retombe sur l'ingestion normale, qui pourra
        proposer une fusion.
        """
        created: list[Offer] = []
        for candidate in candidates:
            confident = (
                product is not None
                and candidate.confidence >= self._settings.merge_threshold
            )
            if confident:
                offer = await self._attach_candidate(candidate, product)
                if offer is not None:
                    created.append(offer)
                continue

            outcome = await self.ingest(
                candidate.to_discovered(),
                identity=candidate.identity_hints,
                source="cross_site_search",
            )
            if outcome.created_offer:
                created.append(outcome.offer)
        return created

    async def _attach_candidate(self, candidate, product) -> Optional[Offer]:
        """Rattache une offre au produit cherché et l'enrichit au passage."""
        if not candidate.identity_hints.is_empty:
            await self._catalog.enrich(product.uuid, ProductDraft(
                name=product.name, identity=candidate.identity_hints,
            ))
        offer, created = await self._offers.upsert(
            product_uuid=product.uuid,
            site=candidate.site,
            url=candidate.url,
            canonical_url=canonical_url(candidate.url),
            price=candidate.price,
            availability=candidate.availability,
        )
        if created:
            await self._bus.publish(Event(EventType.CATALOG_OFFER_LINKED, {
                "product": product, "offer": offer, "source": "cross_site_search",
                "summary": f"trouvé chez {candidate.site} — {candidate.summary}",
            }))
        return offer if created else None

    # ------------------------------------------------------------------ #
    # Relance des recherches infructueuses                                #
    # ------------------------------------------------------------------ #

    async def run_retry_loop(self) -> None:
        """Reprend périodiquement les recherches restées sans résultat.

        C'est ce qui permet de repérer une fiche publiée plusieurs heures
        après les autres, sans jamais repartir de zéro.
        """
        if self._crosssite is None:
            return
        await self._crosssite.run_retry_loop(self._retry_one)

    async def _retry_one(self, attempt) -> None:
        """Rejoue UNE recherche : même produit, même site, identité à jour."""
        product = await self._catalog.get(attempt.product_uuid)
        if product is None:
            return
        if self._attempts is not None and await self._attempts.already_found(
            attempt.product_uuid, attempt.site
        ):
            return   # le produit a été trouvé entre-temps chez ce marchand

        from src.intelligence.keys import SearchKey

        key = SearchKey(
            kind=attempt.key_kind, value=attempt.key_value,
            priority=0, fields=(attempt.key_kind,),
        )
        candidates, _ = await self._crosssite.search_everywhere(
            attempt.product_uuid, product.identity,
            only_sites=[attempt.site], only_keys=[key],
        )
        created = await self._ingest_candidates(candidates, product)
        if created:
            log.alert(
                "Relance fructueuse — « %s » enfin trouvé chez %s (%s).",
                product.name, attempt.site, key,
            )
            await self._bus.publish(Event(EventType.CATALOG_OFFER_LINKED, {
                "product": product, "offer": created[0],
                "source": "retry", "summary": f"trouvé après relance ({key})",
            }))

    def stop(self) -> None:
        if self._crosssite is not None:
            self._crosssite.stop()
