"""Découverte automatique des plugins et stratégies d'exploration."""

import httpx
import pytest

from src.discovery.contracts import DiscoveryContext
from src.discovery.loader import discover_discovery_plugins
from src.discovery.strategies import extract_products, sitemap_product_urls

#: Seuls les tests réseau sont asynchrones — pas de marqueur global ici.
asyncio_test = pytest.mark.asyncio

LISTING_HTML = """
<html><body>
  <div class="tile">
    <a href="/p/pokemon-30-ans-upc.html">
      <img src="/img/upc.jpg" alt="Pokémon 30 Ans UPC">
    </a>
    <span class="price">189,99 €</span>
  </div>
  <div class="tile">
    <a href="/p/manette-pro.html">Manette Pro</a>
    <span class="price">59,99 €</span>
  </div>
  <a href="/aide/livraison">Livraison</a>
  <a href="/p/pokemon-30-ans-upc.html">Doublon</a>
</body></html>
"""

ROBOTS = "User-agent: *\nSitemap: https://boutique.test/sitemap_index.xml\n"
SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex><sitemap><loc>https://boutique.test/sitemap-products.xml</loc></sitemap>
</sitemapindex>"""
SITEMAP_PRODUCTS = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://boutique.test/p/pokemon-upc.html</loc></url>
  <url><loc>https://boutique.test/p/manette.html</loc></url>
  <url><loc>https://boutique.test/aide/contact</loc></url>
</urlset>"""


def context(routes: dict[str, str]) -> DiscoveryContext:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="")
        return httpx.Response(200, text=body)

    return DiscoveryContext(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


# --------------------------------------------------------------------- #
# Chargement des plugins                                                 #
# --------------------------------------------------------------------- #

def test_micromania_discovery_plugin_is_loaded():
    registry = discover_discovery_plugins()
    assert "micromania" in registry.sites
    plugin = registry.get("micromania")
    assert plugin.display_name == "Micromania"
    assert hasattr(plugin, "scan")


def test_registry_is_empty_for_unknown_package():
    assert len(discover_discovery_plugins("paquet.inexistant")) == 0


# --------------------------------------------------------------------- #
# Extraction générique d'un listing                                      #
# --------------------------------------------------------------------- #

def test_listing_extraction_finds_products_with_image_and_price():
    products = extract_products(
        LISTING_HTML, "https://boutique.test/cat/tcg", ("/p/",)
    )
    assert len(products) == 2  # doublon écarté, lien d'aide filtré

    upc = products[0]
    assert upc.url == "https://boutique.test/p/pokemon-30-ans-upc.html"
    assert upc.title == "Pokémon 30 Ans UPC"      # récupéré depuis l'attribut alt
    assert upc.image_url == "https://boutique.test/img/upc.jpg"
    assert upc.price == "189,99 €"


def test_listing_extraction_without_pattern_takes_everything():
    products = extract_products(LISTING_HTML, "https://boutique.test/cat/tcg")
    assert len(products) >= 3


def test_listing_extraction_ignores_pages_without_links():
    assert extract_products("<html><body><p>Rien</p></body></html>", "https://x.fr") == []


# --------------------------------------------------------------------- #
# Sitemap                                                                #
# --------------------------------------------------------------------- #

@asyncio_test
async def test_sitemap_follows_robots_and_index():
    ctx = context({
        "https://boutique.test/robots.txt": ROBOTS,
        "https://boutique.test/sitemap_index.xml": SITEMAP_INDEX,
        "https://boutique.test/sitemap-products.xml": SITEMAP_PRODUCTS,
    })
    urls = await sitemap_product_urls(ctx, "https://boutique.test", ("/p/",))
    assert urls == [
        "https://boutique.test/p/pokemon-upc.html",
        "https://boutique.test/p/manette.html",
    ]


@asyncio_test
async def test_sitemap_falls_back_when_robots_missing():
    ctx = context({"https://boutique.test/sitemap.xml": SITEMAP_PRODUCTS})
    urls = await sitemap_product_urls(ctx, "https://boutique.test", ("/p/",))
    assert len(urls) == 2


@asyncio_test
async def test_sitemap_returns_empty_when_unreachable():
    assert await sitemap_product_urls(context({}), "https://boutique.test") == []


# --------------------------------------------------------------------- #
# Plugin Micromania                                                      #
# --------------------------------------------------------------------- #

@asyncio_test
async def test_micromania_plugin_uses_sitemap_by_default():
    from plugins.micromania.discovery import MicromaniaDiscovery

    ctx = context({
        "https://www.micromania.fr/robots.txt": ROBOTS,
        "https://boutique.test/sitemap_index.xml": SITEMAP_INDEX,
        "https://boutique.test/sitemap-products.xml": SITEMAP_PRODUCTS,
    })
    result = await MicromaniaDiscovery().scan(ctx)
    titles = [product.title for product in result.products]
    assert "Pokemon Upc" in titles      # titre déduit du slug
    assert result.sources_scanned == 1


@asyncio_test
async def test_micromania_plugin_reports_no_source_configured():
    from plugins.micromania.discovery import MicromaniaDiscovery

    ctx = context({})
    ctx.options = {"use_sitemap": False, "listing_urls": []}
    result = await MicromaniaDiscovery().scan(ctx)
    assert not result.complete
    assert result.products == ()
