"""Stratégies d'exploration réutilisables par les plugins.

Ce sont des OUTILS, pas des règles : le moteur ne les utilise jamais
directement. Un plugin compose celles qui conviennent à son site, ou
n'en utilise aucune s'il préfère une API maison, un flux RSS ou une
navigation Playwright.

Deux stratégies fournies :

  `sitemap_product_urls`  — suit le standard robots.txt → sitemap.xml.
                            Ne demande QUE l'URL racine du site : aucune
                            URL de fiche à connaître ni à inventer.

  `listing_products`      — analyse une page de catégorie et en extrait
                            les fiches (lien, titre, image, prix).

Toutes deux sont bornées (nombre d'URL, profondeur, taille) pour ne
jamais marteler un site ni saturer la mémoire.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from src.discovery.contracts import DiscoveredProduct, DiscoveryContext
from src.monitors.base import DEFAULT_HEADERS
from src.utils.logger import get_logger

log = get_logger("discovery.strategies")

_PRICE_RE = re.compile(r"(\d{1,4}[.,]\d{2})\s*€|€\s*(\d{1,4}[.,]\d{2})")
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_SITEMAP_IN_ROBOTS = re.compile(r"^\s*sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


async def _get(ctx: DiscoveryContext, url: str) -> str | None:
    """GET tolérant : retourne None au lieu de lever."""
    try:
        response = await ctx.client.get(
            url, headers=DEFAULT_HEADERS, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        log.check("Découverte : %s injoignable (%s)", url, exc.__class__.__name__)
        return None
    if response.status_code >= 400:
        log.check("Découverte : HTTP %s sur %s", response.status_code, url)
        return None
    return response.text


# --------------------------------------------------------------------- #
# Stratégie 1 : sitemap                                                  #
# --------------------------------------------------------------------- #

async def sitemap_product_urls(
    ctx: DiscoveryContext,
    base_url: str,
    url_patterns: Sequence[str] = (),
    max_sitemaps: int = 12,
    max_urls: int = 3000,
) -> list[str]:
    """URL de fiches produit déclarées par les sitemaps du site.

    `url_patterns` filtre les chemins ressemblant à des fiches produit
    (ex. « /p/ », « .html ») ; vide = tout est retenu.
    """
    roots = await _sitemap_roots(ctx, base_url)
    seen: set[str] = set()
    found: list[str] = []
    queue = list(roots)
    processed = 0

    while queue and processed < max_sitemaps and len(found) < max_urls:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)
        processed += 1

        body = await _get(ctx, sitemap_url)
        if not body:
            continue

        locations = _LOC_RE.findall(body)
        is_index = "<sitemapindex" in body[:2000].lower()
        for location in locations:
            if is_index or location.lower().endswith((".xml", ".xml.gz")):
                if len(queue) + processed < max_sitemaps:
                    queue.append(location)
            elif _matches_any(location, url_patterns):
                found.append(location)
                if len(found) >= max_urls:
                    break

    log.check(
        "Découverte : %d sitemap(s) lus sur %s → %d URL retenues.",
        processed, base_url, len(found),
    )
    return found


async def _sitemap_roots(ctx: DiscoveryContext, base_url: str) -> list[str]:
    """Sitemaps déclarés dans robots.txt, avec repli sur /sitemap.xml."""
    parts = urlsplit(base_url)
    root = f"{parts.scheme or 'https'}://{parts.netloc}"

    robots = await _get(ctx, f"{root}/robots.txt")
    if robots:
        declared = [match.strip() for match in _SITEMAP_IN_ROBOTS.findall(robots)]
        if declared:
            return declared
    return [f"{root}/sitemap.xml"]


def _matches_any(value: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    lowered = value.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


# --------------------------------------------------------------------- #
# Stratégie 2 : pages de listing                                         #
# --------------------------------------------------------------------- #

async def listing_products(
    ctx: DiscoveryContext,
    listing_url: str,
    url_patterns: Sequence[str] = (),
    use_browser: bool = False,
    max_products: int = 200,
) -> list[DiscoveredProduct]:
    """Fiches extraites d'une page de catégorie / nouveautés / précommandes.

    `use_browser` bascule sur le rendu Playwright pour les listings
    construits côté client (chargement progressif, filtres dynamiques).
    """
    html: str | None = None
    if use_browser and ctx.can_render:
        try:
            html = await ctx.renderer.render(listing_url)
        except Exception as exc:  # noqa: BLE001 — repli HTTP
            log.check("Rendu du listing %s impossible (%s) — repli HTTP.",
                      listing_url, exc)
    if html is None:
        html = await _get(ctx, listing_url)
    if not html:
        return []

    return extract_products(html, listing_url, url_patterns, max_products)


def extract_products(
    html: str,
    base_url: str,
    url_patterns: Sequence[str] = (),
    max_products: int = 200,
) -> list[DiscoveredProduct]:
    """Extraction générique de fiches depuis le HTML d'une page de listing.

    Ne suppose aucune structure : on part des liens, puis on remonte au
    conteneur le plus proche pour y chercher titre, image et prix.
    """
    soup = BeautifulSoup(html, "lxml")
    products: list[DiscoveredProduct] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"].strip())
        if not href.startswith("http") or not _matches_any(href, url_patterns):
            continue
        if href in seen:
            continue

        title = _best_title(anchor)
        if not title:
            continue

        seen.add(href)
        container = _container(anchor)
        products.append(DiscoveredProduct(
            url=href,
            title=title,
            image_url=_image(container, base_url),
            price=_price(container),
            source=base_url,
        ))
        if len(products) >= max_products:
            break

    return products


def _best_title(anchor) -> str:
    """Titre le plus parlant : texte du lien, sinon aria-label / title / alt."""
    for candidate in (
        anchor.get_text(" ", strip=True),
        anchor.get("aria-label"),
        anchor.get("title"),
    ):
        text = re.sub(r"\s+", " ", (candidate or "")).strip()
        if 3 <= len(text) <= 200:
            return text
    image = anchor.find("img")
    if image:
        alt = re.sub(r"\s+", " ", (image.get("alt") or "")).strip()
        if 3 <= len(alt) <= 200:
            return alt
    return ""


def _container(anchor):
    """Remonte de quelques niveaux pour englober la vignette du produit."""
    node = anchor
    for _ in range(3):
        if node.parent is None:
            break
        node = node.parent
    return node


def _image(container, base_url: str) -> str | None:
    image = container.find("img") if container else None
    if not image:
        return None
    for attribute in ("src", "data-src", "data-original", "data-lazy"):
        value = image.get(attribute)
        if value and not value.startswith("data:"):
            return urljoin(base_url, value.strip())
    srcset = image.get("srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first:
            return urljoin(base_url, first)
    return None


def _price(container) -> str | None:
    if container is None:
        return None
    match = _PRICE_RE.search(container.get_text(" ", strip=True))
    if not match:
        return None
    return f"{(match.group(1) or match.group(2)).replace('.', ',')} €"


def dedupe(products: Iterable[DiscoveredProduct]) -> list[DiscoveredProduct]:
    """Dédoublonnage par URL, ordre d'apparition conservé."""
    seen: set[str] = set()
    unique: list[DiscoveredProduct] = []
    for product in products:
        if product.url in seen:
            continue
        seen.add(product.url)
        unique.append(product)
    return unique
