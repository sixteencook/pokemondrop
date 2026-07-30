"""Schémas auth, paramètres, monitors, stats et santé."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Auth ----------------------------------------------------------------- #

class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    username: str


# --- Stats ----------------------------------------------------------------- #

class StatsOverviewOut(BaseModel):
    """État global affiché en tête du dashboard."""

    monitor_active: bool = Field(description="Au moins un produit est surveillé")
    products_total: int
    products_enabled: int
    products_watched: int = Field(description="Boucles de surveillance actives")
    sites_count: int
    last_check_at: Optional[datetime]
    last_alert_at: Optional[datetime]
    uptime_seconds: int
    checks_total: int
    alerts_total: int
    avg_response_ms_24h: Optional[float]


class ChecksPerHourPoint(BaseModel):
    """Un point du graphique « checks par heure »."""

    hour: str = Field(description="Début de l'heure, format ISO (ex. 2026-07-30T14:00)")
    total: int
    errors: int
    avg_response_ms: Optional[float]


class AlertsPerDayPoint(BaseModel):
    day: str = Field(description="Jour, format YYYY-MM-DD")
    total: int


class SiteAvailabilityPoint(BaseModel):
    site: str
    availability: str
    count: int


class SiteCountPoint(BaseModel):
    site: str
    count: int


# --- Monitors --------------------------------------------------------------- #

class MonitorOut(BaseModel):
    """Un plugin de site chargé, avec ses agrégats."""

    site: str
    display_name: str
    version: Optional[str]
    base_url: Optional[str]
    description: Optional[str]
    product_count: int
    watched_count: int
    last_check_at: Optional[datetime]
    last_error: Optional[str]
    last_error_at: Optional[datetime]
    avg_response_ms: Optional[float]
    total_checks: int


# --- Paramètres ------------------------------------------------------------- #

class TelegramSettingsOut(BaseModel):
    configured: bool
    chat_count: int
    token_preview: Optional[str] = Field(None, description="Token masqué (…4 derniers caractères)")


class ScreenshotSettingsOut(BaseModel):
    """Configuration du service de captures (lecture seule)."""

    enabled: bool
    available: bool = Field(description="False si Playwright/Chromium est indisponible")
    timeout_ms: int
    quality: int
    max_concurrent: int
    retention_days: int
    image_format: str
    full_page: bool
    directory: str
    pending: int = Field(description="Captures actuellement en file d'attente")


class SettingsOut(BaseModel):
    telegram: TelegramSettingsOut
    screenshots: ScreenshotSettingsOut
    log_level: str
    database: str = Field(description="Type de base (sqlite / postgresql)")
    data_dir: str
    auth_configured: bool


class TelegramChatStatusOut(BaseModel):
    chat_id: str
    ok: bool
    title: Optional[str]


class TelegramStatusOut(BaseModel):
    configured: bool
    bot_ok: bool = Field(description="Le bot répond à getMe")
    bot_username: Optional[str]
    chats: list[TelegramChatStatusOut]


class TelegramTestOut(BaseModel):
    sent: bool
    recipients: int


# --- Santé ------------------------------------------------------------------- #

class HealthOut(BaseModel):
    """Réponse du healthcheck (public, utilisé par Railway)."""

    status: str = Field(description="ok")
    version: str
    uptime_seconds: int


class SystemHealthOut(BaseModel):
    """Page Santé détaillée (authentifiée)."""

    status: str
    version: str
    python_version: str
    railway_environment: Optional[str]
    uptime_seconds: int
    cpu_percent: Optional[float]
    memory_mb: Optional[float]
    scheduler_running: bool
    watchers_active: int
    telegram_configured: bool
    asyncio_tasks: int
    database: str
