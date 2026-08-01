"""Repository des alertes envoyées (page Alertes + captures Playwright)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import AlertRow, ProductRow
from src.models import AlertRecord
from src.repositories.pagination import apply_sort, fetch_page

_SORTABLE = {
    "created_at": AlertRow.created_at,
    "change_type": AlertRow.change_type,
}


def _to_domain(row: AlertRow) -> AlertRecord:
    return AlertRecord(
        id=row.id,
        product_uuid=row.product_uuid,
        change_type=row.change_type,
        old_value=row.old_value,
        new_value=row.new_value,
        price=row.price,
        url=row.url,
        screenshot_path=row.screenshot_path,
        evidence_path=row.evidence_path,
        notified=row.notified,
        created_at=row.created_at,
    )


class AlertRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(
        self,
        product_uuid: str,
        change_type: str,
        old_value: Optional[str],
        new_value: Optional[str],
        price: Optional[str],
        url: str,
        evidence_path: Optional[str] = None,
    ) -> int:
        """Enregistre l'alerte (notified=False) et retourne son id."""
        async with self._sessions() as session:
            row = AlertRow(
                product_uuid=product_uuid,
                change_type=change_type,
                old_value=old_value,
                new_value=new_value,
                price=price,
                url=url,
                evidence_path=evidence_path,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def get(self, alert_id: int) -> Optional[AlertRecord]:
        async with self._sessions() as session:
            row = await session.get(AlertRow, alert_id)
            return _to_domain(row) if row else None

    async def mark_notified(self, alert_id: int) -> None:
        async with self._sessions() as session:
            await session.execute(
                update(AlertRow).where(AlertRow.id == alert_id).values(notified=True)
            )
            await session.commit()

    async def set_screenshot(self, alert_id: int, path: str) -> None:
        """Associe une capture Playwright à l'alerte (phase captures)."""
        async with self._sessions() as session:
            await session.execute(
                update(AlertRow).where(AlertRow.id == alert_id).values(screenshot_path=path)
            )
            await session.commit()

    async def list(
        self,
        product_uuid: Optional[str] = None,
        change_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[AlertRecord]:
        async with self._sessions() as session:
            query = select(AlertRow).order_by(AlertRow.created_at.desc()).limit(limit)
            if product_uuid:
                query = query.where(AlertRow.product_uuid == product_uuid)
            if change_type:
                query = query.where(AlertRow.change_type == change_type)
            rows = (await session.execute(query)).scalars().all()
            return [_to_domain(row) for row in rows]

    async def list_page(
        self,
        page: int = 1,
        page_size: int = 25,
        sort: str = "created_at",
        order: str = "desc",
        product_uuid: Optional[str] = None,
        change_type: Optional[str] = None,
        site: Optional[str] = None,
        notified: Optional[bool] = None,
    ) -> tuple[list[tuple[AlertRecord, Optional[str], Optional[str]]], int]:
        """Page d'alertes enrichies du (nom, site) du produit.

        La jointure est externe : une alerte d'un produit supprimé reste
        consultable (nom/site à None).
        """
        query = (
            select(AlertRow, ProductRow.name, ProductRow.site)
            .outerjoin(ProductRow, AlertRow.product_uuid == ProductRow.uuid)
        )
        if product_uuid:
            query = query.where(AlertRow.product_uuid == product_uuid)
        if change_type:
            query = query.where(AlertRow.change_type == change_type)
        if site:
            query = query.where(ProductRow.site == site.lower())
        if notified is not None:
            query = query.where(AlertRow.notified == notified)
        query = apply_sort(query, _SORTABLE, sort, order, "created_at")
        async with self._sessions() as session:
            rows, total = await fetch_page(session, query, page, page_size, scalars=False)
            return (
                [(_to_domain(row), name, prod_site) for row, name, prod_site in rows],
                total,
            )

    async def count(self) -> int:
        async with self._sessions() as session:
            return int((await session.execute(
                select(func.count(AlertRow.id))
            )).scalar_one())

    async def last_created_at(self) -> Optional[datetime]:
        async with self._sessions() as session:
            return (await session.execute(
                select(func.max(AlertRow.created_at))
            )).scalar_one()

    async def per_day(self, days: int = 14) -> list[dict[str, object]]:
        """Alertes par jour (graphique du dashboard)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        bucket = func.strftime("%Y-%m-%d", AlertRow.created_at)
        async with self._sessions() as session:
            rows = (await session.execute(
                select(bucket, func.count())
                .where(AlertRow.created_at >= cutoff)
                .group_by(bucket)
                .order_by(bucket)
            )).all()
            return [{"day": day, "total": int(total)} for day, total in rows]

    async def purge_product(self, product_uuid: str) -> None:
        async with self._sessions() as session:
            await session.execute(
                delete(AlertRow).where(AlertRow.product_uuid == product_uuid)
            )
            await session.commit()
