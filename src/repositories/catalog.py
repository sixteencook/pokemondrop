"""Repositories du catalogue produit : produits canoniques et offres."""

from __future__ import annotations

import json
import uuid as uuid_lib
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.schema import (
    CatalogProductRow,
    MatchSuggestionRow,
    OfferHistoryRow,
    OfferRow,
    utcnow,
)
from src.intelligence.entities import (
    CanonicalProduct,
    MatchSuggestion,
    Offer,
    OfferSnapshotEntry,
    OfferStatus,
    ProductAttributes,
    ProductDraft,
    ProductIdentifiers,
)
from src.intelligence.identity import ProductIdentity
from src.intelligence.naming import name_key
from src.models import Priority
from src.repositories.pagination import apply_sort, fetch_page

_PRODUCT_SORTABLE = {
    "name": CatalogProductRow.name,
    "brand": CatalogProductRow.brand,
    "created_at": CatalogProductRow.created_at,
    "updated_at": CatalogProductRow.updated_at,
    "release_date": CatalogProductRow.release_date,
}


def _product_to_domain(row: CatalogProductRow) -> CanonicalProduct:
    return CanonicalProduct(
        uuid=row.uuid,
        name=row.name,
        name_key=row.name_key,
        identifiers=ProductIdentifiers(
            ean=row.ean, upc=row.upc, isbn=row.isbn, mpn=row.mpn,
            manufacturer_sku=row.manufacturer_sku,
            manufacturer_ref=row.manufacturer_ref,
        ),
        attributes=ProductAttributes(
            brand=row.brand, collection=row.collection, edition=row.edition,
            category=row.category, release_date=row.release_date,
            image_url=row.image_url,
        ),
        tags=tuple(json.loads(row.tags or "[]")),
        priority=Priority(row.priority),
        created_at=row.created_at,
        updated_at=row.updated_at,
        identity=_load_identity(row),
    )


def _load_identity(row: CatalogProductRow) -> ProductIdentity:
    """Profil d'identité stocké, complété par les colonnes indexées.

    Les colonnes restent la source de vérité du matching SQL ; le JSON
    porte les confiances, les sources, les alias et les images.
    """
    try:
        identity = ProductIdentity.from_dict(json.loads(row.identity or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        identity = ProductIdentity()

    for name, value in (
        ("ean", row.ean), ("upc", row.upc), ("isbn", row.isbn),
        ("mpn", row.mpn), ("sku", row.manufacturer_sku),
        ("manufacturer_part_number", row.manufacturer_ref),
        ("asin", row.asin), ("model_number", row.model_number),
        ("brand", row.brand), ("manufacturer", row.manufacturer),
        ("collection", row.collection), ("edition", row.edition),
        ("release_date", row.release_date), ("primary_image", row.image_url),
        ("canonical_name", row.name),
    ):
        if value and not identity.get(name):
            identity = identity.with_field(name, value, 100, "base")
    return identity


def _offer_to_domain(row: OfferRow) -> Offer:
    return Offer(
        uuid=row.uuid, product_uuid=row.product_uuid, site=row.site, url=row.url,
        canonical_url=row.canonical_url, price=row.price, currency=row.currency,
        availability=row.availability, status=OfferStatus(row.status),
        monitored_uuid=row.monitored_uuid,
        discovery_fingerprint=row.discovery_fingerprint,
        first_seen_at=row.first_seen_at, last_checked_at=row.last_checked_at,
        last_changed_at=row.last_changed_at,
    )


class CatalogRepository:
    """Produits canoniques + suggestions de fusion."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    # -- Produits ------------------------------------------------------- #

    async def get(self, product_uuid: str) -> Optional[CanonicalProduct]:
        async with self._sessions() as session:
            row = await session.get(CatalogProductRow, product_uuid)
            return _product_to_domain(row) if row else None

    async def create(self, draft: ProductDraft) -> CanonicalProduct:
        new_uuid = uuid_lib.uuid4().hex
        async with self._sessions() as session:
            session.add(CatalogProductRow(
                uuid=new_uuid, name=draft.name[:300], name_key=name_key(draft.name),
                brand=draft.attributes.brand, collection=draft.attributes.collection,
                edition=draft.attributes.edition, category=draft.attributes.category,
                release_date=draft.attributes.release_date,
                image_url=draft.attributes.image_url,
                ean=draft.identifiers.ean, upc=draft.identifiers.upc,
                isbn=draft.identifiers.isbn, mpn=draft.identifiers.mpn,
                manufacturer_sku=draft.identifiers.manufacturer_sku,
                manufacturer_ref=draft.identifiers.manufacturer_ref,
                asin=draft.identity.asin,
                model_number=draft.identity.model_number,
                manufacturer=draft.identity.manufacturer,
                identity=json.dumps(draft.identity.to_dict(), ensure_ascii=False),
                tags=json.dumps(list(draft.tags), ensure_ascii=False),
                priority=draft.priority.value,
            ))
            await session.commit()
        return await self.get(new_uuid)  # type: ignore[return-value]

    async def enrich(
        self, product_uuid: str, draft: ProductDraft
    ) -> Optional[CanonicalProduct]:
        """Complète les champs vides sans jamais écraser une valeur connue."""
        async with self._sessions() as session:
            row = await session.get(CatalogProductRow, product_uuid)
            if row is None:
                return None

            for attribute, value in (
                ("ean", draft.identifiers.ean), ("upc", draft.identifiers.upc),
                ("isbn", draft.identifiers.isbn), ("mpn", draft.identifiers.mpn),
                ("manufacturer_sku", draft.identifiers.manufacturer_sku),
                ("manufacturer_ref", draft.identifiers.manufacturer_ref),
                ("brand", draft.attributes.brand),
                ("collection", draft.attributes.collection),
                ("edition", draft.attributes.edition),
                ("category", draft.attributes.category),
                ("release_date", draft.attributes.release_date),
                ("image_url", draft.attributes.image_url),
                ("asin", draft.identity.asin),
                ("model_number", draft.identity.model_number),
                ("manufacturer", draft.identity.manufacturer),
            ):
                if value and not getattr(row, attribute):
                    setattr(row, attribute, value)

            # Le profil d'identité fusionne : chaque champ garde la meilleure
            # confiance, et les alias s'accumulent.
            if not draft.identity.is_empty:
                merged = _load_identity(row).merged_with(draft.identity)
                row.identity = json.dumps(merged.to_dict(), ensure_ascii=False)

            if draft.tags:
                merged = dict.fromkeys([*json.loads(row.tags or "[]"), *draft.tags])
                row.tags = json.dumps(list(merged), ensure_ascii=False)

            row.updated_at = utcnow()
            await session.commit()
            return _product_to_domain(row)

    async def candidates_for(self, draft: ProductDraft) -> list[CanonicalProduct]:
        """Présélection SQL avant le matching fin.

        Évite de charger tout le catalogue : on ne retient que les produits
        partageant un identifiant, une clé de nom ou une marque.
        """
        identifiers = draft.identifiers
        conditions = []
        for column, value in (
            (CatalogProductRow.ean, identifiers.ean),
            (CatalogProductRow.upc, identifiers.upc),
            (CatalogProductRow.isbn, identifiers.isbn),
            (CatalogProductRow.mpn, identifiers.mpn),
            (CatalogProductRow.manufacturer_sku, identifiers.manufacturer_sku),
            (CatalogProductRow.manufacturer_ref, identifiers.manufacturer_ref),
        ):
            if value:
                conditions.append(column == value)

        # Clés d'identité v2 : ASIN et numéro de modèle.
        for column, value in (
            (CatalogProductRow.asin, draft.identity.asin),
            (CatalogProductRow.model_number, draft.identity.model_number),
        ):
            if value:
                conditions.append(column == value)

        conditions.append(CatalogProductRow.name_key == name_key(draft.name))
        if draft.attributes.brand:
            conditions.append(CatalogProductRow.brand == draft.attributes.brand)

        async with self._sessions() as session:
            rows = (await session.execute(
                select(CatalogProductRow).where(or_(*conditions)).limit(200)
            )).scalars().all()
            return [_product_to_domain(row) for row in rows]

    async def list_page(
        self,
        page: int = 1,
        page_size: int = 25,
        sort: str = "updated_at",
        order: str = "desc",
        search: Optional[str] = None,
        brand: Optional[str] = None,
        with_offers_only: bool = True,
    ) -> tuple[list[CanonicalProduct], int]:
        query = select(CatalogProductRow)
        if search:
            query = query.where(CatalogProductRow.name.ilike(f"%{search}%"))
        if brand:
            query = query.where(CatalogProductRow.brand == brand)
        if with_offers_only:
            # Après une fusion, le produit source est conservé (aucune donnée
            # perdue) mais n'a plus d'offre : c'est une coquille vide qui
            # n'a pas à réapparaître comme un doublon dans le catalogue.
            query = query.where(
                select(OfferRow.uuid)
                .where(OfferRow.product_uuid == CatalogProductRow.uuid)
                .exists()
            )
        query = apply_sort(query, _PRODUCT_SORTABLE, sort, order, "updated_at")
        async with self._sessions() as session:
            rows, total = await fetch_page(session, query, page, page_size)
            return [_product_to_domain(row) for row in rows], total

    async def count(self) -> int:
        async with self._sessions() as session:
            return int((await session.execute(
                select(func.count(CatalogProductRow.uuid))
            )).scalar_one())

    # -- Suggestions de fusion ------------------------------------------- #

    async def add_suggestion(
        self, product_uuid: str, candidate_uuid: str,
        score: int, method: str, reason: str,
    ) -> int:
        async with self._sessions() as session:
            existing = (await session.execute(
                select(MatchSuggestionRow).where(
                    MatchSuggestionRow.product_uuid == product_uuid,
                    MatchSuggestionRow.candidate_uuid == candidate_uuid,
                    MatchSuggestionRow.status == "pending",
                )
            )).scalar_one_or_none()
            if existing is not None:
                return existing.id

            row = MatchSuggestionRow(
                product_uuid=product_uuid, candidate_uuid=candidate_uuid,
                score=score, method=method, reason=reason,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def list_suggestions(
        self, status: str = "pending", limit: int = 100
    ) -> list[MatchSuggestion]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(MatchSuggestionRow)
                .where(MatchSuggestionRow.status == status)
                .order_by(MatchSuggestionRow.score.desc())
                .limit(limit)
            )).scalars().all()
            return [
                MatchSuggestion(
                    id=row.id, product_uuid=row.product_uuid,
                    candidate_uuid=row.candidate_uuid, score=row.score,
                    method=row.method, reason=row.reason, status=row.status,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def set_suggestion_status(self, suggestion_id: int, status: str) -> bool:
        async with self._sessions() as session:
            row = await session.get(MatchSuggestionRow, suggestion_id)
            if row is None:
                return False
            row.status = status
            await session.commit()
            return True


class OfferRepository:
    """Offres marchandes et leur historique."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def get(self, offer_uuid: str) -> Optional[Offer]:
        async with self._sessions() as session:
            row = await session.get(OfferRow, offer_uuid)
            return _offer_to_domain(row) if row else None

    async def find_by_url(self, canonical_url: str) -> Optional[Offer]:
        async with self._sessions() as session:
            row = (await session.execute(
                select(OfferRow).where(OfferRow.canonical_url == canonical_url)
            )).scalars().first()
            return _offer_to_domain(row) if row else None

    async def upsert(
        self,
        product_uuid: str,
        site: str,
        url: str,
        canonical_url: str,
        price: Optional[str] = None,
        availability: Optional[str] = None,
        monitored_uuid: Optional[str] = None,
        discovery_fingerprint: Optional[str] = None,
        currency: str = "EUR",
    ) -> tuple[Offer, bool]:
        """Crée l'offre ou met à jour celle qui porte déjà cette URL.

        Toute évolution de prix, de disponibilité ou de statut est
        journalisée dans l'historique.
        """
        async with self._sessions() as session:
            row = (await session.execute(
                select(OfferRow).where(OfferRow.canonical_url == canonical_url)
            )).scalars().first()

            if row is None:
                row = OfferRow(
                    uuid=uuid_lib.uuid4().hex, product_uuid=product_uuid,
                    site=site, url=url, canonical_url=canonical_url,
                    price=price, currency=currency, availability=availability,
                    status=OfferStatus.ACTIVE.value, monitored_uuid=monitored_uuid,
                    discovery_fingerprint=discovery_fingerprint,
                    last_checked_at=utcnow(), last_changed_at=utcnow(),
                )
                session.add(row)
                session.add(OfferHistoryRow(
                    offer_uuid=row.uuid, price=price, availability=availability,
                    status=OfferStatus.ACTIVE.value,
                ))
                await session.commit()
                return _offer_to_domain(row), True

            changed = (
                (price is not None and price != row.price)
                or (availability is not None and availability != row.availability)
            )
            if price is not None:
                row.price = price
            if availability is not None:
                row.availability = availability
            if monitored_uuid and not row.monitored_uuid:
                row.monitored_uuid = monitored_uuid
            row.url = url
            row.last_checked_at = utcnow()
            if changed:
                row.last_changed_at = utcnow()
                session.add(OfferHistoryRow(
                    offer_uuid=row.uuid, price=row.price,
                    availability=row.availability, status=row.status,
                ))
            await session.commit()
            return _offer_to_domain(row), False

    async def set_status(
        self, offer_uuid: str, status: OfferStatus, reason: str = ""
    ) -> Optional[Offer]:
        """Change l'état d'une offre. Aucune offre n'est jamais supprimée."""
        async with self._sessions() as session:
            row = await session.get(OfferRow, offer_uuid)
            if row is None:
                return None
            if row.status != status.value:
                row.status = status.value
                row.last_changed_at = utcnow()
                session.add(OfferHistoryRow(
                    offer_uuid=offer_uuid, price=row.price,
                    availability=row.availability, status=status.value,
                ))
                await session.commit()
            return _offer_to_domain(row)

    async def for_product(self, product_uuid: str) -> list[Offer]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(OfferRow)
                .where(OfferRow.product_uuid == product_uuid)
                .order_by(OfferRow.site)
            )).scalars().all()
            return [_offer_to_domain(row) for row in rows]

    async def for_products(self, product_uuids: list[str]) -> dict[str, list[Offer]]:
        """Offres de plusieurs produits en une requête (listes de l'API)."""
        if not product_uuids:
            return {}
        async with self._sessions() as session:
            rows = (await session.execute(
                select(OfferRow).where(OfferRow.product_uuid.in_(product_uuids))
            )).scalars().all()
        grouped: dict[str, list[Offer]] = {}
        for row in rows:
            grouped.setdefault(row.product_uuid, []).append(_offer_to_domain(row))
        return grouped

    async def by_monitored(self, monitored_uuid: str) -> Optional[Offer]:
        async with self._sessions() as session:
            row = (await session.execute(
                select(OfferRow).where(OfferRow.monitored_uuid == monitored_uuid)
            )).scalars().first()
            return _offer_to_domain(row) if row else None

    async def reassign(self, from_product: str, to_product: str) -> int:
        """Déplace toutes les offres d'un produit vers un autre (fusion)."""
        async with self._sessions() as session:
            rows = (await session.execute(
                select(OfferRow).where(OfferRow.product_uuid == from_product)
            )).scalars().all()
            for row in rows:
                row.product_uuid = to_product
            if rows:
                await session.commit()
            return len(rows)

    async def history(
        self, offer_uuid: str, limit: int = 100
    ) -> list[OfferSnapshotEntry]:
        async with self._sessions() as session:
            rows = (await session.execute(
                select(OfferHistoryRow)
                .where(OfferHistoryRow.offer_uuid == offer_uuid)
                .order_by(OfferHistoryRow.recorded_at.desc())
                .limit(limit)
            )).scalars().all()
            return [
                OfferSnapshotEntry(
                    id=row.id, offer_uuid=row.offer_uuid, price=row.price,
                    availability=row.availability, status=OfferStatus(row.status),
                    recorded_at=row.recorded_at,
                )
                for row in rows
            ]

    async def count(self) -> int:
        async with self._sessions() as session:
            return int((await session.execute(
                select(func.count(OfferRow.uuid))
            )).scalar_one())

    async def last_updated_at(self) -> Optional[datetime]:
        async with self._sessions() as session:
            return (await session.execute(
                select(func.max(OfferRow.last_changed_at))
            )).scalar_one()
