"""Plugin de découverte Micromania.

Deux stratégies complémentaires, choisies dans config/discovery.yaml :

  1. SITEMAP  — suit robots.txt → sitemap.xml, standard du web. Ne demande
                que l'URL racine du site : aucune URL de fiche à connaître
                ni à inventer. C'est la stratégie par défaut, et la seule
                utilisable tant qu'aucune page de catégorie n'est fournie.

  2. LISTINGS — analyse les pages de catégorie / nouveautés / précommandes
                dont les URL sont renseignées dans la configuration
                (`listing_urls`), avec rendu navigateur optionnel pour les
                listings construits côté client.

⚠️ AUCUN vocabulaire produit ici. Le plugin ne sait pas ce qu'est un
Pokémon, un booster ou une console : il remonte TOUTES les fiches trouvées.
Le tri est fait en aval par les règles configurables du moteur
(config/discovery.yaml → rules), ce qui rend le plugin utilisable pour
n'importe quel type de produit.
"""

from __future__ import annotations

from typing import ClassVar, Sequence

from src.discovery.contracts import DiscoveredProduct, DiscoveryContext, ScanResult
from src.discovery.strategies import (
    dedupe,
    listing_products,
    sitemap_product_urls,
)
from src.utils.logger import get_logger

from .metadata import METADATA

log = get_logger("discovery.micromania")

#: Fragments d'URL caractérisant une fiche produit chez ce marchand.
#: À ajuster en observant les URL réelles ; surchargeable via la
#: configuration (`url_patterns`).
DEFAULT_URL_PATTERNS: tuple[str, ...] = ("/p/", "/produit", "/product")


class MicromaniaDiscovery:
    """Explore Micromania et remonte les fiches produit trouvées."""

    site_name: ClassVar[str] = METADATA.site_name
    display_name: ClassVar[str] = METADATA.display_name

    async def scan(self, ctx: DiscoveryContext) -> ScanResult:
        options = ctx.options
        url_patterns: Sequence[str] = tuple(
            options.get("url_patterns") or DEFAULT_URL_PATTERNS
        )
        listing_urls: Sequence[str] = tuple(options.get("listing_urls") or ())
        use_sitemap = bool(options.get("use_sitemap", True))
        use_browser = bool(options.get("use_browser", False))
        max_products = int(options.get("max_products", 300))

        products: list[DiscoveredProduct] = []
        sources = 0
        complete = True

        # --- Pages de listing (catégories, nouveautés, précommandes) ------
        for listing_url in listing_urls:
            found = await listing_products(
                ctx, listing_url, url_patterns,
                use_browser=use_browser, max_products=max_products,
            )
            sources += 1
            if not found:
                complete = False  # page vide ou injoignable : scan partiel
            products.extend(found)

        # --- Sitemap ------------------------------------------------------
        if use_sitemap:
            urls = await sitemap_product_urls(
                ctx, METADATA.base_url, url_patterns,
                max_urls=max_products,
            )
            sources += 1
            if not urls:
                complete = False
            # Le sitemap ne fournit qu'une URL : le titre est déduit du slug,
            # puis remplacé par le vrai titre dès la première vérification.
            products.extend(
                DiscoveredProduct(url=url, title=_title_from_url(url),
                                  source="sitemap")
                for url in urls
            )

        if not listing_urls and not use_sitemap:
            log.error(
                "Découverte Micromania : ni « listing_urls » ni « use_sitemap » — "
                "rien à explorer (voir config/discovery.yaml)."
            )
            return ScanResult(complete=False, note="aucune source configurée")

        unique = dedupe(products)
        return ScanResult(
            products=unique[:max_products],
            complete=complete,
            sources_scanned=sources,
        )


    async def search(self, query, ctx: DiscoveryContext):
        """Recherche inter-sites — méthode PROPRE à ce plugin.

        Le coordinateur ignore les plugins qui n'implémentent pas `search`,
        et ce plugin s'abstient tant qu'aucune URL de recherche n'est
        configurée : on n'invente pas le format d'URL d'un marchand.
        """
        template = (ctx.options.get("search_url_template") or "").strip()
        if not template or "{query}" not in template:
            log.check(
                "Recherche Micromania non configurée "
                "(search_url_template dans config/discovery.yaml)."
            )
            return ()

        from urllib.parse import quote_plus

        url_patterns = tuple(ctx.options.get("url_patterns") or DEFAULT_URL_PATTERNS)
        term = query.best_term
        return await listing_products(
            ctx,
            template.replace("{query}", quote_plus(term)),
            url_patterns,
            use_browser=bool(ctx.options.get("use_browser", False)),
            max_products=int(ctx.options.get("max_search_results", 20)),
        )


def _title_from_url(url: str) -> str:
    """« …/pokemon-30-ans-upc.html » → « Pokemon 30 Ans Upc »."""
    from src.discovery.fingerprint import product_slug

    slug = product_slug(url)
    if not slug:
        return url
    words = [part for part in slug.replace("_", "-").split("-") if part]
    return " ".join(word.capitalize() for word in words)[:200] or url
