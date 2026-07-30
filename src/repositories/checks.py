"""Repository des vérifications (stats, graphiques, temps de réponse)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import CheckRow, ProductRow
from src.models import CheckRecord
from src.repositories.pagination import apply_sort, fetch_page

_SORTABLE = {
    "checked_at": CheckRow.checked_at,
    "response_time_ms": CheckRow.response_time_ms,
    "status": CheckRow.status,
}


def _to_domain(row: CheckRow) -> CheckRecord:
    return CheckRecord(
        id=row.id,
        product_uuid=row.product_uuid,
        status=row.status,
        availability=row.availability,
        response_time_ms=row.response_time_ms,
        error=row.error,
        checked_at=row.checked_at,
    )


class CheckRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(
        self,
        product_uuid: str,
        status: str,
        availability: Optional[str] = None,
        response_time_ms: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        async with self._sessions() as session:
            session.add(CheckRow(
                product_uuid=product_uuid,
                status=status,
                availability=availability,
                response_time_ms=response_time_ms,
                error=error,
            ))
            await session.commit()

    async def recent(
        self, product_uuid: Optional[str] = None, limit: int = 100
    ) -> list[CheckRecord]:
        async with self._sessions() as session:
            query = select(CheckRow).order_by(CheckRow.checked_at.desc()).limit(limit)
            if product_uuid:
                query = query.where(CheckRow.product_uuid == product_uuid)
            rows = (await session.execute(query)).scalars().all()
            return [_to_domain(row) for row in rows]

    async def list_page(
        self,
        page: int = 1,
        page_size: int = 25,
        sort: str = "checked_at",
        order: str = "desc",
        product_uuid: Optional[str] = None,
        status: Optional[str] = None,
    ) -> tuple[list[CheckRecord], int]:
        query = select(CheckRow)
        if product_uuid:
            query = query.where(CheckRow.product_uuid == product_uuid)
        if status:
            query = query.where(CheckRow.status == status)
        query = apply_sort(query, _SORTABLE, sort, order, "checked_at")
        async with self._sessions() as session:
            rows, total = await fetch_page(session, query, page, page_size)
            return [_to_domain(row) for row in rows], total

    async def count(self) -> int:
        async with self._sessions() as session:
            return int((await session.execute(
                select(func.count(CheckRow.id))
            )).scalar_one())

    async def last(self) -> Optional[CheckRecord]:
        async with self._sessions() as session:
            row = (await session.execute(
                select(CheckRow).order_by(CheckRow.checked_at.desc()).limit(1)
            )).scalar_one_or_none()
            return _to_domain(row) if row else None

    async def avg_response_time(self, hours: int = 24) -> Optional[float]:
        """Temps de réponse moyen (ms) des checks réussis sur la période."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self._sessions() as session:
            value = (await session.execute(
                select(func.avg(CheckRow.response_time_ms))
                .where(CheckRow.status == "ok", CheckRow.checked_at >= cutoff)
            )).scalar_one()
            return float(value) if value is not None else None

    async def per_hour(self, hours: int = 24) -> list[dict[str, Any]]:
        """Checks agrégés par heure : volume, erreurs, temps de réponse moyen.

        Alimente les graphiques « checks/heure » et « évolution du temps
        de réponse » du dashboard.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        bucket = func.strftime("%Y-%m-%dT%H:00", CheckRow.checked_at)
        async with self._sessions() as session:
            rows = (await session.execute(
                select(
                    bucket,
                    func.count(),
                    func.sum(case((CheckRow.status == "error", 1), else_=0)),
                    func.avg(case((CheckRow.status == "ok", CheckRow.response_time_ms))),
                )
                .where(CheckRow.checked_at >= cutoff)
                .group_by(bucket)
                .order_by(bucket)
            )).all()
            return [
                {
                    "hour": hour,
                    "total": int(total),
                    "errors": int(errors or 0),
                    "avg_response_ms": float(avg) if avg is not None else None,
                }
                for hour, total, errors, avg in rows
            ]

    async def site_stats(self, site: str) -> dict[str, Any]:
        """Agrégats d'un site pour la page Monitors : dernier check, dernière
        erreur, temps de réponse moyen (24 h), nombre de checks."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        joined = select(CheckRow).join(
            ProductRow, CheckRow.product_uuid == ProductRow.uuid
        ).where(ProductRow.site == site)
        async with self._sessions() as session:
            last_check = (await session.execute(
                joined.order_by(CheckRow.checked_at.desc()).limit(1)
            )).scalar_one_or_none()
            last_error = (await session.execute(
                joined.where(CheckRow.status == "error")
                .order_by(CheckRow.checked_at.desc()).limit(1)
            )).scalar_one_or_none()
            avg_ms = (await session.execute(
                select(func.avg(CheckRow.response_time_ms))
                .select_from(CheckRow)
                .join(ProductRow, CheckRow.product_uuid == ProductRow.uuid)
                .where(ProductRow.site == site, CheckRow.status == "ok",
                       CheckRow.checked_at >= cutoff)
            )).scalar_one()
            total = (await session.execute(
                select(func.count())
                .select_from(CheckRow)
                .join(ProductRow, CheckRow.product_uuid == ProductRow.uuid)
                .where(ProductRow.site == site)
            )).scalar_one()
        return {
            "last_check_at": last_check.checked_at if last_check else None,
            "last_error": last_error.error if last_error else None,
            "last_error_at": last_error.checked_at if last_error else None,
            "avg_response_ms": float(avg_ms) if avg_ms is not None else None,
            "total_checks": int(total),
        }

    async def purge_product(self, product_uuid: str) -> None:
        async with self._sessions() as session:
            await session.execute(
                delete(CheckRow).where(CheckRow.product_uuid == product_uuid)
            )
            await session.commit()

    async def purge_older_than(self, days: int) -> int:
        """Supprime les checks trop anciens (la table grossit vite à 30 s
        d'intervalle). Retourne le nombre de lignes supprimées."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with self._sessions() as session:
            result = await session.execute(
                delete(CheckRow).where(CheckRow.checked_at < cutoff)
            )
            await session.commit()
            return result.rowcount
