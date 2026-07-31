"""Schémas de la couche Découverte (contrat public de l'API v1)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from src.models import DiscoveryRecord, DiscoveryStatus


class DiscoveryOut(BaseModel):
    """Une fiche produit repérée automatiquement."""

    fingerprint: str = Field(description="Identité stable de la fiche")
    site: str
    url: str
    title: str
    image_url: Optional[str]
    price: Optional[str]
    source: str = Field(description="Origine (sitemap, page de listing…)")
    status: DiscoveryStatus
    decision_reason: str = Field(description="Pourquoi ce statut a été retenu")
    product_uuid: Optional[str] = Field(
        None, description="Produit surveillé créé à partir de cette fiche"
    )
    times_seen: int
    first_seen_at: datetime
    last_seen_at: datetime

    @classmethod
    def from_domain(cls, record: DiscoveryRecord) -> "DiscoveryOut":
        return cls(
            fingerprint=record.fingerprint,
            site=record.site,
            url=record.url,
            title=record.title,
            image_url=record.image_url,
            price=record.price,
            source=record.source,
            status=record.status,
            decision_reason=record.decision_reason,
            product_uuid=record.product_uuid,
            times_seen=record.times_seen,
            first_seen_at=record.first_seen_at,
            last_seen_at=record.last_seen_at,
        )


class DiscoveryStatusOut(BaseModel):
    """État de la couche Découverte (page Découverte, en-tête)."""

    enabled: bool
    mode: str = Field(description="auto | review | rules")
    scan_interval: int
    sites: list[str] = Field(description="Sites dotés d'un plugin de découverte")
    counts: dict[str, int] = Field(description="Nombre de fiches par statut")
    last_discovery_at: Optional[datetime]
    last_scan_summary: Optional[str]


class ScanReportOut(BaseModel):
    """Bilan d'un balayage déclenché à la demande."""

    sites_scanned: int
    products_seen: int
    new_products: int
    imported: int
    pending: int
    excluded: int
    gone: int
    errors: list[str]
    summary: str
