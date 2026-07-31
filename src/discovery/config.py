"""Chargement de config/discovery.yaml.

Trois modes d'approbation :

    auto    tout ce qui n'est pas exclu est importé et surveillé aussitôt
    review  tout arrive dans la page « Découverte » pour validation manuelle
    rules   seules les fiches correspondant aux règles sont importées ;
            les autres restent en attente de validation

Dans les trois modes, les règles d'EXCLUSION s'appliquent toujours : une
fiche exclue n'est jamais importée automatiquement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from src.discovery.rules import RuleSet
from src.models import Priority
from src.utils.logger import get_logger

log = get_logger("discovery.config")


class ApprovalMode(str, Enum):
    AUTO = "auto"
    REVIEW = "review"
    RULES = "rules"


@dataclass(frozen=True)
class ImportDefaults:
    """Valeurs appliquées aux produits créés automatiquement."""

    check_interval: int = 60
    priority: Priority = Priority.HIGH
    tags: tuple[str, ...] = ("auto-découvert",)
    enabled: bool = True


@dataclass(frozen=True)
class SiteDiscoveryConfig:
    """Réglages d'un site ; `options` est transmis tel quel au plugin."""

    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoverySettings:
    enabled: bool = False
    mode: ApprovalMode = ApprovalMode.REVIEW
    scan_interval: int = 900
    max_new_per_scan: int = 50
    rules: RuleSet = field(default_factory=RuleSet)
    defaults: ImportDefaults = field(default_factory=ImportDefaults)
    sites: dict[str, SiteDiscoveryConfig] = field(default_factory=dict)

    def for_site(self, site: str) -> SiteDiscoveryConfig:
        return self.sites.get(site.lower(), SiteDiscoveryConfig())


def load_discovery_settings(path: Path) -> DiscoverySettings:
    """Lit le YAML de découverte ; retourne les valeurs par défaut si absent.

    Un fichier illisible ne doit jamais empêcher l'application de démarrer :
    l'erreur est journalisée et la découverte reste désactivée.
    """
    if not path.exists():
        log.ok("Aucun config/discovery.yaml — découverte désactivée.")
        return DiscoverySettings()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.error("config/discovery.yaml illisible (%s) — découverte désactivée.", exc)
        return DiscoverySettings()

    try:
        return _parse(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        log.error("config/discovery.yaml invalide (%s) — découverte désactivée.", exc)
        return DiscoverySettings()


def _parse(raw: dict[str, Any]) -> DiscoverySettings:
    mode_raw = str(raw.get("mode", "review")).strip().lower()
    try:
        mode = ApprovalMode(mode_raw)
    except ValueError:
        log.error("Mode « %s » inconnu — bascule sur « review ».", mode_raw)
        mode = ApprovalMode.REVIEW

    defaults_raw = raw.get("defaults") or {}
    priority_raw = str(defaults_raw.get("priority", "high")).strip().lower()
    try:
        priority = Priority(priority_raw)
    except ValueError:
        log.error("Priorité « %s » inconnue — bascule sur « normal ».", priority_raw)
        priority = Priority.NORMAL

    tags = tuple(
        str(tag).strip().lower()
        for tag in (defaults_raw.get("tags") or ["auto-découvert"])
        if str(tag).strip()
    )

    sites: dict[str, SiteDiscoveryConfig] = {}
    for name, site_raw in (raw.get("sites") or {}).items():
        site_raw = site_raw or {}
        sites[str(name).strip().lower()] = SiteDiscoveryConfig(
            enabled=bool(site_raw.get("enabled", True)),
            options={k: v for k, v in site_raw.items() if k != "enabled"},
        )

    return DiscoverySettings(
        enabled=bool(raw.get("enabled", False)),
        mode=mode,
        scan_interval=max(60, int(raw.get("scan_interval", 900))),
        max_new_per_scan=max(1, int(raw.get("max_new_per_scan", 50))),
        rules=RuleSet.from_config(raw.get("rules")),
        defaults=ImportDefaults(
            check_interval=max(10, int(defaults_raw.get("check_interval", 60))),
            priority=priority,
            tags=tags,
            enabled=bool(defaults_raw.get("enabled", True)),
        ),
        sites=sites,
    )
