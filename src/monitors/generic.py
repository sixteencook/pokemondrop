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

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import ClassVar, Iterable

from bs4 import BeautifulSoup

from src.models import (
    Availability,
    CheckDiagnostics,
    OfferState,
    ProductConfig,
    ProductSnapshot,
    PurchaseAction,
    SellerType,
)
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

#: Zones à retirer du périmètre AVANT toute mesure.
#:
#: Tout ce qui change sans que le produit change doit disparaître ici :
#: c'est la première ligne de défense contre les fausses alertes. Un
#: bandeau cookies qui apparaît, une newsletter qui tourne, un carrousel
#: qui pivote ou un encart publicitaire ne doivent jamais atteindre le
#: hash métier ni la résolution d'action.
DEFAULT_NOISE_SELECTORS = (
    # Recommandations et ventes croisées
    '[class*="carousel"], [class*="slider"], [class*="recommend"], '
    '[class*="cross-sell"], [class*="crosssell"], [class*="upsell"], '
    '[class*="similar"], [class*="related"], [class*="also-like"], '
    '[class*="suggestion"], [class*="you-may"], [class*="bought-together"], '
    # Structure de page
    'footer, nav, header, [role="navigation"], [class*="breadcrumb"], '
    # Bandeaux de consentement
    '[class*="cookie"], [id*="cookie"], [id*="onetrust"], [class*="onetrust"], '
    '[id*="didomi"], [class*="didomi"], [id*="consent"], [class*="consent"], '
    # Newsletter et captation
    '[class*="newsletter"], [id*="newsletter"], [class*="subscribe"], '
    # Publicité et contenus sponsorisés
    '[class*="advert"], [id*="advert"], [class*="publicite"], '
    '[class*="sponsor"], [id*="sponsor"], [class*="promo-banner"], '
    # Modales et surcouches
    '[class*="modal"], [class*="popin"], [class*="popup"], [role="dialog"], '
    # Avis et questions
    '[class*="review"], [id*="review"], [class*="rating"], [class*="avis"]'
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
    #: Ce qui a tranché : mot-clé et provenance, en clair.
    decided_by: str = ""

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

        # UNE action d'achat principale : le reste des libellés relevés ne
        # sert qu'au diagnostic.
        action, decided_by = self._resolve_action(buttons, page_text, diag)
        price = self._extract_price(scope) or self._extract_price(soup)
        status_text = self._extract_status(page_text)

        offer = OfferState(
            action=action,
            native_state=action.value,
            has_buy_box=action in (PurchaseAction.ADD_TO_CART,
                                   PurchaseAction.BUY_NOW,
                                   PurchaseAction.PREORDER),
            # Un marchand classique vend son propre stock : il n'y a pas de
            # place de marché, donc rien à surveiller côté vendeur. Laisser
            # UNKNOWN garantit qu'aucun événement de vendeur ne partira.
            seller_type=SellerType.UNKNOWN,
            price=price,
            currency="EUR" if price else None,
        )

        # Le soupçon de page d'attente n'a de sens QUE si l'analyse est restée
        # inconclusive : une page correctement classée n'était pas un mur
        # anti-robot, même si elle est courte.
        if not offer.conclusive:
            diag.interstitial = self._detect_interstitial(html, page_text, diag.title)
            status_text = diag.interstitial or status_text

        diag.decided_by = decided_by
        self._log_diagnostics(product, offer, diag)

        return ProductSnapshot(
            availability=offer.availability,
            price=price,
            buttons=buttons,          # diagnostic uniquement
            status_text=status_text,
            page_exists=True,
            content_hash=offer.business_hash(),
            offer=offer,
            diagnostics=CheckDiagnostics(
                blocked=diag.interstitial is not None,
                blocked_reason=diag.interstitial or (
                    None if offer.conclusive
                    else "aucune action d'achat identifiée"
                ),
            ),
        )

    # ------------------------------------------------------------------ #
    # Journalisation                                                      #
    # ------------------------------------------------------------------ #

    def _log_diagnostics(
        self,
        product: ProductConfig,
        offer: "OfferState",
        diag: ParseDiagnostics,
    ) -> None:
        """Résumé métier au niveau CHECK, détail complet au niveau DEBUG."""
        availability = offer.availability
        log.check(
            "%s — %s | action=%s | %s | prix=%s | hash=%s",
            self.display_name or self.site_name, product.name,
            offer.label, diag.decided_by or "—",
            offer.price or "—", offer.business_hash(),
        )

        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "════ %s — %s ════\n"
                "  URL              : %s\n"
                "  Bloc retenu      : %s\n"
                "  Action principale: %s (%s)\n"
                "  Décidée par      : %s\n"
                "  État métier      : %s\n"
                "  Prix             : %s\n"
                "  Hash métier      : %s\n"
                "  Libellés retenus : %s\n"
                "  Libellés écartés : %d candidats non porteurs de mot-clé\n"
                "  Diagnostic       : %s",
                self.display_name or self.site_name, product.name,
                product.url,
                diag.scope or "—",
                offer.label, offer.action.value,
                diag.decided_by or "—",
                availability.value,
                offer.price or "—",
                offer.business_hash(),
                diag.matched_buttons or "—",
                max(0, len(diag.candidate_buttons) - len(diag.matched_buttons)),
                diag.summary(),
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

    def _resolve_action(
        self, buttons: list[str], page_text: str, diag: ParseDiagnostics
    ) -> tuple[PurchaseAction, str]:
        """L'unique action d'achat proposée par la fiche.

        Priorité : précommande > panier > indisponibilité (bouton) >
        indisponibilité (texte de la page).

        Une **contradiction** dans les boutons — un « Ajouter au panier »
        ET une mention d'indisponibilité côte à côte — ne conclut rien.
        C'est le cas typique d'un bouton résiduel laissé sur une fiche en
        rupture : conclure « disponible » produirait la pire des fausses
        alertes, un faux retour en stock.
        """
        buttons_text = normalise(" | ".join(buttons))

        purchase = (
            [(kw, PurchaseAction.PREORDER) for kw in self._norm_preorder
             if kw in buttons_text]
            + [(kw, PurchaseAction.ADD_TO_CART) for kw in self._norm_cart
               if kw in buttons_text]
        )
        blocked = [kw for kw in self._norm_unavailable if kw in buttons_text]

        if purchase and blocked:
            diag.matched_keywords = [
                f"{purchase[0][0]} (bouton)", f"{blocked[0]} (bouton)",
            ]
            return PurchaseAction.NONE, (
                f"contradiction : « {purchase[0][0]} » et « {blocked[0]} » "
                f"sur la même fiche"
            )

        if purchase:
            keyword, action = purchase[0]
            diag.matched_keywords = [f"{keyword} (bouton)"]
            return action, f"« {keyword} » (bouton)"

        if blocked:
            diag.matched_keywords = [f"{blocked[0]} (bouton)"]
            return self._unavailable_action(blocked[0]), f"« {blocked[0]} » (bouton)"

        matched = [kw for kw in self._norm_unavailable if kw in page_text]
        if matched:
            diag.matched_keywords = [f"{matched[0]} (page)" ]
            return self._unavailable_action(matched[0]), f"« {matched[0]} » (page)"

        return PurchaseAction.NONE, "aucun mot-clé d'achat ni d'indisponibilité"

    @staticmethod
    def _unavailable_action(keyword: str) -> PurchaseAction:
        """Nuance l'indisponibilité : alerte de retour ou rupture sèche."""
        if any(marker in keyword for marker in
               ("alerter", "prevenir", "prevenez", "alertez", "notify")):
            return PurchaseAction.NOTIFY_ME
        if "bientot" in keyword or "coming soon" in keyword:
            return PurchaseAction.COMING_SOON
        return PurchaseAction.CURRENTLY_UNAVAILABLE
