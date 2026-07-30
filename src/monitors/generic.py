"""Monitor générique : analyse HTML par mots-clés, indépendante du site.

Cette implémentation ne dépend d'aucune structure de page spécifique :
elle détecte les signaux universels (boutons « Précommander » /
« Ajouter au panier », prix en euros, mentions d'indisponibilité) et
calcule un hash du contenu significatif de la page.

Les plugins de sites (plugins/micromania/, plugins/fnac/, …) en héritent
et personnalisent le comportement via de simples attributs de classe :

    class MicromaniaMonitor(GenericHtmlMonitor):
        preorder_keywords = (...)      # depuis plugins/micromania/keywords.py
        price_selectors = "..."        # depuis plugins/micromania/selectors.py

ou, pour un site au HTML vraiment exotique, en surchargeant `parse()`
dans leur parser.py — sans jamais toucher au cœur du projet.
"""

from __future__ import annotations

import hashlib
import re
from typing import ClassVar

from bs4 import BeautifulSoup

from src.models import Availability, ProductConfig, ProductSnapshot
from src.monitors.base import BaseMonitor

# Prix au format européen : « 119,99 € », « 119.99€ », « €119,99 »…
_PRICE_RE = re.compile(r"(\d{1,4}[.,]\d{2})\s*€|€\s*(\d{1,4}[.,]\d{2})")

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


class GenericHtmlMonitor(BaseMonitor):
    """Analyse générique d'une fiche produit e-commerce.

    Points d'extension pour les plugins (attributs de classe) :
        preorder_keywords / add_to_cart_keywords / unavailable_keywords
        price_selectors
    """

    site_name: ClassVar[str] = "generic"
    display_name: ClassVar[str] = "Générique"

    preorder_keywords: ClassVar[tuple[str, ...]] = DEFAULT_PREORDER_KEYWORDS
    add_to_cart_keywords: ClassVar[tuple[str, ...]] = DEFAULT_ADD_TO_CART_KEYWORDS
    unavailable_keywords: ClassVar[tuple[str, ...]] = DEFAULT_UNAVAILABLE_KEYWORDS
    price_selectors: ClassVar[str] = DEFAULT_PRICE_SELECTORS

    def parse(self, html: str, product: ProductConfig) -> ProductSnapshot:
        soup = BeautifulSoup(html, "lxml")

        buttons = self._extract_buttons(soup)
        buttons_text = " | ".join(buttons).lower()
        page_text = soup.get_text(" ", strip=True).lower()

        availability = self._classify(buttons_text, page_text)
        price = self._extract_price(soup)
        status_text = self._extract_status(page_text)

        return ProductSnapshot(
            availability=availability,
            price=price,
            buttons=buttons,
            status_text=status_text,
            page_exists=True,
            content_hash=self._hash_significant_content(buttons, price, availability),
        )

    # ------------------------------------------------------------------ #
    # Extraction                                                          #
    # ------------------------------------------------------------------ #

    @property
    def _all_keywords(self) -> tuple[str, ...]:
        return self.preorder_keywords + self.add_to_cart_keywords + self.unavailable_keywords

    def _extract_buttons(self, soup: BeautifulSoup) -> list[str]:
        """Texte de tous les boutons / liens d'action visibles de la page."""
        texts: list[str] = []
        for tag in soup.find_all(["button", "a", "input"]):
            if tag.name == "input":
                if tag.get("type") not in ("submit", "button"):
                    continue
                text = (tag.get("value") or "").strip()
            else:
                text = tag.get_text(" ", strip=True)
            if 0 < len(text) <= 60:
                lowered = text.lower()
                if any(kw in lowered for kw in self._all_keywords):
                    texts.append(text)
        # Dédoublonnage en conservant l'ordre.
        return list(dict.fromkeys(texts))

    def _extract_price(self, soup: BeautifulSoup) -> str | None:
        """Premier prix trouvé — d'abord dans les balises « prix », sinon la page."""
        for tag in soup.select(self.price_selectors):
            match = _PRICE_RE.search(tag.get_text(" ", strip=True))
            if match:
                return f"{(match.group(1) or match.group(2)).replace('.', ',')} €"
        match = _PRICE_RE.search(soup.get_text(" ", strip=True))
        if match:
            return f"{(match.group(1) or match.group(2)).replace('.', ',')} €"
        return None

    def _extract_status(self, page_text: str) -> str | None:
        for keyword in self.unavailable_keywords:
            if keyword in page_text:
                return keyword
        return None

    def _classify(self, buttons_text: str, page_text: str) -> Availability:
        """Priorité : précommande > panier > indisponible > inconnu."""
        if any(kw in buttons_text for kw in self.preorder_keywords):
            return Availability.PREORDER
        if any(kw in buttons_text for kw in self.add_to_cart_keywords):
            return Availability.IN_STOCK
        if any(kw in buttons_text for kw in self.unavailable_keywords) or any(
            kw in page_text for kw in self.unavailable_keywords
        ):
            return Availability.UNAVAILABLE
        return Availability.UNKNOWN

    def _hash_significant_content(
        self, buttons: list[str], price: str | None, availability: Availability
    ) -> str:
        """Hash des seuls éléments significatifs (pas du HTML brut, trop volatil)."""
        payload = "|".join([availability.value, price or "", *sorted(buttons)])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
