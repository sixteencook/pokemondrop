"""Repository des fiches découvertes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import DiscoveryRow, utcnow
from src.models import DiscoveryRecord, DiscoveryStatus
from src.repositories.pagination import apply_sort, fetch_page

_SORTABLE = {
    "first_seen_at": DiscoveryRow.first_seen_at,
    "last_seen_at": DiscoveryRow.last_seen_at,
    "title": DiscoveryRow.title,
    "site": DiscoveryRow.site,
    "status": DiscoveryRow.status,
}


def _to_domain(row: DiscoveryRow) -> DiscoveryRecord:
    return DiscoveryRecord(
        fingerprint=row.fingerprint,
        site=row.site,
        url=row.url,
        canonical_url=row.canonical_url,
        title=row.title,
        image_url=row.image_url,
        price=row.price,
        sku=row.sku,
        ean=row.ean,
        source=row.source,
        status=DiscoveryStatus(row.status),
        decision_reason=row.decision_reason,
        product_uuid=row.product_uuid,
        times_seen=row.times_seen,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
    )


class DiscoveryRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get(self, fingerprint: str) -> Optional[DiscoveryRecord]:
        async with self._sessions() as session:
            row = await session.get(DiscoveryRow, fingerprint)
            return _to_domain(row) if row else None

    async def record_sighting(
        self,
        fingerprint: str,
        site: str,
        url: str,
        canonical_url: str,
        title: str,
        image_url: Optional[str] = None,
        price: Optional[str] = None,
        sku: Optional[str] = None,
        ean: Optional[str] = None,
        source: str = "",
        status: DiscoveryStatus = DiscoveryStatus.PENDING,
        decision_reason: str = "",
    ) -> tuple[DiscoveryRecord, bool]:
        """Crée la fiche, ou rafraîchit celle déjà connue.

        Retourne (fiche, est_nouvelle). Une fiche déjà décidée (importée,
        ignorée, bloquée) conserve son statut : seuls les métadonnées et
        `last_seen_at` sont mis à jour.
        """
        async with self._sessions() as session:
            row = await session.get(DiscoveryRow, fingerprint)
            if row is None:
                row = DiscoveryRow(
                    fingerprint=fingerprint, site=site, url=url,
                    canonical_url=canonical_url, title=title, image_url=image_url,
                    price=price, sku=sku, ean=ean, source=source,
                    status=status.value, decision_reason=decision_reason,
                )
                session.add(row)
                await session.commit()
                return _to_domain(row), True

            row.title = title or row.title
            row.image_url = image_url or row.image_url
            row.price = price or row.price
            row.url = url
            row.canonical_url = canonical_url
            row.times_seen += 1
            row.last_seen_at = utcnow()
            if row.status == DiscoveryStatus.GONE.value:
                # Réapparue : elle redevient candidate.
                row.status = DiscoveryStatus.PENDING.value
                row.decision_reason = "réapparue sur le site"
            await session.commit()
            return _to_domain(row), False

    async def set_status(
        self,
        fingerprint: str,
        status: DiscoveryStatus,
        reason: str = "",
        product_uuid: Optional[str] = None,
    ) -> Optional[DiscoveryRecord]:
        async with self._sessions() as session:
            row = await session.get(DiscoveryRow, fingerprint)
            if row is None:
                return None
            row.status = status.value
            if reason:
                row.decision_reason = reason
            if product_uuid is not None:
                row.product_uuid = product_uuid
            await session.commit()
            return _to_domain(row)

    async def mark_missing(
        self, site: str, seen_fingerprints: set[str]
    ) -> int:
        """Marque « disparues » les fiches en attente absentes d'un scan complet.

        Les fiches déjà décidées (importées, ignorées, bloquées) ne sont
        jamais touchées : une décision de l'utilisateur ne s'efface pas.
        """
        async with self._sessions() as session:
            query = select(DiscoveryRow).where(
                DiscoveryRow.site == site,
                DiscoveryRow.status == DiscoveryStatus.PENDING.value,
            )
            rows = (await session.execute(query)).scalars().all()
            missing = [row for row in rows if row.fingerprint not in seen_fingerprints]
            for row in missing:
                row.status = DiscoveryStatus.GONE.value
                row.decision_reason = "absente du dernier balayage complet"
            if missing:
                await session.commit()
            return len(missing)

    async def list_page(
        self,
        page: int = 1,
        page_size: int = 25,
        sort: str = "first_seen_at",
        order: str = "desc",
        status: Optional[str] = None,
        site: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[DiscoveryRecord], int]:
        query = select(DiscoveryRow)
        if status:
            query = query.where(DiscoveryRow.status == status)
        if site:
            query = query.where(DiscoveryRow.site == site.lower())
        if search:
            query = query.where(DiscoveryRow.title.ilike(f"%{search}%"))
        query = apply_sort(query, _SORTABLE, sort, order, "first_seen_at")
        async with self._sessions() as session:
            rows, total = await fetch_page(session, query, page, page_size)
            return [_to_domain(row) for row in rows], total

    async def count_by_status(self) -> dict[str, int]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(DiscoveryRow.status, func.count(DiscoveryRow.fingerprint))
                .group_by(DiscoveryRow.status)
            )).all()
            return {status: int(count) for status, count in rows}

    async def blocked_fingerprints(self) -> set[str]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(DiscoveryRow.fingerprint)
                .where(DiscoveryRow.status == DiscoveryStatus.BLOCKED.value)
            )).scalars().all()
            return set(rows)

    async def detach_product(self, product_uuid: str) -> None:
        """Le produit surveillé a été supprimé : la fiche redevient ignorée."""
        async with self._sessions() as session:
            await session.execute(
                update(DiscoveryRow)
                .where(DiscoveryRow.product_uuid == product_uuid)
                .values(
                    product_uuid=None,
                    status=DiscoveryStatus.IGNORED.value,
                    decision_reason="produit surveillé supprimé",
                )
            )
            await session.commit()

    async def count_since(self, hours: int) -> int:
        """Fiches repérées pour la première fois sur la fenêtre."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self._sessions() as session:
            return int((await session.execute(
                select(func.count(DiscoveryRow.fingerprint))
                .where(DiscoveryRow.first_seen_at >= cutoff)
            )).scalar_one())

    async def per_day(self, days: int = 14) -> list[dict[str, object]]:
        """Découvertes par jour — graphique de la page Santé."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        bucket = func.strftime("%Y-%m-%d", DiscoveryRow.first_seen_at)
        async with self._sessions() as session:
            rows = (await session.execute(
                select(bucket, func.count(DiscoveryRow.fingerprint))
                .where(DiscoveryRow.first_seen_at >= cutoff)
                .group_by(bucket).order_by(bucket)
            )).all()
        return [{"day": day, "total": int(total)} for day, total in rows]

    async def last_discovery_at(self) -> Optional[datetime]:
        async with self._sessions() as session:
            return (await session.execute(
                select(func.max(DiscoveryRow.first_seen_at))
            )).scalar_one()
