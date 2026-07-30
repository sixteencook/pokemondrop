"""Repository des produits — la base est la source de vérité.

Convertit ProductRow (SQLAlchemy) ↔ ProductConfig (dataclass métier).
Le YAML n'est plus qu'un seed initial et un format d'export.
"""

from __future__ import annotations

import json
import uuid as uuid_lib
from typing import Any, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import ProductRow, utcnow
from src.models import Priority, ProductConfig
from src.repositories.pagination import apply_sort, fetch_page

#: Colonnes de tri autorisées pour l'API (whitelist anti-injection).
_SORTABLE = {
    "name": ProductRow.name,
    "site": ProductRow.site,
    "priority": ProductRow.priority,
    "check_interval": ProductRow.check_interval,
    "created_at": ProductRow.created_at,
    "updated_at": ProductRow.updated_at,
}

#: Champs modifiables via update() (PATCH de l'API).
_UPDATABLE_FIELDS = {
    "name", "site", "url", "check_interval", "enabled", "group", "priority", "tags",
}


def _to_domain(row: ProductRow) -> ProductConfig:
    return ProductConfig(
        name=row.name,
        site=row.site,
        url=row.url or "",
        check_interval=row.check_interval,
        enabled=row.enabled,
        group=row.group_key,
        uuid=row.uuid,
        priority=Priority(row.priority),
        tags=tuple(json.loads(row.tags or "[]")),
    )


class ProductRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def count(self) -> int:
        async with self._sessions() as session:
            result = await session.execute(select(func.count(ProductRow.uuid)))
            return int(result.scalar_one())

    async def list_all(self) -> list[ProductConfig]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(ProductRow).order_by(ProductRow.created_at)
            )).scalars().all()
            return [_to_domain(row) for row in rows]

    async def list_page(
        self,
        page: int = 1,
        page_size: int = 25,
        sort: str = "created_at",
        order: str = "asc",
        site: Optional[str] = None,
        enabled: Optional[bool] = None,
        tag: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[ProductConfig], int]:
        """Page de produits filtrée/triée + total filtré (pour l'API)."""
        query = select(ProductRow)
        if site:
            query = query.where(ProductRow.site == site.lower())
        if enabled is not None:
            query = query.where(ProductRow.enabled == enabled)
        if tag:
            query = query.where(ProductRow.tags.like(f'%"{tag.lower()}"%'))
        if search:
            query = query.where(ProductRow.name.ilike(f"%{search}%"))
        query = apply_sort(query, _SORTABLE, sort, order, "created_at")
        async with self._sessions() as session:
            rows, total = await fetch_page(session, query, page, page_size)
            return [_to_domain(row) for row in rows], total

    async def sites(self) -> list[str]:
        """Sites distincts présents en base."""
        async with self._sessions() as session:
            rows = (await session.execute(
                select(ProductRow.site).distinct().order_by(ProductRow.site)
            )).scalars().all()
            return list(rows)

    async def count_by_site(self) -> dict[str, int]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(ProductRow.site, func.count(ProductRow.uuid))
                .group_by(ProductRow.site)
            )).all()
            return {site: int(count) for site, count in rows}

    async def count_enabled(self) -> int:
        async with self._sessions() as session:
            result = await session.execute(
                select(func.count(ProductRow.uuid)).where(ProductRow.enabled.is_(True))
            )
            return int(result.scalar_one())

    async def get(self, product_uuid: str) -> Optional[ProductConfig]:
        async with self._sessions() as session:
            row = await session.get(ProductRow, product_uuid)
            return _to_domain(row) if row else None

    async def create(self, product: ProductConfig) -> ProductConfig:
        """Insère le produit ; génère l'uuid immuable s'il est absent."""
        new_uuid = product.uuid or uuid_lib.uuid4().hex
        async with self._sessions() as session:
            session.add(ProductRow(
                uuid=new_uuid,
                name=product.name,
                site=product.site,
                url=product.url,
                group_key=product.group,
                check_interval=product.check_interval,
                enabled=product.enabled,
                priority=product.priority.value,
                tags=json.dumps(list(product.tags), ensure_ascii=False),
            ))
            await session.commit()
        return ProductConfig(
            name=product.name, site=product.site, url=product.url,
            check_interval=product.check_interval, enabled=product.enabled,
            group=product.group, uuid=new_uuid,
            priority=product.priority, tags=product.tags,
        )

    async def update(self, product_uuid: str, **fields: Any) -> Optional[ProductConfig]:
        """Met à jour les champs fournis. L'uuid n'est jamais modifiable."""
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Champs non modifiables : {', '.join(sorted(unknown))}")

        async with self._sessions() as session:
            row = await session.get(ProductRow, product_uuid)
            if row is None:
                return None
            for key, value in fields.items():
                if key == "group":
                    row.group_key = value
                elif key == "priority":
                    row.priority = Priority(value).value
                elif key == "tags":
                    row.tags = json.dumps([str(t).strip().lower() for t in value],
                                          ensure_ascii=False)
                else:
                    setattr(row, key, value)
            row.updated_at = utcnow()
            await session.commit()
            return _to_domain(row)

    async def delete(self, product_uuid: str) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                delete(ProductRow).where(ProductRow.uuid == product_uuid)
            )
            await session.commit()
            return result.rowcount > 0
