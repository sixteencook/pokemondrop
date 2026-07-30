"""Configuration du logging : console lisible + fichiers dans logs/.

Console :
    [INFO] Micromania lancé
    [CHECK] Vérification...
    [OK] Aucun changement
    [ALERTE] Précommande détectée
    [ERROR] Erreur réseau

Fichiers (rotation quotidienne implicite par taille) :
    logs/drop-monitor.log  : tout
    logs/alerts.log        : uniquement les changements / alertes
    logs/errors.log        : uniquement les erreurs
"""

from __future__ import annotations

import itertools
import logging
from collections import deque
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Niveau personnalisé pour les alertes (entre WARNING et ERROR).
ALERT_LEVEL = 35
logging.addLevelName(ALERT_LEVEL, "ALERTE")

# Niveau personnalisé « OK » et « CHECK » (informations de routine).
CHECK_LEVEL = 15
logging.addLevelName(CHECK_LEVEL, "CHECK")


class _ConsoleFormatter(logging.Formatter):
    """Formate les messages console sous la forme [NIVEAU] message."""

    _LABELS = {
        logging.DEBUG: "DEBUG",
        CHECK_LEVEL: "CHECK",
        logging.INFO: "INFO",
        logging.WARNING: "WARN",
        ALERT_LEVEL: "ALERTE",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        label = self._LABELS.get(record.levelno, record.levelname)
        timestamp = self.formatTime(record, "%H:%M:%S")
        return f"{timestamp} [{label}] {record.getMessage()}"


@dataclass(frozen=True)
class LogEntry:
    """Une ligne de log conservée en mémoire pour l'API (page Logs)."""

    id: int
    time: str          # HH:MM:SS
    level: str         # INFO, CHECK, WARN, ALERTE, ERROR…
    logger: str
    message: str


class _BufferHandler(logging.Handler):
    """Ring buffer mémoire : les N dernières lignes, servies par l'API."""

    def __init__(self, maxlen: int = 2000) -> None:
        super().__init__(level=CHECK_LEVEL)
        self._entries: deque[LogEntry] = deque(maxlen=maxlen)
        self._counter = itertools.count(1)

    def emit(self, record: logging.LogRecord) -> None:
        label = _ConsoleFormatter._LABELS.get(record.levelno, record.levelname)
        self._entries.append(LogEntry(
            id=next(self._counter),
            time=self.formatter.formatTime(record, "%H:%M:%S") if self.formatter
            else logging.Formatter().formatTime(record, "%H:%M:%S"),
            level=label,
            logger=record.name,
            message=record.getMessage(),
        ))

    def snapshot(self) -> list[LogEntry]:
        return list(self._entries)


_buffer = _BufferHandler()


def get_log_entries() -> list[LogEntry]:
    """Dernières lignes de log (ordre chronologique)."""
    return _buffer.snapshot()


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    """Initialise la sortie console, les fichiers de logs et le buffer API.

    Idempotent : les appels suivants (tests, CLI + serveur) sont ignorés.
    """
    root = logging.getLogger()
    if getattr(root, "_drop_monitor_configured", False):
        return
    root._drop_monitor_configured = True  # type: ignore[attr-defined]

    log_dir.mkdir(parents=True, exist_ok=True)

    # httpx logge les URL complètes (token Telegram inclus) : on le réduit
    # au silence sauf en cas de vrai problème.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    root.setLevel(min(logging.getLevelName(level) if level != "DEBUG" else logging.DEBUG, CHECK_LEVEL))

    # --- Buffer mémoire pour l'API (page Logs du dashboard) ----------------
    root.addHandler(_buffer)

    # --- Console ---------------------------------------------------------
    console = logging.StreamHandler()
    console.setFormatter(_ConsoleFormatter())
    console.setLevel(logging.getLevelName(level))
    root.addHandler(console)

    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    # --- Fichier principal -------------------------------------------------
    main_file = RotatingFileHandler(
        log_dir / "drop-monitor.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    main_file.setFormatter(file_fmt)
    main_file.setLevel(CHECK_LEVEL)
    root.addHandler(main_file)

    # --- Fichier des alertes -----------------------------------------------
    alerts_file = RotatingFileHandler(
        log_dir / "alerts.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    alerts_file.setFormatter(file_fmt)
    alerts_file.setLevel(ALERT_LEVEL)
    alerts_file.addFilter(lambda record: record.levelno == ALERT_LEVEL)
    root.addHandler(alerts_file)

    # --- Fichier des erreurs -----------------------------------------------
    errors_file = RotatingFileHandler(
        log_dir / "errors.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    errors_file.setFormatter(file_fmt)
    errors_file.setLevel(logging.ERROR)
    root.addHandler(errors_file)


class MonitorLogger(logging.LoggerAdapter):
    """Logger avec méthodes de confort `check()`, `ok()` et `alert()`."""

    def check(self, msg: str, *args: object) -> None:
        self.log(CHECK_LEVEL, msg, *args)

    def ok(self, msg: str, *args: object) -> None:
        self.log(logging.INFO, msg, *args)

    def alert(self, msg: str, *args: object) -> None:
        self.log(ALERT_LEVEL, msg, *args)


def get_logger(name: str) -> MonitorLogger:
    return MonitorLogger(logging.getLogger(name), {})
