"""Pagination commune à tous les repositories.

Contrat unique pour l'API : page 1-indexée, tri sur colonne whitelistée
(jamais de tri sur une expression fournie par le client), total calculé
sur la même requête filtrée.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


def apply_sort(
    query: Select[Any],
    sortable: dict[str, Any],
    sort: str,
    order: str,
    default: str,
) -> Select[Any]:
    """Applique un tri sécurisé : colonne whitelistée, sinon colonne par défaut."""
    column = sortable.get(sort, sortable[default])
    return query.order_by(column.desc() if order == "desc" else column.asc())


async def fetch_page(
    session: AsyncSession,
    query: Select[Any],
    page: int,
    page_size: int,
    scalars: bool = True,
) -> tuple[Sequence[Any], int]:
    """Retourne (lignes de la page, total filtré).

    `scalars=True` pour une requête portant sur une seule entité,
    False pour les requêtes multi-colonnes (jointures).
    """
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = (await session.execute(count_query)).scalar_one()

    result = await session.execute(query.offset((page - 1) * page_size).limit(page_size))
    rows = result.scalars().all() if scalars else result.all()
    return rows, int(total)
