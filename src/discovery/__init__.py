"""Couche Découverte : exploration automatique des sites.

Strictement additive — la surveillance d'URL connues (src/monitors/,
src/core/engine.py) fonctionne exactement comme avant, avec ou sans elle.
"""

from .config import ApprovalMode, DiscoverySettings, load_discovery_settings
from .contracts import DiscoveredProduct, DiscoveryContext, DiscoveryPlugin, ScanResult
from .engine import DiscoveryEngine, ScanReport
from .fingerprint import canonical_url, compute, product_slug
from .loader import DiscoveryRegistry, discover_discovery_plugins
from .rules import RuleMatch, RuleSet

__all__ = [
    "ApprovalMode",
    "DiscoveredProduct",
    "DiscoveryContext",
    "DiscoveryEngine",
    "DiscoveryPlugin",
    "DiscoveryRegistry",
    "DiscoverySettings",
    "RuleMatch",
    "RuleSet",
    "ScanReport",
    "ScanResult",
    "canonical_url",
    "compute",
    "discover_discovery_plugins",
    "load_discovery_settings",
    "product_slug",
]
