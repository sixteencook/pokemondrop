"""Repository des tentatives de recherche inter-sites.

Sert deux usages en une seule table :

  HISTORIQUE  ce qui a été cherché, où, avec quelle clé, et le résultat —
              c'est ce qui permet d'expliquer un rapprochement.

  RELANCE     une recherche infructueuse garde un `next_retry_at`. Le
              moteur la retentera, avec un intervalle qui s'allonge
              progressivement, jusqu'à ce que la fiche apparaisse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import SearchAttemptRow, utcnow

#: États d'une tentative.
STATUS_FOUND = "found"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"
STATUS_UNSUPPORTED = "unsupported"   # le plugin ne sait pas exploiter cette clé


@dataclass(frozen=True)
class SearchAttempt:
    id: int
    product_uuid: str
    site: str
    key_kind: str
    key_value: str
    status: str
    attempts: int
    confidence: int
    matched_fields: tuple[str, ...]
    reason: str
    found_url: Optional[str]
    offer_uuid: Optional[str]
    first_attempt_at: datetime
    last_attempt_at: datetime
    next_retry_at: Optional[datetime]

    @property
    def succeeded(self) -> bool:
        return self.status == STATUS_FOUND


def _to_domain(row: SearchAttemptRow) -> SearchAttempt:
    return SearchAttempt(
        id=row.id, product_uuid=row.product_uuid, site=row.site,
        key_kind=row.key_kind, key_value=row.key_value, status=row.status,
        attempts=row.attempts, confidence=row.confidence,
        matched_fields=tuple(json.loads(row.matched_fields or "[]")),
        reason=row.reason, found_url=row.found_url, offer_uuid=row.offer_uuid,
        first_attempt_at=row.first_attempt_at, last_attempt_at=row.last_attempt_at,
        next_retry_at=row.next_retry_at,
    )


class SearchAttemptRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record(
        self,
        product_uuid: str,
        site: str,
        key_kind: str,
        key_value: str,
        status: str,
        confidence: int = 0,
        matched_fields: tuple[str, ...] = (),
        reason: str = "",
        found_url: Optional[str] = None,
        offer_uuid: Optional[str] = None,
        next_retry_at: Optional[datetime] = None,
    ) -> SearchAttempt:
        """Journalise une tentative, ou met à jour celle déjà connue."""
        async with self._sessions() as session:
            row = (await session.execute(
                select(SearchAttemptRow).where(
                    SearchAttemptRow.product_uuid == product_uuid,
                    SearchAttemptRow.site == site,
                    SearchAttemptRow.key_kind == key_kind,
                    SearchAttemptRow.key_value == key_value,
                )
            )).scalar_one_or_none()

            if row is None:
                # Les valeurs par défaut des colonnes ne sont appliquées qu'au
                # flush : on initialise explicitement ce que l'on incrémente
                # juste après.
                row = SearchAttemptRow(
                    product_uuid=product_uuid, site=site, key_kind=key_kind,
                    key_value=key_value, attempts=0, confidence=0,
                    matched_fields="[]", reason="", status="pending",
                )
                session.add(row)

            row.status = status
            row.attempts += 1
            row.confidence = confidence
            row.matched_fields = json.dumps(list(matched_fields), ensure_ascii=False)
            row.reason = reason
            row.found_url = found_url or row.found_url
            row.offer_uuid = offer_uuid or row.offer_uuid
            row.last_attempt_at = utcnow()
            # Une recherche aboutie n'est plus relancée.
            row.next_retry_at = None if status == STATUS_FOUND else next_retry_at

            await session.commit()
            return _to_domain(row)

    async def due_for_retry(self, limit: int = 50) -> list[SearchAttempt]:
        """Recherches infructueuses dont l'heure de relance est venue."""
        now = datetime.now(timezone.utc)
        async with self._sessions() as session:
            rows = (await session.execute(
                select(SearchAttemptRow)
                .where(
                    SearchAttemptRow.status != STATUS_FOUND,
                    SearchAttemptRow.next_retry_at.is_not(None),
                    SearchAttemptRow.next_retry_at <= now,
                )
                .order_by(SearchAttemptRow.next_retry_at)
                .limit(limit)
            )).scalars().all()
            return [_to_domain(row) for row in rows]

    async def for_product(self, product_uuid: str) -> list[SearchAttempt]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(SearchAttemptRow)
                .where(SearchAttemptRow.product_uuid == product_uuid)
                .order_by(SearchAttemptRow.site, SearchAttemptRow.key_kind)
            )).scalars().all()
            return [_to_domain(row) for row in rows]

    async def already_found(self, product_uuid: str, site: str) -> bool:
        """Le produit a-t-il déjà été trouvé chez ce marchand ?"""
        async with self._sessions() as session:
            found = (await session.execute(
                select(SearchAttemptRow.id).where(
                    SearchAttemptRow.product_uuid == product_uuid,
                    SearchAttemptRow.site == site,
                    SearchAttemptRow.status == STATUS_FOUND,
                ).limit(1)
            )).scalar_one_or_none()
            return found is not None

    async def counts_by_status(self) -> dict[str, int]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(SearchAttemptRow.status, func.count(SearchAttemptRow.id))
                .group_by(SearchAttemptRow.status)
            )).all()
            return {status: int(count) for status, count in rows}

    async def pending_retries(self) -> int:
        async with self._sessions() as session:
            return int((await session.execute(
                select(func.count(SearchAttemptRow.id)).where(
                    SearchAttemptRow.status != STATUS_FOUND,
                    SearchAttemptRow.next_retry_at.is_not(None),
                )
            )).scalar_one())


def next_retry(
    attempts: int, base_seconds: int, multiplier: float, cap_seconds: int
) -> datetime:
    """Prochaine relance : intervalle croissant, plafonné.

    Les premières minutes d'un drop sont les plus décisives : on réessaie
    souvent au début, puis de plus en plus espacé pour ne pas solliciter
    inutilement les sites.
    """
    delay = min(base_seconds * (multiplier ** max(0, attempts - 1)), cap_seconds)
    return datetime.now(timezone.utc) + timedelta(seconds=delay)
