"""Assemblage du routeur /api/v1."""

from fastapi import APIRouter, Depends

from src.web.deps import require_auth

from . import (
    alerts,
    auth,
    catalog,
    checks,
    discoveries,
    health,
    logs,
    monitors,
    products,
    settings,
    stats,
    timeline,
)


def build_v1_router() -> APIRouter:
    """Toutes les routes v1. L'authentification est appliquée par routeur ;
    /health et /auth/login restent publics (healthcheck Railway, connexion)."""
    v1 = APIRouter(prefix="/api/v1")

    v1.include_router(health.router)                 # public
    v1.include_router(auth.router)                   # login public, me/logout protégés
    protected = [Depends(require_auth)]
    v1.include_router(products.router, dependencies=protected)
    v1.include_router(catalog.router, dependencies=protected)
    v1.include_router(discoveries.router, dependencies=protected)
    v1.include_router(alerts.router, dependencies=protected)
    v1.include_router(timeline.router, dependencies=protected)
    v1.include_router(checks.router, dependencies=protected)
    v1.include_router(logs.router, dependencies=protected)
    v1.include_router(stats.router, dependencies=protected)
    v1.include_router(monitors.router, dependencies=protected)
    v1.include_router(settings.router, dependencies=protected)
    return v1
