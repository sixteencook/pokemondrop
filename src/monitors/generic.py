"""Monitor générique : analyse HTML par mots-clés, indépendante du site.

Cette implémentation ne dépend d'aucune structure de page spécifique :
elle détecte les signaux universels (boutons « Précommander » /
« Ajouter au panier », prix en euros, mentions d'indisponibilité) et
calcule un hash du contenu significatif de la page.

ROBUSTESSE DE LA COMPARAISON
----------------------------
Tous les textes comparés passent par `normalise()` :
  - accents repliés   → « PRÉCOMMANDER » et « PRECOMMANDER » se valent ;
  - casse uniformisée ;
  - espaces normalisés → l'espace insécable (\\xa0), très fréquente dans les
    boutons e-commerce, ne casse plus « Ajouter au panier ».

Sans cela, un simple `.lower()` laissait passer des boutons pourtant bien
présents dans la page, et le statut restait « unknown ».

Les plugins de sites (plugins/micromania/, plugins/fnac/, …) héritent de
cette classe et la personnalisent via de simples attributs de classe
(mots-clés, sélecteurs), ou surchargent `parse()` pour un HTML exotique —
sans jamais toucher au cœur du projet.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import ClassVar, Iterable

from bs4 import BeautifulSoup

from src.models import Availability, ProductConfig, ProductSnapshot
from src.monitors.base import BaseMonitor
from src.utils.logger import get_logger

log = get_logger("monitors.parse")

# Prix au format européen : « 119,99 € », « 119.99€ », « €119,99 »…
_PRICE_RE = re.compile(r"(\d{1,4}[.,]\d{2})\s*€|€\s*(\d{1,4}[.,]\d{2})")
_WHITESPACE_RE = re.compile(r"\s+")

#: Longueur maximale d'un libellé de bouton retenu (les icônes et textes
#: pour lecteurs d'écran gonflent facilement le contenu d'un bouton).
MAX_BUTTON_LABEL = 100

#: Nombre de libellés de boutons reportés dans les logs de diagnostic.
DIAGNOSTIC_SAMPLE = 12

# Mots-clés par défaut, communs à la plupart des e-commerces francophones.
DEFAULT_PREORDER_KEYWORDS: tuple[str, ...] = (
    "précommander", "précommande", "pré-commander", "preorder", "pre-order",
)
DEFAULT_ADD_TO_CART_KEYWORDS: tuple[str, ...] = (
    "ajouter au panier", "add to cart", "acheter",
)
DEFAULT_UNAVAILABLE_KEYWORDS: tuple[str, ...] = (
    "indisponible",
    "rupture de stock",
    "épuisé",
    "non disponible",
    "victime de son succès",
    "out of stock",
    "sold out",
    "stock épuisé",
    "bientôt disponible",
    "m'alerter",
    "prévenez-moi",
)
DEFAULT_PRICE_SELECTORS = '[class*="price"], [itemprop="price"], [data-price]'

#: Sélecteurs génériques de boutons d'action, au-delà des balises natives.
DEFAULT_BUTTON_SELECTORS = (
    '[role="button"], [class*="btn"], [class*="button"], [class*="add-to-cart"], '
    '[class*="add-to-basket"], [data-testid*="add"], [class*="cta"]'
)

#: Restriction à la fiche du produit principal — DÉSACTIVÉE par défaut.
#
# Deviner ce conteneur est risqué : sur une vraie fiche Micromania, un
# sélecteur générique attrapait « pdp-short-description », un bloc annexe
# qui ne contient pas le bouton d'achat — le statut retombait à « unknown ».
# Le nettoyage du bruit (DEFAULT_NOISE_SELECTORS) suffit à écarter les
# carrousels, sans ce risque.
#
# Un plugin qui CONNAÎT le bon sélecteur de son site peut l'activer :
#     product_scope_selectors = "#product-content"
DEFAULT_PRODUCT_SCOPE_SELECTORS = ""

#: Zones à retirer du périmètre : recommandations, ventes croisées, pied de page.
DEFAULT_NOISE_SELECTORS = (
    '[class*="carousel"], [class*="slider"], [class*="recommend"], '
    '[class*="cross-sell"], [class*="crosssell"], [class*="upsell"], '
    '[class*="similar"], [class*="related"], [class*="also-like"], '
    '[class*="suggestion"], footer, nav, header'
)

#: Plafond de libellés retenus : au-delà, le hash deviendrait instable et
#: les messages d'alerte illisibles.
MAX_ACTION_BUTTONS = 8

#: Marqueurs de pages d'attente anti-robot ou de murs de consentement,
#: souvent servies avec un code HTTP 200 tout à fait normal.
_INTERSTITIAL_MARKERS: tuple[tuple[str, str], ...] = (
    ("cf-browser-verification", "Cloudflare"),
    ("challenge-platform", "Cloudflare"),
    ("cf_chl", "Cloudflare"),
    ("just a moment", "Cloudflare"),
    ("attention required", "Cloudflare"),
    ("datadome", "DataDome"),
    ("px-captcha", "PerimeterX"),
    ("_incapsula_", "Imperva"),
    ("incapsula incident", "Imperva"),
    ("vous etes une personne", "vérification humaine"),
    ("verification que vous etes", "vérification humaine"),
    ("enable javascript and cookies", "page d'attente JavaScript"),
    ("veuillez activer javascript", "page d'attente JavaScript"),
)


def normalise(text: str) -> str:
    """Replie les accents, uniformise la casse et les espaces.

    « Précommander\\xa0! » → « precommander ! »
    """
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return _WHITESPACE_RE.sub(" ", folded.lower()).strip()


def _normalise_all(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(normalise(value) for value in values)


@dataclass
class ParseDiagnostics:
    """Trace d'une analyse, journalisée pour comprendre un statut inattendu."""

    html_length: int = 0
    title: str = ""
    text_length: int = 0
    candidate_buttons: list[str] = field(default_factory=list)
    matched_buttons: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    interstitial: str | None = None
    scope: str = ""

    def summary(self) -> str:
        sample = ", ".join(self.candidate_buttons[:DIAGNOSTIC_SAMPLE]) or "aucun"
        return (
            f"html={self.html_length / 1024:.1f} Ko, texte={self.text_length} car., "
            f"titre=« {self.title or '—'} », périmètre={self.scope or '—'}, "
            f"boutons candidats={len(self.candidate_buttons)} [{sample}], "
            f"retenus={self.matched_buttons or '—'}, "
            f"mots-clés={self.matched_keywords or 'aucun'}"
        )


class GenericHtmlMonitor(BaseMonitor):
    """Analyse générique d'une fiche produit e-commerce.

    Points d'extension pour les plugins (attributs de classe) :
        preorder_keywords / add_to_cart_keywords / unavailable_keywords
        price_selectors / button_selectors / cookie_selectors
        requires_javascript
    """

    site_name: ClassVar[str] = "generic"
    display_name: ClassVar[str] = "Générique"

    preorder_keywords: ClassVar[tuple[str, ...]] = DEFAULT_PREORDER_KEYWORDS
    add_to_cart_keywords: ClassVar[tuple[str, ...]] = DEFAULT_ADD_TO_CART_KEYWORDS
    unavailable_keywords: ClassVar[tuple[str, ...]] = DEFAULT_UNAVAILABLE_KEYWORDS
    price_selectors: ClassVar[str] = DEFAULT_PRICE_SELECTORS
    button_selectors: ClassVar[str] = DEFAULT_BUTTON_SELECTORS
    product_scope_selectors: ClassVar[str] = DEFAULT_PRODUCT_SCOPE_SELECTORS
    noise_selectors: ClassVar[str] = DEFAULT_NOISE_SELECTORS

    def parse(self, html: str, product: ProductConfig) -> ProductSnapshot:
        soup = BeautifulSoup(html, "lxml")
        diag = ParseDiagnostics(html_length=len(html))

        title_tag = soup.find("title")
        diag.title = title_tag.get_text(strip=True)[:120] if title_tag else ""

        # Le bruit (navigation, pied de page, carrousels de recommandations)
        # est retiré AVANT toute mesure : ni le texte, ni les boutons, ni le
        # prix ne doivent en dépendre.
        self._strip_noise(soup)

        page_text = normalise(soup.get_text(" ", strip=True))
        diag.text_length = len(page_text)

        # Puis restriction à la fiche du produit principal quand elle est
        # identifiable — sinon la page nettoyée fait déjà l'affaire.
        scope = self._product_scope(soup, diag)

        candidates = self._collect_button_labels(scope)
        diag.candidate_buttons = candidates
        buttons = self._filter_action_buttons(candidates)[:MAX_ACTION_BUTTONS]
        diag.matched_buttons = list(buttons)

        buttons_text = normalise(" | ".join(buttons))
        availability = self._classify(buttons_text, page_text, diag)
        price = self._extract_price(scope) or self._extract_price(soup)
        status_text = self._extract_status(page_text)

        # Le soupçon de page d'attente n'a de sens QUE si l'analyse est restée
        # inconclusive : une page correctement classée n'était pas un mur
        # anti-robot, même si elle est courte.
        if availability is Availability.UNKNOWN:
            diag.interstitial = self._detect_interstitial(html, page_text, diag.title)
            status_text = diag.interstitial or status_text

        self._log_diagnostics(product, availability, price, diag)

        return ProductSnapshot(
            availability=availability,
            price=price,
            buttons=buttons,
            status_text=status_text,
            page_exists=True,
            content_hash=self._hash_significant_content(buttons, price, availability),
        )

    # ------------------------------------------------------------------ #
    # Journalisation                                                      #
    # ------------------------------------------------------------------ #

    def _log_diagnostics(
        self,
        product: ProductConfig,
        availability: Availability,
        price: str | None,
        diag: ParseDiagnostics,
    ) -> None:
        """Trace systématique (niveau CHECK : fichiers de logs + dashboard)."""
        log.check(
            "Analyse %s — %s : %s → statut=%s, prix=%s",
            self.site_name, product.name, diag.summary(),
            availability.value, price or "—",
        )

        if diag.interstitial:
            log.error(
                "%s : page d'attente détectée (%s) au lieu de la fiche produit. "
                "Le contenu réel n'est pas accessible par simple requête HTTP.",
                product.name, diag.interstitial,
            )
        elif availability is Availability.UNKNOWN:
            where = (
                f"plugins/{self.site_name}/keywords.py"
                if self.site_name != "generic"
                else "un plugin dédié au site"
            )
            log.error(
                "%s : statut indéterminé. %d bouton(s) candidat(s), aucun mot-clé "
                "d'achat ni d'indisponibilité trouvé — la fiche est probablement "
                "rendue en JavaScript, ou son vocabulaire diffère (à compléter "
                "dans %s).",
                product.name, len(diag.candidate_buttons), where,
            )

    # ------------------------------------------------------------------ #
    # Extraction                                                          #
    # ------------------------------------------------------------------ #

    @property
    def _norm_preorder(self) -> tuple[str, ...]:
        return _normalise_all(self.preorder_keywords)

    @property
    def _norm_cart(self) -> tuple[str, ...]:
        return _normalise_all(self.add_to_cart_keywords)

    @property
    def _norm_unavailable(self) -> tuple[str, ...]:
        return _normalise_all(self.unavailable_keywords)

    @property
    def _all_keywords(self) -> tuple[str, ...]:
        return self._norm_preorder + self._norm_cart + self._norm_unavailable

    def _detect_interstitial(
        self, html: str, page_text: str, title: str
    ) -> str | None:
        """Repère une page anti-robot / mur de consentement servie en HTTP 200."""
        haystack = f"{normalise(title)} {page_text} {html[:4000].lower()}"
        for marker, label in _INTERSTITIAL_MARKERS:
            if marker in haystack:
                return f"page d'attente {label}"
        # Une fiche produit fait toujours plusieurs milliers de caractères.
        if len(page_text) < 200:
            return "page quasi vide (contenu rendu côté client ?)"
        return None

    #: Contenu minimal pour qu'un conteneur soit considéré comme la fiche.
    MIN_SCOPE_TEXT = 200

    def _strip_noise(self, soup: BeautifulSoup) -> None:
        """Retire en place les zones parasites de la page.

        L'arbre vient d'être construit dans `parse()` : il n'est partagé
        avec personne, la modification est donc sûre.
        """
        try:
            for noise in soup.select(self.noise_selectors):
                noise.decompose()
        except Exception:  # noqa: BLE001 — sélecteur de plugin invalide
            log.error("Sélecteur de bruit invalide pour le site %s.", self.site_name)

    def _product_scope(self, soup: BeautifulSoup, diag: ParseDiagnostics):
        """Conteneur de la fiche produit, ou la page nettoyée en repli."""
        if not self.product_scope_selectors:
            diag.scope = "page nettoyée"
            return soup
        try:
            containers = soup.select(self.product_scope_selectors)
        except Exception:  # noqa: BLE001 — sélecteur de plugin invalide
            log.error("Sélecteur de périmètre invalide pour le site %s.", self.site_name)
            containers = []

        # Le plus petit conteneur AYANT DU CONTENU : le plus proche du produit,
        # sans retenir un élément décoratif qui se trouverait correspondre.
        meaningful = [
            tag for tag in containers
            if len(tag.get_text(" ", strip=True)) >= self.MIN_SCOPE_TEXT
        ]
        if not meaningful:
            diag.scope = "page nettoyée"
            return soup

        scope = min(meaningful, key=lambda tag: len(tag.get_text(" ", strip=True)))
        label = (scope.get("class") or [""])[0] or scope.get("id") or ""
        diag.scope = f"<{scope.name}> {label}".strip()
        return scope

    def _collect_button_labels(self, soup: BeautifulSoup) -> list[str]:
        """TOUS les libellés d'action de la page, mots-clés ou non.

        Sert au diagnostic : savoir ce que la page contient réellement est
        la première chose à vérifier quand un statut reste « unknown ».
        """
        labels: list[str] = []

        def add(raw: str | None) -> None:
            if not raw:
                return
            text = _WHITESPACE_RE.sub(" ", raw).strip()
            if 0 < len(text) <= MAX_BUTTON_LABEL:
                labels.append(text)

        for tag in soup.find_all(["button", "a", "input"]):
            if tag.name == "input":
                if tag.get("type") not in ("submit", "button"):
                    continue
                add(tag.get("value"))
            else:
                add(tag.get_text(" ", strip=True))
            add(tag.get("aria-label"))
            add(tag.get("title"))

        # Boutons personnalisés (div/span stylés, composants applicatifs).
        try:
            for tag in soup.select(self.button_selectors):
                if tag.name in ("button", "a", "input"):
                    continue  # déjà couvert ci-dessus
                add(tag.get_text(" ", strip=True))
                add(tag.get("aria-label"))
        except Exception:  # noqa: BLE001 — sélecteur de plugin invalide
            log.error("Sélecteur de boutons invalide pour le site %s.", self.site_name)

        return list(dict.fromkeys(labels))  # dédoublonné, ordre conservé

    def _filter_action_buttons(self, candidates: list[str]) -> list[str]:
        """Ne conserve que les libellés porteurs d'un mot-clé connu."""
        keywords = self._all_keywords
        return [
            label for label in candidates
            if any(keyword in normalise(label) for keyword in keywords)
        ]

    def _extract_price(self, soup: BeautifulSoup) -> str | None:
        """Premier prix trouvé — d'abord dans les balises « prix », sinon la page."""
        try:
            tags = soup.select(self.price_selectors)
        except Exception:  # noqa: BLE001 — sélecteur de plugin invalide
            log.error("Sélecteur de prix invalide pour le site %s.", self.site_name)
            tags = []
        for tag in tags:
            match = _PRICE_RE.search(tag.get_text(" ", strip=True))
            if match:
                return f"{(match.group(1) or match.group(2)).replace('.', ',')} €"
        match = _PRICE_RE.search(soup.get_text(" ", strip=True))
        if match:
            return f"{(match.group(1) or match.group(2)).replace('.', ',')} €"
        return None

    def _extract_status(self, page_text: str) -> str | None:
        for keyword in self._norm_unavailable:
            if keyword in page_text:
                return keyword
        return None

    def _classify(
        self, buttons_text: str, page_text: str, diag: ParseDiagnostics
    ) -> Availability:
        """Priorité : précommande > panier > indisponible > inconnu."""
        matched = [kw for kw in self._norm_preorder if kw in buttons_text]
        if matched:
            diag.matched_keywords = [f"{kw} (bouton)" for kw in matched]
            return Availability.PREORDER

        matched = [kw for kw in self._norm_cart if kw in buttons_text]
        if matched:
            diag.matched_keywords = [f"{kw} (bouton)" for kw in matched]
            return Availability.IN_STOCK

        matched = [kw for kw in self._norm_unavailable if kw in buttons_text]
        if matched:
            diag.matched_keywords = [f"{kw} (bouton)" for kw in matched]
            return Availability.UNAVAILABLE

        matched = [kw for kw in self._norm_unavailable if kw in page_text]
        if matched:
            diag.matched_keywords = [f"{kw} (page)" for kw in matched]
            return Availability.UNAVAILABLE

        return Availability.UNKNOWN

    def _hash_significant_content(
        self, buttons: list[str], price: str | None, availability: Availability
    ) -> str:
        """Hash des seuls éléments significatifs (pas du HTML brut, trop volatil)."""
        payload = "|".join([availability.value, price or "", *sorted(buttons)])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
