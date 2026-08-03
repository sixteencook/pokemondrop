"""Monitor Amazon — volontairement conservateur.

Le parser rend une analyse motivée ; ce monitor la traduit pour le cœur
et laisse une trace complète dans les logs.

Règle de conduite : quand la page ne permet pas de conclure (interception,
captcha, confiance insuffisante, mauvaise destination de livraison), l'état
devient UNKNOWN. Le moteur conservera alors l'état précédent plutôt que
d'annoncer un faux changement.

LOCALISATION
------------
Amazon ne sert pas la même page selon la langue et le pays de livraison
associés à la session. Une requête sans préférence laisse Amazon choisir —
souvent les États-Unis — et une offre non proposée à cette destination
s'affiche « Actuellement indisponible » alors qu'elle est ouverte depuis la
France. Ce monitor demande donc explicitement la version française avec
livraison France (`prepare_request`), constate ce qui a été servi, et le
journalise.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import ClassVar, Optional

from src.models import (
    CheckDiagnostics,
    OfferState,
    ProductConfig,
    ProductSnapshot,
)
from src.monitors.base import DEFAULT_HEADERS, BaseMonitor, RequestPlan
from src.utils.logger import get_logger

from . import marketplace, parser
from .parser import INCONCLUSIVE_STATES, AmazonState

log = get_logger("monitors.amazon")

#: Score minimal pour retenir un état. En dessous, l'analyse est jugée
#: trop fragile et l'état devient UNKNOWN.
DEFAULT_MIN_CONFIDENCE = 60

#: États trahissant un site qui refuse de servir la page. Ils alimentent
#: l'indicateur « blocage » de la page Santé.
BLOCKING_STATES = frozenset({
    AmazonState.INTERCEPTED, AmazonState.CAPTCHA, AmazonState.CLOUDFLARE,
})


def _blocked_reason(analysis: parser.PageAnalysis) -> Optional[str]:
    """Raison, en clair, pour laquelle la page n'a pas permis de conclure.

    Reprend telle quelle la décision déjà calculée par le parser : aucune
    analyse supplémentaire, donc aucun coût.
    """
    if analysis.state in BLOCKING_STATES:
        return analysis.label
    if analysis.locale_blocked:
        return "contexte de livraison incorrect"
    if analysis.downgraded:
        return "confiance insuffisante"
    if analysis.state is AmazonState.UNKNOWN:
        return "aucune action d'achat identifiée"
    return None


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

    #: Refuse de conclure « indisponible » sur une page servie pour un
    #: autre pays de livraison. À ne désactiver qu'en connaissance de
    #: cause : c'est exactement le piège qui produit de faux négatifs.
    enforce_delivery_country: ClassVar[bool] = True

    def prepare_request(self, url: str) -> RequestPlan:
        """Demande la version française, livraison France, quand c'est possible.

        L'URL surveillée n'est pas modifiée en base : seule la requête
        porte le paramètre de langue et les cookies de préférence.
        """
        preference = marketplace.preference_for(url)
        headers = dict(DEFAULT_HEADERS)
        headers.update(preference.headers)
        return RequestPlan(
            url=preference.url,
            headers=headers,
            cookies=dict(preference.cookies),
            locale=preference.browser_locale,
            timezone=preference.timezone,
            description=preference.summary,
        )

    async def check(self, product: ProductConfig) -> ProductSnapshot:
        """Normalise l'URL avant toute requête.

        Un lien affilié, sponsorisé ou truffé de `ref=` désigne le même
        produit : le ramener à sa forme canonique évite de surveiller deux
        fois la même fiche.
        """
        canonical = parser.canonical_url(product.url)
        if canonical != product.url:
            log.check("URL Amazon normalisée : %s -> %s", product.url, canonical)
            product = replace(product, url=canonical)
        return await super().check(product)

    def parse(self, html: str, product: ProductConfig) -> ProductSnapshot:
        analysis = parser.analyse(
            html,
            min_confidence=self.min_confidence,
            url=product.url,
            enforce_delivery_country=self.enforce_delivery_country,
        )
        asin = parser.extract_asin(product.url)
        preference = marketplace.preference_for(product.url)
        offer = parser.build_offer(analysis, asin)

        self._log_analysis(product, analysis, offer, preference, asin, len(html))

        details = analysis.buy_box.as_details()
        details.update(analysis.locale.as_details())
        details.update({
            "etat_amazon": analysis.state.value,
            "etat_libelle": analysis.label,
            "confiance": str(analysis.confidence.score),
            "confiance_detail": analysis.confidence.detail,
            "perimetre": analysis.scope,
            "decision": analysis.reason,
            "localisation_demandee": preference.summary,
            "selecteur_decisif": analysis.evidence.selector or "—",
            "origine_decision": analysis.evidence.origin or "—",
            "invitation_dom": "oui" if analysis.invitation.present else "non",
            "invitation_motif": analysis.invitation.reason,
            "action_principale": offer.label,
            "action_libelle": analysis.action.label or "—",
            "type_vendeur": offer.seller_type.value,
            "hash_metier": offer.business_hash(),
            "controles_ignores": analysis.action.describe_ignored(),
        })

        retained = analysis.retained_scope
        if retained is not None:
            details["bloc_achat"] = retained.identifier
            details["bloc_achat_motif"] = retained.reason
        if analysis.scope_candidates:
            details["blocs_achat_examines"] = str(len(analysis.scope_candidates))
        if asin:
            details["asin"] = asin
        if analysis.downgraded:
            details["declasse"] = "confiance insuffisante"
        if analysis.locale_blocked:
            details["declasse"] = (
                f"page servie pour une livraison en "
                f"{analysis.locale.delivery_country}, "
                f"{analysis.locale.expected_country} attendu"
            )

        diagnostics = CheckDiagnostics(
            confidence=analysis.confidence.score,
            blocked=analysis.state in BLOCKING_STATES,
            blocked_reason=_blocked_reason(analysis),
        )

        return ProductSnapshot(
            availability=offer.availability,
            price=offer.price,
            # Libellés conservés pour le diagnostic seulement : ils
            # n'entrent ni dans le hash, ni dans la détection.
            buttons=list(analysis.buttons),
            status_text=analysis.label,
            page_exists=analysis.state is not AmazonState.ERROR,
            content_hash=offer.business_hash(),
            offer=offer,
            diagnostics=diagnostics,
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
        offer: OfferState,
        preference: marketplace.LocalePreference,
        asin: Optional[str],
        html_length: int,
    ) -> None:
        """Résumé métier systématique, puis bloc DEBUG complet si demandé.

        Le résumé part au niveau CHECK : invisible en console, mais présent
        dans `logs/` et dans la page Logs du dashboard. Le bloc détaillé
        part au niveau DEBUG, activé par `PLUGIN_DEBUG=true`.
        """
        box = analysis.buy_box
        locale = analysis.locale

        log.check(
            "Amazon — %s | %s | action=%s | confiance=%d | vendeur=%s (%s) | "
            "prix=%s | livraison=%s | hash=%s",
            product.name,
            analysis.label,
            offer.label,
            analysis.confidence.score,
            box.seller or "—", offer.seller_type.value,
            offer.price or "—",
            locale.delivery_country or "non détecté",
            offer.business_hash(),
        )

        if analysis.locale_blocked:
            log.check(
                "Amazon — %s : conclusion ÉCARTÉE. %s",
                product.name, analysis.reason,
            )
        elif analysis.state in INCONCLUSIVE_STATES:
            log.check(
                "Amazon — %s : page non concluante (%s). Le dernier état "
                "métier connu sera conservé, aucune alerte ne sera émise.",
                product.name, analysis.label,
            )

        if not log.isEnabledFor(logging.DEBUG):
            return

        candidates = "\n".join(
            f"           - {candidate.describe()}"
            for candidate in analysis.scope_candidates
        ) or "           - aucun bloc d'achat identifié"

        ignored = "\n".join(
            f"           - {control.label} [{control.selector}] : {control.reason}"
            for control in analysis.action.ignored[:12]
        ) or "           - aucun"

        log.debug(
            "════ AMAZON — %s ════\n"
            "  URL surveillée   : %s\n"
            "  URL canonique    : %s\n"
            "  URL appelée      : %s\n"
            "  Marketplace      : %s\n"
            "  Pays livraison   : %s\n"
            "  Langue           : %s (%s)\n"
            "  Localisation     : %s\n"
            "  ── décision ──\n"
            "  Bloc retenu      : %s (%s)\n"
            "  Sélecteur        : %s\n"
            "  Action principale: %s — « %s » via %s\n"
            "  État métier      : %s (natif : %s)\n"
            "  Disponibilité    : %s\n"
            "  Confiance        : %d [%s]\n"
            "  Prix             : %s %s\n"
            "  Vendeur          : %s (%s) · expédié par %s\n"
            "  Buy box          : %s\n"
            "  Hash métier      : %s\n"
            "  ASIN             : %s | HTML : %.1f Ko\n"
            "  Invitation       : %s\n"
            "  ── blocs d'achat examinés ──\n%s\n"
            "  ── contrôles ignorés (%d sur %d examinés) ──\n%s",
            product.name,
            product.url,
            parser.canonical_url(product.url),
            preference.url,
            locale.marketplace_domain,
            locale.delivery_summary,
            locale.language or "non détectée", locale.language_source or "—",
            preference.summary,
            analysis.scope,
            analysis.retained_scope.identifier if analysis.retained_scope else "—",
            analysis.evidence.describe(),
            offer.label, analysis.action.label or "—",
            analysis.action.origin or "—",
            offer.action.value, offer.native_state,
            offer.availability.value,
            analysis.confidence.score, analysis.confidence.detail,
            offer.price or "—", offer.currency or "",
            box.seller or "—", offer.seller_type.value, box.shipped_by or "—",
            "oui" if offer.has_buy_box else "non",
            offer.business_hash(),
            asin or "—", html_length / 1024,
            analysis.invitation.describe(),
            candidates,
            len(analysis.action.ignored), analysis.action.examined,
            ignored,
        )
