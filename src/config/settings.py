"""Chargement des paramètres d'environnement (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _parse_chat_ids(raw_multi: str, raw_single: str) -> tuple[str, ...]:
    """Combine TELEGRAM_CHAT_IDS (liste séparée par des virgules) et
    TELEGRAM_CHAT_ID (ancien format, toujours supporté), sans doublon."""
    ids = [part.strip() for part in raw_multi.split(",") if part.strip()]
    if raw_single.strip() and raw_single.strip() not in ids:
        ids.insert(0, raw_single.strip())
    return tuple(ids)


@dataclass(frozen=True)
class ScreenshotSettings:
    """Configuration du service de captures (Playwright).

    `quality` (1-100) : PNG étant sans perte, la qualité pilote la densité de
    rendu — ≥ 80 capture en 2× (rendu « retina »), sinon en 1×. Si
    SCREENSHOT_FORMAT vaut jpeg, la valeur est en plus passée à l'encodeur.
    """

    enabled: bool = True
    timeout_ms: int = 20000
    quality: int = 90
    max_concurrent: int = 2
    retention_days: int = 90
    settle_delay_ms: int = 400
    image_format: str = "png"
    full_page: bool = True
    viewport_width: int = 1440
    viewport_height: int = 900
    max_attempts: int = 2
    queue_size: int = 50
    directory: Path = Path("data/screenshots")

    @property
    def device_scale_factor(self) -> float:
        return 2.0 if self.quality >= 80 else 1.0

    @classmethod
    def from_env(cls, data_dir: Path) -> "ScreenshotSettings":
        default_dir = data_dir / "screenshots"
        raw_enabled = os.getenv("SCREENSHOTS_ENABLED", "true").strip().lower()
        return cls(
            enabled=raw_enabled not in ("0", "false", "no", "off"),
            timeout_ms=int(os.getenv("SCREENSHOT_TIMEOUT", "20000")),
            quality=max(1, min(100, int(os.getenv("SCREENSHOT_QUALITY", "90")))),
            max_concurrent=max(1, int(os.getenv("SCREENSHOT_MAX_CONCURRENT", "2"))),
            retention_days=int(os.getenv("SCREENSHOT_RETENTION_DAYS", "90")),
            settle_delay_ms=int(os.getenv("SCREENSHOT_SETTLE_DELAY", "400")),
            image_format=os.getenv("SCREENSHOT_FORMAT", "png").strip().lower(),
            full_page=os.getenv("SCREENSHOT_FULL_PAGE", "true").strip().lower()
            not in ("0", "false", "no", "off"),
            max_attempts=max(1, int(os.getenv("SCREENSHOT_MAX_ATTEMPTS", "2"))),
            directory=Path(os.getenv("SCREENSHOTS_DIR", str(default_dir))).resolve(),
        )


@dataclass(frozen=True)
class AppSettings:
    """Paramètres globaux de l'application, issus du fichier .env."""

    telegram_bot_token: str
    telegram_chat_ids: tuple[str, ...]
    log_level: str
    data_dir: Path
    log_dir: Path
    database_url: str
    dashboard_username: str = ""
    dashboard_password: str = ""
    secret_key: str = ""
    token_ttl_hours: int = 24
    screenshots: ScreenshotSettings = field(default_factory=ScreenshotSettings)
    browser_fallback: bool = True
    browser_fallback_max_concurrent: int = 2
    #: Diagnostic complet des plugins dans logs/debug.log (PLUGIN_DEBUG).
    #: À activer pour comprendre une décision en moins de 30 secondes.
    plugin_debug: bool = False

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_ids)

    @property
    def auth_configured(self) -> bool:
        return bool(self.dashboard_username and self.dashboard_password)

    @property
    def evidence_dir(self) -> Path:
        """Dossier des pages archivées lors des alertes importantes."""
        return self.data_dir / "evidence"

    @classmethod
    def load(cls, env_file: Path | None = None) -> "AppSettings":
        """Charge le .env (s'il existe) puis lit les variables d'environnement.

        DATA_DIR : dossier des données persistantes (SQLite, captures) —
        sur Railway, pointer vers le volume monté.
        LOG_DIR : dossier des fichiers de logs. En conteneur, le placer sur
        le volume (les logs restent aussi sur stdout, capté par Railway).
        DATABASE_URL : par défaut SQLite dans DATA_DIR ; accepte plus tard
        une URL PostgreSQL (postgresql+asyncpg://…) sans autre changement.
        """
        load_dotenv(dotenv_path=env_file)
        data_dir = Path(os.getenv("DATA_DIR", "data")).resolve()
        default_db = f"sqlite+aiosqlite:///{(data_dir / 'drop_monitor.db').as_posix()}"
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_ids=_parse_chat_ids(
                os.getenv("TELEGRAM_CHAT_IDS", ""),
                os.getenv("TELEGRAM_CHAT_ID", ""),
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            data_dir=data_dir,
            log_dir=Path(os.getenv("LOG_DIR", "logs")).resolve(),
            database_url=os.getenv("DATABASE_URL", default_db).strip() or default_db,
            dashboard_username=os.getenv("DASHBOARD_USERNAME", "").strip(),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            secret_key=os.getenv("SECRET_KEY", "").strip(),
            token_ttl_hours=int(os.getenv("AUTH_TOKEN_TTL_HOURS", "24")),
            screenshots=ScreenshotSettings.from_env(data_dir),
            browser_fallback=os.getenv("BROWSER_FALLBACK_ENABLED", "true")
            .strip().lower() not in ("0", "false", "no", "off"),
            browser_fallback_max_concurrent=max(
                1, int(os.getenv("BROWSER_FALLBACK_MAX_CONCURRENT", "2"))
            ),
            plugin_debug=os.getenv("PLUGIN_DEBUG", "false").strip().lower()
            in ("1", "true", "yes", "on"),
        )
