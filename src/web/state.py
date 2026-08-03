"""Contexte applicatif partagé par le serveur web.

Construit au démarrage (lifespan), il porte toutes les dépendances :
base, repositories, bus, moteur, notifications, client HTTP, services.
Les routes n'instancient jamais rien : elles lisent ce contexte.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from src.config import AppSettings
from src.core import EventBus, MonitorEngine
from src.db import Database
from src.discovery import DiscoveryEngine, DiscoverySettings
from src.intelligence import IntelligenceSettings, ProductIntelligenceEngine
from src.models import GlobalSettings
from src.monitors import MonitorRegistry
from src.notifications import NotificationManager
from src.repositories import (
    AlertRepository,
    CatalogRepository,
    CheckRepository,
    DiscoveryRepository,
    EngineEventRepository,
    OfferRepository,
    ProductRepository,
    SnapshotRepository,
    TimelineRepository,
)
from src.repositories.search_attempts import SearchAttemptRepository
from src.services import (
    HealthService,
    ProductStoryService,
    ScreenshotService,
    StatsService,
)
from src.web.ws import WsHub


@dataclass
class AppContext:
    settings: AppSettings
    defaults: GlobalSettings
    db: Database
    client: httpx.AsyncClient
    bus: EventBus
    registry: MonitorRegistry
    engine: MonitorEngine
    notifications: NotificationManager
    products: ProductRepository
    snapshots: SnapshotRepository
    checks: CheckRepository
    timeline: TimelineRepository
    alerts: AlertRepository
    discoveries: DiscoveryRepository
    catalog: CatalogRepository
    offers: OfferRepository
    attempts: SearchAttemptRepository
    engine_events: EngineEventRepository
    stats: StatsService
    screenshots: ScreenshotService
    discovery_settings: DiscoverySettings
    intelligence_settings: IntelligenceSettings
    base_dir: Path
    #: Service d'observabilité (page Santé) — posé juste après la
    #: construction du contexte, comme `stats`.
    health: Optional[HealthService] = None
    #: Histoire complète d'un produit canonique (timeline fusionnée).
    story: Optional[ProductStoryService] = None
    discovery_engine: Optional[DiscoveryEngine] = None
    discovery_task: Optional[object] = None
    retry_task: Optional[object] = None
    intelligence: Optional[ProductIntelligenceEngine] = None
    hub: WsHub = field(default_factory=WsHub)
    loop: Optional[asyncio.AbstractEventLoop] = None
    started_at: float = field(default_factory=time.monotonic)
    engine_task: Optional[object] = None  # asyncio.Task, posé par le lifespan
