"""Repository des snapshots — dernier état connu de chaque produit.

Remplace les fichiers data/state/*.json (migrés automatiquement au
premier démarrage, voir src/db/seed.py).
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import ProductRow, SnapshotRow, utcnow
from src.models import ProductSnapshot


class SnapshotRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def load(self, product_uuid: str) -> Optional[ProductSnapshot]:
        async with self._sessions() as session:
            row = await session.get(SnapshotRow, product_uuid)
            if row is None:
                return None
            try:
                return ProductSnapshot.from_dict(json.loads(row.payload))
            except (json.JSONDecodeError, ValueError, KeyError):
                return None  # payload corrompu → nouvelle baseline

    async def save(self, product_uuid: str, snapshot: ProductSnapshot) -> None:
        payload = json.dumps(snapshot.to_dict(), ensure_ascii=False)
        async with self._sessions() as session:
            row = await session.get(SnapshotRow, product_uuid)
            if row is None:
                session.add(SnapshotRow(product_uuid=product_uuid, payload=payload))
            else:
                row.payload = payload
                row.updated_at = utcnow()
            await session.commit()

    async def load_many(self, product_uuids: list[str]) -> dict[str, ProductSnapshot]:
        """Snapshots de plusieurs produits en une requête (listes de l'API)."""
        if not product_uuids:
            return {}
        result: dict[str, ProductSnapshot] = {}
        async with self._sessions() as session:
            rows = (await session.execute(
                select(SnapshotRow).where(SnapshotRow.product_uuid.in_(product_uuids))
            )).scalars().all()
            for row in rows:
                try:
                    result[row.product_uuid] = ProductSnapshot.from_dict(
                        json.loads(row.payload)
                    )
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue
        return result

    async def availability_by_site(self) -> list[dict[str, object]]:
        """Répartition des disponibilités courantes par site (graphique)."""
        availability = func.json_extract(SnapshotRow.payload, "$.availability")
        async with self._sessions() as session:
            rows = (await session.execute(
                select(ProductRow.site, availability, func.count())
                .join(ProductRow, ProductRow.uuid == SnapshotRow.product_uuid)
                .group_by(ProductRow.site, availability)
            )).all()
            return [
                {"site": site, "availability": avail or "unknown", "count": int(count)}
                for site, avail, count in rows
            ]

    async def delete(self, product_uuid: str) -> None:
        async with self._sessions() as session:
            row = await session.get(SnapshotRow, product_uuid)
            if row is not None:
                await session.delete(row)
                await session.commit()
