"""Routes santé : healthcheck public (Railway) et santé détaillée."""

from __future__ import annotations

import asyncio
import os
import platform

from fastapi import APIRouter, Depends, HTTPException, Query

import src
from src.web.deps import get_ctx, require_auth
from src.web.schemas.observability import (
    AnomalyOut,
    DiagnosticsOut,
    DiscoveryHealthOut,
    EngineEventOut,
    EngineOverviewOut,
    IncidentOut,
    IntelligenceHealthOut,
    PhaseTimingsOut,
    PluginHealthOut,
    ProductHealthOut,
    SystemScoreOut,
)
from src.web.schemas.system import HealthOut, SystemHealthOut
from src.web.state import AppContext

router = APIRouter(tags=["Santé"])

#: Fenêtre d'observation par défaut, en heures.
DEFAULT_WINDOW = 24


@router.get(
    "/health",
    response_model=HealthOut,
    summary="Healthcheck",
    description="Public : utilisé par le healthcheck Railway et les sondes.",
)
async def health(ctx: AppContext = Depends(get_ctx)) -> HealthOut:
    return HealthOut(
        status="ok",
        version=src.__version__,
        uptime_seconds=ctx.stats.uptime_seconds,
    )


@router.get(
    "/health/system",
    response_model=SystemHealthOut,
    summary="Santé détaillée",
    description="CPU, mémoire, scheduler, Telegram, tâches asyncio (page Santé).",
    dependencies=[Depends(require_auth)],
)
async def system_health(ctx: AppContext = Depends(get_ctx)) -> SystemHealthOut:
    cpu_percent = memory_mb = None
    try:
        import psutil

        process = psutil.Process()
        cpu_percent = process.cpu_percent(interval=None)
        memory_mb = round(process.memory_info().rss / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001 — psutil indisponible ne casse pas la page
        pass

    engine_task = ctx.engine_task
    scheduler_running = bool(
        engine_task is not None and not getattr(engine_task, "done", lambda: True)()
    )
    return SystemHealthOut(
        status="ok",
        version=src.__version__,
        python_version=platform.python_version(),
        railway_environment=os.getenv("RAILWAY_ENVIRONMENT"),
        uptime_seconds=ctx.stats.uptime_seconds,
        cpu_percent=cpu_percent,
        memory_mb=memory_mb,
        scheduler_running=scheduler_running,
        watchers_active=ctx.engine.active_count,
        telegram_configured=ctx.settings.telegram_configured,
        asyncio_tasks=len(asyncio.all_tasks()),
        database=ctx.settings.database_url.split("+")[0].split(":")[0],
    )


# --------------------------------------------------------------------- #
# Observabilité                                                          #
# --------------------------------------------------------------------- #

def _health_service(ctx: AppContext):
    if ctx.health is None:  # pragma: no cover — contexte incomplet
        raise HTTPException(503, "Service d'observabilité indisponible.")
    return ctx.health


@router.get(
    "/health/overview",
    response_model=EngineOverviewOut,
    summary="Vue globale du moteur",
    description="Plugins, produits, offres, découvertes, alertes, erreurs et "
                "temps d'analyse sur la fenêtre demandée. Tout est agrégé "
                "depuis des lignes déjà écrites par le cycle de surveillance.",
    dependencies=[Depends(require_auth)],
)
async def overview(
    hours: int = Query(DEFAULT_WINDOW, ge=1, le=24 * 30),
    ctx: AppContext = Depends(get_ctx),
) -> EngineOverviewOut:
    return EngineOverviewOut(**await _health_service(ctx).overview(hours))


@router.get(
    "/plugins/health",
    response_model=list[PluginHealthOut],
    summary="Santé de chaque plugin",
    description="Une carte par plugin : Health Score, taux de succès, "
                "erreurs HTTP, bascules navigateur, interceptions, états "
                "indéterminés et dernière erreur.",
    dependencies=[Depends(require_auth)],
    tags=["Santé"],
)
async def plugins_health(
    hours: int = Query(DEFAULT_WINDOW, ge=1, le=24 * 30),
    ctx: AppContext = Depends(get_ctx),
) -> list[PluginHealthOut]:
    return [
        PluginHealthOut(**card)
        for card in await _health_service(ctx).plugins(hours)
    ]


@router.get(
    "/products/{product_uuid}/health",
    response_model=ProductHealthOut,
    summary="Santé d'un produit",
    description="Dernière analyse, dernière alerte, confiance moyenne, "
                "vérifications, erreurs et derniers événements techniques.",
    dependencies=[Depends(require_auth)],
    tags=["Santé"],
)
async def product_health(
    product_uuid: str,
    hours: int = Query(DEFAULT_WINDOW, ge=1, le=24 * 30),
    ctx: AppContext = Depends(get_ctx),
) -> ProductHealthOut:
    data = await _health_service(ctx).product(product_uuid, hours)
    if data is None:
        raise HTTPException(404, "Produit introuvable.")
    return ProductHealthOut(**data)


@router.get(
    "/diagnostics",
    response_model=DiagnosticsOut,
    summary="Diagnostic complet",
    description="Vue globale, plugins, découverte, intelligence, anomalies "
                "détectées automatiquement, historique technique et séries "
                "prêtes à tracer. C'est la réponse unique qui alimente la "
                "page Santé du dashboard.",
    dependencies=[Depends(require_auth)],
)
async def diagnostics(
    hours: int = Query(DEFAULT_WINDOW, ge=1, le=24 * 30),
    history_limit: int = Query(100, ge=1, le=500),
    ctx: AppContext = Depends(get_ctx),
) -> DiagnosticsOut:
    service = _health_service(ctx)
    return DiagnosticsOut(
        overview=EngineOverviewOut(**await service.overview(hours)),
        system=SystemScoreOut(**await service.system_score(hours)),
        plugins=[
            PluginHealthOut(**card) for card in await service.plugins(hours)
        ],
        discovery=DiscoveryHealthOut(**await service.discovery(hours)),
        intelligence=IntelligenceHealthOut(**await service.intelligence()),
        anomalies=[
            AnomalyOut(**anomaly) for anomaly in await service.anomalies()
        ],
        incidents=[
            IncidentOut(**incident) for incident in await service.incidents(hours)
        ],
        timings=PhaseTimingsOut(**await service.timings(hours)),
        history=[
            EngineEventOut(**event)
            for event in await service.history(history_limit)
        ],
        charts=await service.charts(),
    )
