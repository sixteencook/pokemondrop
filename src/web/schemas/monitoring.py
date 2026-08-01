"""Schémas alertes, timeline, checks et logs."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from src.models import AlertRecord, CheckRecord, TimelineEntry
from src.utils import LogEntry


class AlertOut(BaseModel):
    """Une alerte envoyée (page Alertes)."""

    id: int
    product_uuid: str
    product_name: Optional[str] = Field(None, description="None si le produit a été supprimé")
    site: Optional[str] = None
    change_type: str
    old_value: Optional[str]
    new_value: Optional[str]
    price: Optional[str]
    url: str
    screenshot_path: Optional[str] = Field(
        None, description="Chemin relatif de la capture sur le disque"
    )
    screenshot_url: Optional[str] = Field(
        None, description="URL de service de la capture (authentifiée), si elle existe"
    )
    evidence_url: Optional[str] = Field(
        None, description="Page analysée au moment de la décision, si archivée"
    )
    notified: bool
    created_at: datetime

    @classmethod
    def from_domain(
        cls, record: AlertRecord, product_name: Optional[str], site: Optional[str]
    ) -> "AlertOut":
        return cls(
            id=record.id,
            product_uuid=record.product_uuid,
            product_name=product_name,
            site=site,
            change_type=record.change_type,
            old_value=record.old_value,
            new_value=record.new_value,
            price=record.price,
            url=record.url,
            screenshot_path=record.screenshot_path,
            screenshot_url=(
                f"/api/v1/alerts/{record.id}/screenshot" if record.screenshot_path else None
            ),
            evidence_url=(
                f"/api/v1/alerts/{record.id}/evidence" if record.evidence_path else None
            ),
            notified=record.notified,
            created_at=record.created_at,
        )


class TimelineEntryOut(BaseModel):
    """Un événement de la timeline d'un produit."""

    id: int
    product_uuid: str
    event_type: str
    label: str = Field(description="Libellé lisible (Précommande ouverte, Rupture de stock…)")
    old_value: Optional[str]
    new_value: Optional[str]
    price: Optional[str]
    created_at: datetime

    @classmethod
    def from_domain(cls, entry: TimelineEntry) -> "TimelineEntryOut":
        return cls(
            id=entry.id,
            product_uuid=entry.product_uuid,
            event_type=entry.event_type,
            label=entry.label,
            old_value=entry.old_value,
            new_value=entry.new_value,
            price=entry.price,
            created_at=entry.created_at,
        )


class CheckOut(BaseModel):
    """Une vérification effectuée."""

    id: int
    product_uuid: str
    status: str
    availability: Optional[str]
    response_time_ms: Optional[int]
    error: Optional[str]
    checked_at: datetime

    @classmethod
    def from_domain(cls, record: CheckRecord) -> "CheckOut":
        return cls(
            id=record.id,
            product_uuid=record.product_uuid,
            status=record.status,
            availability=record.availability,
            response_time_ms=record.response_time_ms,
            error=record.error,
            checked_at=record.checked_at,
        )


class LogEntryOut(BaseModel):
    """Une ligne de log du buffer mémoire."""

    id: int
    time: str
    level: str
    logger: str
    message: str

    @classmethod
    def from_domain(cls, entry: LogEntry) -> "LogEntryOut":
        return cls(id=entry.id, time=entry.time, level=entry.level,
                   logger=entry.logger, message=entry.message)
