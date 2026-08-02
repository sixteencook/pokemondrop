"""Place de marché, langue et pays de livraison d'une page Amazon.

POURQUOI CE MODULE EXISTE
-------------------------
Une même fiche Amazon n'a pas le même contenu selon la place de marché
(`amazon.fr`, `amazon.de`…), la langue et — surtout — le **pays de
livraison** associé à la session. Un ASIN affiché « Demande d'invitation »
depuis la France peut apparaître « Actuellement indisponible » si Amazon
croit livrer aux États-Unis : l'offre n'est simplement pas proposée à cette
destination.

Une requête sans cookie ne porte aucune préférence : Amazon choisit alors
lui-même une destination, souvent les États-Unis. Le moteur analysait donc
une *autre version* de la page que celle vue par l'utilisateur, et en
tirait une conclusion négative parfaitement fausse.

Ce module fait deux choses, et rien d'autre :

  1. **Demander** explicitement la version française avec livraison en
     France, quand l'URL le permet (`preference_for`).
  2. **Constater** ce qu'Amazon a réellement servi (`detect`), pour que le
     parser puisse refuser de conclure si la page ne correspond pas.

Aucune usurpation ici : on transmet les mêmes préférences de langue et de
devise qu'un navigateur ordinaire, et on se contente de lire ce que la
page annonce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.monitors.generic import normalise


@dataclass(frozen=True)
class Marketplace:
    """Une place de marché Amazon et ses réglages naturels."""

    domain: str          # amazon.fr
    host: str            # www.amazon.fr
    country: str         # FR (pays de livraison par défaut)
    country_label: str   # France
    language: str        # fr_FR
    accept_language: str  # fr-FR,fr;q=0.9,en;q=0.5
    currency: str        # EUR
    browser_locale: str  # fr-FR
    timezone: str        # Europe/Paris

    #: Cookie porteur de la langue choisie. Amazon en nomme un par domaine
    #: (`lc-acbfr`, `lc-acbde`…) sauf sur `.com`, qui utilise `lc-main`.
    @property
    def language_cookie(self) -> str:
        if self.domain == "amazon.com":
            return "lc-main"
        return f"lc-acb{self.domain.rsplit('.', 1)[-1]}"


#: Places de marché connues. En ajouter une ne demande aucune logique :
#: une ligne suffit.
MARKETPLACES: dict[str, Marketplace] = {
    market.domain: market
    for market in (
        Marketplace("amazon.fr", "www.amazon.fr", "FR", "France", "fr_FR",
                    "fr-FR,fr;q=0.9,en;q=0.5", "EUR", "fr-FR", "Europe/Paris"),
        Marketplace("amazon.be", "www.amazon.com.be", "BE", "Belgique", "fr_BE",
                    "fr-BE,fr;q=0.9,en;q=0.5", "EUR", "fr-BE", "Europe/Brussels"),
        Marketplace("amazon.de", "www.amazon.de", "DE", "Allemagne", "de_DE",
                    "de-DE,de;q=0.9,en;q=0.5", "EUR", "de-DE", "Europe/Berlin"),
        Marketplace("amazon.es", "www.amazon.es", "ES", "Espagne", "es_ES",
                    "es-ES,es;q=0.9,en;q=0.5", "EUR", "es-ES", "Europe/Madrid"),
        Marketplace("amazon.it", "www.amazon.it", "IT", "Italie", "it_IT",
                    "it-IT,it;q=0.9,en;q=0.5", "EUR", "it-IT", "Europe/Rome"),
        Marketplace("amazon.nl", "www.amazon.nl", "NL", "Pays-Bas", "nl_NL",
                    "nl-NL,nl;q=0.9,en;q=0.5", "EUR", "nl-NL", "Europe/Amsterdam"),
        Marketplace("amazon.co.uk", "www.amazon.co.uk", "GB", "Royaume-Uni",
                    "en_GB", "en-GB,en;q=0.9", "GBP", "en-GB", "Europe/London"),
        Marketplace("amazon.com", "www.amazon.com", "US", "États-Unis", "en_US",
                    "en-US,en;q=0.9", "USD", "en-US", "America/New_York"),
    )
}

#: Place de marché privilégiée par ce projet : Amazon.fr, livraison France.
#: C'est elle qui sert de destination par défaut quand l'URL n'en désigne
#: aucune, et de référence pour juger une page « conforme ».
PREFERRED: Marketplace = MARKETPLACES["amazon.fr"]


# --------------------------------------------------------------------- #
# Reconnaissance du pays de livraison affiché par la page                #
# --------------------------------------------------------------------- #

#: Emplacements du « glow » (le bandeau « Livrer à … » d'Amazon), du plus
#: explicite au moins précis. L'ordre sert la traçabilité : on retient le
#: premier qui parle, et on dit lequel c'était.
DELIVERY_SELECTORS: tuple[str, ...] = (
    "#glow-ingress-line2",
    "#contextualIngressPtLabel_deliveryShortLine",
    "#nav-global-location-slot #glow-ingress-line2",
    "#glow-ingress-block",
    "#nav-global-location-slot",
    "[data-csa-c-slot-id='nav-global-location-slot']",
    "#deliveryBlockMessage",
    "#mir-layout-DELIVERY_BLOCK",
)

#: Libellés de pays, en français et en anglais, ramenés à leur code ISO.
#: Seuls les pays réellement rencontrés sur les fiches surveillées sont
#: listés : un pays inconnu ressort comme « non reconnu », jamais deviné.
COUNTRY_LABELS: dict[str, str] = {
    "france": "FR",
    "belgique": "BE", "belgium": "BE", "belgie": "BE",
    "allemagne": "DE", "germany": "DE", "deutschland": "DE",
    "espagne": "ES", "spain": "ES", "espana": "ES",
    "italie": "IT", "italy": "IT", "italia": "IT",
    "pays-bas": "NL", "pays bas": "NL", "netherlands": "NL", "nederland": "NL",
    "luxembourg": "LU",
    "suisse": "CH", "switzerland": "CH", "schweiz": "CH",
    "royaume-uni": "GB", "royaume uni": "GB", "united kingdom": "GB",
    "etats-unis": "US", "etats unis": "US", "united states": "US", "usa": "US",
    "canada": "CA",
    "portugal": "PT",
    "irlande": "IE", "ireland": "IE",
    "autriche": "AT", "austria": "AT", "osterreich": "AT",
    "pologne": "PL", "poland": "PL", "polska": "PL",
    "suede": "SE", "sweden": "SE",
    "japon": "JP", "japan": "JP",
}

#: Préfixes du glow à retirer avant de chercher un pays.
_DELIVERY_PREFIXES: tuple[str, ...] = (
    "livrer a", "livrer en", "livraison a", "livraison en", "livraison",
    "mettre a jour la position", "deliver to", "delivering to",
    "update location", "choisir le lieu de livraison",
)

#: Code postal français : « Livrer à 75001 » vaut France sans ambiguïté.
_FR_POSTCODE_RE = re.compile(r"\b(?:0[1-9]|[1-8]\d|9[0-8])\d{3}\b")

_LANG_RE = re.compile(r"^([a-z]{2})(?:[-_]([a-z]{2}))?", re.IGNORECASE)


def marketplace_for(url: str) -> Optional[Marketplace]:
    """Place de marché désignée par une URL, ou None si ce n'est pas Amazon."""
    host = (urlsplit(url or "").netloc or "").lower().split(":")[0]
    if not host:
        return None
    for domain, market in MARKETPLACES.items():
        if host == domain or host.endswith("." + domain):
            return market
    return None


# --------------------------------------------------------------------- #
# Ce que l'on DEMANDE                                                    #
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class LocalePreference:
    """Localisation demandée à Amazon pour une requête donnée.

    C'est la trace de ce que *nous* avons exigé — à comparer avec ce que
    la page a réellement renvoyé (`PageLocale`).
    """

    marketplace: Marketplace
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    browser_locale: str = ""
    timezone: str = ""
    applied: bool = True
    note: str = ""

    @property
    def summary(self) -> str:
        """Résumé lisible, destiné aux logs et au dashboard."""
        if not self.applied:
            return f"aucune (— {self.note})" if self.note else "aucune"
        return (
            f"{self.marketplace.domain} · langue {self.marketplace.language} · "
            f"livraison souhaitée {self.marketplace.country_label} · "
            f"devise {self.marketplace.currency}"
        )


def preference_for(url: str) -> LocalePreference:
    """Localisation à demander pour cette URL.

    Amazon.fr en français avec livraison France est privilégié dès que
    l'URL le permet. Une URL pointant explicitement vers une autre place
    de marché est **respectée telle quelle** : réécrire `amazon.de` en
    `amazon.fr` reviendrait à inventer une fiche qui n'existe peut-être
    pas — la règle « ne jamais inventer d'URL produit » vaut ici aussi.
    """
    market = marketplace_for(url) or PREFERRED
    note = "" if market is PREFERRED else (
        f"URL sur {market.domain} : place de marché respectée, "
        f"{PREFERRED.domain} non imposé"
    )

    return LocalePreference(
        marketplace=market,
        url=with_language(url, market),
        # Seule la langue est imposée : le reste des en-têtes est celui du
        # navigateur déclaré par le cœur, inchangé.
        headers={"Accept-Language": market.accept_language},
        cookies={
            # Langue d'affichage choisie, exactement comme le sélecteur de
            # langue du site la pose.
            market.language_cookie: market.language,
            # Devise d'affichage.
            "i18n-prefs": market.currency,
        },
        browser_locale=market.browser_locale,
        timezone=market.timezone,
        applied=True,
        note=note,
    )


def with_language(url: str, market: Marketplace) -> str:
    """Ajoute `language=<locale>` à l'URL, sans écraser un paramètre existant."""
    if not url:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("language", market.language)
    return urlunsplit(
        (parts.scheme or "https", parts.netloc, parts.path,
         urlencode(query), parts.fragment)
    )


# --------------------------------------------------------------------- #
# Ce que l'on CONSTATE                                                   #
# --------------------------------------------------------------------- #

@dataclass
class PageLocale:
    """Localisation réellement servie par la page analysée."""

    marketplace: Optional[Marketplace] = None
    host: str = ""
    language: Optional[str] = None
    language_source: str = ""
    delivery_country: Optional[str] = None
    delivery_label: Optional[str] = None
    delivery_source: str = ""
    currency: Optional[str] = None

    @property
    def marketplace_domain(self) -> str:
        return self.marketplace.domain if self.marketplace else (self.host or "inconnu")

    @property
    def delivery_known(self) -> bool:
        return self.delivery_country is not None

    @property
    def expected_country(self) -> str:
        """Pays de livraison attendu pour cette place de marché."""
        return (self.marketplace or PREFERRED).country

    @property
    def delivers_to_expected_country(self) -> bool:
        """La page est-elle servie pour le bon pays de livraison ?

        Une destination **inconnue** n'est pas comptée comme conforme :
        c'est précisément le cas où l'on ne veut pas conclure au négatif.
        """
        return self.delivery_country == self.expected_country

    @property
    def delivery_summary(self) -> str:
        if not self.delivery_known:
            return "non détecté"
        label = self.delivery_label or self.delivery_country
        return f"{self.delivery_country} ({label}) — via {self.delivery_source}"

    def as_details(self) -> dict[str, str]:
        """Clés exposées dans le snapshot, donc visibles au dashboard."""
        details = {
            "marketplace": self.marketplace_domain,
            "pays_livraison": self.delivery_country or "non détecté",
            "langue": self.language or "non détectée",
        }
        if self.delivery_label:
            details["livraison_libelle"] = self.delivery_label[:80]
        if self.delivery_source:
            details["livraison_selecteur"] = self.delivery_source
        if self.currency:
            details["devise_page"] = self.currency
        return details


def detect(soup, url: str = "") -> PageLocale:
    """Lit la place de marché, la langue et le pays de livraison d'une page."""
    result = PageLocale()
    result.host = (urlsplit(url or "").netloc or "").lower()
    result.marketplace = marketplace_for(url)

    result.language, result.language_source = _detect_language(soup)
    country, label, source = _detect_delivery(soup)
    result.delivery_country = country
    result.delivery_label = label
    result.delivery_source = source
    result.currency = _detect_currency(soup)

    # Sans indication de langue dans le document, celle de la place de
    # marché reste l'hypothèse la plus raisonnable — mais elle est
    # signalée comme telle.
    if result.language is None and result.marketplace is not None:
        result.language = result.marketplace.language
        result.language_source = "défaut de la place de marché"

    return result


def _detect_language(soup) -> tuple[Optional[str], str]:
    html_tag = soup.find("html")
    raw = (html_tag.get("lang") if html_tag else None) or ""
    match = _LANG_RE.match(raw.strip())
    if match:
        language = match.group(1).lower()
        if match.group(2):
            language = f"{language}_{match.group(2).upper()}"
        return language, "<html lang>"

    meta = soup.find("meta", attrs={"property": "og:locale"})
    if meta and meta.get("content"):
        return meta["content"].strip(), "meta og:locale"
    return None, ""


def _detect_delivery(soup) -> tuple[Optional[str], Optional[str], str]:
    """Pays de livraison affiché, son libellé brut et le sélecteur utilisé."""
    for selector in DELIVERY_SELECTORS:
        try:
            node = soup.select_one(selector)
        except Exception:  # noqa: BLE001 — sélecteur refusé par le parseur
            continue
        if node is None:
            continue
        label = " ".join(node.get_text(" ", strip=True).split())
        if not label:
            continue
        country = country_from_label(label)
        if country is not None:
            return country, label[:120], selector
    return None, None, ""


def country_from_label(label: str) -> Optional[str]:
    """Code ISO d'un libellé de livraison (« Livrer à France » → FR)."""
    text = normalise(label)
    for prefix in _DELIVERY_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip(" :,-")
            break

    if not text:
        return None
    if text in COUNTRY_LABELS:
        return COUNTRY_LABELS[text]

    # Le glow affiche souvent « 75001 Paris » : un code postal français
    # est une destination France sans ambiguïté possible.
    if _FR_POSTCODE_RE.search(text) and not _foreign_hint(text):
        return "FR"

    for name, code in COUNTRY_LABELS.items():
        if re.search(rf"\b{re.escape(name)}\b", text):
            return code
    return None


def _foreign_hint(text: str) -> bool:
    """Un pays étranger est nommé : le code postal ne tranche plus."""
    return any(
        code != "FR" and re.search(rf"\b{re.escape(name)}\b", text)
        for name, code in COUNTRY_LABELS.items()
    )


def _detect_currency(soup) -> Optional[str]:
    meta = soup.find("meta", attrs={"itemprop": "priceCurrency"})
    if meta and meta.get("content"):
        return meta["content"].strip().upper()[:3]
    return None
