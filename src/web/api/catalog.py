"""Routes du catalogue produit (Product Intelligence Engine)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.discovery.contracts import DiscoveredProduct
from src.web.deps import get_ctx
from src.web.schemas import Page, PageParams, SortParams, page_params, sort_params
from src.web.schemas.catalog import (
    CatalogProductOut,
    CatalogStatusOut,
    CrossSiteReportOut,
    ManualProductIn,
    MatchSuggestionOut,
    OfferHistoryOut,
    OfferOut,
    ProductIdentityOut,
    SearchAttemptOut,
)
from src.web.state import AppContext

router = APIRouter(prefix="/catalog", tags=["Catalogue"])


async def _product_or_404(ctx: AppContext, product_uuid: str):
    product = await ctx.catalog.get(product_uuid)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Produit introuvable dans le catalogue.")
    return product


@router.get(
    "/products",
    response_model=Page[CatalogProductOut],
    summary="Produits du catalogue",
    description="Produits canoniques avec toutes leurs offres marchandes. "
                "Un produit vendu sur cinq sites n'apparaît qu'UNE fois. "
                "Tri : name, brand, release_date, created_at, updated_at.",
)
async def list_products(
    ctx: AppContext = Depends(get_ctx),
    pagination: PageParams = Depends(page_params),
    sorting: SortParams = Depends(sort_params),
    search: Optional[str] = Query(None, description="Recherche sur le nom"),
    brand: Optional[str] = Query(None),
    include_empty: bool = Query(
        False,
        description="Inclure les produits sans offre (coquilles laissées "
                    "par une fusion). Masqués par défaut.",
    ),
) -> Page[CatalogProductOut]:
    items, total = await ctx.catalog.list_page(
        page=pagination.page, page_size=pagination.page_size,
        sort=sorting.sort, order=sorting.order, search=search, brand=brand,
        with_offers_only=not include_empty,
    )
    offers = await ctx.offers.for_products([product.uuid for product in items])
    return Page.build(
        [
            CatalogProductOut.from_domain(product, offers.get(product.uuid, []))
            for product in items
        ],
        total, pagination,
    )


@router.get(
    "/products/{product_uuid}",
    response_model=CatalogProductOut,
    summary="Fiche produit et ses offres",
)
async def get_product(
    product_uuid: str, ctx: AppContext = Depends(get_ctx)
) -> CatalogProductOut:
    product = await _product_or_404(ctx, product_uuid)
    return CatalogProductOut.from_domain(
        product, await ctx.offers.for_product(product_uuid)
    )


@router.get(
    "/offers/{offer_uuid}/history",
    response_model=list[OfferHistoryOut],
    summary="Historique d'une offre",
    description="Chaque évolution de prix, de disponibilité ou de statut. "
                "Une offre n'est jamais supprimée : son historique reste entier.",
)
async def offer_history(
    offer_uuid: str, ctx: AppContext = Depends(get_ctx)
) -> list[OfferHistoryOut]:
    if await ctx.offers.get(offer_uuid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Offre introuvable.")
    return [
        OfferHistoryOut.from_domain(entry)
        for entry in await ctx.offers.history(offer_uuid)
    ]


@router.get(
    "/products/{product_uuid}/identity",
    response_model=ProductIdentityOut,
    summary="Profil d'identité",
    description="Toutes les informations connues du produit, avec la "
                "confiance et la source de chacune, plus la liste des "
                "recherches possibles par pouvoir discriminant.",
)
async def product_identity(
    product_uuid: str, ctx: AppContext = Depends(get_ctx)
) -> ProductIdentityOut:
    product = await _product_or_404(ctx, product_uuid)
    return ProductIdentityOut.from_domain(product.identity)


@router.get(
    "/products/{product_uuid}/search-attempts",
    response_model=list[SearchAttemptOut],
    summary="Historique des recherches",
    description="Ce qui a été cherché, chez qui, avec quelle clé et avec "
                "quel résultat — y compris les échecs, avec l'heure de leur "
                "prochaine relance automatique.",
)
async def product_search_attempts(
    product_uuid: str, ctx: AppContext = Depends(get_ctx)
) -> list[SearchAttemptOut]:
    await _product_or_404(ctx, product_uuid)
    return [
        SearchAttemptOut.from_domain(attempt)
        for attempt in await ctx.attempts.for_product(product_uuid)
    ]


@router.get(
    "/status",
    response_model=CatalogStatusOut,
    summary="État de l'intelligence produit",
)
async def catalog_status(ctx: AppContext = Depends(get_ctx)) -> CatalogStatusOut:
    engine = ctx.intelligence
    settings = ctx.intelligence_settings
    suggestions = await ctx.catalog.list_suggestions()
    crosssite = getattr(engine, "_crosssite", None) if engine else None
    strategies = getattr(engine, "_strategies", None) if engine else None
    return CatalogStatusOut(
        enabled=bool(engine and engine.enabled),
        merge_threshold=settings.merge_threshold,
        suggestion_floor=settings.suggestion_floor,
        cross_site_search=bool(crosssite and crosssite.enabled),
        products=await ctx.catalog.count(),
        offers=await ctx.offers.count(),
        pending_suggestions=len(suggestions),
        methods=engine.methods if engine else [],
        search_capable_sites=crosssite.capable_sites if crosssite else [],
        identity_strategies=strategies.names if strategies else [],
        pending_retries=await ctx.attempts.pending_retries(),
    )


@router.get(
    "/suggestions",
    response_model=list[MatchSuggestionOut],
    summary="Fusions à valider",
    description="Rapprochements dont la confiance est inférieure au seuil de "
                "fusion automatique. Rien n'est fusionné sans validation.",
)
async def list_suggestions(
    ctx: AppContext = Depends(get_ctx)
) -> list[MatchSuggestionOut]:
    suggestions = await ctx.catalog.list_suggestions()
    results = []
    for suggestion in suggestions:
        product = await ctx.catalog.get(suggestion.product_uuid)
        candidate = await ctx.catalog.get(suggestion.candidate_uuid)
        results.append(MatchSuggestionOut.from_domain(
            suggestion,
            product.name if product else None,
            candidate.name if candidate else None,
        ))
    return results


@router.post(
    "/suggestions/{suggestion_id}/accept",
    response_model=CatalogProductOut,
    summary="Accepter une fusion",
    description="Les offres du produit rapproché rejoignent le produit cible.",
)
async def accept_suggestion(
    suggestion_id: int, ctx: AppContext = Depends(get_ctx)
) -> CatalogProductOut:
    suggestion = next(
        (item for item in await ctx.catalog.list_suggestions()
         if item.id == suggestion_id),
        None,
    )
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Suggestion introuvable ou déjà traitée.")
    if ctx.intelligence is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Intelligence produit indisponible.")

    merged = await ctx.intelligence.merge(
        suggestion.product_uuid, suggestion.candidate_uuid
    )
    if merged is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Fusion impossible : produit manquant.")
    await ctx.catalog.set_suggestion_status(suggestion_id, "accepted")
    return CatalogProductOut.from_domain(
        merged, await ctx.offers.for_product(merged.uuid)
    )


@router.post(
    "/suggestions/{suggestion_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Refuser une fusion",
)
async def reject_suggestion(
    suggestion_id: int, ctx: AppContext = Depends(get_ctx)
) -> None:
    if not await ctx.catalog.set_suggestion_status(suggestion_id, "rejected"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Suggestion introuvable.")


@router.post(
    "/products/{product_uuid}/find-offers",
    response_model=list[OfferOut],
    summary="Chercher ce produit chez les autres marchands",
    description="Interroge tous les plugins sachant chercher, avec "
                "l'identifiant le plus fort disponible (EAN, UPC, MPN, nom). "
                "Chaque plugin choisit librement sa méthode.",
)
async def find_offers(
    product_uuid: str, ctx: AppContext = Depends(get_ctx)
) -> CrossSiteReportOut:
    await _product_or_404(ctx, product_uuid)
    crosssite = getattr(ctx.intelligence, "_crosssite", None)
    if ctx.intelligence is None or crosssite is None or not crosssite.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Recherche inter-sites désactivée (intelligence."
                   "cross_site_search dans config/discovery.yaml), ou aucun "
                   "plugin ne sait chercher.",
        )
    _, report = await ctx.intelligence.find_across_sites(product_uuid)
    return CrossSiteReportOut(
        sites_queried=report.sites_queried, keys_tried=report.keys_tried,
        candidates_found=report.candidates_found,
        offers_created=report.offers_created,
        retries_scheduled=report.retries_scheduled,
        errors=report.errors, summary=report.summary(),
    )


@router.post(
    "/products",
    response_model=CatalogProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter une fiche manuellement",
    description="Source de découverte « manuelle » : la fiche passe par la "
                "même corrélation que celles trouvées automatiquement.",
)
async def add_manual(
    body: ManualProductIn, ctx: AppContext = Depends(get_ctx)
) -> CatalogProductOut:
    if ctx.intelligence is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Intelligence produit indisponible.")
    outcome = await ctx.intelligence.ingest(
        DiscoveredProduct(
            url=body.url, title=body.title, site=body.site.lower(),
            price=body.price, ean=body.ean, sku=body.sku, brand=body.brand,
            source="manual",
        ),
        source="manual",
    )
    return CatalogProductOut.from_domain(
        outcome.product, await ctx.offers.for_product(outcome.product.uuid)
    )
