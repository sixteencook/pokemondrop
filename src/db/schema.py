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


class SchemaVersionRow(Base):
    """Migrations appliquées (voir migrations.py)."""

    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
