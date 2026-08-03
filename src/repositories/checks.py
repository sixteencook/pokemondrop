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
        fetch_source: Optional[str] = None,
        http_status: Optional[int] = None,
        confidence: Optional[int] = None,
    ) -> None:
        """Écrit la ligne de vérification, diagnostics compris.

        Les trois derniers champs sont l'observabilité : ils voyagent dans
        l'écriture qui a lieu de toute façon, sans requête supplémentaire.
        """
        async with self._sessions() as session:
            session.add(CheckRow(
                product_uuid=product_uuid,
                status=status,
                availability=availability,
                response_time_ms=response_time_ms,
                error=error,
                fetch_source=fetch_source,
                http_status=http_status,
                confidence=confidence,
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

    async def health_by_site(self, hours: int = 24) -> dict[str, dict[str, Any]]:
        """Santé de CHAQUE plugin sur la fenêtre, en une seule requête.

        Tout est déduit des colonnes déjà écrites par le cycle : aucune
        instrumentation supplémentaire, aucun parcours de ligne en Python.
        Le regroupement par site se fait côté base.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        status = CheckRow.http_status
        async with self._sessions() as session:
            rows = (await session.execute(
                select(
                    ProductRow.site,
                    func.count(),
                    func.sum(case((CheckRow.status == "error", 1), else_=0)),
                    func.avg(case((CheckRow.status == "ok",
                                   CheckRow.response_time_ms))),
                    func.sum(case((CheckRow.availability == "unknown", 1), else_=0)),
                    func.sum(case((CheckRow.fetch_source == "browser", 1), else_=0)),
                    func.avg(CheckRow.confidence),
                    func.sum(case((status == 403, 1), else_=0)),
                    func.sum(case((status == 429, 1), else_=0)),
                    func.sum(case((status == 404, 1), else_=0)),
                    func.sum(case((status >= 500, 1), else_=0)),
                    func.sum(case((status.between(400, 499), 1), else_=0)),
                )
                .select_from(CheckRow)
                .join(ProductRow, CheckRow.product_uuid == ProductRow.uuid)
                .where(CheckRow.checked_at >= cutoff)
                .group_by(ProductRow.site)
            )).all()

        return {
            site: {
                "checks": int(total or 0),
                "errors": int(errors or 0),
                "avg_response_ms": float(avg_ms) if avg_ms is not None else None,
                "unknown_states": int(unknown or 0),
                "browser_checks": int(browser or 0),
                "avg_confidence": float(avg_conf) if avg_conf is not None else None,
                "http_403": int(forbidden or 0),
                "http_429": int(throttled or 0),
                "http_404": int(missing or 0),
                "http_5xx": int(server or 0),
                "http_4xx": int(client or 0),
            }
            for (site, total, errors, avg_ms, unknown, browser, avg_conf,
                 forbidden, throttled, missing, server, client) in rows
        }

    async def product_health(
        self, product_uuid: str, hours: int = 24
    ) -> dict[str, Any]:
        """Mêmes indicateurs, pour un seul produit (onglet Santé du produit)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self._sessions() as session:
            row = (await session.execute(
                select(
                    func.count(),
                    func.sum(case((CheckRow.status == "error", 1), else_=0)),
                    func.avg(case((CheckRow.status == "ok",
                                   CheckRow.response_time_ms))),
                    func.sum(case((CheckRow.availability == "unknown", 1), else_=0)),
                    func.sum(case((CheckRow.fetch_source == "browser", 1), else_=0)),
                    func.avg(CheckRow.confidence),
                )
                .where(
                    CheckRow.product_uuid == product_uuid,
                    CheckRow.checked_at >= cutoff,
                )
            )).one()
            total_all = (await session.execute(
                select(func.count()).select_from(CheckRow)
                .where(CheckRow.product_uuid == product_uuid)
            )).scalar_one()
            last_error = (await session.execute(
                select(CheckRow)
                .where(CheckRow.product_uuid == product_uuid,
                       CheckRow.status == "error")
                .order_by(CheckRow.checked_at.desc()).limit(1)
            )).scalar_one_or_none()

        total, errors, avg_ms, unknown, browser, avg_conf = row
        return {
            "checks_window": int(total or 0),
            "checks_total": int(total_all or 0),
            "errors": int(errors or 0),
            "avg_response_ms": float(avg_ms) if avg_ms is not None else None,
            "unknown_states": int(unknown or 0),
            "browser_checks": int(browser or 0),
            "avg_confidence": float(avg_conf) if avg_conf is not None else None,
            "last_error": last_error.error if last_error else None,
            "last_error_at": last_error.checked_at if last_error else None,
        }

    async def avg_by_fetch_source(
        self, hours: int = 24
    ) -> tuple[Optional[float], Optional[float]]:
        """Temps moyen (HTTP, navigateur) — les deux en une requête.

        Aucune mesure dédiée n'est nécessaire : le temps de réponse et la
        voie empruntée sont déjà écrits sur chaque ligne de `checks`.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self._sessions() as session:
            row = (await session.execute(
                select(
                    func.avg(case((CheckRow.fetch_source == "http",
                                   CheckRow.response_time_ms))),
                    func.avg(case((CheckRow.fetch_source == "browser",
                                   CheckRow.response_time_ms))),
                ).where(CheckRow.status == "ok", CheckRow.checked_at >= cutoff)
            )).one()
        http, browser = row
        return (
            float(http) if http is not None else None,
            float(browser) if browser is not None else None,
        )

    async def confidence_per_hour(
        self, hours: int = 48
    ) -> list[dict[str, Any]]:
        """Confiance moyenne par heure — la dérive se voit d'un coup d'œil."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        bucket = func.strftime("%Y-%m-%dT%H:00", CheckRow.checked_at)
        async with self._sessions() as session:
            rows = (await session.execute(
                select(bucket, func.avg(CheckRow.confidence))
                .where(
                    CheckRow.checked_at >= cutoff,
                    CheckRow.confidence.is_not(None),
                )
                .group_by(bucket).order_by(bucket)
            )).all()
        return [
            {"hour": hour, "avg_confidence": round(float(avg), 1)}
            for hour, avg in rows if avg is not None
        ]

    async def product_confidence_history(
        self, product_uuid: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Derniers scores de confiance d'un produit, du plus ancien au plus récent."""
        async with self._sessions() as session:
            rows = (await session.execute(
                select(CheckRow.checked_at, CheckRow.confidence)
                .where(
                    CheckRow.product_uuid == product_uuid,
                    CheckRow.confidence.is_not(None),
                )
                .order_by(CheckRow.checked_at.desc()).limit(limit)
            )).all()
        return [
            {"at": moment, "confidence": int(value)}
            for moment, value in reversed(rows)
        ]

    async def last_check_by_site(self) -> dict[str, datetime]:
        """Date de la dernière vérification de chaque site — une requête."""
        async with self._sessions() as session:
            rows = (await session.execute(
                select(ProductRow.site, func.max(CheckRow.checked_at))
                .select_from(CheckRow)
                .join(ProductRow, CheckRow.product_uuid == ProductRow.uuid)
                .group_by(ProductRow.site)
            )).all()
        return {site: last for site, last in rows if last is not None}

    async def avg_confidence_window(
        self, site: str, start_hours: int, end_hours: int = 0
    ) -> Optional[float]:
        """Confiance moyenne d'un site sur une fenêtre glissante.

        Sert à comparer les dernières 24 h à la semaine écoulée pour
        repérer une dégradation.
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=start_hours)
        end = now - timedelta(hours=end_hours)
        async with self._sessions() as session:
            value = (await session.execute(
                select(func.avg(CheckRow.confidence))
                .select_from(CheckRow)
                .join(ProductRow, CheckRow.product_uuid == ProductRow.uuid)
                .where(
                    ProductRow.site == site,
                    CheckRow.checked_at >= start,
                    CheckRow.checked_at < end,
                )
            )).scalar_one()
        return float(value) if value is not None else None

    async def site_window(
        self, site: str, start_hours: int, end_hours: int = 0
    ) -> dict[str, Any]:
        """Indicateurs bruts d'un site sur une fenêtre — base des anomalies."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=start_hours)
        end = now - timedelta(hours=end_hours)
        async with self._sessions() as session:
            row = (await session.execute(
                select(
                    func.count(),
                    func.sum(case((CheckRow.fetch_source == "browser", 1), else_=0)),
                    func.sum(case((CheckRow.availability == "unknown", 1), else_=0)),
                    func.sum(case((CheckRow.http_status == 403, 1), else_=0)),
                    func.avg(case((CheckRow.status == "ok",
                                   CheckRow.response_time_ms))),
                )
                .select_from(CheckRow)
                .join(ProductRow, CheckRow.product_uuid == ProductRow.uuid)
                .where(
                    ProductRow.site == site,
                    CheckRow.checked_at >= start,
                    CheckRow.checked_at < end,
                )
            )).one()

        total, browser, unknown, forbidden, avg_ms = row
        return {
            "checks": int(total or 0),
            "browser_checks": int(browser or 0),
            "unknown_states": int(unknown or 0),
            "http_403": int(forbidden or 0),
            "avg_response_ms": float(avg_ms) if avg_ms is not None else None,
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
