"""Analyse d'une fiche Amazon : états, buy box, URL canonique.

Toute la connaissance d'Amazon du projet tient dans ce fichier et dans
`keywords.py`. Le cœur, lui, ne voit qu'un `ProductSnapshot`.

Trois principes de robustesse :

  1. Aucun sélecteur n'est indispensable. On combine plusieurs ancrages
     connus (#availability, #add-to-cart-button, #outOfStock…) avec une
     lecture du texte normalisé : si Amazon renomme un identifiant, le
     texte reste.
  2. Les états sont évalués du plus spécifique au plus général —
     l'invitation avant la précommande, la précommande avant le stock.
  3. Une page d'interception (robot, connexion) n'est JAMAIS interprétée
     comme une rupture : elle rend UNKNOWN, ce qui déclenche l'escalade
     vers le navigateur côté cœur.
"""

from __future__ import annotations

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

    Plus riche que la disponibilité générique du cœur : c'est cette
    précision qui permet de distinguer une demande d'invitation d'une
    vraie précommande.
    """

    AVAILABLE = "available"
    INVITATION = "invitation"
    PREORDER = "preorder"
    COMING_SOON = "coming_soon"
    OUT_OF_STOCK = "out_of_stock"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


#: Traduction vers le vocabulaire du cœur.
#:
#: INVITATION est ramené à PREORDER : la page est vivante et l'utilisateur
#: doit agir, ce qui est bien le signal attendu pour un drop. Le libellé
#: exact reste dans `status_text` et `details` pour lever toute ambiguïté.
STATE_TO_AVAILABILITY: dict[AmazonState, Availability] = {
    AmazonState.AVAILABLE: Availability.IN_STOCK,
    AmazonState.PREORDER: Availability.PREORDER,
    AmazonState.INVITATION: Availability.PREORDER,
    AmazonState.COMING_SOON: Availability.UNAVAILABLE,
    AmazonState.OUT_OF_STOCK: Availability.UNAVAILABLE,
    AmazonState.UNAVAILABLE: Availability.UNAVAILABLE,
    AmazonState.UNKNOWN: Availability.UNKNOWN,
}

#: Libellé lisible de chaque état, repris dans les alertes.
STATE_LABELS: dict[AmazonState, str] = {
    AmazonState.AVAILABLE: "disponible",
    AmazonState.INVITATION: "demande d'invitation",
    AmazonState.PREORDER: "précommande",
    AmazonState.COMING_SOON: "bientôt disponible",
    AmazonState.OUT_OF_STOCK: "rupture de stock",
    AmazonState.UNAVAILABLE: "indisponible",
    AmazonState.UNKNOWN: "état indéterminé",
}

#: ASIN : 10 caractères alphanumériques, toujours en majuscules.
ASIN_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|product|-)/([A-Z0-9]{10})(?:[/?#]|$)", re.IGNORECASE
)
_ASIN_PARAM_RE = re.compile(r"[?&](?:asin|ASIN)=([A-Z0-9]{10})", re.IGNORECASE)

_PRICE_RE = re.compile(r"(\d{1,4}(?:[   ]?\d{3})*[.,]\d{2})")
_CURRENCY_SIGNS = {"€": "EUR", "$": "USD", "£": "GBP"}

#: Ancrages historiquement stables de la fiche produit.
_BUY_SELECTORS = (
    "#add-to-cart-button", "#buy-now-button", "#submit.add-to-cart",
    "#one-click-button", "input[name='submit.add-to-cart']",
)
_PRICE_SELECTORS = (
    "#corePrice_feature_div .a-offscreen", "#corePriceDisplay_desktop_feature_div "
    ".a-offscreen", ".priceToPay .a-offscreen", "#price_inside_buybox",
    "#newBuyBoxPrice", ".a-price .a-offscreen",
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
    """Ramène toute forme d'URL Amazon à `https://<hôte>/dp/<ASIN>`.

    Absorbe les liens affiliés (`?tag=`), sponsorisés (`/gp/slredirect/`),
    les `ref=`, `utm_*`, les chemins localisés et les variantes `gp/product`.
    Une URL non reconnue est rendue telle quelle : mieux vaut surveiller
    une URL exotique que de la casser.
    """
    if not url:
        return url
    parts = urlsplit(url.strip())
    host = (parts.netloc or default_host).lower()
    if host.startswith("www."):
        host = host  # conservé : amazon impose www sur la plupart des domaines
    asin = extract_asin(url)
    if not asin:
        # Pas d'ASIN : on nettoie au moins la requête et l'ancre.
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

    def as_details(self) -> dict[str, str]:
        """Représentation plate, telle qu'exposée par le cœur."""
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
class PageAnalysis:
    """Analyse complète d'une page Amazon."""

    state: AmazonState = AmazonState.UNKNOWN
    title: Optional[str] = None
    buttons: list[str] = field(default_factory=list)
    buy_box: BuyBox = field(default_factory=BuyBox)
    bot_wall: bool = False
    matched: tuple[str, ...] = ()

    @property
    def availability(self) -> Availability:
        return STATE_TO_AVAILABILITY[self.state]

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]


# --------------------------------------------------------------------- #
# Analyse                                                                #
# --------------------------------------------------------------------- #

def analyse(html: str) -> PageAnalysis:
    """Lit une page Amazon et en déduit l'état, la buy box et les boutons."""
    soup = BeautifulSoup(html, "lxml")
    result = PageAnalysis()

    page_text = normalise(soup.get_text(" ", strip=True))
    result.bot_wall = _hits(keywords.BOT_WALL, page_text) != ()
    if result.bot_wall:
        # Page d'interception : on ne conclut RIEN, le cœur ré-essaiera
        # avec un vrai navigateur.
        result.state = AmazonState.UNKNOWN
        return result

    result.title = _first_text(soup, _TITLE_SELECTORS)
    result.buttons = _action_labels(soup)
    result.buy_box = _read_buy_box(soup, page_text)

    buttons_text = normalise(" | ".join(result.buttons))
    availability_text = normalise(
        " ".join(_all_text(soup, _AVAILABILITY_SELECTORS))
    )
    result.state, result.matched = _classify(
        buttons_text, availability_text, page_text, bool(result.buy_box.has_buy_box)
    )
    return result


def _classify(
    buttons_text: str, availability_text: str, page_text: str, has_buy_box: bool
) -> tuple[AmazonState, tuple[str, ...]]:
    """Du plus spécifique au plus général."""
    # 1. Invitation : formulation très particulière, jamais ambiguë.
    hits = _hits(keywords.INVITATION, buttons_text, availability_text, page_text)
    if hits:
        return AmazonState.INVITATION, hits

    # 2. Précommande : présente dans le bouton d'achat.
    hits = _hits(keywords.PREORDER, buttons_text, availability_text)
    if hits:
        return AmazonState.PREORDER, hits

    # 3. Rupture : la mention prime sur un bouton résiduel.
    hits = _hits(keywords.OUT_OF_STOCK, availability_text)
    if hits:
        return AmazonState.OUT_OF_STOCK, hits

    # 4. Achat possible : bouton présent, ou mention explicite de stock.
    hits = _hits(keywords.AVAILABLE, buttons_text, availability_text)
    if hits or has_buy_box:
        return AmazonState.AVAILABLE, hits or ("buy box",)

    # 5. Rupture mentionnée ailleurs dans la page.
    hits = _hits(keywords.OUT_OF_STOCK, page_text)
    if hits:
        return AmazonState.OUT_OF_STOCK, hits

    # 6. Sortie annoncée.
    hits = _hits(keywords.COMING_SOON, availability_text, page_text)
    if hits:
        return AmazonState.COMING_SOON, hits

    # 7. Indisponibilité durable.
    hits = _hits(keywords.UNAVAILABLE, availability_text, page_text)
    if hits:
        return AmazonState.UNAVAILABLE, hits

    return AmazonState.UNKNOWN, ()


def _read_buy_box(soup: BeautifulSoup, page_text: str) -> BuyBox:
    """Prix, devise, vendeur, expéditeur, variation et édition."""
    box = BuyBox()

    raw_price = _first_text(soup, _PRICE_SELECTORS)
    if raw_price:
        box.price, box.currency = _parse_price(raw_price)
    if box.price is None:
        match = _PRICE_RE.search(page_text)
        if match:
            box.price = _clean_amount(match.group(1))

    box.has_buy_box = any(soup.select_one(selector) for selector in _BUY_SELECTORS)

    merchant_text = " ".join(_all_text(soup, _MERCHANT_SELECTORS))
    box.seller = _after_label(merchant_text, keywords.SOLD_BY)
    box.shipped_by = _after_label(merchant_text, keywords.SHIPPED_BY)

    availability_text = " ".join(_all_text(soup, _AVAILABILITY_SELECTORS)).strip()
    if availability_text:
        box.stock_note = " ".join(availability_text.split())[:120]

    box.variation = _selected_variation(soup)
    box.edition = _labelled_value(soup, ("edition", "format", "plateforme", "platform"))
    return box


# --------------------------------------------------------------------- #
# Utilitaires                                                            #
# --------------------------------------------------------------------- #

def _hits(patterns: tuple[str, ...], *haystacks: str) -> tuple[str, ...]:
    joined = " ".join(haystack for haystack in haystacks if haystack)
    return tuple(pattern for pattern in patterns if pattern in joined)


def _first_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> Optional[str]:
    for selector in selectors:
        try:
            tag = soup.select_one(selector)
        except Exception:  # noqa: BLE001 — sélecteur invalide
            continue
        if tag is None:
            continue
        text = tag.get_text(" ", strip=True) or (tag.get("value") or "")
        text = " ".join(text.split())
        if text:
            return text
    return None


def _all_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> list[str]:
    texts: list[str] = []
    for selector in selectors:
        try:
            for tag in soup.select(selector):
                text = " ".join(tag.get_text(" ", strip=True).split())
                if text:
                    texts.append(text)
        except Exception:  # noqa: BLE001
            continue
    return texts


def _action_labels(soup: BeautifulSoup) -> list[str]:
    """Libellés des boutons d'achat et de la zone de disponibilité."""
    labels: list[str] = []

    for selector in _BUY_SELECTORS:
        try:
            tag = soup.select_one(selector)
        except Exception:  # noqa: BLE001
            continue
        if tag is None:
            continue
        text = (tag.get("value") or tag.get("aria-label")
                or tag.get_text(" ", strip=True) or "")
        text = " ".join(text.split())
        if text:
            labels.append(text)

    for tag in soup.find_all(["input", "button", "a", "span"], limit=400):
        candidate = (tag.get("value") if tag.name == "input" else None) or (
            tag.get("aria-label") or ""
        )
        text = " ".join((candidate or "").split())
        if 3 <= len(text) <= 80:
            labels.append(text)

    labels.extend(_all_text(soup, _AVAILABILITY_SELECTORS))
    return list(dict.fromkeys(labels))[:12]


def _parse_price(raw: str) -> tuple[Optional[str], Optional[str]]:
    currency = next(
        (code for sign, code in _CURRENCY_SIGNS.items() if sign in raw), None
    )
    match = _PRICE_RE.search(raw)
    return (_clean_amount(match.group(1)) if match else None), currency


def _clean_amount(raw: str) -> str:
    """« 1 234,56 » → « 1234,56 € » (format d'affichage du projet)."""
    cleaned = raw.replace(" ", "").replace(" ", "").replace(" ", "")
    return f"{cleaned.replace('.', ',')} €"


def _after_label(text: str, labels: tuple[str, ...]) -> Optional[str]:
    """Valeur suivant « Vendu par » / « Expédié par » dans la buy box."""
    normalised = normalise(text)
    for label in labels:
        index = normalised.find(label)
        if index == -1:
            continue
        # On repart du texte d'origine pour conserver la casse réelle.
        tail = text[index + len(label):].strip(" :•|-\n\t")
        value = re.split(r"\s{2,}|[|•]|Expédié par|Vendu par|Ships from|Sold by",
                         tail, maxsplit=1)[0]
        value = " ".join(value.split())[:80]
        if value:
            return value
    return None


def _selected_variation(soup: BeautifulSoup) -> Optional[str]:
    """Variante sélectionnée (taille, couleur, édition)."""
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
    """Valeur d'une ligne du tableau de caractéristiques."""
    for row in soup.select("tr, li"):
        text = normalise(row.get_text(" ", strip=True))
        if any(text.startswith(label) for label in labels):
            raw = " ".join(row.get_text(" ", strip=True).split())
            parts = re.split(r"[:‎]", raw, maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()[:80]
    return None
