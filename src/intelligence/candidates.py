"""Résultat d'une recherche chez un marchand.

Un plugin ne rend plus une simple URL : il rend un CANDIDAT motivé —
sa confiance, les champs qui ont concordé, et pourquoi. C'est ce qui
permet au dashboard d'expliquer un rapprochement au lieu de l'imposer.

    confidence : 96
    matched_fields : upc, brand
    reason : « UPC identique »
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.discovery.contracts import DiscoveredProduct
from src.intelligence.identity import ProductIdentity


@dataclass(frozen=True)
class OfferCandidate:
    """Fiche trouvée chez un marchand, avec la justification du rapprochement."""

    url: str
    title: str
    site: str = ""
    price: Optional[str] = None
    image_url: Optional[str] = None
    availability: Optional[str] = None
    confidence: int = 0
    matched_fields: tuple[str, ...] = ()
    reason: str = ""
    #: Ce que le plugin a appris en chemin (nouvel UPC, MPN, marque…).
    identity_hints: ProductIdentity = field(default_factory=ProductIdentity)

    def with_site(self, site: str) -> "OfferCandidate":
        from dataclasses import replace

        return replace(self, site=site)

    def to_discovered(self) -> DiscoveredProduct:
        """Conversion vers le contrat de découverte, pour l'ingestion."""
        return DiscoveredProduct(
            url=self.url,
            title=self.title,
            site=self.site,
            image_url=self.image_url,
            price=self.price,
            ean=self.identity_hints.ean,
            sku=self.identity_hints.sku,
            mpn=self.identity_hints.mpn,
            brand=self.identity_hints.brand,
            release_date=self.identity_hints.release_date,
            source="cross_site_search",
        )

    @property
    def summary(self) -> str:
        fields = ", ".join(self.matched_fields) or "aucun champ"
        return f"{self.confidence} ({fields}) — {self.reason or 'sans motif'}"
