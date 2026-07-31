"""Monitor Amazon.

Contrairement au monitor générique, celui-ci n'analyse pas la page par
mots-clés épars : il délègue à `parser.py`, qui rend un état Amazon
typé (`AmazonState`) puis le traduit vers le vocabulaire du cœur.

Escalade navigateur : héritée de BaseMonitor et volontairement passive.
Amazon sert souvent une page d'interception en HTTP 200 ; le parser la
reconnaît et rend UNKNOWN, ce qui déclenche une seconde tentative avec
Chromium. Une page lisible en HTTP n'ouvre JAMAIS de navigateur.
"""

from __future__ import annotations

from typing import ClassVar

from src.models import ProductConfig, ProductSnapshot
from src.monitors.base import BaseMonitor
from src.utils.logger import get_logger

from . import parser

log = get_logger("monitors.amazon")


class AmazonMonitor(BaseMonitor):
    """Surveillance d'une fiche produit Amazon."""

    site_name: ClassVar[str] = "amazon"
    display_name: ClassVar[str] = "Amazon"

    #: Bandeaux de consentement Amazon (capture d'écran et rendu de secours).
    cookie_selectors: ClassVar[tuple[str, ...]] = (
        "#sp-cc-accept",
        'input[name="accept"]',
        "#a-autoid-0",
        'button[data-cel-widget="sp-cc-accept"]',
    )

    #: La fiche est lisible en HTTP dans la majorité des cas : on n'impose
    #: pas le navigateur, on l'escalade seulement si l'analyse échoue.
    requires_javascript: ClassVar[bool] = False

    async def check(self, product: ProductConfig) -> ProductSnapshot:
        """Normalise l'URL avant toute requête.

        Un lien affilié, sponsorisé ou truffé de `ref=` désigne le même
        produit : le ramener à sa forme canonique évite de surveiller deux
        fois la même fiche et stabilise le suivi.
        """
        canonical = parser.canonical_url(product.url)
        if canonical != product.url:
            log.check("URL Amazon normalisée : %s → %s", product.url, canonical)
            from dataclasses import replace

            product = replace(product, url=canonical)
        return await super().check(product)

    def parse(self, html: str, product: ProductConfig) -> ProductSnapshot:
        analysis = parser.analyse(html)

        if analysis.bot_wall:
            log.check(
                "%s : page d'interception Amazon — nouvelle tentative avec "
                "le navigateur.", product.name,
            )
            return ProductSnapshot(page_exists=True, status_text="page d'interception")

        details = analysis.buy_box.as_details()
        details["etat_amazon"] = analysis.state.value
        asin = parser.extract_asin(product.url)
        if asin:
            details["asin"] = asin

        log.check(
            "Analyse amazon — %s : état=%s (%s), prix=%s, vendeur=%s, "
            "buy box=%s, boutons=%s",
            product.name, analysis.state.value,
            ", ".join(analysis.matched) or "aucun indice",
            analysis.buy_box.price or "—",
            analysis.buy_box.seller or "—",
            "oui" if analysis.buy_box.has_buy_box else "non",
            analysis.buttons[:4] or "—",
        )

        return ProductSnapshot(
            availability=analysis.availability,
            price=analysis.buy_box.price,
            buttons=analysis.buttons[:8],
            status_text=analysis.label,
            page_exists=True,
            content_hash=self._hash(analysis),
            details=details,
        )

    def _hash(self, analysis: parser.PageAnalysis) -> str:
        """Empreinte des seuls éléments décisifs.

        Le vendeur et l'expéditeur en sont volontairement absents : Amazon
        fait tourner ses marchands, et cela déclencherait des alertes sans
        rapport avec la disponibilité.
        """
        import hashlib

        payload = "|".join([
            analysis.state.value,
            analysis.buy_box.price or "",
            "buybox" if analysis.buy_box.has_buy_box else "",
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
