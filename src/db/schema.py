"""Schéma SQLAlchemy (lignes de base de données).

Ces classes ne sortent JAMAIS de la couche db/repositories : le reste de
l'application manipule les dataclasses de src/models. C'est ce découplage
(pattern Repository) qui permettra de passer de SQLite à PostgreSQL en
changeant uniquement DATABASE_URL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProductRow(Base):
    __tablename__ = "products"

    uuid: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    site: Mapped[str] = mapped_column(String(50), index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    group_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    check_interval: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SnapshotRow(Base):
    """Dernier état connu d'un produit (remplace data/state/*.json)."""

    __tablename__ = "snapshots"

    product_uuid: Mapped[str] = mapped_column(String(32), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)  # ProductSnapshot en JSON
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CheckRow(Base):
    """Une vérification effectuée (stats : checks/heure, temps de réponse…)."""

    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_uuid: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(10))  # "ok" | "error"
    availability: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class TimelineRow(Base):
    """Timeline complète d'un produit : chaque événement de sa vie."""

    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_uuid: Mapped[str] = mapped_column(String(32), index=True)
    event_type: Mapped[str] = mapped_column(String(30), index=True)
    label: Mapped[str] = mapped_column(String(200))
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class AlertRow(Base):
    """Alertes envoyées (avec, plus tard, le chemin de la capture Playwright)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_uuid: Mapped[str] = mapped_column(String(32), index=True)
    change_type: Mapped[str] = mapped_column(String(30), index=True)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    url: Mapped[str] = mapped_column(Text, default="")
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class DiscoveryRow(Base):
    """Fiche produit repérée par la couche Découverte.

    `fingerprint` est l'identité stable de la fiche (voir
    src/discovery/fingerprint.py) : c'est lui qui évite de « redécouvrir »
    en boucle le même produit.

    `status` : pending | imported | ignored | blocked | gone
      - blocked = « toujours ignorer » (décision durable de l'utilisateur)
      - gone    = disparue d'un balayage complet du site
    """

    __tablename__ = "discoveries"

    fingerprint: Mapped[str] = mapped_column(String(32), primary_key=True)
    site: Mapped[str] = mapped_column(String(50), index=True)
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(300))
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ean: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    product_uuid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CatalogProductRow(Base):
    """Produit canonique — le vrai produit, sans aucune URL.

    Les URL vivent dans `offers`. Un même produit peut donc être proposé
    par plusieurs marchands sans être dupliqué.
    """

    __tablename__ = "catalog_products"

    uuid: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    name_key: Mapped[str] = mapped_column(String(300), index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    collection: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    edition: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    release_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ean: Mapped[Optional[str]] = mapped_column(String(14), nullable=True, index=True)
    upc: Mapped[Optional[str]] = mapped_column(String(14), nullable=True, index=True)
    isbn: Mapped[Optional[str]] = mapped_column(String(14), nullable=True, index=True)
    mpn: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    manufacturer_sku: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )
    manufacturer_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OfferRow(Base):
    """Offre d'un marchand pour un produit canonique.

    JAMAIS supprimée : elle change de `status` pour conserver l'historique.
    `monitored_uuid` relie l'offre au produit surveillé (table `products`),
    ce qui laisse la surveillance existante totalement inchangée.
    """

    __tablename__ = "offers"

    uuid: Mapped[str] = mapped_column(String(32), primary_key=True)
    product_uuid: Mapped[str] = mapped_column(String(32), index=True)
    site: Mapped[str] = mapped_column(String(50), index=True)
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, index=True)
    price: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    availability: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    monitored_uuid: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    discovery_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OfferHistoryRow(Base):
    """Historique d'une offre : chaque évolution de prix, dispo ou statut."""

    __tablename__ = "offer_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    offer_uuid: Mapped[str] = mapped_column(String(32), index=True)
    price: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    availability: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class MatchSuggestionRow(Base):
    """Rapprochement sous le seuil de confiance : validation manuelle."""

    __tablename__ = "match_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_uuid: Mapped[str] = mapped_column(String(32), index=True)
    candidate_uuid: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SchemaVersionRow(Base):
    """Migrations appliquées (voir migrations.py)."""

    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
