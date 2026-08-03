"""Repository des événements techniques — l'historique de la page Santé.

Écriture rare par construction : un cycle nominal ne produit aucune ligne.
Lecture optimisée pour deux usages seulement :

  * un **flux** des N derniers événements (l'historique du dashboard) ;
  * des **comptages groupés** par source et par nature, sur une fenêtre.

Rien d'autre. Toute autre statistique se déduit déjà des tables existantes
(`checks`, `discoveries`, `alerts`, `search_attempts`…), qu'il est inutile
de dupliquer ici.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import EngineEventRow
from src.models import DEFAULT_SEVERITY, EVENT_LABELS, EventKind, EventScope, Severity


@dataclass(frozen=True)
class EngineEventRecord:
    """Un événement technique, prêt à être affiché."""

    id: int
    scope: str
    source: str
    kind: str
    severity: str
    product_uuid: Optional[str]
    detail: str
    duration_ms: Optional[int]
    created_at: datetime

    @property
    def label(self) -> str:
        try:
            return EVENT_LABELS[EventKind(self.kind)]
        except ValueError:
            return self.kind


def _to_domain(row: EngineEventRow) -> EngineEventRecord:
    return EngineEventRecord(
        id=row.id,
        scope=row.scope,
        source=row.source,
        kind=row.kind,
        severity=row.severity,
        product_uuid=row.product_uuid,
        detail=row.detail,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
    )


class EngineEventRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(
        self,
        scope: EventScope,
        source: str,
        kind: EventKind,
        detail: str = "",
        product_uuid: Optional[str] = None,
        severity: Optional[Severity] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        resolved = severity or DEFAULT_SEVERITY.get(kind, Severity.INFO)
        async with self._sessions() as session:
            session.add(EngineEventRow(
                scope=scope.value,
                source=source,
                kind=kind.value,
                severity=resolved.value,
                product_uuid=product_uuid,
                detail=detail[:500],
                duration_ms=duration_ms,
            ))
            await session.commit()

    async def recent(
        self,
        limit: int = 100,
        source: Optional[str] = None,
        product_uuid: Optional[str] = None,
    ) -> list[EngineEventRecord]:
        """Les N derniers événements, du plus récent au plus ancien."""
        query = select(EngineEventRow).order_by(EngineEventRow.id.desc()).limit(limit)
        if source:
            query = query.where(EngineEventRow.source == source)
        if product_uuid:
            query = query.where(EngineEventRow.product_uuid == product_uuid)
        async with self._sessions() as session:
            rows = (await session.execute(query)).scalars().all()
            return [_to_domain(row) for row in rows]

    async def counts_by_source(self, hours: int = 24) -> dict[str, dict[str, int]]:
        """{source: {nature: nombre}} sur la fenêtre — UNE seule requête."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self._sessions() as session:
            rows = (await session.execute(
                select(
                    EngineEventRow.source, EngineEventRow.kind, func.count()
                )
                .where(EngineEventRow.created_at >= cutoff)
                .group_by(EngineEventRow.source, EngineEventRow.kind)
            )).all()

        counts: dict[str, dict[str, int]] = {}
        for source, kind, total in rows:
            counts.setdefault(source, {})[kind] = int(total)
        return counts

    async def per_hour(
        self, kinds: tuple[EventKind, ...], hours: int = 24
    ) -> list[dict[str, object]]:
        """Volume horaire par nature — alimente les graphiques de la page Santé."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        bucket = func.strftime("%Y-%m-%dT%H:00", EngineEventRow.created_at)
        values = [kind.value for kind in kinds]
        async with self._sessions() as session:
            rows = (await session.execute(
                select(bucket, EngineEventRow.kind, func.count())
                .where(
                    EngineEventRow.created_at >= cutoff,
                    EngineEventRow.kind.in_(values),
                )
                .group_by(bucket, EngineEventRow.kind)
                .order_by(bucket)
            )).all()

        buckets: dict[str, dict[str, object]] = {}
        for hour, kind, total in rows:
            entry = buckets.setdefault(hour, {"hour": hour})
            entry[kind] = int(total)
        for entry in buckets.values():
            for value in values:
                entry.setdefault(value, 0)
        return list(buckets.values())

    async def average_durations(self, hours: int = 24) -> dict[str, float]:
        """Temps moyen de chaque phase mesurée — une seule requête.

        Les phases sans durée (incidents) sont naturellement écartées par
        la moyenne SQL, qui ignore les NULL.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self._sessions() as session:
            rows = (await session.execute(
                select(EngineEventRow.kind, func.avg(EngineEventRow.duration_ms))
                .where(
                    EngineEventRow.created_at >= cutoff,
                    EngineEventRow.duration_ms.is_not(None),
                )
                .group_by(EngineEventRow.kind)
            )).all()
        return {kind: float(avg) for kind, avg in rows if avg is not None}

    async def since(
        self, hours: int, kinds: Optional[tuple[EventKind, ...]] = None
    ) -> list[EngineEventRecord]:
        """Événements de la fenêtre, du plus ancien au plus récent.

        Sert à reconstituer les chaînes d'incidents : l'ordre chronologique
        est ce qui permet de relier « 403 » puis « bascule navigateur »
        puis « succès » sur le même produit.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = (
            select(EngineEventRow)
            .where(EngineEventRow.created_at >= cutoff)
            .order_by(EngineEventRow.id)
        )
        if kinds:
            query = query.where(
                EngineEventRow.kind.in_([kind.value for kind in kinds])
            )
        async with self._sessions() as session:
            rows = (await session.execute(query)).scalars().all()
            return [_to_domain(row) for row in rows]

    async def for_products(
        self, product_uuids: list[str], limit: int = 200
    ) -> list[EngineEventRecord]:
        """Événements de plusieurs produits — alimente la timeline canonique."""
        if not product_uuids:
            return []
        async with self._sessions() as session:
            rows = (await session.execute(
                select(EngineEventRow)
                .where(EngineEventRow.product_uuid.in_(product_uuids))
                .order_by(EngineEventRow.id.desc())
                .limit(limit)
            )).scalars().all()
            return [_to_domain(row) for row in rows]

    async def last_error(self, source: str) -> Optional[EngineEventRecord]:
        """Dernier événement de gravité « erreur » pour un plugin."""
        async with self._sessions() as session:
            row = (await session.execute(
                select(EngineEventRow)
                .where(
                    EngineEventRow.source == source,
                    EngineEventRow.severity == Severity.ERROR.value,
                )
                .order_by(EngineEventRow.id.desc())
                .limit(1)
            )).scalar_one_or_none()
            return _to_domain(row) if row else None

    async def purge_older_than(self, days: int) -> int:
        """L'historique technique n'a d'intérêt que récent."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with self._sessions() as session:
            result = await session.execute(
                delete(EngineEventRow).where(EngineEventRow.created_at < cutoff)
            )
            await session.commit()
            return result.rowcount
