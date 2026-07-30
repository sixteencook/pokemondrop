"""Routes santé : healthcheck public (Railway) et santé détaillée."""

from __future__ import annotations

import asyncio
import os
import platform

from fastapi import APIRouter, Depends

import src
from src.web.deps import get_ctx, require_auth
from src.web.schemas.system import HealthOut, SystemHealthOut
from src.web.state import AppContext

router = APIRouter(tags=["Santé"])


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
