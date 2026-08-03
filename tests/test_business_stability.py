"""Stabilité métier : ce qui doit alerter, et surtout ce qui ne doit pas.

Objectif de la phase 1.0 : tourner plusieurs semaines sans une seule
fausse alerte. Chaque test ci-dessous prend une page réaliste, en modifie
**une seule chose**, et vérifie que le moteur réagit — ou reste muet —
comme un acheteur l'attendrait.

Le principe qui gouverne tout le fichier : mieux vaut manquer un
événement que d'en inventer un.
"""

import httpx
import pytest

from plugins.amazon.monitor import AmazonMonitor
from plugins.micromania.monitor import MicromaniaMonitor
from src.core.detector import detect_changes
from src.models import Availability, ChangeType, PurchaseAction, SellerType
from tests.helpers import make_product

ASIN = "B0H3PRH89L"
AMAZON_URL = f"https://www.amazon.fr/dp/{ASIN}"


# --------------------------------------------------------------------- #
# Pages de référence                                                     #
# --------------------------------------------------------------------- #

def amazon_page(
    *,
    buy_block: str = '<input id="add-to-cart-button" type="submit" '
                     'value="Ajouter au panier">',
    availability: str = "En stock.",
    price: str = "189,99 €",
    seller: str = "Amazon.fr",
    footer: str = "Conditions générales de vente 2026",
    wishlist: str = "Ajouter à votre liste d'envies",
    address: str = "Livrer à 67360 Durrenbach",
    prime: str = "Essayez Prime gratuitement pendant 30 jours",
    carousel: str = "Produit similaire A",
    sponsored: str = "Sponsorisé — Manette sans fil",
    reviews: str = "1 248 évaluations",
    delivery_date: str = "Livraison prévue le 21 août",
    token: str = "JQAvtlJLg/XuwxxSTIqxA==",
) -> str:
    """Fiche Amazon réaliste : un bloc d'achat noyé dans du bruit.

    Chaque paramètre correspond à une zone que le marchand fait varier
    sans que l'offre change. Aucune ne doit atteindre le hash métier.
    """
    return f"""
    <html lang="fr-FR"><head><title>Amazon.fr</title></head><body>
      <div id="navbar"><a href="/">Accueil</a><a href="/panier">Panier</a></div>
      <div id="nav-global-location-slot">
        <span id="glow-ingress-line2">{address}</span>
      </div>
      <span id="productTitle">Pokémon 30 Ans Ultra Premium Collection</span>
      <div id="ppd">
        <div id="desktop_buybox">
          <div id="corePrice_feature_div">
            <span class="a-offscreen">{price}</span>
          </div>
          <div id="availability"><span>{availability}</span></div>
          {buy_block}
          <input type="hidden" name="anti-csrftoken-a2z" value="{token}">
          <div id="merchant-info">Vendu par {seller} Expédié par Amazon</div>
          <span id="addToWishlist-button">{wishlist}</span>
          <div id="primeSignupModal"><button>{prime}</button></div>
          <div id="installmentCalculator"><button>Payer en 4 fois</button></div>
        </div>
      </div>
      <div id="mir-layout-DELIVERY_BLOCK">{delivery_date}</div>
      <div class="a-carousel" id="similarities_feature_div">
        <span>{carousel}</span><button>Ajouter au panier</button>
      </div>
      <div data-component-type="sp-sponsored-result">{sponsored}</div>
      <div id="customer-reviews_feature_div">{reviews}</div>
      <div id="navFooter">{footer}</div>
      <p>{"Description détaillée du produit. " * 30}</p>
    </body></html>
    """


def micromania_page(
    *,
    button: str = "Précommander",
    price: str = "189,99 €",
    cookies: str = "Nous utilisons des cookies pour améliorer votre expérience",
    newsletter: str = "Inscrivez-vous à la newsletter et recevez -10 %",
    carousel: str = "Produit similaire A",
    advert: str = "Soldes d'été jusqu'à -50 %",
    footer: str = "Micromania-Zing 2026 — mentions légales",
    reviews: str = "12 avis clients",
) -> str:
    return f"""
    <html lang="fr"><head><title>Fiche produit</title></head><body>
      <header><nav><a href="/">Accueil</a></nav></header>
      <div id="onetrust-banner-sdk" class="cookie-banner">{cookies}</div>
      <div class="newsletter-block">{newsletter}</div>
      <div class="advert-banner">{advert}</div>
      <main class="product-detail">
        <h1>Pokémon 30 Ans Ultra Premium Collection</h1>
        <span class="price">{price}</span>
        <button class="btn-add-to-cart">{button}</button>
        <p>{"Description détaillée du produit. " * 25}</p>
      </main>
      <section class="carousel recommended-products">
        <span>{carousel}</span><button>Ajouter au panier</button>
      </section>
      <div class="reviews-block">{reviews}</div>
      <footer>{footer}</footer>
    </body></html>
    """


@pytest.fixture
def amazon() -> AmazonMonitor:
    return AmazonMonitor(httpx.AsyncClient())


@pytest.fixture
def micromania() -> MicromaniaMonitor:
    return MicromaniaMonitor(httpx.AsyncClient())


@pytest.fixture
def amazon_product():
    return make_product(site="amazon", url=AMAZON_URL)


@pytest.fixture
def micromania_product():
    return make_product(site="micromania", url="https://www.micromania.fr/p/x.html")


def events_between(monitor, product, before: str, after: str):
    """Événements métier produits par le passage d'une page à l'autre."""
    old = monitor.parse(before, product)
    new = monitor.parse(after, product)
    return old, new, detect_changes(product, old, new)


# ===================================================================== #
# AMAZON — le bruit ne doit RIEN produire                                #
# ===================================================================== #

NOISE_VARIATIONS = {
    "pied de page": dict(footer="Conditions générales de vente 2027"),
    "liste d'envies": dict(wishlist="Ajouter à ma liste de souhaits"),
    "adresse de livraison": dict(address="Livrer à 75001 Paris"),
    "Prime": dict(prime="Profitez de Prime : livraison en 1 jour"),
    "carrousel": dict(carousel="Produit similaire B"),
    "produit sponsorisé": dict(sponsored="Sponsorisé — Casque audio"),
    "avis clients": dict(reviews="1 249 évaluations"),
    "date de livraison": dict(delivery_date="Livraison prévue le 22 août"),
    "jeton anti-CSRF": dict(token="Zz9KKlmNOPqrstUVwxyz1A=="),
}


@pytest.mark.parametrize("what,variation", NOISE_VARIATIONS.items())
def test_amazon_noise_never_produces_an_event(
    amazon, amazon_product, what, variation
):
    """Amazon modifie {what} seul : l'acheteur ne voit aucun changement."""
    old, new, events = events_between(
        amazon, amazon_product, amazon_page(), amazon_page(**variation)
    )

    assert old.availability is Availability.IN_STOCK
    assert events == [], f"{what} ne doit produire aucun événement"
    assert old.content_hash == new.content_hash, (
        f"{what} ne doit pas entrer dans le hash métier"
    )


def test_amazon_all_noise_at_once_still_produces_nothing(amazon, amazon_product):
    """Cumul : toutes les zones de bruit changent en même temps."""
    everything = {key: value for variation in NOISE_VARIATIONS.values()
                  for key, value in variation.items()}
    old, new, events = events_between(
        amazon, amazon_product, amazon_page(), amazon_page(**everything)
    )

    assert events == []
    assert old.content_hash == new.content_hash


def test_amazon_third_party_seller_rotation_is_silent(amazon, amazon_product):
    """Deux revendeurs tiers différents : même situation pour l'acheteur."""
    old, new, events = events_between(
        amazon, amazon_product,
        amazon_page(seller="Boutique Alpha"),
        amazon_page(seller="Boutique Beta"),
    )

    assert old.offer.seller_type is SellerType.THIRD_PARTY
    assert events == []
    assert old.content_hash == new.content_hash


def test_amazon_remaining_quantity_is_not_an_event(amazon, amazon_product):
    """« Il ne reste plus que 3 » → « plus que 2 » : rien pour l'acheteur."""
    old, new, events = events_between(
        amazon, amazon_product,
        amazon_page(availability="Il ne reste plus que 3 exemplaire(s) en stock."),
        amazon_page(availability="Il ne reste plus que 2 exemplaire(s) en stock."),
    )

    assert old.availability is Availability.IN_STOCK
    assert events == []


# ===================================================================== #
# AMAZON — les vrais changements DOIVENT produire un événement           #
# ===================================================================== #

def test_amazon_price_change_is_an_event(amazon, amazon_product):
    _, _, events = events_between(
        amazon, amazon_product,
        amazon_page(price="189,99 €"), amazon_page(price="179,99 €"),
    )

    assert [event.change_type for event in events] == [ChangeType.PRICE_CHANGED]
    assert events[0].old_value == "189,99 €"
    assert events[0].new_value == "179,99 €"


def test_amazon_preorder_opening_is_an_event(amazon, amazon_product):
    _, new, events = events_between(
        amazon, amazon_product,
        amazon_page(availability="Actuellement indisponible.", buy_block=""),
        amazon_page(availability="Date de sortie : 21 août 2026.",
                    buy_block='<input id="add-to-cart-button" '
                              'value="Précommander">'),
    )

    assert [event.change_type for event in events] == [ChangeType.PREORDER_OPENED]
    assert new.offer.action is PurchaseAction.PREORDER


def test_a_live_purchase_control_beats_a_release_announcement(
    amazon, amazon_product
):
    """Faux négatif le plus coûteux du projet, verrouillé par ce test.

    Une fiche de précommande affiche presque toujours une date de sortie
    et une mention « bientôt disponible ». Les traiter comme un état ferait
    manquer l'ouverture de la précommande — exactement l'événement que le
    projet existe pour attraper.
    """
    snapshot = amazon.parse(
        amazon_page(availability="Bientôt disponible. Date de sortie : 21 août.",
                    buy_block='<input id="add-to-cart-button" '
                              'value="Précommander">'),
        amazon_product,
    )

    assert snapshot.offer.action is PurchaseAction.PREORDER
    assert snapshot.availability is Availability.PREORDER
    assert snapshot.details["selecteur_decisif"] == "#add-to-cart-button"


def test_coming_soon_still_applies_without_any_control(amazon, amazon_product):
    """Sans bouton, « bientôt disponible » reste une attente."""
    snapshot = amazon.parse(
        amazon_page(availability="Bientôt disponible.", buy_block=""),
        amazon_product,
    )

    assert snapshot.offer.action is PurchaseAction.COMING_SOON
    assert snapshot.availability is Availability.UNAVAILABLE


def test_amazon_invitation_opening_has_its_own_event(amazon, amazon_product):
    """Le signal le plus attendu du projet mérite son propre événement."""
    _, new, events = events_between(
        amazon, amazon_product,
        amazon_page(availability="Actuellement indisponible.", buy_block=""),
        amazon_page(availability="Disponible sur invitation", buy_block=""),
    )

    assert [event.change_type for event in events] == [ChangeType.INVITATION_OPENED]
    assert new.offer.action is PurchaseAction.REQUEST_INVITE
    assert new.availability is Availability.PREORDER


def test_amazon_going_out_of_stock_is_an_event(amazon, amazon_product):
    _, _, events = events_between(
        amazon, amazon_product,
        amazon_page(),
        amazon_page(availability="Actuellement indisponible.", buy_block=""),
    )

    assert [event.change_type for event in events] == [ChangeType.WENT_OUT_OF_STOCK]


def test_amazon_back_in_stock_is_an_event(amazon, amazon_product):
    _, _, events = events_between(
        amazon, amazon_product,
        amazon_page(availability="Actuellement indisponible.", buy_block=""),
        amazon_page(),
    )

    assert [event.change_type for event in events] == [ChangeType.BACK_IN_STOCK]


def test_amazon_leaving_the_buy_box_is_an_event(amazon, amazon_product):
    """Amazon laisse la place à un revendeur tiers : vrai signal d'achat."""
    _, _, events = events_between(
        amazon, amazon_product,
        amazon_page(seller="Amazon.fr"), amazon_page(seller="Boutique Tierce"),
    )

    assert ChangeType.SELLER_LEFT_BUYBOX in [e.change_type for e in events]


def test_amazon_returning_as_seller_is_an_event(amazon, amazon_product):
    _, _, events = events_between(
        amazon, amazon_product,
        amazon_page(seller="Boutique Tierce"), amazon_page(seller="Amazon.fr"),
    )

    assert ChangeType.SELLER_BECAME_OFFICIAL in [e.change_type for e in events]


def test_amazon_stock_return_does_not_also_announce_the_price(
    amazon, amazon_product
):
    """Un seul message : le prix accompagne le retour en stock, il ne le double pas."""
    _, _, events = events_between(
        amazon, amazon_product,
        amazon_page(availability="Actuellement indisponible.", buy_block="",
                    price=""),
        amazon_page(),
    )

    assert [event.change_type for event in events] == [ChangeType.BACK_IN_STOCK]


# ===================================================================== #
# AMAZON — contexte France                                               #
# ===================================================================== #

def test_amazon_outside_france_concludes_nothing(amazon, amazon_product):
    """Aucun état n'est retenu hors livraison France, même positif."""
    snapshot = amazon.parse(
        amazon_page(address="Livraison : États-Unis"), amazon_product
    )

    assert snapshot.availability is Availability.UNKNOWN
    assert not snapshot.conclusive
    assert snapshot.details["pays_livraison"] == "US"
    assert "US" in snapshot.details["declasse"]


def test_amazon_outside_france_produces_no_event(amazon, amazon_product):
    """Et surtout : la bascule vers un contexte étranger n'alerte pas."""
    _, _, events = events_between(
        amazon, amazon_product,
        amazon_page(),
        amazon_page(address="Livraison : États-Unis",
                    availability="Actuellement indisponible.", buy_block=""),
    )

    assert events == []


# ===================================================================== #
# MICROMANIA — même philosophie                                          #
# ===================================================================== #

MICROMANIA_NOISE = {
    "bandeau cookies": dict(cookies="Ce site utilise des traceurs publicitaires"),
    "newsletter": dict(newsletter="Newsletter : -15 % sur votre commande"),
    "publicité": dict(advert="Black Friday : jusqu'à -70 %"),
    "carrousel": dict(carousel="Produit similaire B"),
    "pied de page": dict(footer="Micromania-Zing 2027 — mentions légales"),
    "avis clients": dict(reviews="13 avis clients"),
}


@pytest.mark.parametrize("what,variation", MICROMANIA_NOISE.items())
def test_micromania_noise_never_produces_an_event(
    micromania, micromania_product, what, variation
):
    old, new, events = events_between(
        micromania, micromania_product,
        micromania_page(), micromania_page(**variation),
    )

    assert old.availability is Availability.PREORDER
    assert events == [], f"{what} ne doit produire aucun événement"
    assert old.content_hash == new.content_hash


def test_micromania_all_noise_at_once_produces_nothing(
    micromania, micromania_product
):
    everything = {key: value for variation in MICROMANIA_NOISE.values()
                  for key, value in variation.items()}
    _, _, events = events_between(
        micromania, micromania_product,
        micromania_page(), micromania_page(**everything),
    )
    assert events == []


def test_micromania_price_change_is_an_event(micromania, micromania_product):
    _, _, events = events_between(
        micromania, micromania_product,
        micromania_page(price="189,99 €"), micromania_page(price="159,99 €"),
    )

    assert [event.change_type for event in events] == [ChangeType.PRICE_CHANGED]


def test_micromania_preorder_to_in_stock_is_an_event(
    micromania, micromania_product
):
    _, new, events = events_between(
        micromania, micromania_product,
        micromania_page(button="Précommander"),
        micromania_page(button="Ajouter au panier"),
    )

    assert [event.change_type for event in events] == [ChangeType.BACK_IN_STOCK]
    assert new.offer.action is PurchaseAction.ADD_TO_CART


def test_micromania_going_unavailable_is_an_event(micromania, micromania_product):
    _, _, events = events_between(
        micromania, micromania_product,
        micromania_page(button="Ajouter au panier"),
        micromania_page(button="Produit indisponible"),
    )

    assert [event.change_type for event in events] == [ChangeType.WENT_OUT_OF_STOCK]


def test_micromania_contradictory_buttons_conclude_nothing(
    micromania, micromania_product
):
    """Un bouton d'achat résiduel à côté d'une mention de rupture.

    Conclure « disponible » produirait la pire des fausses alertes : un
    faux retour en stock. On préfère ne rien conclure.
    """
    html = micromania_page(button="Ajouter au panier").replace(
        "<span class=\"price\">",
        "<button>Produit indisponible</button><span class=\"price\">",
    )
    snapshot = micromania.parse(html, micromania_product)

    assert snapshot.availability is Availability.UNKNOWN
    assert not snapshot.conclusive


def test_amazon_seller_name_is_readable(amazon, amazon_product):
    """Cas réel : Amazon empile « Vendeur » et « Expéditeur » dans un bloc.

    Sans nettoyage, l'alerte Telegram affichait
    « Amazon Amazon Expéditeur / Vendeur Amazon ».
    """
    html = amazon_page().replace(
        "<div id=\"merchant-info\">Vendu par Amazon.fr Expédié par Amazon</div>",
        "<div id=\"merchant-info\">Vendu par Amazon Amazon Expéditeur / "
        "Vendeur Amazon</div>",
    )
    snapshot = amazon.parse(html, amazon_product)

    assert snapshot.offer.seller_name == "Amazon"
    assert snapshot.offer.seller_type is SellerType.OFFICIAL


def test_micromania_seller_events_never_fire(micromania, micromania_product):
    """Un marchand sans place de marché n'a pas de vendeur à surveiller."""
    snapshot = micromania.parse(micromania_page(), micromania_product)
    assert snapshot.offer.seller_type is SellerType.UNKNOWN


# ===================================================================== #
# Garanties transverses                                                  #
# ===================================================================== #

def test_the_business_hash_ignores_button_labels(amazon, amazon_product):
    """Amazon renomme son bouton sans changer l'offre : hash identique."""
    old, new, events = events_between(
        amazon, amazon_product,
        amazon_page(buy_block='<input id="add-to-cart-button" '
                              'value="Ajouter au panier">'),
        amazon_page(buy_block='<input id="add-to-cart-button" '
                              'value="Ajouter au panier maintenant">'),
    )

    assert old.content_hash == new.content_hash
    assert events == []


def test_the_scope_version_invalidates_stored_state():
    """Changer les règles d'analyse doit invalider les états mémorisés.

    Sans cela, une évolution du parseur se lirait comme un changement du
    produit et déclencherait une alerte au premier cycle.
    """
    from src.models import OfferState

    base = OfferState(action=PurchaseAction.ADD_TO_CART, price="10,00 €")
    bumped = OfferState(action=PurchaseAction.ADD_TO_CART, price="10,00 €",
                        scope_version="autre-version")
    assert base.business_hash() != bumped.business_hash()


def test_an_offer_state_survives_a_round_trip():
    """L'état métier est persisté et relu sans perte."""
    from src.models import OfferState, ProductSnapshot

    state = OfferState(
        action=PurchaseAction.REQUEST_INVITE, native_state="invitation",
        has_buy_box=True, seller_type=SellerType.OFFICIAL,
        seller_name="Amazon.fr", price="189,99 €", currency="EUR",
        identifier=ASIN,
    )
    snapshot = ProductSnapshot(
        availability=state.availability, offer=state, page_exists=True
    )
    restored = ProductSnapshot.from_dict(snapshot.to_dict())

    assert restored.offer == state
    assert restored.offer.business_hash() == state.business_hash()


def test_a_corrupted_stored_offer_does_not_crash():
    """Un état écrit par une version antérieure ne bloque jamais le démarrage."""
    from src.models import OfferState

    restored = OfferState.from_dict({"action": "inconnue", "seller_type": "?"})
    assert restored.action is PurchaseAction.NONE
    assert restored.seller_type is SellerType.UNKNOWN
