"""Routes stats : état global + agrégats pour les graphiques du dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.web.deps import get_ctx
from src.web.schemas.system import (
    AlertsPerDayPoint,
    ChecksPerHourPoint,
    SiteAvailabilityPoint,
    SiteCountPoint,
    StatsOverviewOut,
)
from src.web.state import AppContext

router = APIRouter(prefix="/stats", tags=["Statistiques"])


@router.get(
    "/overview",
    response_model=StatsOverviewOut,
    summary="État global",
    description="Compteurs et indicateurs affichés en tête du dashboard.",
)
async def overview(ctx: AppContext = Depends(get_ctx)) -> StatsOverviewOut:
    return StatsOverviewOut(**await ctx.stats.overview())


@router.get(
    "/checks-per-hour",
    response_model=list[ChecksPerHourPoint],
    summary="Checks par heure",
    description="Volume de checks, erreurs et temps de réponse moyen, agrégés "
                "par heure — alimente les graphiques « checks/heure » et "
                "« évolution du temps de réponse ».",
)
async def checks_per_hour(
    ctx: AppContext = Depends(get_ctx),
    hours: int = Query(24, ge=1, le=168),
) -> list[ChecksPerHourPoint]:
    return [ChecksPerHourPoint(**point) for point in await ctx.checks.per_hour(hours)]


@router.get(
    "/alerts-per-day",
    response_model=list[AlertsPerDayPoint],
    summary="Alertes par jour",
)
async def alerts_per_day(
    ctx: AppContext = Depends(get_ctx),
    days: int = Query(14, ge=1, le=90),
) -> list[AlertsPerDayPoint]:
    return [AlertsPerDayPoint(**point) for point in await ctx.alerts.per_day(days)]


@router.get(
    "/availability-by-site",
    response_model=list[SiteAvailabilityPoint],
    summary="Disponibilité par site",
    description="Répartition des statuts courants (précommande, stock, "
                "indisponible…) par site.",
)
async def availability_by_site(
    ctx: AppContext = Depends(get_ctx),
) -> list[SiteAvailabilityPoint]:
    return [SiteAvailabilityPoint(**point)
            for point in await ctx.snapshots.availability_by_site()]


@router.get(
    "/products-by-site",
    response_model=list[SiteCountPoint],
    summary="Répartition des produits par site",
)
async def products_by_site(ctx: AppContext = Depends(get_ctx)) -> list[SiteCountPoint]:
    counts = await ctx.products.count_by_site()
    return [SiteCountPoint(site=site, count=count)
            for site, count in sorted(counts.items())]
