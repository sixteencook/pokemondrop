"""Recherche inter-sites : « ce produit existe-t-il ailleurs ? ».

Quand un marchand révèle l'EAN d'un produit, le moteur interroge tous les
autres plugins pour retrouver la même référence chez eux. Chaque plugin
décide seul de SA méthode (HTTP, API, sitemap, recherche interne,
navigation Playwright) : le coordinateur n'en sait rien.

Contrat, volontairement optionnel : un plugin de découverte qui sait
chercher expose

    async def search(self, query: SearchQuery, ctx: DiscoveryContext)
            -> Sequence[DiscoveredProduct]

Les plugins qui ne l'implémentent pas sont simplement ignorés — aucune
obligation, aucune rupture.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, Sequence

from src.discovery.contracts import DiscoveredProduct, DiscoveryContext
from src.discovery.loader import DiscoveryRegistry
from src.intelligence.entities import CanonicalProduct
from src.utils.logger import get_logger

log = get_logger("intelligence.search")


@dataclass(frozen=True)
class SearchQuery:
    """Ce que l'on cherche, du plus discriminant au plus vague."""

    name: str
    ean: Optional[str] = None
    upc: Optional[str] = None
    mpn: Optional[str] = None
    brand: Optional[str] = None

    @classmethod
    def from_product(cls, product: CanonicalProduct) -> "SearchQuery":
        return cls(
            name=product.name,
            ean=product.identifiers.ean,
            upc=product.identifiers.upc,
            mpn=product.identifiers.mpn,
            brand=product.attributes.brand,
        )

    @property
    def best_term(self) -> str:
        """Terme le plus discriminant disponible."""
        return self.ean or self.upc or self.mpn or self.name


@dataclass(frozen=True)
class SiteSearchResult:
    site: str
    products: tuple[DiscoveredProduct, ...]
    error: str = ""


class CrossSiteSearchCoordinator:
    """Interroge en parallèle tous les plugins sachant chercher."""

    def __init__(
        self,
        registry: DiscoveryRegistry,
        context_factory,
        max_sites: int = 6,
        timeout: float = 30.0,
    ) -> None:
        self._registry = registry
        self._context_factory = context_factory
        self._max_sites = max_sites
        self._timeout = timeout

    @property
    def capable_sites(self) -> list[str]:
        """Sites dont le plugin sait répondre à une recherche."""
        return [
            plugin.site_name for plugin in self._registry.all()
            if callable(getattr(plugin, "search", None))
        ]

    async def search(
        self, query: SearchQuery, exclude_sites: Sequence[str] = ()
    ) -> list[SiteSearchResult]:
        """Cherche le produit chez tous les autres marchands.

        Un plugin lent ou en erreur n'affecte jamais les autres.
        """
        excluded = {site.lower() for site in exclude_sites}
        plugins = [
            plugin for plugin in self._registry.all()
            if callable(getattr(plugin, "search", None))
            and plugin.site_name.lower() not in excluded
        ][: self._max_sites]

        if not plugins:
            return []

        log.check(
            "Recherche inter-sites « %s » sur %d site(s).",
            query.best_term, len(plugins),
        )
        tasks = [self._search_one(plugin, query) for plugin in plugins]
        return list(await asyncio.gather(*tasks))

    async def _search_one(self, plugin, query: SearchQuery) -> SiteSearchResult:
        try:
            options = getattr(plugin, "options", {}) or {}
            ctx: DiscoveryContext = self._context_factory(options)
            found = await asyncio.wait_for(
                plugin.search(query, ctx), timeout=self._timeout
            )
            products = tuple(
                product.with_site(plugin.site_name) for product in (found or ())
            )
            log.check("Recherche %s : %d résultat(s).", plugin.site_name, len(products))
            return SiteSearchResult(site=plugin.site_name, products=products)
        except asyncio.TimeoutError:
            return SiteSearchResult(plugin.site_name, (), "délai dépassé")
        except Exception as exc:  # noqa: BLE001 — isolation par site
            log.error("Recherche %s en échec : %s", plugin.site_name, exc)
            return SiteSearchResult(plugin.site_name, (), str(exc))
