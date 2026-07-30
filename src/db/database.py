"""Connexion à la base de données.

SQLite aujourd'hui (sqlite+aiosqlite:///data/drop_monitor.db), PostgreSQL
demain (postgresql+asyncpg://…) : seul DATABASE_URL change, le reste de
l'application passe par les repositories.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.db.migrations import run_migrations


class Database:
    """Cycle de vie de la connexion + fabrique de sessions."""

    def __init__(self, url: str) -> None:
        if url.startswith("sqlite"):
            # S'assure que le dossier du fichier SQLite existe.
            db_path = url.split("///", 1)[-1]
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine = create_async_engine(url, echo=False)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    async def init(self) -> None:
        """Crée le schéma et applique les migrations manquantes."""
        await run_migrations(self._engine)

    async def dispose(self) -> None:
        await self._engine.dispose()
