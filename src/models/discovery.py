"""Enregistrement d'une fiche découverte (contrat repository ↔ application)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class DiscoveryStatus(str, Enum):
    """Cycle de vie d'une fiche découverte."""

    PENDING = "pending"      # en attente de décision
    IMPORTED = "imported"    # ajoutée à la surveillance
    IGNORED = "ignored"      # écartée cette fois-ci
    BLOCKED = "blocked"      # « toujours ignorer » — décision durable
    GONE = "gone"            # disparue du site


@dataclass(frozen=True)
class DiscoveryRecord:
    fingerprint: str
    site: str
    url: str
    canonical_url: str
    title: str
    image_url: Optional[str]
    price: Optional[str]
    sku: Optional[str]
    ean: Optional[str]
    source: str
    status: DiscoveryStatus
    decision_reason: str
    product_uuid: Optional[str]
    times_seen: int
    first_seen_at: datetime
    last_seen_at: datetime
