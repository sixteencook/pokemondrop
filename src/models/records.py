"""Enregistrements historisés (lignes lues/écrites via les repositories).

Ces dataclasses sont le contrat entre la couche Repository et le reste de
l'application : l'API et le dashboard ne manipuleront jamais de lignes
SQLAlchemy directement, uniquement ces objets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class CheckRecord:
    """Résultat d'une vérification (alimente stats et graphiques)."""

    id: int
    product_uuid: str
    status: str                      # "ok" | "error"
    availability: Optional[str]      # Availability.value, None si erreur
    response_time_ms: Optional[int]
    error: Optional[str]
    checked_at: datetime


@dataclass(frozen=True)
class TimelineEntry:
    """Un événement de la timeline d'un produit (TOUT l'historique,
    pas seulement les alertes : baseline, prix détecté, boutons…)."""

    id: int
    product_uuid: str
    event_type: str                  # ChangeType.value ou "baseline"
    label: str                       # libellé lisible affiché dans la timeline
    old_value: Optional[str]
    new_value: Optional[str]
    price: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class AlertRecord:
    """Une alerte envoyée (sous-ensemble « notifiable » de la timeline)."""

    id: int
    product_uuid: str
    change_type: str
    old_value: Optional[str]
    new_value: Optional[str]
    price: Optional[str]
    url: str
    screenshot_path: Optional[str]   # rempli plus tard par le service Playwright
    evidence_path: Optional[str]     # HTML archivé au moment de la décision
    notified: bool
    created_at: datetime
