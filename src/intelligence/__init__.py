"""Product Intelligence Engine.

Le logiciel ne raisonne plus en URL mais en PRODUITS : une URL n'est qu'une
offre parmi d'autres pour un même produit.

Couche strictement additive — la découverte, la surveillance, les alertes
et le dashboard existants fonctionnent avec ou sans elle.
"""

from .candidates import OfferCandidate
from .config import IntelligenceSettings, load_intelligence_settings
from .crosssite import CrossSiteIntelligence, CrossSiteReport, CrossSiteSettings
from .engine import IngestOutcome, ProductIntelligenceEngine
from .identity import IdentityField, ProductIdentity
from .keys import KEY_PRIORITIES, SearchKey, build_search_keys
from .strategies import (
    IdentityContext,
    IdentityStrategy,
    IdentityStrategyRegistry,
    discover_identity_strategies,
)
from .entities import (
    CanonicalProduct,
    MatchSuggestion,
    Offer,
    OfferStatus,
    ProductAttributes,
    ProductDraft,
    ProductIdentifiers,
)
from .matching import MatchingEngine, MatchResult, MatchStrategy, default_strategies
from .offer_sync import OfferSyncService
from .naming import name_key, normalise_name, similarity
from .search import CrossSiteSearchCoordinator, SearchQuery

__all__ = [
    "KEY_PRIORITIES",
    "CanonicalProduct",
    "CrossSiteIntelligence",
    "CrossSiteReport",
    "CrossSiteSearchCoordinator",
    "CrossSiteSettings",
    "IdentityContext",
    "IdentityField",
    "IdentityStrategy",
    "IdentityStrategyRegistry",
    "OfferCandidate",
    "ProductIdentity",
    "SearchKey",
    "build_search_keys",
    "discover_identity_strategies",
    "IngestOutcome",
    "IntelligenceSettings",
    "MatchResult",
    "MatchStrategy",
    "MatchSuggestion",
    "MatchingEngine",
    "Offer",
    "OfferStatus",
    "OfferSyncService",
    "ProductAttributes",
    "ProductDraft",
    "ProductIdentifiers",
    "ProductIntelligenceEngine",
    "SearchQuery",
    "default_strategies",
    "load_intelligence_settings",
    "name_key",
    "normalise_name",
    "similarity",
]
