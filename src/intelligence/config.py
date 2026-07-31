"""Configuration du Product Intelligence Engine (config/discovery.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.utils.logger import get_logger

log = get_logger("intelligence.config")


@dataclass(frozen=True)
class IntelligenceSettings:
    """Réglages de la couche d'intelligence produit.

    `merge_threshold` : score minimal pour fusionner automatiquement deux
    fiches. En dessous, le rapprochement part en file de validation —
    rien n'est jamais fusionné à tort en silence.
    """

    enabled: bool = True
    merge_threshold: int = 90
    suggestion_floor: int = 70
    cross_site_search: bool = False
    cross_site_max_sites: int = 6
    auto_monitor_offers: bool = True

    @classmethod
    def from_config(cls, raw: dict[str, Any] | None) -> "IntelligenceSettings":
        raw = raw or {}
        threshold = max(1, min(100, int(raw.get("merge_threshold", 90))))
        floor = max(1, min(threshold, int(raw.get("suggestion_floor", 70))))
        return cls(
            enabled=bool(raw.get("enabled", True)),
            merge_threshold=threshold,
            suggestion_floor=floor,
            cross_site_search=bool(raw.get("cross_site_search", False)),
            cross_site_max_sites=max(1, int(raw.get("cross_site_max_sites", 6))),
            auto_monitor_offers=bool(raw.get("auto_monitor_offers", True)),
        )


def load_intelligence_settings(path: Path) -> IntelligenceSettings:
    """Lit la section `intelligence` du YAML de découverte."""
    if not path.exists():
        return IntelligenceSettings()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return IntelligenceSettings.from_config(raw.get("intelligence"))
    except (yaml.YAMLError, ValueError, TypeError, AttributeError) as exc:
        log.error("Section « intelligence » invalide (%s) — valeurs par défaut.", exc)
        return IntelligenceSettings()
