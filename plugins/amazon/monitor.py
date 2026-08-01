"""Monitor Amazon — volontairement conservateur.

Le parser rend une analyse motivée ; ce monitor la traduit pour le cœur
et laisse une trace complète dans les logs.

Règle de conduite : quand la page ne permet pas de conclure (interception,
captcha, confiance insuffisante), l'état devient UNKNOWN. Le moteur
conservera alors l'état précédent plutôt que d'annoncer un faux changement.
"""

from __future__ import annotations

from dataclasses import replace
from typing import ClassVar, Optional

from src.models import ProductConfig, ProductSnapshot
from src.monitors.base import BaseMonitor
from src.utils.logger import get_logger

from . import parser
from .parser import INCONCLUSIVE_STATES, AmazonState

log = get_logger("monitors.amazon")

#: Score minimal pour retenir un état. En dessous, l'analyse est jugée
#: trop fragile et l'état devient UNKNOWN.
DEFAULT_MIN_CONFIDENCE = 60


class AmazonMonitor(BaseMonitor):
    """Surveillance d'une fiche produit Amazon."""

    site_name: ClassVar[str] = "amazon"
    display_name: ClassVar[str] = "Amazon"

    cookie_selectors: ClassVar[tuple[str, ...]] = (
        "#sp-cc-accept",
        'input[name="accept"]',
        "#a-autoid-0",
        'button[data-cel-widget="sp-cc-accept"]',
    )

    requires_javascript: ClassVar[bool] = False

    #: Seuil de confiance, ajustable par sous-classe ou en configuration.
    min_confidence: ClassVar[int] = DEFAULT_MIN_CONFIDENCE

    async def check(self, product: ProductConfig) -> ProductSnapshot:
        """Normalise l'URL avant toute requête.

        Un lien affilié, sponsorisé ou truffé de `ref=` désigne le même
        produit : le ramener à sa forme canonique évite de surveiller deux
        fois la même fiche.
        """
        canonical = parser.canonical_url(product.url)
        if canonical != product.url:
            log.check("URL Amazon normalisée : %s → %s", product.url, canonical)
            product = replace(product, url=canonical)
        return await super().check(product)

    def parse(self, html: str, product: ProductConfig) -> ProductSnapshot:
        analysis = parser.analyse(html, min_confidence=self.min_confidence)
        asin = parser.extract_asin(product.url)

        self._log_analysis(product, analysis, asin, len(html))

        details = analysis.buy_box.as_details()
        details.update({
            "etat_amazon": analysis.state.value,
            "etat_libelle": analysis.label,
            "confiance": str(analysis.confidence.score),
            "confiance_detail": analysis.confidence.detail,
            "perimetre": analysis.scope,
            "decision": analysis.reason,
        })
        if asin:
            details["asin"] = asin
        if analysis.downgraded:
            details["declasse"] = "confiance insuffisante"

        return ProductSnapshot(
            availability=analysis.availability,
            price=analysis.buy_box.price,
            buttons=list(analysis.buttons),
            status_text=analysis.label,
            page_exists=analysis.state is not AmazonState.ERROR,
            content_hash=analysis.decision_hash(),
            details=details,
            raw_html=html,
        )

    # ------------------------------------------------------------------ #
    # Traçabilité                                                         #
    # ------------------------------------------------------------------ #

    def _log_analysis(
        self,
        product: ProductConfig,
        analysis: parser.PageAnalysis,
        asin: Optional[str],
        html_length: int,
    ) -> None:
        """Trace complète : toute décision doit pouvoir être reproduite."""
        box = analysis.buy_box
        log.check(
            "Amazon — %s | état=%s (%s) | confiance=%d [%s] | périmètre=%s\n"
            "         titre=%s | asin=%s | prix=%s %s | vendeur=%s | "
            "expédié=%s | buy box=%s\n"
            "         boutons retenus=%s\n"
            "         boutons ignorés=%s\n"
            "         décision=%s | html=%.1f Ko | hash=%s",
            product.name,
            analysis.state.value, analysis.label,
            analysis.confidence.score, analysis.confidence.detail,
            analysis.scope,
            (analysis.title or "—")[:80], asin or "—",
            box.price or "—", box.currency or "",
            box.seller or "—", box.shipped_by or "—",
            "oui" if box.has_buy_box else "non",
            analysis.buttons or "—",
            analysis.ignored_buttons or "—",
            analysis.reason,
            html_length / 1024,
            analysis.decision_hash(),
        )

        if analysis.state in INCONCLUSIVE_STATES:
            log.check(
                "Amazon — %s : page non concluante (%s). L'état précédent "
                "sera conservé, aucune alerte ne sera émise.",
                product.name, analysis.label,
            )
