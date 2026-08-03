"""Analyse d'une fiche Amazon : périmètre, états, buy box, confiance.

Toute la connaissance d'Amazon du projet tient dans ce fichier et dans
`keywords.py`. Le cœur, lui, ne voit qu'un `ProductSnapshot`.

PRINCIPE DIRECTEUR : mieux vaut manquer une alerte qu'en produire une
fausse. Chaque mécanisme ci-dessous va dans ce sens.

  1. PÉRIMÈTRE. On analyse le plus petit conteneur pertinent — buy box,
     puis bloc d'achat, puis fiche produit, et seulement en dernier
     recours la page entière. Cookies, Prime, recommandations, produits
     sponsorisés, carrousels, questions/réponses, navigation et pied de
     page sont retirés AVANT toute mesure.

  2. LIBELLÉS PROPRES. Seuls les vrais libellés de boutons sont retenus.
     Les champs cachés d'Amazon contiennent des jetons en base64 : les
     laisser entrer faisait varier le hash à chaque chargement et
     affichait des chaînes illisibles dans les alertes.

  3. CONFIANCE. Chaque analyse reçoit un score. Sous le seuil, l'état
     devient UNKNOWN et rien n'est notifié : on attend la prochaine
     vérification plutôt que de conclure sur une page douteuse.

  4. ÉTATS EXPLICITES. Interception, captcha, Cloudflare, erreur et
     « revendeur tiers seulement » sont des états à part entière, jamais
     confondus avec une rupture.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from src.models import (
    Availability,
    OfferState,
    PurchaseAction,
    SellerType,
)
from src.monitors.generic import normalise

from . import actions, keywords, marketplace
from .actions import ActionResolution
from .marketplace import PageLocale


class AmazonState(str, Enum):
    """État natif d'une fiche Amazon.

    Bien plus riche que la disponibilité générique du cœur : c'est cette
    précision qui rend les alertes explicables.
    """

    AVAILABLE = "available"
    INVITATION = "invitation"
    PREORDER = "preorder"
    COMING_SOON = "coming_soon"
    OUT_OF_STOCK = "out_of_stock"
    UNAVAILABLE = "unavailable"
    THIRD_PARTY_ONLY = "third_party_only"   # vendu, mais pas par Amazon
    INTERCEPTED = "intercepted"             # mur anti-robot
    CAPTCHA = "captcha"
    CLOUDFLARE = "cloudflare"
    ERROR = "error"                         # page d'erreur du site
    UNKNOWN = "unknown"


#: États où la page n'a rien pu nous apprendre : ils ne concluent JAMAIS.
INCONCLUSIVE_STATES: frozenset[AmazonState] = frozenset({
    AmazonState.INTERCEPTED, AmazonState.CAPTCHA, AmazonState.CLOUDFLARE,
    AmazonState.ERROR, AmazonState.UNKNOWN,
})

#: Traduction vers le vocabulaire du cœur.
#:
#: INVITATION est ramené à PREORDER : la page est vivante et l'utilisateur
#: doit agir. Le libellé exact reste dans `status_text` et `details`.
STATE_TO_AVAILABILITY: dict[AmazonState, Availability] = {
    AmazonState.AVAILABLE: Availability.IN_STOCK,
    AmazonState.PREORDER: Availability.PREORDER,
    AmazonState.INVITATION: Availability.PREORDER,
    AmazonState.THIRD_PARTY_ONLY: Availability.IN_STOCK,
    AmazonState.COMING_SOON: Availability.UNAVAILABLE,
    AmazonState.OUT_OF_STOCK: Availability.UNAVAILABLE,
    AmazonState.UNAVAILABLE: Availability.UNAVAILABLE,
    AmazonState.INTERCEPTED: Availability.UNKNOWN,
    AmazonState.CAPTCHA: Availability.UNKNOWN,
    AmazonState.CLOUDFLARE: Availability.UNKNOWN,
    AmazonState.ERROR: Availability.UNKNOWN,
    AmazonState.UNKNOWN: Availability.UNKNOWN,
}

STATE_LABELS: dict[AmazonState, str] = {
    AmazonState.AVAILABLE: "disponible",
    AmazonState.INVITATION: "demande d'invitation",
    AmazonState.PREORDER: "précommande",
    AmazonState.COMING_SOON: "bientôt disponible",
    AmazonState.OUT_OF_STOCK: "temporairement en rupture",
    AmazonState.UNAVAILABLE: "indisponible",
    AmazonState.THIRD_PARTY_ONLY: "revendeur tiers uniquement",
    AmazonState.INTERCEPTED: "page d'interception",
    AmazonState.CAPTCHA: "captcha",
    AmazonState.CLOUDFLARE: "protection Cloudflare",
    AmazonState.ERROR: "page en erreur",
    AmazonState.UNKNOWN: "état indéterminé",
}

# --------------------------------------------------------------------- #
# Périmètre d'analyse                                                    #
# --------------------------------------------------------------------- #

#: Du plus étroit au plus large. Le premier conteneur exploitable gagne.
#:
#: Les sélecteurs sont énumérés un par un — et non regroupés en une seule
#: chaîne — afin de pouvoir dire ensuite *lequel exactement* a fourni le
#: périmètre retenu. Une page Amazon porte souvent plusieurs blocs d'achat
#: (buy box principale, offre d'occasion, encart « autres vendeurs ») :
#: savoir lequel a servi est indispensable pour comprendre un statut.
SCOPE_SELECTORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("buy box", ("#desktop_buybox", "#buybox", "#qualifiedBuybox",
                 "#buyBoxAccordion")),
    ("bloc achat", ("#rightCol", "#addToCart_feature_div",
                    "#desktop_qualifiedBuyBox")),
    ("fiche produit", ("#ppd", "#dp-container", "#centerCol", "#dp")),
)

#: Zones qui polluent l'analyse : elles changent en permanence sans que le
#: produit bouge. Retirées avant toute mesure, y compris du hash.
NOISE_SELECTORS: str = (
    "#navbar, #nav-main, #navFooter, footer, header, "
    "#sp-cc, #sp-cc-banner, [data-cel-widget*='cookie'], "
    "#similarities_feature_div, #sims-consolidated-1_feature_div, "
    "#sims-consolidated-2_feature_div, #purchase-sims-feature, "
    "[class*='sponsored'], [data-component-type='sp-sponsored-result'], "
    "[id*='sponsored'], [class*='carousel'], [class*='Carousel'], "
    "#ask-btf_feature_div, #askATFLink, #customer-reviews_feature_div, "
    "#reviewsMedley, #cr-dp-summarization-attributes, "
    "#promotions_feature_div, #applicable_promotion_list_sec, "
    "#prime-ad-container, [id*='prime-benefit'], "
    "#rhf, #rhf-container, #HLCXComparisonWidget_feature_div, "
    "#dp-ads-center-promo_feature_div, #va-related-videos-widget"
)

# --------------------------------------------------------------------- #
# Expressions                                                            #
# --------------------------------------------------------------------- #

ASIN_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|product|-)/([A-Z0-9]{10})(?:[/?#]|$)", re.IGNORECASE
)
_ASIN_PARAM_RE = re.compile(r"[?&](?:asin|ASIN)=([A-Z0-9]{10})", re.IGNORECASE)

_PRICE_RE = re.compile(r"(\d{1,4}(?:[   ]?\d{3})*[.,]\d{2})")
_CURRENCY_SIGNS = {"€": "EUR", "$": "USD", "£": "GBP"}

#: Chaîne ressemblant à un jeton technique plutôt qu'à un libellé humain :
#: base64, identifiants opaques, suites sans espace ni voyelle.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=_-]{16,}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{12,}$")

_BUY_SELECTORS = (
    "#add-to-cart-button", "#buy-now-button", "#submit\\.add-to-cart",
    "#one-click-button", "input[name='submit.add-to-cart']",
    "#addToCart input[type='submit']",
)
_PRICE_SELECTORS = (
    "#corePrice_feature_div .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-offscreen",
    ".priceToPay .a-offscreen", "#price_inside_buybox", "#newBuyBoxPrice",
    ".a-price .a-offscreen",
)
_TITLE_SELECTORS = ("#productTitle", "#title span", "h1#title")
_AVAILABILITY_SELECTORS = ("#availability", "#outOfStock", "#availability span")
_MERCHANT_SELECTORS = (
    "#merchant-info", "#sellerProfileTriggerId", "#tabular-buybox",
    "#fulfillerInfoFeature_feature_div", "#merchantInfoFeature_feature_div",
)


# --------------------------------------------------------------------- #
# URL                                                                    #
# --------------------------------------------------------------------- #

def extract_asin(url: str) -> Optional[str]:
    """ASIN d'une URL Amazon, quelle que soit sa forme."""
    for pattern in (ASIN_RE, _ASIN_PARAM_RE):
        match = pattern.search(url or "")
        if match:
            return match.group(1).upper()
    return None


def canonical_url(url: str, default_host: str = "www.amazon.fr") -> str:
    """Ramène toute forme d'URL Amazon à `https://<hôte>/dp/<ASIN>`."""
    if not url:
        return url
    parts = urlsplit(url.strip())
    host = (parts.netloc or default_host).lower()
    asin = extract_asin(url)
    if not asin:
        return urlunsplit((parts.scheme or "https", host, parts.path, "", ""))
    return f"{parts.scheme or 'https'}://{host}/dp/{asin}"


# --------------------------------------------------------------------- #
# Résultat d'analyse                                                     #
# --------------------------------------------------------------------- #

@dataclass
class BuyBox:
    """Ce que la buy box révèle, quand elle est lisible."""

    price: Optional[str] = None
    currency: Optional[str] = None
    seller: Optional[str] = None
    shipped_by: Optional[str] = None
    stock_note: Optional[str] = None
    variation: Optional[str] = None
    edition: Optional[str] = None
    has_buy_box: bool = False
    #: Sélecteur exact du bouton d'achat trouvé, quand il y en a un.
    buy_selector: Optional[str] = None

    @property
    def sold_by_amazon(self) -> bool:
        seller = normalise(self.seller or "")
        return bool(seller) and "amazon" in seller

    def as_details(self) -> dict[str, str]:
        mapping = {
            "vendeur": self.seller,
            "expedie_par": self.shipped_by,
            "devise": self.currency,
            "stock": self.stock_note,
            "variation": self.variation,
            "edition": self.edition,
            "buy_box": "oui" if self.has_buy_box else "non",
        }
        return {key: value for key, value in mapping.items() if value}


@dataclass
class Confidence:
    """Score de fiabilité d'une analyse, détaillé pour être explicable."""

    points: dict[str, int] = field(default_factory=dict)

    def award(self, reason: str, value: int) -> None:
        self.points[reason] = value

    @property
    def score(self) -> int:
        return min(100, sum(self.points.values()))

    @property
    def detail(self) -> str:
        return ", ".join(
            f"{reason}+{value}" for reason, value in self.points.items()
        ) or "aucun indice"


@dataclass(frozen=True)
class TextSource:
    """Un fragment de page soumis à la classification, avec sa provenance.

    C'est ce qui permet de répondre à « quel sélecteur exact a décidé de
    l'état ? » : la classification ne travaille plus sur un texte agrégé,
    mais sur une liste de fragments identifiés.
    """

    origin: str      # boutons | disponibilité | périmètre
    selector: str    # sélecteur CSS exact, ou description de l'élément
    text: str        # normalisé, sert à la comparaison
    excerpt: str     # texte d'origine, pour la lecture humaine


@dataclass(frozen=True)
class ScopeCandidate:
    """Un bloc d'achat envisagé comme périmètre d'analyse."""

    label: str                     # buy box | bloc achat | fiche produit
    selector: str                  # sélecteur exact ayant trouvé le bloc
    identifier: str                # div#desktop_buybox
    material: tuple[str, ...] = () # sélecteurs décisifs trouvés dedans
    retained: bool = False
    reason: str = ""

    def describe(self) -> str:
        mark = "RETENU" if self.retained else "écarté"
        return f"{self.identifier} [{self.label}] {mark} — {self.reason}"


@dataclass(frozen=True)
class Evidence:
    """Le fragment précis qui a produit l'état final."""

    origin: str = ""
    selector: str = ""
    markers: tuple[str, ...] = ()
    excerpt: str = ""

    @property
    def known(self) -> bool:
        return bool(self.selector)

    def describe(self) -> str:
        if not self.known:
            return "aucun (aucun mot-clé reconnu)"
        markers = ", ".join(self.markers) or "—"
        return f"{self.selector} ({self.origin}) : « {markers} »"


@dataclass
class InvitationProbe:
    """Présence — et sort — du bouton « Demande d'invitation ».

    Ce bouton est le signal le plus important du projet : quand il existe
    dans la page mais ne ressort pas dans l'état final, il faut pouvoir
    dire pourquoi sans relire le HTML à la main.
    """

    present: bool = False
    markers: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    survived_noise: bool = False
    in_scope: bool = False
    used: bool = False
    reason: str = ""

    def describe(self) -> str:
        if not self.present:
            return "absent du DOM"
        where = ", ".join(self.locations) or "emplacement non localisé"
        return f"présent ({where}) — {self.reason}"


@dataclass
class PageAnalysis:
    """Analyse complète d'une page Amazon, entièrement traçable."""

    state: AmazonState = AmazonState.UNKNOWN
    #: État issu de la classification, AVANT déclassement éventuel par la
    #: confiance ou par le garde-fou de localisation.
    classified_state: AmazonState = AmazonState.UNKNOWN
    title: Optional[str] = None
    buttons: list[str] = field(default_factory=list)
    ignored_buttons: list[str] = field(default_factory=list)
    buy_box: BuyBox = field(default_factory=BuyBox)
    confidence: Confidence = field(default_factory=Confidence)
    scope: str = "page entière"
    reason: str = ""
    matched: tuple[str, ...] = ()
    downgraded: bool = False
    #: L'unique action d'achat retenue, sa provenance et ce qui a été écarté.
    action: ActionResolution = field(default_factory=ActionResolution)
    #: Localisation réellement servie par la page.
    locale: PageLocale = field(default_factory=PageLocale)
    #: Tous les blocs d'achat examinés, retenu comme écartés.
    scope_candidates: tuple[ScopeCandidate, ...] = ()
    #: Sélecteur exact ayant déterminé l'état.
    evidence: Evidence = field(default_factory=Evidence)
    #: Sort réservé au bouton « Demande d'invitation ».
    invitation: InvitationProbe = field(default_factory=InvitationProbe)
    #: État négatif écarté parce que la page n'était pas servie pour le bon
    #: pays de livraison.
    locale_blocked: bool = False

    @property
    def inconclusive(self) -> bool:
        return self.state in INCONCLUSIVE_STATES

    @property
    def retained_scope(self) -> Optional[ScopeCandidate]:
        return next(
            (candidate for candidate in self.scope_candidates if candidate.retained),
            None,
        )

    @property
    def availability(self) -> Availability:
        return STATE_TO_AVAILABILITY[self.state]

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]

    def decision_hash(self) -> str:
        """Empreinte des seuls éléments décisifs.

        Vendeur, expéditeur, promotions et libellés annexes en sont
        exclus : Amazon les fait varier sans que le produit change.
        """
        payload = "|".join([
            self.state.value,
            self.buy_box.price or "",
            "buybox" if self.buy_box.has_buy_box else "",
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------- #
# Analyse                                                                #
# --------------------------------------------------------------------- #

def analyse(
    html: str,
    min_confidence: int = 60,
    url: str = "",
    enforce_delivery_country: bool = True,
) -> PageAnalysis:
    """Lit une page Amazon et rend une analyse motivée.

    `url` sert à identifier la place de marché ; `enforce_delivery_country`
    active le refus de conclure au négatif quand la page a été servie pour
    un autre pays de livraison que celui de la place de marché.
    """
    result = PageAnalysis()

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001 — HTML illisible
        result.state = AmazonState.ERROR
        result.reason = "HTML illisible"
        return result

    full_text = normalise(soup.get_text(" ", strip=True))

    # 1. Page d'interception : on ne conclut RIEN.
    blocked = _detect_block(html, full_text)
    if blocked is not None:
        result.state = blocked
        result.reason = f"page non exploitable ({STATE_LABELS[blocked]})"
        result.locale = marketplace.detect(soup, url)
        result.invitation.reason = "page non analysée (contenu non exploitable)"
        return result

    # 2. Localisation SERVIE par la page : place de marché, langue et
    #    surtout pays de livraison. Lue avant tout nettoyage, car le glow
    #    « Livrer à … » vit dans la barre de navigation, elle-même retirée
    #    comme bruit à l'étape suivante.
    result.locale = marketplace.detect(soup, url)

    # 3. Sort du bouton « Demande d'invitation » : repéré et marqué sur le
    #    DOM intact, pour pouvoir dire ensuite ce qu'il est devenu.
    result.invitation = _probe_invitation(soup, full_text)

    # 4. Périmètre : le plus petit conteneur pertinent, nettoyé du bruit.
    _strip_noise(soup)
    result.invitation.survived_noise = bool(_marked_nodes(soup))

    scope, result.scope, result.scope_candidates = _resolve_scope(soup)
    result.invitation.in_scope = bool(_marked_nodes(scope))

    result.title = _first_text(soup, _TITLE_SELECTORS)
    labels = _action_labels(scope)
    result.buttons = [label.excerpt for label in labels if label.origin == "boutons"]
    result.ignored_buttons = [
        label.excerpt for label in labels if label.origin == "ignoré"
    ]
    result.buy_box = _read_buy_box(scope, soup)

    # Une seule action d'achat principale — tout le reste de la page est
    # du bruit par construction (voir plugins/amazon/actions.py).
    raw_scope_text = " ".join(scope.get_text(" ", strip=True).split())
    result.action = actions.resolve(
        scope,
        _availability_sources(scope),
        _describe(scope),
        raw_scope_text,
        has_buy_box=result.buy_box.has_buy_box,
        buy_selector=result.buy_box.buy_selector or "",
    )
    result.state = _state_for(result.action.action, result.buy_box)
    result.matched = (result.action.label,) if result.action.resolved else ()
    result.evidence = Evidence(
        origin=result.action.origin,
        selector=result.action.selector,
        markers=result.matched,
        excerpt=result.action.label,
    )
    # Une décision tirée du texte d'un bloc entier ne désigne rien d'utile :
    # on descend jusqu'à l'élément qui porte réellement le libellé.
    if result.action.origin == "texte du périmètre":
        result.evidence = _refine_evidence(scope, result.evidence)
    result.classified_state = result.state
    result.reason = (
        f"{', '.join(result.matched)} (périmètre : {result.scope}, "
        f"sélecteur : {result.evidence.selector or '—'})"
        if result.matched else f"aucun indice dans le périmètre {result.scope}"
    )

    _explain_invitation(result)

    # 5. Confiance : sous le seuil, on refuse de conclure.
    result.confidence = _score(result, soup)
    if result.confidence.score < min_confidence and not result.inconclusive:
        result.downgraded = True
        result.reason = (
            f"confiance {result.confidence.score} < {min_confidence} "
            f"({result.confidence.detail}) — état non retenu : {result.state.value}"
        )
        result.state = AmazonState.UNKNOWN

    # 6. Garde-fou de localisation, appliqué en dernier.
    if enforce_delivery_country:
        _guard_delivery_country(result)

    return result


def build_offer(analysis: PageAnalysis, asin: Optional[str]) -> OfferState:
    """Traduit une analyse en état métier — le seul objet que le moteur compare.

    Rien de ce qui vient du HTML n'y entre : ni libellé de bouton, ni texte
    de page, ni sélecteur. Amazon peut refondre son interface sans que cet
    état bouge d'un caractère.
    """
    if analysis.state is AmazonState.UNKNOWN:
        # Aucune conclusion : l'action reste NONE, donc la disponibilité
        # reste inconnue et le moteur conserve son dernier état.
        return OfferState(native_state=analysis.state.value, identifier=asin)

    box = analysis.buy_box
    return OfferState(
        action=_offer_action(analysis),
        native_state=analysis.state.value,
        has_buy_box=box.has_buy_box,
        seller_type=_seller_type(box),
        seller_name=box.seller,
        price=box.price,
        currency=box.currency,
        identifier=asin,
    )


def _offer_action(analysis: PageAnalysis) -> PurchaseAction:
    """Action métier finale, alignée sur l'état natif retenu."""
    if analysis.state is AmazonState.THIRD_PARTY_ONLY:
        return PurchaseAction.THIRD_PARTY_ONLY
    return analysis.action.action


def _seller_type(box: BuyBox) -> SellerType:
    """Qui vend, du point de vue de l'acheteur.

    Un vendeur non renseigné reste `UNKNOWN` : l'absence d'information ne
    doit jamais se transformer en changement de vendeur.
    """
    if not box.seller:
        return SellerType.UNKNOWN
    return SellerType.OFFICIAL if box.sold_by_amazon else SellerType.THIRD_PARTY


def _guard_delivery_country(result: PageAnalysis) -> None:
    """Refuse TOUTE conclusion tirée d'une page destinée à un autre pays.

    Une page servie pour une autre destination ne décrit pas l'offre que
    voit l'utilisateur : l'assortiment, le vendeur, le prix et jusqu'à
    l'existence de l'offre en dépendent. Un « indisponible » y est faux,
    mais un « disponible » y est tout aussi trompeur — il annoncerait un
    achat impossible depuis la France.

    Aucun état n'est donc conservé : ni négatif, ni positif. L'état devient
    UNKNOWN, le moteur garde le dernier état métier connu, et rien n'est
    notifié.

    Le déclenchement demande une destination **connue et différente** de
    celle attendue. Une page muette sur le sujet ne suffit pas :
    transformer l'absence d'information en refus de conclure paralyserait
    la surveillance au lieu de la fiabiliser.
    """
    locale = result.locale
    if result.state is AmazonState.UNKNOWN:
        return
    if not locale.delivery_known or locale.delivers_to_expected_country:
        return

    result.locale_blocked = True
    result.reason = (
        f"état « {STATE_LABELS[result.state]} » écarté : page servie pour une "
        f"livraison en {locale.delivery_country} "
        f"({locale.delivery_label or 'libellé absent'}) alors que "
        f"{locale.marketplace_domain} attend {locale.expected_country}. "
        f"L'offre proposée à cette destination n'est pas celle vue depuis la "
        f"France — aucune conclusion n'est tirée, le dernier état métier "
        f"connu est conservé."
    )
    result.state = AmazonState.UNKNOWN


def _detect_block(html: str, full_text: str) -> Optional[AmazonState]:
    """Distingue captcha, Cloudflare, interception et page d'erreur."""
    lowered = html.lower()
    if "cf-browser-verification" in lowered or "cloudflare" in lowered:
        return AmazonState.CLOUDFLARE
    if "captcha" in lowered or "validatecaptcha" in lowered:
        return AmazonState.CAPTCHA
    if _hits(keywords.BOT_WALL, full_text):
        return AmazonState.INTERCEPTED
    if len(full_text) < 200:
        return AmazonState.ERROR
    return None


def _strip_noise(soup: BeautifulSoup) -> None:
    """Retire en place les zones qui changent sans que le produit bouge."""
    try:
        for tag in soup.select(NOISE_SELECTORS):
            tag.decompose()
    except Exception:  # noqa: BLE001 — sélecteur refusé par le parseur
        pass


def _resolve_scope(
    soup: BeautifulSoup,
) -> tuple[BeautifulSoup, str, tuple[ScopeCandidate, ...]]:
    """Plus petit conteneur exploitable, avec repli sur la page entière.

    Tous les blocs d'achat rencontrés sont consignés — retenu comme
    écartés, avec le motif — pour que le choix soit vérifiable après coup.
    """
    candidates: list[ScopeCandidate] = []
    retained: Optional[BeautifulSoup] = None
    retained_label = "page entière"

    for label, selectors in SCOPE_SELECTORS:
        for selector in selectors:
            try:
                nodes = soup.select(selector)
            except Exception:  # noqa: BLE001 — sélecteur refusé par le parseur
                continue
            for node in nodes:
                material = _decision_material(node)
                if retained is None and material:
                    retained, retained_label = node, label
                    reason = "premier bloc contenant de quoi décider : " + ", ".join(
                        material
                    )
                else:
                    reason = (
                        "un bloc plus étroit a déjà été retenu"
                        if material else
                        "ni bouton d'achat, ni disponibilité, ni prix"
                    )
                candidates.append(ScopeCandidate(
                    label=label,
                    selector=selector,
                    identifier=_describe(node),
                    material=material,
                    retained=node is retained,
                    reason=reason,
                ))

    if retained is None:
        candidates.append(ScopeCandidate(
            label="page entière",
            selector="html",
            identifier="html",
            retained=True,
            reason="aucun bloc d'achat exploitable — repli sur la page entière",
        ))
        return soup, "page entière", tuple(candidates)

    return retained, retained_label, tuple(candidates)


def _decision_material(node) -> tuple[str, ...]:
    """Sélecteurs décisifs (achat, disponibilité, prix) présents dans un bloc."""
    found: list[str] = []
    for selector in (*_BUY_SELECTORS, *_AVAILABILITY_SELECTORS, *_PRICE_SELECTORS):
        try:
            if node.select_one(selector) is not None:
                found.append(selector)
        except Exception:  # noqa: BLE001
            continue
    return tuple(found)


def _describe(node) -> str:
    """Identifiant lisible d'un élément : `div#desktop_buybox`, `span.a-color`."""
    if node is None or not getattr(node, "name", None):
        return "—"
    identifier = node.get("id") if hasattr(node, "get") else None
    if identifier:
        return f"{node.name}#{identifier}"
    classes = node.get("class") if hasattr(node, "get") else None
    if classes:
        return f"{node.name}.{'.'.join(classes[:2])}"
    return node.name


#: Traduction de l'action métier vers l'état natif Amazon.
#:
#: L'état natif est plus riche que la disponibilité générique : c'est lui
#: qui porte la nuance (invitation, revendeur tiers, bientôt disponible)
#: dans les logs, les alertes et le hash métier.
_ACTION_TO_STATE: dict[PurchaseAction, AmazonState] = {
    PurchaseAction.ADD_TO_CART: AmazonState.AVAILABLE,
    PurchaseAction.BUY_NOW: AmazonState.AVAILABLE,
    PurchaseAction.THIRD_PARTY_ONLY: AmazonState.THIRD_PARTY_ONLY,
    PurchaseAction.PREORDER: AmazonState.PREORDER,
    PurchaseAction.REQUEST_INVITE: AmazonState.INVITATION,
    PurchaseAction.NOTIFY_ME: AmazonState.OUT_OF_STOCK,
    PurchaseAction.TEMPORARILY_UNAVAILABLE: AmazonState.OUT_OF_STOCK,
    PurchaseAction.CURRENTLY_UNAVAILABLE: AmazonState.OUT_OF_STOCK,
    PurchaseAction.DISCONTINUED: AmazonState.UNAVAILABLE,
    PurchaseAction.COMING_SOON: AmazonState.COMING_SOON,
    PurchaseAction.NONE: AmazonState.UNKNOWN,
}

#: Actions qui décrivent un achat immédiatement possible.
_PURCHASABLE_NOW: frozenset[PurchaseAction] = frozenset({
    PurchaseAction.ADD_TO_CART, PurchaseAction.BUY_NOW,
})


def _state_for(action: PurchaseAction, buy_box: BuyBox) -> AmazonState:
    """État natif correspondant à l'action retenue.

    Seul ajustement : un achat possible mais assuré par un revendeur tiers
    devient `THIRD_PARTY_ONLY`. C'est la décision documentée du projet —
    une rotation entre revendeurs reste silencieuse, mais Amazon qui laisse
    la place à un tiers est un vrai changement.
    """
    if action in _PURCHASABLE_NOW and buy_box.seller and not buy_box.sold_by_amazon:
        return AmazonState.THIRD_PARTY_ONLY
    return _ACTION_TO_STATE[action]


def _availability_sources(scope) -> tuple[tuple[str, str], ...]:
    """Mentions de disponibilité, chacune avec son sélecteur exact."""
    sources: list[tuple[str, str]] = []
    for selector in _AVAILABILITY_SELECTORS:
        for text in _all_text(scope, (selector,)):
            sources.append((selector, text))
    return tuple(sources)


def _refine_evidence(scope, evidence: Evidence) -> Evidence:
    """Descend jusqu'à l'élément exact porteur du mot-clé décisif.

    Un mot-clé trouvé dans le texte du périmètre désigne au départ tout le
    bloc (`div#desktop_buybox`), ce qui n'aide pas : sur une fiche réelle,
    le libellé d'invitation vit dans un `span#hdp-invite-button-announce`
    qu'aucune règle ne cite. On retrouve donc l'élément le plus profond qui
    le porte, pour que le sélecteur annoncé soit celui du HTML.
    """
    if not evidence.known or not evidence.markers:
        return evidence

    marker = evidence.markers[0]
    node = _innermost_carrier(scope, marker)
    if node is None:
        return evidence

    raw = " ".join(node.get_text(" ", strip=True).split()) or (node.get("value") or "")
    return replace(
        evidence,
        selector=_locate(node),
        excerpt=" ".join(raw.split())[:120] or evidence.excerpt,
    )


def _innermost_carrier(scope, marker: str):
    """Élément le plus profond dont le texte ou un attribut porte `marker`."""
    try:
        for node in scope.find_all(string=True):
            parent = node.parent
            if parent is None or parent.name in _NON_VISIBLE_TAGS:
                continue
            if marker in normalise(str(node)):
                return parent

        for tag in scope.find_all(attrs={"aria-label": True}):
            if marker in normalise(tag.get("aria-label") or ""):
                return tag

        for tag in scope.find_all("input"):
            if marker in normalise(tag.get("value") or ""):
                return tag
    except Exception:  # noqa: BLE001 — affinage : jamais critique
        return None
    return None
def _score(analysis: PageAnalysis, soup: BeautifulSoup) -> Confidence:
    """Score de fiabilité : 5 indices à 20 points chacun."""
    confidence = Confidence()

    if analysis.buy_box.price:
        confidence.award("prix", 20)
    if analysis.buttons:
        confidence.award("bouton", 20)
    if analysis.buy_box.has_buy_box or analysis.buy_box.stock_note:
        confidence.award("buy box", 20)
    if analysis.title:
        confidence.award("identité", 20)
    if _has_structured_data(soup):
        confidence.award("json-ld", 20)

    return confidence


def _has_structured_data(soup: BeautifulSoup) -> bool:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if script.string and "product" in script.string.lower():
            return True
    return bool(soup.find(attrs={"itemtype": re.compile("Product", re.I)}))


def _read_buy_box(scope, soup: BeautifulSoup) -> BuyBox:
    """Prix, devise, vendeur, expéditeur, variation et édition."""
    box = BuyBox()

    raw_price = _first_text(scope, _PRICE_SELECTORS) or _first_text(
        soup, _PRICE_SELECTORS
    )
    if raw_price:
        box.price, box.currency = _parse_price(raw_price)

    box.buy_selector = next(
        (selector for selector in _BUY_SELECTORS
         if _select_one(scope, selector) is not None),
        None,
    )
    box.has_buy_box = box.buy_selector is not None

    merchant_text = " ".join(
        _all_text(scope, _MERCHANT_SELECTORS) or _all_text(soup, _MERCHANT_SELECTORS)
    )
    box.seller = _after_label(merchant_text, keywords.SOLD_BY)
    box.shipped_by = _after_label(merchant_text, keywords.SHIPPED_BY)

    availability_text = " ".join(_all_text(scope, _AVAILABILITY_SELECTORS)).strip()
    if availability_text:
        box.stock_note = " ".join(availability_text.split())[:120]

    box.variation = _selected_variation(soup)
    box.edition = _labelled_value(soup, ("edition", "format", "plateforme", "platform"))
    return box


# --------------------------------------------------------------------- #
# Libellés de boutons                                                    #
# --------------------------------------------------------------------- #

def is_meaningful_label(text: str) -> bool:
    """Un libellé lisible par un humain, pas un jeton technique.

    Les formulaires Amazon contiennent des champs cachés dont la valeur
    est un jeton en base64 : les retenir faisait varier le hash à chaque
    chargement et affichait des chaînes illisibles dans les alertes.
    """
    cleaned = " ".join((text or "").split())
    if not (2 <= len(cleaned) <= 60):
        return False
    if _TOKEN_RE.match(cleaned) or _HEX_RE.match(cleaned):
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]", cleaned):
        return False
    # Un libellé humain contient des voyelles et peu de caractères exotiques.
    letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", cleaned)
    if letters and not re.search(r"[aeiouyàâäéèêëîïôöùûü]", letters, re.I):
        return False
    return True


def _action_labels(scope) -> tuple[TextSource, ...]:
    """Libellés d'action, chacun avec le sélecteur ou l'élément d'origine.

    L'origine vaut « boutons » pour un libellé retenu et « ignoré » pour un
    libellé écarté : c'est ce qui permet, plus loin, de dire qu'un texte
    existait bien mais n'a pas participé à la décision.
    """
    kept: dict[str, TextSource] = {}
    ignored: dict[str, TextSource] = {}

    def offer(raw: Optional[str], selector: str) -> None:
        text = " ".join((raw or "").split())
        if not text:
            return
        bucket, origin = (
            (kept, "boutons") if is_meaningful_label(text) else (ignored, "ignoré")
        )
        bucket.setdefault(
            text, TextSource(origin, selector, normalise(text), text)
        )

    for selector in _BUY_SELECTORS:
        tag = _select_one(scope, selector)
        if tag is None:
            continue
        offer(tag.get("value") or tag.get("aria-label")
              or tag.get_text(" ", strip=True), selector)

    # Seuls les boutons réels sont examinés : les champs cachés portent
    # des jetons, jamais des libellés.
    try:
        controls = scope.find_all(["button", "a"], limit=200)
        controls += [
            tag for tag in scope.find_all("input", limit=200)
            if (tag.get("type") or "").lower() in ("submit", "button")
        ]
    except Exception:  # noqa: BLE001
        controls = []

    for tag in controls:
        origin = _describe(tag)
        offer(tag.get("value") if tag.name == "input" else None, origin)
        offer(tag.get("aria-label"), origin)
        if tag.name != "input":
            offer(tag.get_text(" ", strip=True), origin)

    for selector in _AVAILABILITY_SELECTORS:
        for text in _all_text(scope, (selector,)):
            offer(text, selector)

    return (*list(kept.values())[:8], *list(ignored.values())[:8])


# --------------------------------------------------------------------- #
# Sonde « Demande d'invitation »                                         #
# --------------------------------------------------------------------- #

#: Attribut posé sur les éléments porteurs d'un libellé d'invitation.
#: Il survit — ou non — au nettoyage du bruit et à la réduction du
#: périmètre : c'est ce qui permet de dire ce qu'est devenu le bouton.
INVITATION_MARK = "data-dm-invitation"

#: Éléments dont le contenu textuel n'est pas du texte affiché.
_NON_VISIBLE_TAGS = frozenset({"script", "style", "noscript", "template"})


def _probe_invitation(soup: BeautifulSoup, full_text: str) -> InvitationProbe:
    """Repère les libellés d'invitation sur le DOM encore intact.

    Le texte visible ne suffit pas : Amazon place régulièrement le libellé
    dans un `aria-label` ou dans la `value` d'un bouton. Les trois sont
    donc examinés.
    """
    markers, locations = _mark_invitation_nodes(soup)
    markers = tuple(dict.fromkeys((*_hits(keywords.INVITATION, full_text), *markers)))

    if not markers:
        return InvitationProbe(present=False, reason="absent du DOM")
    return InvitationProbe(present=True, markers=markers, locations=locations)


def _mark_invitation_nodes(soup: BeautifulSoup) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Marque les éléments portant un libellé d'invitation, et les décrit."""
    located: list[str] = []
    found: list[str] = []

    def mark(tag, markers: tuple[str, ...]) -> None:
        if tag is None or not getattr(tag, "name", None):
            return
        if tag.name in _NON_VISIBLE_TAGS:
            return
        tag[INVITATION_MARK] = "1"
        located.append(_locate(tag))
        found.extend(markers)

    try:
        for node in soup.find_all(string=True):
            parent = node.parent
            if parent is None or parent.name in _NON_VISIBLE_TAGS:
                continue
            markers = _hits(keywords.INVITATION, normalise(str(node)))
            if markers:
                mark(parent, markers)

        for tag in soup.find_all(attrs={"aria-label": True}):
            markers = _hits(keywords.INVITATION, normalise(tag.get("aria-label") or ""))
            if markers:
                mark(tag, markers)

        for tag in soup.find_all("input"):
            markers = _hits(keywords.INVITATION, normalise(tag.get("value") or ""))
            if markers:
                mark(tag, markers)
    except Exception:  # noqa: BLE001 — DOM inattendu : la sonde n'est pas critique
        pass

    return tuple(dict.fromkeys(found)), tuple(dict.fromkeys(located))[:5]


def _locate(tag) -> str:
    """Emplacement lisible d'un élément : `div#autre-bloc > span`.

    Un `<span>` nu ne dit rien ; le premier ancêtre identifié situe le
    libellé dans la page et suffit à retrouver le bloc fautif.
    """
    own = _describe(tag)
    if tag.get("id"):
        return own
    for parent in tag.parents:
        if getattr(parent, "get", None) is None:
            continue
        if parent.get("id") or parent.get("class"):
            return f"{_describe(parent)} > {own}"
    return own


def _marked_nodes(node) -> list:
    """Éléments d'invitation encore présents sous `node`."""
    try:
        return node.select(f"[{INVITATION_MARK}]")
    except Exception:  # noqa: BLE001
        return []


def _explain_invitation(result: PageAnalysis) -> None:
    """Dit pourquoi le bouton d'invitation a — ou n'a pas — servi."""
    probe = result.invitation
    if not probe.present:
        probe.reason = "absent du DOM"
        return

    probe.used = result.classified_state is AmazonState.INVITATION
    if probe.used:
        probe.reason = "retenu : c'est lui qui fixe l'état"
    elif not probe.survived_noise:
        probe.reason = (
            "retiré avec le bruit avant analyse (carrousel, recommandations, "
            "produits sponsorisés, navigation ou pied de page)"
        )
    elif not probe.in_scope:
        probe.reason = (
            f"présent dans la page mais hors du périmètre retenu "
            f"({result.scope}) — il appartient à un autre bloc"
        )
    else:
        probe.reason = (
            "dans le périmètre retenu, mais aucun mot-clé d'invitation "
            "reconnu à la classification (libellé inconnu de keywords.py ?)"
        )


# --------------------------------------------------------------------- #
# Utilitaires                                                            #
# --------------------------------------------------------------------- #

def _hits(patterns: tuple[str, ...], *haystacks: str) -> tuple[str, ...]:
    joined = " ".join(haystack for haystack in haystacks if haystack)
    return tuple(pattern for pattern in patterns if pattern in joined)


def _select_one(node, selector: str):
    try:
        return node.select_one(selector)
    except Exception:  # noqa: BLE001 — sélecteur invalide
        return None


def _first_text(node, selectors: tuple[str, ...]) -> Optional[str]:
    for selector in selectors:
        tag = _select_one(node, selector)
        if tag is None:
            continue
        text = tag.get_text(" ", strip=True) or (tag.get("value") or "")
        text = " ".join(text.split())
        if text:
            return text
    return None


def _all_text(node, selectors: tuple[str, ...]) -> list[str]:
    texts: list[str] = []
    for selector in selectors:
        try:
            for tag in node.select(selector):
                text = " ".join(tag.get_text(" ", strip=True).split())
                if text:
                    texts.append(text)
        except Exception:  # noqa: BLE001
            continue
    return texts


def _parse_price(raw: str) -> tuple[Optional[str], Optional[str]]:
    currency = next(
        (code for sign, code in _CURRENCY_SIGNS.items() if sign in raw), None
    )
    match = _PRICE_RE.search(raw)
    return (_clean_amount(match.group(1)) if match else None), currency


def _clean_amount(raw: str) -> str:
    cleaned = raw.replace(" ", "").replace(" ", "").replace(" ", "")
    return f"{cleaned.replace('.', ',')} €"


#: Séparateurs qui marquent la fin d'un nom de vendeur. Amazon empile
#: « Vendeur », « Expéditeur » et leurs traductions dans le même bloc :
#: sans coupure, on récolte « Amazon Amazon Expéditeur / Vendeur Amazon ».
_SELLER_STOP_RE = re.compile(
    r"\s{2,}|[|•/]|Expédié par|Expéditeur|Vendu par|Vendeur|Ships from|"
    r"Sold by|Dispatched from|Livré par",
    re.IGNORECASE,
)


def _after_label(text: str, labels: tuple[str, ...]) -> Optional[str]:
    normalised = normalise(text)
    for label in labels:
        index = normalised.find(label)
        if index == -1:
            continue
        tail = text[index + len(label):].strip(" :•|-\n\t")
        value = _SELLER_STOP_RE.split(tail, maxsplit=1)[0]
        value = _collapse_repeats(" ".join(value.split()))[:80]
        if value:
            return value
    return None


def _collapse_repeats(text: str) -> str:
    """« Amazon Amazon » → « Amazon ».

    Amazon répète le nom du vendeur pour les lecteurs d'écran ; le texte
    concaténé le fait apparaître deux fois de suite.
    """
    words: list[str] = []
    for word in text.split():
        if not words or normalise(word) != normalise(words[-1]):
            words.append(word)
    return " ".join(words)


def _selected_variation(soup: BeautifulSoup) -> Optional[str]:
    for selector in (
        "#variation_style_name .selection", "#variation_size_name .selection",
        "#variation_color_name .selection", ".swatchSelect .a-size-base",
        "#inline-twister-expanded-dimension-text-style_name",
    ):
        value = _first_text(soup, (selector,))
        if value:
            return value[:80]
    return None


def _labelled_value(soup: BeautifulSoup, labels: tuple[str, ...]) -> Optional[str]:
    for row in soup.select("tr, li"):
        text = normalise(row.get_text(" ", strip=True))
        if any(text.startswith(label) for label in labels):
            raw = " ".join(row.get_text(" ", strip=True).split())
            parts = re.split(r"[:‎]", raw, maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()[:80]
    return None
