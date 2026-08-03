"""Localisation Amazon et traçabilité de la décision.

Cas réel à l'origine de ces tests : l'ASIN B0H3PRH89L affichait
« Demande d'invitation » dans le navigateur de l'utilisateur, tandis que
le moteur lisait « Livraison : États-Unis / Actuellement indisponible ».
Les deux analysaient bien la même URL — mais pas la même version de la
page, faute de préférence de langue et de pays de livraison.

Aucun appel réseau : les pages sont des extraits représentatifs du HTML
réellement servi par Amazon.
"""

import httpx
import pytest

from plugins.amazon import marketplace
from plugins.amazon.monitor import AmazonMonitor
from plugins.amazon.parser import AmazonState, analyse
from src.models import Availability
from src.monitors.renderer import RenderError
from tests.helpers import make_product

ASIN = "B0H3PRH89L"
URL = f"https://www.amazon.fr/dp/{ASIN}"

FILLER = "<p>" + "Description du produit Pokémon. " * 30 + "</p>"


def page(body: str, glow: str = "", lang: str = "fr-FR") -> str:
    """Fiche minimale, avec ou sans bandeau « Livrer à … »."""
    banner = (
        f'<div id="nav-global-location-slot">'
        f'<span id="glow-ingress-line2">{glow}</span></div>'
        if glow else ""
    )
    return f"""
    <html lang="{lang}"><head><title>Amazon.fr</title></head><body>
      {banner}
      <span id="productTitle">Pokémon 30 Ans Ultra Premium Collection</span>
      {body}
      {FILLER}
    </body></html>
    """


@pytest.fixture
def monitor() -> AmazonMonitor:
    return AmazonMonitor(httpx.AsyncClient())


@pytest.fixture
def product():
    return make_product(site="amazon", url=URL)


# --------------------------------------------------------------------- #
# Ce que l'on demande : Amazon.fr, français, livraison France            #
# --------------------------------------------------------------------- #

def test_preference_targets_france_by_default():
    preference = marketplace.preference_for(URL)

    assert preference.marketplace is marketplace.PREFERRED
    assert "language=fr_FR" in preference.url
    assert preference.cookies["lc-acbfr"] == "fr_FR"
    assert preference.cookies["i18n-prefs"] == "EUR"
    assert preference.headers["Accept-Language"].startswith("fr-FR")
    assert preference.browser_locale == "fr-FR"
    assert preference.timezone == "Europe/Paris"


def test_url_without_host_falls_back_to_amazon_fr():
    assert marketplace.preference_for(f"/dp/{ASIN}").marketplace is marketplace.PREFERRED


def test_foreign_marketplace_is_respected_not_rewritten():
    """Réécrire amazon.de en amazon.fr inventerait une fiche."""
    preference = marketplace.preference_for(f"https://www.amazon.de/dp/{ASIN}")

    assert preference.marketplace.domain == "amazon.de"
    assert "amazon.de" in preference.url
    assert preference.cookies["lc-acbde"] == "de_DE"
    assert "amazon.fr" in preference.note        # le choix est explicité


def test_existing_language_parameter_is_not_overwritten():
    url = f"{URL}?language=en_GB"
    assert "language=en_GB" in marketplace.preference_for(url).url


def test_monitor_request_plan_carries_the_localisation(monitor):
    plan = monitor.prepare_request(URL)

    assert "language=fr_FR" in plan.url
    assert plan.cookies["lc-acbfr"] == "fr_FR"
    assert plan.locale == "fr-FR"
    assert plan.timezone == "Europe/Paris"
    # Les en-têtes standard du cœur restent en place.
    assert "User-Agent" in plan.headers
    assert plan.headers["Accept-Language"].startswith("fr-FR")


# --------------------------------------------------------------------- #
# Ce que l'on constate : la page a-t-elle été servie pour la France ?    #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("label,expected", [
    ("France", "FR"),
    ("Livrer à France", "FR"),
    ("Livraison : France", "FR"),
    ("75001 Paris", "FR"),
    ("États-Unis", "US"),
    ("Deliver to United States", "US"),
    ("Livraison : Belgique", "BE"),
    ("Allemagne", "DE"),
])
def test_delivery_country_is_read_from_the_glow(label, expected):
    assert marketplace.country_from_label(label) == expected


def test_unknown_destination_is_not_guessed():
    assert marketplace.country_from_label("Choisir le lieu de livraison") is None


def test_language_comes_from_the_html_tag(monitor, product):
    analysis = analyse(page("", glow="France"), url=URL)
    assert analysis.locale.language == "fr_FR"
    assert analysis.locale.language_source == "<html lang>"


def test_locale_details_reach_the_snapshot(monitor, product):
    html = page(
        '<input id="add-to-cart-button" type="submit" value="Ajouter au panier">',
        glow="Livrer à France",
    )
    details = monitor.parse(html, product).details

    assert details["marketplace"] == "amazon.fr"
    assert details["pays_livraison"] == "FR"
    assert details["langue"] == "fr_FR"
    assert details["livraison_selecteur"] == "#glow-ingress-line2"
    assert "livraison souhaitée France" in details["localisation_demandee"]


# --------------------------------------------------------------------- #
# Garde-fou : ne pas conclure « indisponible » depuis un autre pays      #
# --------------------------------------------------------------------- #

OUT_OF_STOCK_BLOCK = (
    '<div id="desktop_buybox">'
    '<div id="availability"><span>Actuellement indisponible.</span></div>'
    '</div>'
)


def test_unavailable_from_the_united_states_is_refused(monitor, product):
    """Le cas exact du bug : la destination fabrique l'indisponibilité."""
    snapshot = monitor.parse(
        page(OUT_OF_STOCK_BLOCK, glow="Livraison : États-Unis"), product
    )

    assert snapshot.availability is Availability.UNKNOWN
    assert snapshot.details["etat_amazon"] == AmazonState.UNKNOWN.value
    assert snapshot.details["pays_livraison"] == "US"
    assert "US" in snapshot.details["declasse"]
    assert "livraison" in snapshot.details["decision"].lower()


def test_unavailable_from_france_is_trusted(monitor, product):
    snapshot = monitor.parse(page(OUT_OF_STOCK_BLOCK, glow="France"), product)

    assert snapshot.availability is Availability.UNAVAILABLE
    assert snapshot.details["etat_amazon"] == AmazonState.OUT_OF_STOCK.value


def test_unknown_destination_does_not_paralyse_the_monitoring(monitor, product):
    """Sans bandeau de livraison, l'analyse reste valable.

    Transformer l'absence d'information en refus de conclure ferait taire
    la surveillance sur toutes les pages allégées.
    """
    snapshot = monitor.parse(page(OUT_OF_STOCK_BLOCK), product)
    assert snapshot.availability is Availability.UNAVAILABLE


def test_no_state_survives_a_foreign_destination(monitor, product):
    """Hors contexte France, AUCUN état n'est retenu — pas même positif.

    L'offre proposée à une autre destination n'est pas celle que voit
    l'utilisateur : annoncer une invitation ouverte serait aussi faux
    qu'annoncer une rupture.
    """
    html = page(
        '<div id="desktop_buybox">'
        '<div id="availability"><span>Demande d\'invitation</span></div></div>',
        glow="Livraison : États-Unis",
    )
    snapshot = monitor.parse(html, product)

    assert snapshot.details["etat_amazon"] == AmazonState.UNKNOWN.value
    assert snapshot.availability is Availability.UNKNOWN
    assert not snapshot.conclusive
    assert "US" in snapshot.details["declasse"]


def test_the_guard_can_be_switched_off(product):
    class Permissive(AmazonMonitor):
        enforce_delivery_country = False

    snapshot = Permissive(httpx.AsyncClient()).parse(
        page(OUT_OF_STOCK_BLOCK, glow="États-Unis"), product
    )
    assert snapshot.availability is Availability.UNAVAILABLE


# --------------------------------------------------------------------- #
# Quel sélecteur a décidé ?                                              #
# --------------------------------------------------------------------- #

def test_decisive_selector_is_the_availability_block(monitor, product):
    snapshot = monitor.parse(page(OUT_OF_STOCK_BLOCK, glow="France"), product)

    assert snapshot.details["selecteur_decisif"] == "#availability"
    assert snapshot.details["origine_decision"] == "bloc disponibilité"


def test_decisive_selector_is_the_buy_button(monitor, product):
    html = page(
        '<div id="desktop_buybox">'
        '<input id="add-to-cart-button" type="submit" value="Ajouter au panier">'
        '</div>',
        glow="France",
    )
    snapshot = monitor.parse(html, product)

    assert snapshot.details["selecteur_decisif"] == "#add-to-cart-button"
    assert snapshot.details["origine_decision"] == "contrôle d'achat"


def test_decisive_selector_points_at_the_exact_element(monitor, product):
    """Cas réel : sur B0H3PRH89L le libellé vit dans un `span` annexe.

    Aucune règle ne cite ce sélecteur ; le mot-clé est donc trouvé dans le
    texte du périmètre. Annoncer « div#desktop_buybox » n'aiderait
    personne — c'est l'élément porteur qui doit être nommé.
    """
    html = page(
        '<div id="desktop_buybox">'
        '<div id="corePrice_feature_div"><span class="a-offscreen">19,99 €</span></div>'
        '<div id="availability"><span>Rejoignez la file d\'attente</span></div>'
        '<span id="hdp-invite-button-announce">Demander une invitation</span>'
        '</div>',
        glow="France",
    )
    analysis = analyse(html, url=URL)

    assert analysis.state is AmazonState.INVITATION
    assert analysis.evidence.selector == "#hdp-invite-button-announce"
    assert analysis.evidence.origin == "contrôle d'achat"
    assert analysis.evidence.excerpt == "Demander une invitation"


def test_a_page_without_any_clue_names_no_selector(monitor, product):
    snapshot = monitor.parse(page("<div>Rien d'exploitable ici.</div>"), product)
    assert snapshot.details["selecteur_decisif"] == "—"


# --------------------------------------------------------------------- #
# Quel bloc d'achat a été retenu, et pourquoi ?                          #
# --------------------------------------------------------------------- #

def test_the_retained_buy_block_is_named_with_its_reason(monitor, product):
    html = page(
        '<div id="buybox"><span>Autres vendeurs sur Amazon</span></div>'
        '<div id="desktop_buybox">'
        '<input id="add-to-cart-button" type="submit" value="Ajouter au panier">'
        '</div>',
        glow="France",
    )
    analysis = analyse(html, url=URL)

    retained = analysis.retained_scope
    assert retained is not None
    assert retained.identifier == "div#desktop_buybox"
    assert "#add-to-cart-button" in retained.reason

    rejected = [c for c in analysis.scope_candidates if not c.retained]
    assert any(c.identifier == "div#buybox" for c in rejected)
    assert any("ni bouton d'achat" in c.reason for c in rejected)
    assert sum(1 for c in analysis.scope_candidates if c.retained) == 1


def test_the_retained_block_reaches_the_snapshot(monitor, product):
    html = page(
        '<div id="desktop_buybox">'
        '<div id="availability"><span>En stock.</span></div></div>',
        glow="France",
    )
    details = monitor.parse(html, product).details

    assert details["bloc_achat"] == "div#desktop_buybox"
    assert details["blocs_achat_examines"] == "1"


def test_without_any_buy_block_the_whole_page_is_used(monitor, product):
    analysis = analyse(page('<button>Précommander</button>'), url=URL)

    assert analysis.scope == "page entière"
    assert analysis.retained_scope.reason.startswith("aucun bloc d'achat")


# --------------------------------------------------------------------- #
# Le bouton « Demande d'invitation » : présent, mais ignoré ?            #
# --------------------------------------------------------------------- #

def test_invitation_absent_is_stated_as_such(monitor, product):
    details = monitor.parse(page(OUT_OF_STOCK_BLOCK, glow="France"), product).details

    assert details["invitation_dom"] == "non"
    assert details["invitation_motif"] == "absent du DOM"


def test_invitation_used_is_stated_as_such(monitor, product):
    html = page('<div id="availability"><span>Demande d\'invitation</span></div>',
                glow="France")
    details = monitor.parse(html, product).details

    assert details["invitation_dom"] == "oui"
    assert "retenu" in details["invitation_motif"]


def test_invitation_removed_with_the_noise_is_explained(monitor, product):
    """Une invitation venue d'un carrousel ne doit pas décider — mais doit
    se voir dans le diagnostic."""
    html = page(
        '<div class="carousel"><span>Demander une invitation</span></div>'
        + OUT_OF_STOCK_BLOCK,
        glow="France",
    )
    analysis = analyse(html, url=URL)

    assert analysis.invitation.present
    assert not analysis.invitation.survived_noise
    assert not analysis.invitation.used
    assert "bruit" in analysis.invitation.reason
    assert analysis.state is AmazonState.OUT_OF_STOCK


def test_invitation_outside_the_retained_scope_is_explained(monitor, product):
    html = page(
        OUT_OF_STOCK_BLOCK
        + '<div id="autre-bloc"><span>Demander une invitation</span></div>',
        glow="France",
    )
    analysis = analyse(html, url=URL)

    assert analysis.invitation.present
    assert analysis.invitation.survived_noise
    assert not analysis.invitation.in_scope
    assert "hors du périmètre" in analysis.invitation.reason
    assert "div#autre-bloc" in " ".join(analysis.invitation.locations)


def test_invitation_in_an_aria_label_is_located(monitor, product):
    html = page(
        '<div id="desktop_buybox">'
        '<div id="corePrice_feature_div"><span class="a-offscreen">189,99 €</span></div>'
        '<button aria-label="Demander une invitation" id="invite">Participer</button>'
        '</div>',
        glow="France",
    )
    analysis = analyse(html, url=URL)

    assert analysis.invitation.present
    assert analysis.invitation.used
    assert analysis.state is AmazonState.INVITATION
    assert analysis.evidence.selector == "button#invite"
    assert analysis.evidence.excerpt == "Demander une invitation"


# --------------------------------------------------------------------- #
# La localisation atteint bien le navigateur en cas d'escalade           #
# --------------------------------------------------------------------- #

class RecordingRenderer:
    """Renderer factice qui enregistre la localisation demandée."""

    def __init__(self, html: str) -> None:
        self._html = html
        self.cookies: dict[str, str] = {}
        self.locale = None
        self.timezone = None
        self.urls: list[str] = []

    @property
    def available(self) -> bool:
        return True

    async def render(self, url, cookie_selectors=(), *, cookies=None,
                     locale=None, timezone=None) -> str:
        self.urls.append(url)
        self.cookies = dict(cookies or {})
        self.locale = locale
        self.timezone = timezone
        if self._html is None:
            raise RenderError("échec simulé")
        return self._html


@pytest.mark.asyncio
async def test_browser_escalation_keeps_the_french_localisation(product):
    """Un 403 ne doit pas faire perdre la langue ni la destination."""
    rendered = page(
        '<div id="desktop_buybox">'
        '<div id="availability"><span>Demande d\'invitation</span></div></div>',
        glow="Livrer à France",
    )
    renderer = RecordingRenderer(rendered)

    def handler(request: httpx.Request) -> httpx.Response:
        # La requête HTTP porte déjà la préférence.
        assert "language=fr_FR" in str(request.url)
        assert "lc-acbfr=fr_FR" in request.headers.get("cookie", "")
        return httpx.Response(403, text="<html><body>Forbidden</body></html>")

    monitor = AmazonMonitor(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), renderer
    )
    snapshot = await monitor.check(product)

    assert renderer.cookies["lc-acbfr"] == "fr_FR"
    assert renderer.cookies["i18n-prefs"] == "EUR"
    assert renderer.locale == "fr-FR"
    assert renderer.timezone == "Europe/Paris"
    assert "language=fr_FR" in renderer.urls[0]
    assert snapshot.availability is Availability.PREORDER
