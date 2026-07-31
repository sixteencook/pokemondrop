"""Découverte et recherche Amazon.

Le Product Intelligence Engine ne connaît JAMAIS d'URL Amazon : il fournit
une identité produit et une clé à essayer. C'est ce plugin — et lui seul —
qui décide comment chercher.

Stratégie par clé :

    asin            accès direct à la fiche canonique /dp/<ASIN>
    ean / upc / gtin recherche du code (Amazon indexe les codes-barres)
    mpn / modèle    recherche de la référence
    nom / alias     recherche texte, confiance ajustée par la proximité

Le résultat d'une recherche texte n'est jamais rendu tel quel : sa
confiance est pondérée par la ressemblance du titre, pour ne pas polluer
le catalogue avec des articles voisins.

⚠️ Amazon protège fortement ses pages de recherche. Ce plugin se contente
d'un accès ordinaire (HTTP, puis navigateur si nécessaire) et ne contourne
aucune protection : une page d'interception donne « aucun résultat », qui
sera simplement retenté plus tard par le moteur.
"""

from __future__ import annotations

from typing import ClassVar, Optional, Sequence
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from src.discovery.contracts import DiscoveredProduct, DiscoveryContext, ScanResult
from src.discovery.strategies import dedupe, sitemap_product_urls
from src.intelligence.candidates import OfferCandidate
from src.intelligence.identity import ProductIdentity
from src.intelligence.keys import KEY_PRIORITIES
from src.intelligence.naming import similarity
from src.monitors.base import DEFAULT_HEADERS
from src.utils.logger import get_logger

from . import keywords, parser
from .parser import ASIN_RE, canonical_url, extract_asin

log = get_logger("discovery.amazon")

#: Recherche Amazon : point d'entrée public et stable du site.
#: Surchargeable par site (amazon.de, .co.uk…) via config/discovery.yaml.
DEFAULT_SEARCH_TEMPLATE = "https://www.amazon.fr/s?k={query}"

#: Confiance minimale d'un résultat de recherche texte : en dessous, on
#: préfère ne rien remonter plutôt que de créer une offre douteuse.
MIN_TEXT_CONFIDENCE = 45


class AmazonDiscovery:
    """Explore Amazon et sait y retrouver un produit par n'importe quelle clé."""

    site_name: ClassVar[str] = "amazon"
    display_name: ClassVar[str] = "Amazon"

    # ------------------------------------------------------------------ #
    # Exploration périodique                                              #
    # ------------------------------------------------------------------ #

    async def scan(self, ctx: DiscoveryContext) -> ScanResult:
        """Balayage large : pages de listing configurées, puis sitemap.

        Amazon n'expose pas de sitemap produit exploitable en pratique :
        sans `listing_urls` configurées, le plugin le dit et s'abstient
        plutôt que de deviner des URL.
        """
        options = ctx.options
        listing_urls: Sequence[str] = tuple(options.get("listing_urls") or ())
        max_products = int(options.get("max_products", 200))

        if not listing_urls and not options.get("use_sitemap", False):
            log.check(
                "Découverte Amazon : aucune source configurée "
                "(listing_urls dans config/discovery.yaml)."
            )
            return ScanResult(complete=False, note="aucune source configurée")

        products: list[DiscoveredProduct] = []
        sources = 0
        complete = True

        for listing_url in listing_urls:
            html = await self._fetch(ctx, listing_url)
            sources += 1
            if not html:
                complete = False
                continue
            products.extend(self._products_from_html(html, listing_url))

        if options.get("use_sitemap", False):
            urls = await sitemap_product_urls(
                ctx, "https://www.amazon.fr", ("/dp/",), max_urls=max_products
            )
            sources += 1
            if not urls:
                complete = False
            products.extend(
                DiscoveredProduct(url=canonical_url(url), title=_fallback_title(url),
                                  source="sitemap")
                for url in urls
            )

        unique = dedupe(products)
        return ScanResult(products=unique[:max_products], complete=complete,
                          sources_scanned=sources)

    # ------------------------------------------------------------------ #
    # Recherche pilotée par l'identité                                    #
    # ------------------------------------------------------------------ #

    async def search(
        self, identity: ProductIdentity, ctx: DiscoveryContext, key=None
    ) -> list[OfferCandidate]:
        """Retrouve le produit sur Amazon avec la clé fournie."""
        kind = key.kind if key is not None else "canonical_name"
        value = key.value if key is not None else (identity.canonical_name or "")
        if not value:
            return []

        if kind == "asin":
            return await self._by_asin(ctx, value, identity)
        return await self._by_query(ctx, kind, value, identity)

    async def _by_asin(
        self, ctx: DiscoveryContext, asin: str, identity: ProductIdentity
    ) -> list[OfferCandidate]:
        """Accès direct : l'ASIN désigne une fiche unique et canonique."""
        url = canonical_url(f"https://www.amazon.fr/dp/{asin}")
        html = await self._fetch(ctx, url, allow_browser=True)
        if not html:
            return []

        analysis = parser.analyse(html)
        if analysis.bot_wall or not analysis.title:
            log.check("Amazon : fiche %s illisible (interception ou page vide).", asin)
            return []

        return [OfferCandidate(
            url=url, title=analysis.title, price=analysis.buy_box.price,
            availability=analysis.availability.value,
            confidence=KEY_PRIORITIES["asin"],
            matched_fields=("asin",),
            reason=f"ASIN {asin} — fiche canonique",
            identity_hints=ProductIdentity.build(source="amazon", asin=asin),
        )]

    async def _by_query(
        self, ctx: DiscoveryContext, kind: str, value: str, identity: ProductIdentity
    ) -> list[OfferCandidate]:
        """Recherche Amazon, puis pondération par la ressemblance du titre."""
        template = (ctx.options.get("search_url_template")
                    or DEFAULT_SEARCH_TEMPLATE)
        if "{query}" not in template:
            log.error("search_url_template Amazon invalide : {query} manquant.")
            return []

        url = template.replace("{query}", quote_plus(value))
        html = await self._fetch(ctx, url, allow_browser=True)
        if not html:
            return []

        if any(marker in html.lower() for marker in keywords.BOT_WALL):
            log.check("Amazon : recherche interceptée pour « %s ».", value)
            return []

        base_confidence = KEY_PRIORITIES.get(kind, 50)
        strong = base_confidence >= KEY_PRIORITIES["sku"]
        reference = identity.canonical_name or ""
        limit = int(ctx.options.get("max_search_results", 10))

        candidates: list[OfferCandidate] = []
        for found in self._products_from_html(html, url)[:limit]:
            confidence = base_confidence
            matched = [kind]
            reason = f"trouvé par recherche {kind}"

            if not strong:
                proximity = similarity(found.title, reference) if reference else 0.0
                confidence = int(base_confidence * max(0.0, proximity))
                matched.append("canonical_name")
                reason = f"recherche {kind}, nom proche à {proximity:.0%}"
                if confidence < MIN_TEXT_CONFIDENCE:
                    continue

            asin = extract_asin(found.url)
            candidates.append(OfferCandidate(
                url=found.url, title=found.title, price=found.price,
                image_url=found.image_url, confidence=confidence,
                matched_fields=tuple(matched), reason=reason,
                identity_hints=(
                    ProductIdentity.build(source="amazon", asin=asin)
                    if asin else ProductIdentity()
                ),
            ))
        return candidates

    # ------------------------------------------------------------------ #
    # Utilitaires                                                         #
    # ------------------------------------------------------------------ #

    async def _fetch(
        self, ctx: DiscoveryContext, url: str, allow_browser: bool = False
    ) -> Optional[str]:
        """HTTP d'abord ; navigateur seulement si la réponse est inutilisable.

        Chromium n'est jamais ouvert pour une page qui se lit très bien en
        HTTP : c'est la règle du projet, et Amazon la rend particulièrement
        rentable.
        """
        html: Optional[str] = None
        try:
            response = await ctx.client.get(
                url, headers=DEFAULT_HEADERS, follow_redirects=True
            )
            if response.status_code < 400:
                html = response.text
        except Exception as exc:  # noqa: BLE001 — repli navigateur
            log.check("Amazon : requête HTTP en échec (%s)", exc.__class__.__name__)

        if html and not _looks_blocked(html):
            return html

        if not (allow_browser and ctx.can_render):
            return html

        log.check("Amazon : réponse HTTP inexploitable — passage au navigateur.")
        try:
            return await ctx.renderer.render(url, AMAZON_COOKIE_SELECTORS)
        except Exception as exc:  # noqa: BLE001
            log.check("Amazon : rendu navigateur en échec (%s)", exc)
            return html

    def _products_from_html(self, html: str, base_url: str) -> list[DiscoveredProduct]:
        """Extrait les fiches d'une page de résultats ou de listing.

        On part des liens `/dp/` : c'est le seul point stable des pages
        Amazon, qui changent de structure très souvent.
        """
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:  # noqa: BLE001
            return []

        found: dict[str, DiscoveredProduct] = {}
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not ASIN_RE.search(href):
                continue
            url = canonical_url(
                href if href.startswith("http") else f"https://www.amazon.fr{href}"
            )
            if url in found:
                continue
            title = _title_near(anchor)
            if not title:
                continue
            found[url] = DiscoveredProduct(
                url=url, title=title, image_url=_image_near(anchor),
                price=_price_near(anchor), source=base_url,
            )
        return list(found.values())


#: Bandeaux de consentement Amazon, partagés avec le monitor.
AMAZON_COOKIE_SELECTORS: tuple[str, ...] = (
    "#sp-cc-accept",
    'input[name="accept"]',
    'button[data-cel-widget="sp-cc-accept"]',
)


def _looks_blocked(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in keywords.BOT_WALL) or len(html) < 800


def _title_near(anchor) -> str:
    """Titre du produit : texte du lien, sinon libellé voisin."""
    for candidate in (
        anchor.get_text(" ", strip=True),
        anchor.get("aria-label"),
        anchor.get("title"),
    ):
        text = " ".join((candidate or "").split())
        if 5 <= len(text) <= 250:
            return text
    image = anchor.find("img")
    if image:
        alt = " ".join((image.get("alt") or "").split())
        if 5 <= len(alt) <= 250:
            return alt
    return ""


def _container(anchor):
    node = anchor
    for _ in range(4):
        if node.parent is None:
            break
        node = node.parent
    return node


def _image_near(anchor) -> Optional[str]:
    container = _container(anchor)
    image = container.find("img") if container else None
    if not image:
        return None
    for attribute in ("src", "data-src", "data-image-latency"):
        value = image.get(attribute)
        if value and value.startswith("http"):
            return value
    return None


def _price_near(anchor) -> Optional[str]:
    container = _container(anchor)
    if container is None:
        return None
    tag = container.select_one(".a-price .a-offscreen, .a-color-price")
    if tag is None:
        return None
    from .parser import _parse_price

    price, _ = _parse_price(tag.get_text(" ", strip=True))
    return price


def _fallback_title(url: str) -> str:
    asin = extract_asin(url)
    return f"Fiche Amazon {asin}" if asin else url
