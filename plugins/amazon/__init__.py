"""Plugin Amazon — plugin de référence du projet.

Tout nouveau marchand (Fnac, Cultura, King Jouet, Leclerc, Carrefour,
Smyths, Boulanger…) se calque sur cette structure :

    plugins/<site>/
    ├── __init__.py     METADATA + exports
    ├── keywords.py     vocabulaire du site, sans logique
    ├── parser.py       états typés, buy box, URL canonique
    ├── monitor.py      surveillance d'une fiche connue
    ├── identity.py     stratégie d'enrichissement (auto-découverte)
    └── discovery.py    exploration + recherche par identité

Rien de ce qui touche Amazon ne sort de ce dossier : le cœur ne manipule
que des interfaces (BaseMonitor, DiscoveryPlugin, IdentityStrategy).
"""

from src.monitors.plugin import PluginMetadata

METADATA = PluginMetadata(
    site_name="amazon",
    display_name="Amazon",
    version="1.0.0",
    base_url="https://www.amazon.fr",
    description="Fiches produit Amazon : états natifs, buy box et identité "
                "complète (ASIN, UPC/EAN, MPN, modèle).",
)

from .discovery import AmazonDiscovery  # noqa: E402
from .identity import AmazonIdentityStrategy  # noqa: E402
from .monitor import AmazonMonitor  # noqa: E402
from .parser import AmazonState, analyse, canonical_url, extract_asin  # noqa: E402

__all__ = [
    "METADATA",
    "AmazonDiscovery",
    "AmazonIdentityStrategy",
    "AmazonMonitor",
    "AmazonState",
    "analyse",
    "canonical_url",
    "extract_asin",
]
