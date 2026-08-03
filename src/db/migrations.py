"""Migrations légères versionnées.

Le schéma initial (version 1) est créé par `Base.metadata.create_all`
(idempotent). Les évolutions futures s'ajoutent dans MIGRATIONS sous
forme de listes d'instructions SQL, appliquées une seule fois chacune
et tracées dans la table schema_version.

Exemple d'évolution future :
    MIGRATIONS[2] = ["ALTER TABLE products ADD COLUMN image_url TEXT"]
"""

from __future__ import annotations

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.db.schema import Base, SchemaVersionRow
from src.utils.logger import get_logger

log = get_logger("db.migrations")

#: version → instructions SQL à exécuter. La version 1 est le schéma initial.
MIGRATIONS: dict[int, list[str]] = {
    1: [],  # create_all
    # v2 — Product Intelligence v2 : identité multi-clés.
    # `create_all` crée les tables manquantes mais n'ajoute JAMAIS de
    # colonne à une table existante : ces ALTER sont donc indispensables
    # pour les bases déjà en service.
    2: [
        "ALTER TABLE catalog_products ADD COLUMN asin VARCHAR(20)",
        "ALTER TABLE catalog_products ADD COLUMN model_number VARCHAR(100)",
        "ALTER TABLE catalog_products ADD COLUMN manufacturer VARCHAR(120)",
        "ALTER TABLE catalog_products ADD COLUMN identity TEXT DEFAULT '{}'",
        "CREATE INDEX IF NOT EXISTS ix_catalog_products_asin "
        "ON catalog_products (asin)",
        "CREATE INDEX IF NOT EXISTS ix_catalog_products_model_number "
        "ON catalog_products (model_number)",
    ],
    # v3 — archivage du HTML ayant motivé une alerte.
    3: [
        "ALTER TABLE alerts ADD COLUMN evidence_path TEXT",
    ],
    # v4 — observabilité. Les trois colonnes de `checks` sont remplies par
    # une écriture qui a lieu de toute façon : coût nul pour le cycle.
    # `engine_events` est créée par create_all sur les bases neuves ; le
    # CREATE TABLE ci-dessous couvre les bases déjà en service.
    4: [
        "ALTER TABLE checks ADD COLUMN fetch_source VARCHAR(10)",
        "ALTER TABLE checks ADD COLUMN http_status INTEGER",
        "ALTER TABLE checks ADD COLUMN confidence INTEGER",
        """
        CREATE TABLE IF NOT EXISTS engine_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope VARCHAR(20) NOT NULL,
            source VARCHAR(50) NOT NULL,
            kind VARCHAR(30) NOT NULL,
            severity VARCHAR(10) DEFAULT 'info',
            product_uuid VARCHAR(32),
            detail TEXT DEFAULT '',
            created_at DATETIME NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_engine_events_created_at "
        "ON engine_events (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_engine_events_source "
        "ON engine_events (source)",
        "CREATE INDEX IF NOT EXISTS ix_engine_events_kind "
        "ON engine_events (kind)",
    ],
    # v5 — durée des phases du moteur (captures, Discovery, Intelligence).
    # Le temps HTTP et le temps navigateur, eux, se déduisent déjà de
    # `checks.response_time_ms` croisé avec `checks.fetch_source`.
    5: [
        "ALTER TABLE engine_events ADD COLUMN duration_ms INTEGER",
    ],
}


async def run_migrations(engine: AsyncEngine) -> None:
    """Crée le schéma si besoin puis applique les migrations manquantes.

    Sur une base NEUVE, `create_all` produit directement le schéma final :
    les migrations sont alors simplement marquées comme appliquées, sans
    être rejouées (leurs ALTER échoueraient sur des colonnes existantes).
    """
    async with engine.begin() as conn:
        tables_before = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        is_fresh = "schema_version" not in tables_before

        await conn.run_sync(Base.metadata.create_all)

        if is_fresh:
            for version in sorted(MIGRATIONS):
                await conn.execute(
                    SchemaVersionRow.__table__.insert().values(version=version)
                )
            return

        applied = set(
            (await conn.execute(select(SchemaVersionRow.version))).scalars().all()
        )
        for version in sorted(MIGRATIONS):
            if version in applied:
                continue
            executed = 0
            for statement in MIGRATIONS[version]:
                try:
                    await conn.execute(text(statement))
                    executed += 1
                except OperationalError as exc:
                    # Colonne ou index déjà présent : la migration a déjà été
                    # appliquée partiellement, on poursuit sans bloquer.
                    log.check("Migration %d — instruction ignorée (%s).",
                              version, str(exc.orig)[:120])
            await conn.execute(
                SchemaVersionRow.__table__.insert().values(version=version)
            )
            if MIGRATIONS[version]:
                log.ok("Migration %d appliquée (%d/%d instruction(s)).",
                       version, executed, len(MIGRATIONS[version]))
