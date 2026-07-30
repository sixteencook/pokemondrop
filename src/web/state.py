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
from src.models import GlobalSettings
from src.monitors import MonitorRegistry
from src.notifications import NotificationManager
from src.repositories import (
    AlertRepository,
    CheckRepository,
    ProductRepository,
    SnapshotRepository,
    TimelineRepository,
)
from src.services import ScreenshotService, StatsService
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
    stats: StatsService
    screenshots: ScreenshotService
    base_dir: Path
    hub: WsHub = field(default_factory=WsHub)
    loop: Optional[asyncio.AbstractEventLoop] = None
    started_at: float = field(default_factory=time.monotonic)
    engine_task: Optional[object] = None  # asyncio.Task, posé par le lifespan
