"""Utilitaires partagés des tests (base de données jetable, produits)."""

from __future__ import annotations

from pathlib import Path

from src.db import Database
from src.models import Priority, ProductConfig


async def make_db(tmp_path: Path) -> Database:
    """Base SQLite jetable, initialisée, dans le dossier temporaire du test."""
    db = Database(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    await db.init()
    return db


def make_product(**overrides) -> ProductConfig:
    defaults = dict(
        name="Pokémon 30 Ans ETB",
        site="micromania",
        url="https://example.com/produit",
        check_interval=60,
        enabled=True,
        group="pokemon-30-etb",
        priority=Priority.HIGH,
        tags=("pokemon", "etb"),
    )
    defaults.update(overrides)
    return ProductConfig(**defaults)
