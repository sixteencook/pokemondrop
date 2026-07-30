"""Point d'entrée du Drop Monitor (mode CLI, sans interface web).

Usage :
    python main.py
    python main.py --config config/products.yaml
    python main.py --test-telegram      # envoie un message de test puis quitte

Câblage : le moteur publie sur l'EventBus ; l'EventRecorder (SQLite) et
les notifications sont des abonnés. La base de données est la source de
vérité des produits — le YAML n'est qu'un seed initial, importé au
premier démarrage.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from src.config import AppSettings, ConfigError, load_config
from src.core import EventBus, MonitorEngine
from src.db import Database, import_products_from_yaml, migrate_legacy_state
from src.monitors import create_registry
from src.notifications import NotificationManager, TelegramNotifier
from src.repositories import (
    AlertRepository,
    CheckRepository,
    ProductRepository,
    SnapshotRepository,
    TimelineRepository,
)
from src.services import EventRecorder, ScreenshotService, send_test_alert
from src.utils import setup_logging
from src.utils.logger import get_logger

BASE_DIR = Path(__file__).resolve().parent

#: Rétention de l'historique des checks (la table grossit vite).
CHECKS_RETENTION_DAYS = 30

log = get_logger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drop Monitor — alertes de disponibilité produits")
    parser.add_argument(
        "--config",
        type=Path,
        default=BASE_DIR / "config" / "products.yaml",
        help="Chemin du fichier de configuration YAML (seed initial)",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Envoie un message de test Telegram puis quitte",
    )
    return parser.parse_args()


async def run() -> int:
    args = parse_args()

    settings = AppSettings.load(BASE_DIR / ".env")
    setup_logging(settings.log_dir, settings.log_level)

    try:
        defaults, yaml_products = load_config(args.config)
    except ConfigError as exc:
        log.error("Configuration invalide : %s", exc)
        return 1

    timeout = httpx.Timeout(defaults.request_timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if args.test_telegram:
            if not settings.telegram_configured:
                log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS manquants dans .env")
                return 1
            ok = await send_test_alert(settings, client)
            log.ok(
                "Test Telegram (%d destinataire(s)) : %s",
                len(settings.telegram_chat_ids),
                "message envoyé ✅" if ok else "échec ❌",
            )
            return 0 if ok else 1

        # --- Base de données ---------------------------------------------
        db = Database(settings.database_url)
        await db.init()
        products_repo = ProductRepository(db.session_factory)
        snapshots_repo = SnapshotRepository(db.session_factory)
        checks_repo = CheckRepository(db.session_factory)
        timeline_repo = TimelineRepository(db.session_factory)
        alerts_repo = AlertRepository(db.session_factory)

        # Migration progressive : seed YAML puis reprise de l'ancien état JSON.
        await import_products_from_yaml(products_repo, yaml_products)
        db_products = await products_repo.list_all()
        await migrate_legacy_state(db_products, BASE_DIR / "data" / "state", snapshots_repo)

        purged = await checks_repo.purge_older_than(CHECKS_RETENTION_DAYS)
        if purged:
            log.ok("Historique : %d check(s) de plus de %d jours purgés.",
                   purged, CHECKS_RETENTION_DAYS)

        # --- Event bus et abonnés -----------------------------------------
        # L'ordre d'abonnement compte : base → captures → notifications.
        bus = EventBus()
        registry = create_registry(client)

        recorder = EventRecorder(checks_repo, timeline_repo, alerts_repo)
        recorder.attach_to(bus)

        screenshots = ScreenshotService(settings.screenshots, bus, registry)
        screenshots.attach_to(bus)
        await screenshots.start()

        notifications = NotificationManager(
            screenshots_dir=settings.screenshots.directory
        )
        if settings.telegram_configured:
            notifications.register(
                TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_ids, client)
            )
        else:
            log.ok(
                "Telegram non configuré (.env) — les changements seront "
                "uniquement loggés, aucune alerte ne sera envoyée."
            )
        notifications.attach_to(bus)

        # --- Moteur (produits relus depuis la base : modif à chaud) --------
        engine = MonitorEngine(
            registry, bus, snapshots_repo, defaults,
            product_provider=products_repo.list_all,
        )

        log.ok("Drop Monitor démarré — %d produit(s) en base.", len(db_products))
        try:
            await engine.run()
        except asyncio.CancelledError:
            pass
        finally:
            await screenshots.stop()
            await db.dispose()
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(run())
    except KeyboardInterrupt:
        print("\nArrêt demandé — à bientôt !")
        exit_code = 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
