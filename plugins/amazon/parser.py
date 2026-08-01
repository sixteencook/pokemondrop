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
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from src.models import Availability
from src.monitors.generic import normalise

from . import keywords


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
SCOPE_SELECTORS: tuple[tuple[str, str], ...] = (
    ("buy box", "#desktop_buybox, #buybox, #qualifiedBuybox, #buyBoxAccordion"),
    ("bloc achat", "#rightCol, #addToCart_feature_div, #desktop_qualifiedBuyBox"),
    ("fiche produit", "#ppd, #dp-container, #centerCol, #dp"),
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


@dataclass
class PageAnalysis:
    """Analyse complète d'une page Amazon, entièrement traçable."""

    state: AmazonState = AmazonState.UNKNOWN
    title: Optional[str] = None
    buttons: list[str] = field(default_factory=list)
    ignored_buttons: list[str] = field(default_factory=list)
    buy_box: BuyBox = field(default_factory=BuyBox)
    confidence: Confidence = field(default_factory=Confidence)
    scope: str = "page entière"
    reason: str = ""
    matched: tuple[str, ...] = ()
    downgraded: bool = False

    @property
    def inconclusive(self) -> bool:
        return self.state in INCONCLUSIVE_STATES

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

def analyse(html: str, min_confidence: int = 60) -> PageAnalysis:
    """Lit une page Amazon et rend une analyse motivée."""
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
        return result

    # 2. Périmètre : le plus petit conteneur pertinent, nettoyé du bruit.
    _strip_noise(soup)
    scope, result.scope = _resolve_scope(soup)

    result.title = _first_text(soup, _TITLE_SELECTORS)
    result.buttons, result.ignored_buttons = _action_labels(scope)
    result.buy_box = _read_buy_box(scope, soup)

    scope_text = normalise(scope.get_text(" ", strip=True))
    buttons_text = normalise(" | ".join(result.buttons))
    availability_text = normalise(" ".join(_all_text(scope, _AVAILABILITY_SELECTORS)))

    result.state, result.matched = _classify(
        buttons_text, availability_text, scope_text, result.buy_box
    )
    result.reason = (
        f"{', '.join(result.matched)} (périmètre : {result.scope})"
        if result.matched else f"aucun indice dans le périmètre {result.scope}"
    )

    # 3. Confiance : sous le seuil, on refuse de conclure.
    result.confidence = _score(result, soup)
    if result.confidence.score < min_confidence and not result.inconclusive:
        result.downgraded = True
        result.reason = (
            f"confiance {result.confidence.score} < {min_confidence} "
            f"({result.confidence.detail}) — état non retenu : {result.state.value}"
        )
        result.state = AmazonState.UNKNOWN

    return result


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


def _resolve_scope(soup: BeautifulSoup) -> tuple[BeautifulSoup, str]:
    """Plus petit conteneur exploitable, avec repli sur la page entière."""
    for label, selectors in SCOPE_SELECTORS:
        try:
            candidates = soup.select(selectors)
        except Exception:  # noqa: BLE001
            continue
        for candidate in candidates:
            # Un conteneur retenu doit contenir de quoi décider.
            if _has_decision_material(candidate):
                return candidate, label
    return soup, "page entière"


def _has_decision_material(node) -> bool:
    """Le conteneur porte-t-il un bouton d'achat ou une disponibilité ?"""
    for selector in (*_BUY_SELECTORS, *_AVAILABILITY_SELECTORS, *_PRICE_SELECTORS):
        try:
            if node.select_one(selector) is not None:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _classify(
    buttons_text: str, availability_text: str, scope_text: str, buy_box: BuyBox
) -> tuple[AmazonState, tuple[str, ...]]:
    """Du plus spécifique au plus général."""
    hits = _hits(keywords.INVITATION, buttons_text, availability_text, scope_text)
    if hits:
        return AmazonState.INVITATION, hits

    hits = _hits(keywords.PREORDER, buttons_text, availability_text)
    if hits:
        return AmazonState.PREORDER, hits

    # La mention de rupture prime sur un bouton resté en place.
    hits = _hits(keywords.OUT_OF_STOCK, availability_text)
    if hits:
        return AmazonState.OUT_OF_STOCK, hits

    hits = _hits(keywords.AVAILABLE, buttons_text, availability_text)
    if hits or buy_box.has_buy_box:
        indices = hits or ("buy box",)
        # Achetable, mais pas vendu par Amazon : information utile.
        if buy_box.seller and not buy_box.sold_by_amazon:
            return AmazonState.THIRD_PARTY_ONLY, (*indices, "vendeur tiers")
        return AmazonState.AVAILABLE, indices

    hits = _hits(keywords.OUT_OF_STOCK, scope_text)
    if hits:
        return AmazonState.OUT_OF_STOCK, hits

    hits = _hits(keywords.COMING_SOON, availability_text, scope_text)
    if hits:
        return AmazonState.COMING_SOON, hits

    hits = _hits(keywords.UNAVAILABLE, availability_text, scope_text)
    if hits:
        return AmazonState.UNAVAILABLE, hits

    return AmazonState.UNKNOWN, ()


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

    box.has_buy_box = any(_select_one(scope, selector) for selector in _BUY_SELECTORS)

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


def _action_labels(scope) -> tuple[list[str], list[str]]:
    """Libellés d'action retenus, et libellés écartés (pour le débogage)."""
    kept: list[str] = []
    ignored: list[str] = []

    def offer(raw: Optional[str]) -> None:
        text = " ".join((raw or "").split())
        if not text:
            return
        (kept if is_meaningful_label(text) else ignored).append(text)

    for selector in _BUY_SELECTORS:
        tag = _select_one(scope, selector)
        if tag is None:
            continue
        offer(tag.get("value") or tag.get("aria-label")
              or tag.get_text(" ", strip=True))

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
        offer(tag.get("value") if tag.name == "input" else None)
        offer(tag.get("aria-label"))
        if tag.name != "input":
            offer(tag.get_text(" ", strip=True))

    for text in _all_text(scope, _AVAILABILITY_SELECTORS):
        offer(text)

    return (
        list(dict.fromkeys(kept))[:8],
        list(dict.fromkeys(ignored))[:8],
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


def _after_label(text: str, labels: tuple[str, ...]) -> Optional[str]:
    normalised = normalise(text)
    for label in labels:
        index = normalised.find(label)
        if index == -1:
            continue
        tail = text[index + len(label):].strip(" :•|-\n\t")
        value = re.split(r"\s{2,}|[|•]|Expédié par|Vendu par|Ships from|Sold by",
                         tail, maxsplit=1)[0]
        value = " ".join(value.split())[:80]
        if value:
            return value
    return None


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
