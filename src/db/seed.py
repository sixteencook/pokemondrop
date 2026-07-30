"""Migration progressive depuis la version fichiers.

- Premier démarrage : si la table products est vide, les produits de
  config/products.yaml sont importés (le YAML devient un simple seed).
- L'ancien état data/state/*.json est repris comme snapshot initial pour
  chaque produit qui n'en a pas encore en base : ainsi, AUCUNE fausse
  alerte de « nouveau produit » après la migration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from src.models import ProductConfig
from src.utils.logger import get_logger
from src.utils.state_store import StateStore

if TYPE_CHECKING:  # import différé : évite le cycle db ↔ repositories
    from src.repositories import ProductRepository, SnapshotRepository

log = get_logger("db.seed")


async def import_products_from_yaml(
    repo: "ProductRepository", products: list[ProductConfig]
) -> int:
    """Importe les produits du YAML si la base est vide. Retourne le nombre importé."""
    if await repo.count() > 0:
        return 0
    imported = 0
    for product in products:
        await repo.create(product)
        imported += 1
    if imported:
        log.ok("Seed initial : %d produit(s) importé(s) depuis le YAML vers SQLite.",
               imported)
    return imported


async def migrate_legacy_state(
    products: list[ProductConfig],
    state_dir: Path,
    snapshots: "SnapshotRepository",
) -> int:
    """Reprend les snapshots JSON historiques pour les produits sans état en base."""
    if not state_dir.exists():
        return 0
    store = StateStore(state_dir)
    migrated = 0
    for product in products:
        if not product.uuid:
            continue
        if await snapshots.load(product.uuid) is not None:
            continue
        legacy = store.load(product.key)
        if legacy is not None:
            await snapshots.save(product.uuid, legacy)
            migrated += 1
    if migrated:
        log.ok("État migré : %d snapshot(s) JSON repris en base.", migrated)
    return migrated
