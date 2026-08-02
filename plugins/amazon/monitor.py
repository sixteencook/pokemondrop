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

from dataclasses import replace
from typing import ClassVar, Optional

from src.models import ProductConfig, ProductSnapshot
from src.monitors.base import DEFAULT_HEADERS, BaseMonitor, RequestPlan
from src.utils.logger import get_logger

from . import marketplace, parser
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

        self._log_analysis(product, analysis, preference, asin, len(html))

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
        preference: marketplace.LocalePreference,
        asin: Optional[str],
        html_length: int,
    ) -> None:
        """Trace complète : toute décision doit pouvoir être reproduite.

        Journalisée au niveau CHECK : invisible en console par défaut, mais
        présente dans `logs/` et dans la page Logs du dashboard, là où l'on
        va chercher pourquoi un statut est inattendu.
        """
        box = analysis.buy_box
        locale = analysis.locale

        log.check(
            "Amazon — %s | état=%s (%s) | confiance=%d [%s]\n"
            "         titre=%s | asin=%s | prix=%s %s | vendeur=%s | "
            "expédié=%s | buy box=%s\n"
            "         boutons retenus=%s\n"
            "         boutons ignorés=%s\n"
            "         décision=%s | html=%.1f Ko | hash=%s",
            product.name,
            analysis.state.value, analysis.label,
            analysis.confidence.score, analysis.confidence.detail,
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

        # Bloc de localisation : c'est lui qui explique une page « autre »
        # que celle vue dans un navigateur.
        log.check(
            "Amazon — %s | LOCALISATION\n"
            "         demandée      : %s\n"
            "         URL appelée   : %s\n"
            "         marketplace   : %s\n"
            "         langue        : %s (%s)\n"
            "         livraison     : %s\n"
            "         conforme      : %s",
            product.name,
            preference.summary,
            preference.url,
            locale.marketplace_domain,
            locale.language or "non détectée",
            locale.language_source or "—",
            locale.delivery_summary,
            "oui" if locale.delivers_to_expected_country else
            f"NON — {locale.expected_country} attendu",
        )

        # Bloc de décision : quel bloc d'achat, quel sélecteur, et ce
        # qu'est devenu le bouton d'invitation.
        candidates = "\n".join(
            f"           - {candidate.describe()}"
            for candidate in analysis.scope_candidates
        ) or "           - aucun bloc d'achat identifié"

        log.check(
            "Amazon — %s | DÉCISION\n"
            "         périmètre     : %s\n"
            "         sélecteur     : %s\n"
            "         extrait       : %s\n"
            "         invitation    : %s\n"
            "         blocs d'achat :\n%s",
            product.name,
            analysis.scope,
            analysis.evidence.describe(),
            analysis.evidence.excerpt or "—",
            analysis.invitation.describe(),
            candidates,
        )

        if analysis.locale_blocked:
            log.check(
                "Amazon — %s : conclusion négative ÉCARTÉE. %s",
                product.name, analysis.reason,
            )

        if analysis.state in INCONCLUSIVE_STATES:
            log.check(
                "Amazon — %s : page non concluante (%s). L'état précédent "
                "sera conservé, aucune alerte ne sera émise.",
                product.name, analysis.label,
            )
