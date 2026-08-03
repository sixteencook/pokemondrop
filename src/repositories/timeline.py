"""Repository de la timeline — l'historique COMPLET de chaque produit."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import TimelineRow
from src.models import TimelineEntry
from src.repositories.pagination import apply_sort, fetch_page

_SORTABLE = {
    "created_at": TimelineRow.created_at,
    "event_type": TimelineRow.event_type,
}


def _to_domain(row: TimelineRow) -> TimelineEntry:
    return TimelineEntry(
        id=row.id,
        product_uuid=row.product_uuid,
        event_type=row.event_type,
        label=row.label,
        old_value=row.old_value,
        new_value=row.new_value,
        price=row.price,
        created_at=row.created_at,
    )


class TimelineRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(
        self,
        product_uuid: str,
        event_type: str,
        label: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        price: Optional[str] = None,
    ) -> None:
        async with self._sessions() as session:
            session.add(TimelineRow(
                product_uuid=product_uuid,
                event_type=event_type,
                label=label,
                old_value=old_value,
                new_value=new_value,
                price=price,
            ))
            await session.commit()

    async def for_product(
        self, product_uuid: str, limit: int = 200
    ) -> list[TimelineEntry]:
        """Timeline d'un produit, du plus récent au plus ancien."""
        async with self._sessions() as session:
            rows = (await session.execute(
                select(TimelineRow)
                .where(TimelineRow.product_uuid == product_uuid)
                .order_by(TimelineRow.created_at.desc(), TimelineRow.id.desc())
                .limit(limit)
            )).scalars().all()
            return [_to_domain(row) for row in rows]

    async def for_products(
        self, product_uuids: list[str], limit: int = 400
    ) -> list[TimelineEntry]:
        """Timeline de PLUSIEURS produits surveillés, en une requête.

        C'est la base de la timeline d'un produit canonique : un même
        produit peut être suivi chez plusieurs marchands, chacun avec sa
        propre entrée dans `products`.
        """
        if not product_uuids:
            return []
        async with self._sessions() as session:
            rows = (await session.execute(
                select(TimelineRow)
                .where(TimelineRow.product_uuid.in_(product_uuids))
                .order_by(TimelineRow.created_at.desc(), TimelineRow.id.desc())
                .limit(limit)
            )).scalars().all()
            return [_to_domain(row) for row in rows]

    async def count_by_type(self, product_uuids: list[str]) -> dict[str, int]:
        """Nombre d'événements par nature — base des métriques métier."""
        if not product_uuids:
            return {}
        async with self._sessions() as session:
            rows = (await session.execute(
                select(TimelineRow.event_type, func.count(TimelineRow.id))
                .where(TimelineRow.product_uuid.in_(product_uuids))
                .group_by(TimelineRow.event_type)
            )).all()
        return {event_type: int(total) for event_type, total in rows}

    async def list_page(
        self,
        page: int = 1,
        page_size: int = 25,
        sort: str = "created_at",
        order: str = "desc",
        product_uuid: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> tuple[list[TimelineEntry], int]:
        query = select(TimelineRow)
        if product_uuid:
            query = query.where(TimelineRow.product_uuid == product_uuid)
        if event_type:
            query = query.where(TimelineRow.event_type == event_type)
        query = apply_sort(query, _SORTABLE, sort, order, "created_at")
        async with self._sessions() as session:
            rows, total = await fetch_page(session, query, page, page_size)
            return [_to_domain(row) for row in rows], total

    async def purge_product(self, product_uuid: str) -> None:
        async with self._sessions() as session:
            await session.execute(
                delete(TimelineRow).where(TimelineRow.product_uuid == product_uuid)
            )
            await session.commit()

    async def recent(self, limit: int = 100) -> list[TimelineEntry]:
        """Derniers événements tous produits confondus (flux du dashboard)."""
        async with self._sessions() as session:
            rows = (await session.execute(
                select(TimelineRow)
                .order_by(TimelineRow.created_at.desc(), TimelineRow.id.desc())
                .limit(limit)
            )).scalars().all()
            return [_to_domain(row) for row in rows]
