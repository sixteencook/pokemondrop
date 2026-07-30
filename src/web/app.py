"""Usine FastAPI du Drop Monitor.

Une seule application héberge :
  - l'API REST versionnée (/api/v1, Swagger sur /api/docs) ;
  - le moteur de surveillance (tâche de fond démarrée dans le lifespan) ;
  - le frontend compilé (frontend/dist, monté quand il existera).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import AppSettings, ConfigError, load_config
from src.core import EventBus, MonitorEngine
from src.db import Database, import_products_from_yaml, migrate_legacy_state
from src.models import GlobalSettings
from src.monitors import create_registry
from src.notifications import NotificationManager, TelegramNotifier
from src.repositories import (
    AlertRepository,
    CheckRepository,
    ProductRepository,
    SnapshotRepository,
    TimelineRepository,
)
from src.services import EventRecorder, ScreenshotService, StatsService
from src.utils import setup_logging
from src.utils.logger import get_logger
from src.web.api import build_v1_router
from src.web.state import AppContext
from src.web.ws import EventBroadcaster, WsHub, WsLogHandler, register_websocket

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CHECKS_RETENTION_DAYS = 30

log = get_logger("web")

_API_DESCRIPTION = """
API du **Drop Monitor** — surveillance de disponibilité de produits
(précommandes, retours en stock) avec alertes Telegram.

- Authentification par cookie httpOnly (`POST /api/v1/auth/login`) ou en-tête
  `Authorization: Bearer <token>`.
- Toutes les listes volumineuses sont paginées (`page`, `page_size`) et triées
  (`sort`, `order`) avec une enveloppe commune `{items, total, page, page_size, pages}`.
- Les modifications de produits sont appliquées **à chaud** par le moteur,
  sans redémarrage.
"""

_OPENAPI_TAGS = [
    {"name": "Authentification", "description": "Connexion, session, déconnexion."},
    {"name": "Produits", "description": "CRUD des produits surveillés, vérification "
                                        "immédiate, timeline par produit."},
    {"name": "Alertes", "description": "Historique des alertes envoyées."},
    {"name": "Timeline", "description": "Flux d'activité global."},
    {"name": "Checks", "description": "Historique des vérifications."},
    {"name": "Logs", "description": "Dernières lignes de log."},
    {"name": "Statistiques", "description": "Indicateurs du dashboard."},
    {"name": "Monitors", "description": "Plugins de sites chargés."},
    {"name": "Paramètres", "description": "Configuration et diagnostic Telegram."},
    {"name": "Santé", "description": "Healthcheck et santé système."},
]


async def _build_context(
    settings: AppSettings, config_path: Optional[Path]
) -> AppContext:
    """Construit toutes les dépendances (même câblage que le CLI)."""
    defaults = GlobalSettings()
    yaml_products = []
    if config_path is not None:
        try:
            defaults, yaml_products = load_config(config_path)
        except ConfigError as exc:
            log.error("Seed YAML ignoré (configuration invalide) : %s", exc)

    db = Database(settings.database_url)
    await db.init()
    products = ProductRepository(db.session_factory)
    snapshots = SnapshotRepository(db.session_factory)
    checks = CheckRepository(db.session_factory)
    timeline = TimelineRepository(db.session_factory)
    alerts = AlertRepository(db.session_factory)

    if yaml_products:
        await import_products_from_yaml(products, yaml_products)
    db_products = await products.list_all()
    await migrate_legacy_state(db_products, BASE_DIR / "data" / "state", snapshots)
    purged = await checks.purge_older_than(CHECKS_RETENTION_DAYS)
    if purged:
        log.ok("Historique : %d check(s) de plus de %d jours purgés.",
               purged, CHECKS_RETENTION_DAYS)

    client = httpx.AsyncClient(timeout=httpx.Timeout(defaults.request_timeout))
    registry = create_registry(client)

    # Ordre d'abonnement (le bus respecte l'ordre) :
    #   1) la base           → pose alert_id dans le payload
    #   2) les captures      → enfilent (instantané) et posent screenshot_pending
    #   3) le WebSocket      → le dashboard n'attend ni Playwright ni Telegram
    #   4) les notifications → envoient, ou patientent si une capture est en cours
    bus = EventBus()
    EventRecorder(checks, timeline, alerts).attach_to(bus)

    screenshots = ScreenshotService(settings.screenshots, bus, registry)
    screenshots.attach_to(bus)

    hub = WsHub()
    EventBroadcaster(hub).attach_to(bus)

    notifications = NotificationManager(screenshots_dir=settings.screenshots.directory)
    if settings.telegram_configured:
        notifications.register(TelegramNotifier(
            settings.telegram_bot_token, settings.telegram_chat_ids, client
        ))
    else:
        log.ok("Telegram non configuré — alertes uniquement en logs/base.")
    notifications.attach_to(bus)

    engine = MonitorEngine(registry, bus, snapshots, defaults,
                           product_provider=products.list_all)

    ctx = AppContext(
        settings=settings, defaults=defaults, db=db, client=client, bus=bus,
        registry=registry, engine=engine, notifications=notifications,
        products=products, snapshots=snapshots, checks=checks,
        timeline=timeline, alerts=alerts,
        stats=None,  # type: ignore[arg-type] — posé juste en dessous
        screenshots=screenshots,
        base_dir=BASE_DIR,
        hub=hub,
        loop=asyncio.get_running_loop(),
    )
    ctx.stats = StatsService(products, checks, alerts, registry, engine,
                             ctx.started_at)
    return ctx


def _log_boot_summary(settings: AppSettings) -> None:
    """Résumé de l'environnement au démarrage (diagnostic des déploiements)."""
    import os
    import platform

    import src

    environment = os.getenv("RAILWAY_ENVIRONMENT") or "local"
    log.ok(
        "Drop Monitor v%s — Python %s, environnement « %s », port %s",
        src.__version__, platform.python_version(), environment,
        os.getenv("PORT", "8000"),
    )
    log.ok(
        "Stockage — base : %s · données : %s · captures : %s · logs : %s",
        settings.database_url.split("+")[0].split(":")[0],
        settings.data_dir, settings.screenshots.directory, settings.log_dir,
    )
    if environment != "local" and not str(settings.data_dir).startswith(("/data", "/mnt")):
        log.error(
            "DATA_DIR vaut « %s » : hors d'un volume monté, la base et les "
            "captures seront PERDUES au prochain déploiement.", settings.data_dir,
        )


def create_app(
    settings: Optional[AppSettings] = None,
    config_path: Optional[Path] = BASE_DIR / "config" / "products.yaml",
    run_engine: bool = True,
) -> FastAPI:
    """Crée l'application. `settings`/`config_path`/`run_engine` sont
    surchargés par les tests."""
    app_settings = settings or AppSettings.load(BASE_DIR / ".env")
    setup_logging(app_settings.log_dir, app_settings.log_level)

    _log_boot_summary(app_settings)

    if not app_settings.secret_key:
        log.error("SECRET_KEY absent : clé de session éphémère générée — "
                  "les connexions ne survivront pas à un redémarrage.")
    if not app_settings.auth_configured:
        log.error("DASHBOARD_USERNAME / DASHBOARD_PASSWORD absents : l'API "
                  "refusera toute connexion tant qu'ils ne sont pas définis.")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx = await _build_context(app_settings, config_path)
        app.state.ctx = ctx

        # Diffusion des logs vers les clients WebSocket connectés.
        import logging as _logging

        ws_log_handler = WsLogHandler(ctx.hub, ctx.loop)
        _logging.getLogger().addHandler(ws_log_handler)
        log_pump = asyncio.create_task(ws_log_handler.pump(), name="ws-log-pump")

        await ctx.screenshots.start()

        if run_engine:
            ctx.engine_task = asyncio.create_task(ctx.engine.run(), name="monitor-engine")
            log.ok("Moteur de surveillance démarré dans le serveur web.")
        try:
            yield
        finally:
            ctx.engine.stop()
            task = ctx.engine_task
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await ctx.screenshots.stop()  # ferme proprement Chromium
            log_pump.cancel()
            _logging.getLogger().removeHandler(ws_log_handler)
            await ctx.client.aclose()
            await ctx.db.dispose()
            log.ok("Serveur arrêté proprement.")

    app = FastAPI(
        title="Drop Monitor API",
        version="1.0.0",
        description=_API_DESCRIPTION,
        openapi_tags=_OPENAPI_TAGS,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.include_router(build_v1_router())
    register_websocket(app)

    # Frontend compilé : SPA avec fallback sur index.html (routing côté client).
    dist = BASE_DIR / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            candidate = (dist / full_path).resolve()
            # Fichier statique réel (manifest, sw.js, icônes…) et jamais
            # d'échappement hors de dist ; sinon → shell React.
            if (
                full_path
                and candidate.is_file()
                and candidate.is_relative_to(dist.resolve())
            ):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
