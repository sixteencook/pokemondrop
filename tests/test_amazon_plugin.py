"""Plugin Amazon : URL, états, buy box, identité, recherche.

Aucun appel réseau : les pages sont des extraits représentatifs du HTML
réellement servi par Amazon.
"""

import httpx
import pytest

from plugins.amazon import AmazonState, canonical_url, extract_asin
from plugins.amazon.discovery import AmazonDiscovery
from plugins.amazon.identity import AmazonIdentityStrategy
from plugins.amazon.monitor import AmazonMonitor
from src.discovery.contracts import DiscoveryContext
from src.intelligence.identity import ProductIdentity
from src.intelligence.keys import SearchKey
from src.intelligence.strategies import IdentityContext
from src.models import Availability
from tests.helpers import make_product

asyncio_test = pytest.mark.asyncio

ASIN = "B0H3PRH89L"
EAN = "4006381333931"
UPC = "036000291452"


def page(body: str, extra: str = "") -> str:
    return f"""
    <html lang="fr"><head><title>Amazon.fr</title>{extra}</head><body>
      <span id="productTitle">Pokémon 30 Ans Ultra Premium Collection</span>
      {body}
      <p>{"Description du produit. " * 30}</p>
    </body></html>
    """


@pytest.fixture
def monitor() -> AmazonMonitor:
    return AmazonMonitor(httpx.AsyncClient())


@pytest.fixture
def product():
    return make_product(site="amazon", url=f"https://www.amazon.fr/dp/{ASIN}")


# --------------------------------------------------------------------- #
# URL : toutes les formes ramenées à la forme canonique                  #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("url", [
    f"https://www.amazon.fr/dp/{ASIN}",
    f"https://www.amazon.fr/gp/product/{ASIN}",
    f"https://www.amazon.fr/gp/aw/d/{ASIN}",
    f"https://www.amazon.fr/Pokemon-Ultra-Premium/dp/{ASIN}/ref=sr_1_3?keywords=upc",
    f"https://www.amazon.fr/dp/{ASIN}?tag=affilie-21&linkCode=ogi",
    f"https://www.amazon.fr/dp/{ASIN}/?utm_source=newsletter&utm_medium=mail",
    f"https://amazon.fr/dp/{ASIN}#customerReviews",
])
def test_every_url_form_is_canonicalised(url):
    assert extract_asin(url) == ASIN
    assert canonical_url(url).endswith(f"/dp/{ASIN}")
    assert "?" not in canonical_url(url)


def test_lowercase_asin_is_uppercased():
    assert extract_asin(f"https://www.amazon.fr/dp/{ASIN.lower()}") == ASIN


def test_asin_from_query_parameter():
    assert extract_asin(f"https://www.amazon.fr/gp/offer-listing?asin={ASIN}") == ASIN


def test_url_without_asin_is_left_usable():
    url = "https://www.amazon.fr/s?k=pokemon"
    assert extract_asin(url) is None
    assert canonical_url(url).startswith("https://www.amazon.fr/s")


def test_monitor_normalises_the_url_before_checking(monitor):
    """Un lien affilié ne doit pas créer une seconde surveillance."""
    messy = f"https://www.amazon.fr/Pokemon/dp/{ASIN}/ref=sr_1_1?tag=aff-21"
    assert canonical_url(messy) == f"https://www.amazon.fr/dp/{ASIN}"


# --------------------------------------------------------------------- #
# États Amazon                                                           #
# --------------------------------------------------------------------- #

def test_available_with_add_to_cart(monitor, product):
    html = page("""
      <div id="availability"><span>En stock.</span></div>
      <input id="add-to-cart-button" type="submit" value="Ajouter au panier">
    """)
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.IN_STOCK
    assert snapshot.details["etat_amazon"] == AmazonState.AVAILABLE.value


def test_buy_now_alone_is_enough(monitor, product):
    html = page('<input id="buy-now-button" type="submit" value="Acheter maintenant">')
    assert monitor.parse(html, product).availability is Availability.IN_STOCK


def test_invitation_request(monitor, product):
    html = page("""
      <div id="availability"><span>Demande d'invitation</span></div>
      <span>Demander une invitation</span>
    """)
    snapshot = monitor.parse(html, product)
    assert snapshot.details["etat_amazon"] == AmazonState.INVITATION.value
    assert snapshot.status_text == "demande d'invitation"
    # Signal actionnable pour un drop : traité comme une précommande.
    assert snapshot.availability is Availability.PREORDER


def test_preorder(monitor, product):
    html = page('<input id="add-to-cart-button" value="Précommander">')
    snapshot = monitor.parse(html, product)
    assert snapshot.details["etat_amazon"] == AmazonState.PREORDER.value
    assert snapshot.availability is Availability.PREORDER


def test_coming_soon(monitor, product):
    html = page('<div id="availability"><span>Bientôt disponible</span></div>')
    snapshot = monitor.parse(html, product)
    assert snapshot.details["etat_amazon"] == AmazonState.COMING_SOON.value
    assert snapshot.availability is Availability.UNAVAILABLE


@pytest.mark.parametrize("text", [
    "Temporairement en rupture de stock",
    "Actuellement indisponible.",
    "Rupture de stock",
    "Currently unavailable",
])
def test_out_of_stock_variants(monitor, product, text):
    html = page(f'<div id="availability"><span>{text}</span></div>')
    snapshot = monitor.parse(html, product)
    assert snapshot.details["etat_amazon"] == AmazonState.OUT_OF_STOCK.value
    assert snapshot.availability is Availability.UNAVAILABLE


def test_out_of_stock_wins_over_a_residual_button(monitor, product):
    """Amazon laisse parfois le bouton en place sur une fiche en rupture."""
    html = page("""
      <div id="availability"><span>Temporairement en rupture de stock</span></div>
      <input id="add-to-cart-button" value="Ajouter au panier">
    """)
    assert monitor.parse(html, product).availability is Availability.UNAVAILABLE


def test_case_accents_and_spacing_are_ignored(monitor, product):
    html = page(
        '<div id="availability"><span>TEMPORAIREMENT  EN\xa0RUPTURE DE STOCK</span></div>'
    )
    assert monitor.parse(html, product).availability is Availability.UNAVAILABLE


def test_bot_wall_never_concludes(monitor, product):
    """Une page d'interception ne doit jamais passer pour une rupture."""
    html = (
        "<html><body>Saisissez les caractères que vous voyez ci-dessous"
        "</body></html>"
    )
    snapshot = monitor.parse(html, product)
    assert snapshot.availability is Availability.UNKNOWN
    assert snapshot.status_text == "page d'interception"


# --------------------------------------------------------------------- #
# Buy box                                                                #
# --------------------------------------------------------------------- #

def test_buy_box_details_are_extracted(monitor, product):
    html = page("""
      <div id="corePrice_feature_div"><span class="a-offscreen">189,99 €</span></div>
      <div id="availability"><span>Il ne reste plus que 3 exemplaire(s)</span></div>
      <div id="merchant-info">Vendu par Amazon.fr  Expédié par Amazon.fr</div>
      <input id="add-to-cart-button" value="Ajouter au panier">
    """)
    snapshot = monitor.parse(html, product)

    assert snapshot.price == "189,99 €"
    assert snapshot.details["devise"] == "EUR"
    assert snapshot.details["vendeur"].startswith("Amazon")
    assert snapshot.details["expedie_par"].startswith("Amazon")
    assert snapshot.details["buy_box"] == "oui"
    assert "3 exemplaire" in snapshot.details["stock"]
    assert snapshot.details["asin"] == ASIN


def test_price_with_thousands_separator(monitor, product):
    html = page(
        '<div id="corePrice_feature_div"><span class="a-offscreen">1 234,56 €</span></div>'
        '<input id="add-to-cart-button" value="Ajouter au panier">'
    )
    assert monitor.parse(html, product).price == "1234,56 €"


SELLER_TEMPLATE = """
  <div id="corePrice_feature_div"><span class="a-offscreen">189,99 €</span></div>
  <div id="merchant-info">Vendu par {seller}</div>
  <input id="add-to-cart-button" value="Ajouter au panier">
"""


def test_seller_rotation_within_the_same_kind_is_silent(monitor, product):
    """Deux revendeurs tiers différents : même état, donc aucune alerte."""
    first = monitor.parse(page(SELLER_TEMPLATE.format(seller="Boutique A")), product)
    second = monitor.parse(page(SELLER_TEMPLATE.format(seller="Boutique B")), product)

    assert first.content_hash == second.content_hash
    assert first.details["vendeur"] != second.details["vendeur"]
    assert first.details["etat_amazon"] == AmazonState.THIRD_PARTY_ONLY.value


def test_amazon_replaced_by_a_reseller_is_a_real_change(monitor, product):
    """Amazon qui laisse la place à un revendeur EST un changement réel.

    Ce n'est pas un faux positif : c'est exactement ce que la demande
    « Amazon absent mais revendeur présent » cherche à faire remonter.
    """
    by_amazon = monitor.parse(page(SELLER_TEMPLATE.format(seller="Amazon.fr")), product)
    by_reseller = monitor.parse(
        page(SELLER_TEMPLATE.format(seller="Boutique Tierce")), product
    )

    assert by_amazon.details["etat_amazon"] == AmazonState.AVAILABLE.value
    assert by_reseller.details["etat_amazon"] == AmazonState.THIRD_PARTY_ONLY.value
    assert by_amazon.content_hash != by_reseller.content_hash


def test_price_change_does_change_the_hash(monitor, product):
    def build(price: str) -> str:
        return page(
            f'<div id="corePrice_feature_div"><span class="a-offscreen">{price}</span></div>'
            '<input id="add-to-cart-button" value="Ajouter au panier">'
        )

    assert (monitor.parse(build("189,99 €"), product).content_hash
            != monitor.parse(build("179,99 €"), product).content_hash)


# --------------------------------------------------------------------- #
# Identité                                                               #
# --------------------------------------------------------------------- #

@asyncio_test
async def test_identity_extracts_amazon_details():
    html = f"""
    <html><body>
      <div id="detailBullets_feature_div"><ul>
        <li><span>UPC :</span><span>{UPC}</span></li>
        <li><span>Numéro du modèle :</span><span>10-10410-102</span></li>
        <li><span>Fabricant :</span><span>The Pokémon Company</span></li>
        <li><span>Date de sortie :</span><span>21/08/2026</span></li>
      </ul></div>
    </body></html>
    """
    identity = await AmazonIdentityStrategy().enrich(
        ProductIdentity(),
        IdentityContext(site="amazon", url=f"https://www.amazon.fr/dp/{ASIN}",
                        html=html),
    )
    assert identity.asin == ASIN
    assert identity.upc == UPC
    assert identity.ean == "0" + UPC          # UPC-A normalisé en EAN-13
    assert identity.model_number == "10-10410-102"
    assert identity.manufacturer == "The Pokémon Company"
    assert identity.release_date == "2026-08-21"


@asyncio_test
async def test_identity_reads_the_table_layout():
    html = f"""
    <html><body><table>
      <tr><th>EAN</th><td>{EAN}</td></tr>
      <tr><th>Marque</th><td>Pokémon</td></tr>
    </table></body></html>
    """
    identity = await AmazonIdentityStrategy().enrich(
        ProductIdentity(), IdentityContext(site="amazon", html=html)
    )
    assert identity.ean == EAN
    assert identity.brand == "Pokémon"


@asyncio_test
async def test_identity_finds_asin_in_html_when_url_has_none():
    identity = await AmazonIdentityStrategy().enrich(
        ProductIdentity(),
        IdentityContext(site="amazon", url="https://www.amazon.fr/s?k=x",
                        html=f'<script>var data = {{"ASIN":"{ASIN}"}};</script>'),
    )
    assert identity.asin == ASIN


@asyncio_test
async def test_identity_strategy_ignores_other_sites():
    identity = await AmazonIdentityStrategy().enrich(
        ProductIdentity(),
        IdentityContext(site="micromania", url="https://micromania.fr/p/x"),
    )
    assert identity is None


@asyncio_test
async def test_identity_never_overwrites_a_better_source():
    known = ProductIdentity().with_field("brand", "Pokémon", 100, "json-ld")
    identity = await AmazonIdentityStrategy().enrich(
        known,
        IdentityContext(site="amazon", url=f"https://www.amazon.fr/dp/{ASIN}",
                        html="<table><tr><th>Marque</th><td>Autre</td></tr></table>"),
    )
    assert identity.brand == "Pokémon"


# --------------------------------------------------------------------- #
# Recherche pilotée par l'identité                                       #
# --------------------------------------------------------------------- #

SEARCH_RESULTS = f"""
<html><body>
  <div class="s-result-item">
    <a href="/Pokemon-Ultra-Premium/dp/{ASIN}/ref=sr_1_1">
      <img src="https://m.media-amazon.com/upc.jpg"
           alt="Pokémon 30 Ans Ultra Premium Collection">
    </a>
    <span class="a-price"><span class="a-offscreen">189,99 €</span></span>
  </div>
  <div class="s-result-item">
    <a href="/Manette/dp/B00MANETTE1/ref=sr_1_2">Manette sans fil</a>
  </div>
  <p>{"Résultats de recherche Amazon. " * 20}</p>
</body></html>
"""

PRODUCT_PAGE = f"""
<html><body>
  <span id="productTitle">Pokémon 30 Ans Ultra Premium Collection</span>
  <div id="corePrice_feature_div"><span class="a-offscreen">189,99 €</span></div>
  <input id="add-to-cart-button" value="Ajouter au panier">
  <p>{"Description. " * 40}</p>
</body></html>
"""


def context(routes: dict[str, str], options: dict | None = None) -> DiscoveryContext:
    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, body in routes.items():
            if fragment in str(request.url):
                return httpx.Response(200, text=body)
        return httpx.Response(404, text="")

    return DiscoveryContext(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        options=options or {},
    )


@asyncio_test
async def test_search_by_asin_goes_straight_to_the_canonical_page():
    ctx = context({f"/dp/{ASIN}": PRODUCT_PAGE})
    candidates = await AmazonDiscovery().search(
        ProductIdentity.build(asin=ASIN), ctx, SearchKey("asin", ASIN, 93)
    )
    assert len(candidates) == 1
    assert candidates[0].url == f"https://www.amazon.fr/dp/{ASIN}"
    assert candidates[0].confidence == 93
    assert candidates[0].matched_fields == ("asin",)
    assert candidates[0].identity_hints.asin == ASIN


@asyncio_test
async def test_search_by_ean_uses_the_search_page():
    ctx = context({"/s?k=": SEARCH_RESULTS})
    candidates = await AmazonDiscovery().search(
        ProductIdentity.build(ean=EAN,
                              canonical_name="Pokémon 30 Ans Ultra Premium Collection"),
        ctx, SearchKey("ean", EAN, 100),
    )
    assert candidates
    assert candidates[0].confidence == 100          # clé forte, non pondérée
    assert candidates[0].url.endswith(f"/dp/{ASIN}")


@asyncio_test
async def test_text_search_is_weighted_by_name_similarity():
    ctx = context({"/s?k=": SEARCH_RESULTS})
    candidates = await AmazonDiscovery().search(
        ProductIdentity.build(canonical_name="Pokémon 30 Ans Ultra Premium Collection"),
        ctx, SearchKey("canonical_name", "Pokémon 30 Ans UPC", 70),
    )
    titles = [candidate.title for candidate in candidates]
    assert any("Ultra Premium" in title for title in titles)
    # L'article sans rapport est écarté par le seuil de confiance.
    assert not any("Manette" in title for title in titles)


@asyncio_test
async def test_intercepted_search_returns_nothing_rather_than_noise():
    ctx = context({"/s?k=": "<html><body>Saisissez les caractères</body></html>"})
    candidates = await AmazonDiscovery().search(
        ProductIdentity.build(ean=EAN), ctx, SearchKey("ean", EAN, 100)
    )
    assert candidates == []


@asyncio_test
async def test_unreachable_page_returns_nothing():
    ctx = context({})
    assert await AmazonDiscovery().search(
        ProductIdentity.build(asin=ASIN), ctx, SearchKey("asin", ASIN, 93)
    ) == []


@asyncio_test
async def test_scan_without_configuration_abstains():
    result = await AmazonDiscovery().scan(context({}))
    assert result.products == ()
    assert not result.complete


@asyncio_test
async def test_scan_reads_configured_listings():
    ctx = context({"/gp/new-releases": SEARCH_RESULTS},
                  {"listing_urls": ["https://www.amazon.fr/gp/new-releases"]})
    result = await AmazonDiscovery().scan(ctx)
    assert any(f"/dp/{ASIN}" in product.url for product in result.products)


# --------------------------------------------------------------------- #
# Intégration au cœur                                                    #
# --------------------------------------------------------------------- #

def test_plugin_is_auto_discovered_everywhere():
    from src.discovery.loader import discover_discovery_plugins
    from src.intelligence import discover_identity_strategies
    from src.monitors import create_registry

    registry = create_registry(httpx.AsyncClient())
    assert "amazon" in registry.known_sites
    assert registry.get("amazon").display_name == "Amazon"

    metadata = registry.get_metadata("amazon")
    assert metadata is not None and metadata.version == "1.0.0"

    assert "amazon" in discover_discovery_plugins().sites
    assert any("amazon_details" in name
               for name in discover_identity_strategies().names)


def test_amazon_does_not_use_the_generic_parser(monitor, product):
    """Le monitor Amazon a son propre parser, pas celui du plugin generic."""
    from src.monitors.generic import GenericHtmlMonitor

    assert not isinstance(monitor, GenericHtmlMonitor)
