"""Routes de la couche Découverte."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.models import DiscoveryStatus
from src.web.deps import get_ctx
from src.web.schemas import Page, PageParams, SortParams, page_params, sort_params
from src.web.schemas.discovery import DiscoveryOut, DiscoveryStatusOut, ScanReportOut
from src.web.state import AppContext

router = APIRouter(prefix="/discoveries", tags=["Découverte"])


async def _get_or_404(ctx: AppContext, fingerprint: str):
    record = await ctx.discoveries.get(fingerprint)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Fiche découverte introuvable.")
    return record


@router.get(
    "",
    response_model=Page[DiscoveryOut],
    summary="Fiches découvertes",
    description="Liste paginée des fiches repérées automatiquement. "
                "Filtres : statut (pending, imported, ignored, blocked, gone), "
                "site, recherche sur le titre. Tri : first_seen_at, "
                "last_seen_at, title, site, status.",
)
async def list_discoveries(
    ctx: AppContext = Depends(get_ctx),
    pagination: PageParams = Depends(page_params),
    sorting: SortParams = Depends(sort_params),
    status_filter: Optional[DiscoveryStatus] = Query(None, alias="status"),
    site: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Recherche sur le titre"),
) -> Page[DiscoveryOut]:
    items, total = await ctx.discoveries.list_page(
        page=pagination.page, page_size=pagination.page_size,
        sort=sorting.sort, order=sorting.order,
        status=status_filter.value if status_filter else None,
        site=site, search=search,
    )
    return Page.build([DiscoveryOut.from_domain(item) for item in items],
                      total, pagination)


@router.get(
    "/status",
    response_model=DiscoveryStatusOut,
    summary="État de la découverte",
)
async def discovery_status(ctx: AppContext = Depends(get_ctx)) -> DiscoveryStatusOut:
    engine = ctx.discovery_engine
    report = engine.last_report if engine else None
    return DiscoveryStatusOut(
        enabled=bool(engine and engine.enabled),
        mode=ctx.discovery_settings.mode.value,
        scan_interval=ctx.discovery_settings.scan_interval,
        sites=engine.sites if engine else [],
        counts=await ctx.discoveries.count_by_status(),
        last_discovery_at=await ctx.discoveries.last_discovery_at(),
        last_scan_summary=report.summary() if report else None,
    )


@router.post(
    "/scan",
    response_model=ScanReportOut,
    summary="Lancer un balayage",
    description="Déclenche immédiatement une exploration de tous les sites "
                "activés, sans attendre le prochain cycle.",
)
async def trigger_scan(ctx: AppContext = Depends(get_ctx)) -> ScanReportOut:
    engine = ctx.discovery_engine
    if engine is None or not engine.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Découverte désactivée : activez-la dans config/discovery.yaml.",
        )
    report = await engine.scan_all()
    return ScanReportOut(
        sites_scanned=report.sites_scanned, products_seen=report.products_seen,
        new_products=report.new_products, imported=report.imported,
        pending=report.pending, excluded=report.excluded, gone=report.gone,
        errors=report.errors, summary=report.summary(),
    )


@router.post(
    "/{fingerprint}/approve",
    response_model=DiscoveryOut,
    summary="Ajouter à la surveillance",
    description="Crée le produit et démarre sa surveillance à chaud, sans "
                "redémarrage.",
)
async def approve(fingerprint: str, ctx: AppContext = Depends(get_ctx)) -> DiscoveryOut:
    record = await _get_or_404(ctx, fingerprint)
    if record.product_uuid:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Cette fiche est déjà sous surveillance.")
    if ctx.discovery_engine is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Couche Découverte indisponible.")
    await ctx.discovery_engine.import_product(record)
    return DiscoveryOut.from_domain(await _get_or_404(ctx, fingerprint))


@router.post(
    "/{fingerprint}/ignore",
    response_model=DiscoveryOut,
    summary="Ignorer",
    description="Écarte la fiche. Elle pourra réapparaître si le site la "
                "republie.",
)
async def ignore(fingerprint: str, ctx: AppContext = Depends(get_ctx)) -> DiscoveryOut:
    await _get_or_404(ctx, fingerprint)
    record = await ctx.discoveries.set_status(
        fingerprint, DiscoveryStatus.IGNORED, "ignorée manuellement"
    )
    return DiscoveryOut.from_domain(record)


@router.post(
    "/{fingerprint}/block",
    response_model=DiscoveryOut,
    summary="Toujours ignorer",
    description="Décision durable : la fiche ne sera plus jamais proposée, "
                "même si le site la republie.",
)
async def block(fingerprint: str, ctx: AppContext = Depends(get_ctx)) -> DiscoveryOut:
    await _get_or_404(ctx, fingerprint)
    record = await ctx.discoveries.set_status(
        fingerprint, DiscoveryStatus.BLOCKED, "bloquée définitivement"
    )
    return DiscoveryOut.from_domain(record)
