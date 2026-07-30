"""Migrations légères versionnées.

Le schéma initial (version 1) est créé par `Base.metadata.create_all`
(idempotent). Les évolutions futures s'ajoutent dans MIGRATIONS sous
forme de listes d'instructions SQL, appliquées une seule fois chacune
et tracées dans la table schema_version.

Exemple d'évolution future :
    MIGRATIONS[2] = ["ALTER TABLE products ADD COLUMN image_url TEXT"]
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.db.schema import Base, SchemaVersionRow
from src.utils.logger import get_logger

log = get_logger("db.migrations")

#: version → instructions SQL à exécuter. La version 1 est le schéma initial.
MIGRATIONS: dict[int, list[str]] = {
    1: [],  # create_all
}


async def run_migrations(engine: AsyncEngine) -> None:
    """Crée le schéma si besoin puis applique les migrations manquantes."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        applied = set(
            (await conn.execute(select(SchemaVersionRow.version))).scalars().all()
        )
        for version in sorted(MIGRATIONS):
            if version in applied:
                continue
            for statement in MIGRATIONS[version]:
                await conn.execute(text(statement))
            await conn.execute(
                SchemaVersionRow.__table__.insert().values(version=version)
            )
            if MIGRATIONS[version]:
                log.ok("Migration %d appliquée (%d instruction(s)).",
                       version, len(MIGRATIONS[version]))
