"""Observabilité : santé du moteur, score par plugin, auto-diagnostic.

CE QUE CE SERVICE N'EST PAS
---------------------------
Ce n'est pas une couche de métriques décoratives. Chaque indicateur
exposé ici doit répondre à une question de maintenance :

  * le moteur tourne-t-il ?
  * quel plugin se dégrade ?
  * quel produit est instable ?
  * un site commence-t-il à bloquer ?
  * une régression est-elle apparue depuis hier ?

COÛT
----
Aucune donnée n'est produite spécialement pour cette page. Tout vient de
lignes déjà écrites par le cycle de surveillance :

  * `checks` — une ligne par vérification, diagnostics compris ;
  * `engine_events` — uniquement les incidents (un cycle nominal n'en
    écrit aucun) ;
  * `discoveries`, `alerts`, `search_attempts`, `catalog_products`,
    `match_suggestions` — déjà alimentées par les moteurs existants.

Les agrégations sont faites par la base, groupées par site, sur une
fenêtre glissante. La page ne ralentit donc jamais la surveillance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.core.engine import MonitorEngine
from src.models import EVENT_LABELS, EventKind, Severity
from src.monitors import MonitorRegistry
from src.repositories import (
    AlertRepository,
    CatalogRepository,
    CheckRepository,
    DiscoveryRepository,
    EngineEventRepository,
    OfferRepository,
    ProductRepository,
)

# --------------------------------------------------------------------- #
# Health Score — la formule, entièrement documentée                      #
# --------------------------------------------------------------------- #
#
# Le score part de 100 et retire des pénalités. Chaque pénalité est
# proportionnelle à un TAUX (jamais à un nombre brut : un plugin qui fait
# 10 000 vérifications ne doit pas être puni d'avoir 10 erreurs), plafonnée
# à un poids maximal.
#
#   pénalité = min(poids_max, taux / taux_de_reference * poids_max)
#
# `taux_de_reference` est le taux à partir duquel la pénalité est PLEINE.
# Exemple : 10 % d'erreurs coûtent la totalité des 30 points d'erreur.
#
# Les poids traduisent une hiérarchie de gravité assumée :
#   - une erreur réseau empêche toute surveillance          → 30
#   - un blocage (403/429/captcha) annonce la fin du service → 25
#   - un état indéterminé fait manquer des drops             → 20
#   - un fallback navigateur est un avertissement précoce    → 10
#   - la lenteur ne fausse rien, elle coûte des ressources   →  8
#   - une confiance qui baisse précède les faux états        →  7
#
# Total des poids : 100. Un plugin qui cumule tout tombe donc à 0.

#: (libellé, poids maximal, taux de référence)
SCORE_WEIGHTS: tuple[tuple[str, int, float], ...] = (
    ("erreurs", 30, 0.10),
    ("blocages", 25, 0.05),
    ("états indéterminés", 20, 0.20),
    ("bascules navigateur", 10, 0.30),
    ("lenteur", 8, 1.0),
    ("confiance", 7, 1.0),
)

#: Au-delà de ce temps de réponse moyen, la pénalité de lenteur est pleine.
SLOW_RESPONSE_MS = 8000.0

#: En dessous de cette confiance moyenne, la pénalité de confiance est pleine.
LOW_CONFIDENCE_FLOOR = 50.0

#: Nombre minimal de vérifications avant de prononcer un score. En dessous,
#: le plugin est « en observation » : trois checks ne font pas une tendance.
MIN_CHECKS_FOR_SCORE = 10

#: Seuils d'état affiché.
HEALTHY_SCORE = 90
DEGRADED_SCORE = 70


@dataclass(frozen=True)
class ScoreBreakdown:
    """Score d'un plugin et le détail de ce qui l'a fait baisser."""

    score: int
    status: str
    penalties: dict[str, int]

    @property
    def main_issue(self) -> Optional[str]:
        """Le poste de pénalité le plus lourd, pour l'afficher en une ligne."""
        if not self.penalties:
            return None
        label, value = max(self.penalties.items(), key=lambda item: item[1])
        return label if value > 0 else None


def compute_score(stats: dict[str, Any]) -> ScoreBreakdown:
    """Applique la formule documentée ci-dessus à un jeu d'indicateurs."""
    checks = int(stats.get("checks") or 0)
    if checks < MIN_CHECKS_FOR_SCORE:
        return ScoreBreakdown(score=100, status="observation", penalties={})

    blocked = (
        int(stats.get("http_403") or 0)
        + int(stats.get("http_429") or 0)
        + int(stats.get("blocked") or 0)
    )
    avg_ms = stats.get("avg_response_ms")
    avg_confidence = stats.get("avg_confidence")

    rates = {
        "erreurs": int(stats.get("errors") or 0) / checks,
        "blocages": blocked / checks,
        "états indéterminés": int(stats.get("unknown_states") or 0) / checks,
        "bascules navigateur": int(stats.get("browser_checks") or 0) / checks,
        "lenteur": (
            min(1.0, avg_ms / SLOW_RESPONSE_MS) if avg_ms else 0.0
        ),
        "confiance": (
            max(0.0, (LOW_CONFIDENCE_FLOOR - avg_confidence) / LOW_CONFIDENCE_FLOOR)
            if avg_confidence is not None else 0.0
        ),
    }

    penalties: dict[str, int] = {}
    for label, weight, reference in SCORE_WEIGHTS:
        rate = rates.get(label, 0.0)
        penalty = min(weight, (rate / reference) * weight) if reference else 0.0
        penalties[label] = round(penalty)

    score = max(0, min(100, 100 - sum(penalties.values())))
    status = (
        "healthy" if score >= HEALTHY_SCORE
        else "degraded" if score >= DEGRADED_SCORE
        else "unhealthy"
    )
    return ScoreBreakdown(score=score, status=status, penalties=penalties)


# --------------------------------------------------------------------- #
# Auto-diagnostic                                                        #
# --------------------------------------------------------------------- #

#: Une anomalie n'est signalée que si elle porte sur assez de mesures.
MIN_SAMPLE_FOR_ANOMALY = 20

#: Facteur d'aggravation à partir duquel une dérive est signalée.
DRIFT_FACTOR = 2.0

#: Plancher absolu : une dérive relative sur des taux minuscules n'est
#: pas une anomalie (passer de 0,5 % à 1,5 % ne mérite pas d'alerte).
DRIFT_FLOOR = 0.10

#: Ralentissement relatif à partir duquel on parle de dégradation.
SLOWDOWN_FACTOR = 1.5

#: Baisse de confiance, en points, jugée significative.
CONFIDENCE_DROP = 8.0


@dataclass(frozen=True)
class Anomaly:
    """Un problème détecté par le moteur lui-même."""

    severity: str          # warning | error
    source: str            # plugin ou module concerné
    title: str
    detail: str


class HealthService:
    """Assemble les vues de la page Santé à partir de l'existant."""

    def __init__(
        self,
        products: ProductRepository,
        checks: CheckRepository,
        alerts: AlertRepository,
        discoveries: DiscoveryRepository,
        catalog: CatalogRepository,
        offers: OfferRepository,
        events: EngineEventRepository,
        registry: MonitorRegistry,
        engine: MonitorEngine,
        attempts: Optional[Any] = None,
    ) -> None:
        #: Facultatif : sans lui, les compteurs de recherche inter-sites
        #: valent zéro plutôt que d'empêcher la page de s'afficher.
        self._attempts = attempts
        self._products = products
        self._checks = checks
        self._alerts = alerts
        self._discoveries = discoveries
        self._catalog = catalog
        self._offers = offers
        self._events = events
        self._registry = registry
        self._engine = engine

    # ------------------------------------------------------------------ #
    # Vue globale                                                         #
    # ------------------------------------------------------------------ #

    async def overview(self, hours: int = 24) -> dict[str, Any]:
        """Les chiffres de tête : le moteur va-t-il bien, en un coup d'œil ?"""
        by_site = await self._checks.health_by_site(hours)
        discovery_counts = await self._discoveries.count_by_status()

        checks = sum(entry["checks"] for entry in by_site.values())
        errors = sum(entry["errors"] for entry in by_site.values())
        weighted = [
            (entry["checks"], entry["avg_response_ms"])
            for entry in by_site.values() if entry["avg_response_ms"] is not None
        ]
        total_weighted = sum(count for count, _ in weighted)

        return {
            "window_hours": hours,
            "engine_running": self._engine.active_count > 0,
            "plugins_active": len(self._registry.known_sites),
            "products_watched": self._engine.active_count,
            "products_total": await self._products.count(),
            "offers_total": await self._offers.count(),
            "canonical_products": await self._catalog.count(),
            "discoveries_today": await self._discoveries.count_since(hours),
            "discoveries_pending": discovery_counts.get("pending", 0),
            "alerts_today": await self._alerts.count_since(hours),
            "errors_today": errors,
            "checks_today": checks,
            "avg_response_ms": (
                round(sum(count * value for count, value in weighted) / total_weighted, 1)
                if total_weighted else None
            ),
            "avg_response_by_plugin": {
                site: entry["avg_response_ms"] for site, entry in by_site.items()
            },
        }

    # ------------------------------------------------------------------ #
    # Santé par plugin                                                    #
    # ------------------------------------------------------------------ #

    async def plugins(self, hours: int = 24) -> list[dict[str, Any]]:
        """Une carte par plugin chargé, score compris."""
        by_site = await self._checks.health_by_site(hours)
        events = await self._events.counts_by_source(hours)
        last_checks = await self._checks.last_check_by_site()
        product_counts = await self._products.count_by_site()

        watched: dict[str, int] = {}
        for product in self._engine.active_products:
            watched[product.site] = watched.get(product.site, 0) + 1

        cards: list[dict[str, Any]] = []
        for site in sorted(self._registry.known_sites):
            monitor = self._registry.get(site)
            metadata = self._registry.get_metadata(site)
            stats = dict(by_site.get(site, _EMPTY_SITE_STATS))
            site_events = events.get(site, {})

            stats["blocked"] = site_events.get(EventKind.BLOCKED.value, 0)
            breakdown = compute_score(stats)
            last_error = await self._events.last_error(site)

            cards.append({
                "site": site,
                "display_name": monitor.display_name or site,
                "version": metadata.version if metadata else None,
                "score": breakdown.score,
                "status": breakdown.status,
                "penalties": breakdown.penalties,
                "main_issue": breakdown.main_issue,
                "products_watched": watched.get(site, 0),
                "products_total": product_counts.get(site, 0),
                "last_check_at": last_checks.get(site),
                "success_rate": _success_rate(stats),
                **stats,
                "browser_renders": site_events.get(
                    EventKind.BROWSER_RENDER.value, 0
                ),
                "browser_fallbacks": site_events.get(
                    EventKind.BROWSER_FALLBACK.value, 0
                ),
                "captchas": site_events.get(EventKind.BLOCKED.value, 0),
                "timeouts": site_events.get(EventKind.TIMEOUT.value, 0),
                "network_errors": site_events.get(EventKind.NETWORK_ERROR.value, 0),
                "low_confidence": site_events.get(
                    EventKind.LOW_CONFIDENCE.value, 0
                ),
                "locale_mismatch": site_events.get(
                    EventKind.LOCALE_MISMATCH.value, 0
                ),
                "pages_missing": site_events.get(EventKind.PAGE_MISSING.value, 0),
                "last_error": last_error.detail if last_error else None,
                "last_error_at": last_error.created_at if last_error else None,
            })
        return cards

    # ------------------------------------------------------------------ #
    # Santé d'un produit                                                  #
    # ------------------------------------------------------------------ #

    async def product(self, product_uuid: str, hours: int = 24) -> Optional[dict[str, Any]]:
        """Onglet Santé d'un produit : est-il stable, et depuis quand ?"""
        product = await self._products.get(product_uuid)
        if product is None:
            return None

        stats = await self._checks.product_health(product_uuid, hours)
        last_check = (await self._checks.recent(product_uuid, limit=1)) or []
        last_alert = await self._alerts.last_for_product(product_uuid)
        events = await self._events.recent(limit=20, product_uuid=product_uuid)
        counts = {
            event.kind: sum(1 for other in events if other.kind == event.kind)
            for event in events
        }
        # Le score d'un produit se calcule avec la MÊME formule que celle
        # des plugins : un seul barème à comprendre, pas deux.
        breakdown = compute_score({
            "checks": stats["checks_window"],
            "errors": stats["errors"],
            "unknown_states": stats["unknown_states"],
            "browser_checks": stats["browser_checks"],
            "avg_response_ms": stats["avg_response_ms"],
            "avg_confidence": stats["avg_confidence"],
            "blocked": counts.get(EventKind.BLOCKED.value, 0),
        })

        return {
            "uuid": product_uuid,
            "name": product.name,
            "site": product.site,
            "url": product.url,
            "score": breakdown.score,
            "status": breakdown.status,
            "main_issue": breakdown.main_issue,
            "browser_fallbacks": counts.get(EventKind.BROWSER_FALLBACK.value, 0),
            "confidence_history": await self._checks.product_confidence_history(
                product_uuid
            ),
            "last_check_at": last_check[0].checked_at if last_check else None,
            "last_availability": last_check[0].availability if last_check else None,
            "last_alert_at": last_alert.created_at if last_alert else None,
            "last_alert_type": last_alert.change_type if last_alert else None,
            "last_screenshot": last_alert.screenshot_path if last_alert else None,
            "last_evidence": last_alert.evidence_path if last_alert else None,
            **stats,
            "recent_events": [
                {
                    "kind": event.kind,
                    "label": event.label,
                    "severity": event.severity,
                    "detail": event.detail,
                    "created_at": event.created_at,
                }
                for event in events
            ],
        }

    # ------------------------------------------------------------------ #
    # Découverte et Product Intelligence                                  #
    # ------------------------------------------------------------------ #

    async def discovery(self, hours: int = 24) -> dict[str, Any]:
        by_status = await self._discoveries.count_by_status()
        searches = await self._search_stats()
        return {
            "found_today": await self._discoveries.count_since(hours),
            "found_this_week": await self._discoveries.count_since(24 * 7),
            "imported": by_status.get("imported", 0),
            "pending": by_status.get("pending", 0),
            "ignored": by_status.get("ignored", 0),
            "blocked": by_status.get("blocked", 0),
            "last_discovery_at": await self._discoveries.last_discovery_at(),
            "per_day": await self._discoveries.per_day(14),
            **searches,
        }

    async def _search_stats(self) -> dict[str, Any]:
        """Recherches inter-sites : lancées, abouties, infructueuses, en relance.

        La mémoire des recherches est déjà tenue par `search_attempts` —
        une ligne par (produit, site, clé). Rien à recalculer.
        """
        if self._attempts is None:
            return {
                "searches_total": 0, "searches_found": 0,
                "searches_empty": 0, "searches_retrying": 0,
            }
        by_status = await self._attempts.counts_by_status()
        return {
            "searches_total": sum(by_status.values()),
            "searches_found": by_status.get("found", 0),
            "searches_empty": by_status.get("not_found", 0)
                              + by_status.get("pending", 0),
            "searches_retrying": await self._attempts.pending_retries(),
        }

    async def intelligence(self) -> dict[str, Any]:
        coverage = await self._catalog.identifier_coverage()
        suggestions = await self._catalog.suggestion_stats()
        return {
            "canonical_products": coverage.get("total", 0),
            "offers": await self._offers.count(),
            "merged_automatically": suggestions.get("accepted", 0),
            "pending_validation": suggestions.get("pending", 0),
            "rejected": suggestions.get("rejected", 0),
            "avg_confidence": suggestions.get("avg_score"),
            "identifiers": {
                key: value for key, value in coverage.items() if key != "total"
            },
        }

    # ------------------------------------------------------------------ #
    # Historique et graphiques                                            #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Temps moyen de chaque étage du moteur                               #
    # ------------------------------------------------------------------ #

    async def timings(self, hours: int = 24) -> dict[str, Optional[float]]:
        """Temps moyen par phase, en millisecondes.

        HTTP et navigateur ne sont PAS mesurés séparément : ils se lisent
        déjà dans `checks`, en croisant `response_time_ms` avec
        `fetch_source`. Les trois autres phases portent leur durée dans
        `engine_events`.
        """
        durations = await self._events.average_durations(hours)
        http, browser = await self._checks.avg_by_fetch_source(hours)
        return {
            "http_ms": http,
            "browser_ms": browser,
            "screenshot_ms": durations.get(EventKind.SCREENSHOT.value),
            "discovery_scan_ms": durations.get(EventKind.DISCOVERY_SCAN.value),
            "intelligence_ms": durations.get(EventKind.CATALOG_MERGED.value),
        }

    # ------------------------------------------------------------------ #
    # Incidents : la vie du moteur, racontée                              #
    # ------------------------------------------------------------------ #

    async def incidents(self, hours: int = 24, limit: int = 50) -> list[dict[str, Any]]:
        """Reconstitue les enchaînements « problème → réaction → issue ».

        Un 403 seul n'apprend rien. « 403, puis bascule navigateur, puis
        état concluant » raconte que le garde-fou a fonctionné ; « 403,
        bascule, toujours indéterminé » raconte l'inverse. C'est cette
        différence qui compte au débogage.

        La corrélation se fait par produit et par proximité temporelle :
        les événements d'un même cycle sont écrits à quelques millisecondes
        d'intervalle.
        """
        events = await self._events.since(hours)
        chains: dict[tuple[str, str], dict[str, Any]] = {}
        ordered: list[dict[str, Any]] = []

        for event in events:
            if event.severity == Severity.INFO.value:
                continue
            key = (event.product_uuid or event.source, _cycle_key(event.created_at))
            chain = chains.get(key)
            if chain is None:
                chain = {
                    "source": event.source,
                    "product_uuid": event.product_uuid,
                    "started_at": event.created_at,
                    "steps": [],
                    "outcome": "en cours",
                }
                chains[key] = chain
                ordered.append(chain)
            chain["steps"].append({
                "label": event.label,
                "detail": event.detail,
                "severity": event.severity,
                "at": event.created_at,
            })

        for chain in ordered:
            chain["outcome"] = _chain_outcome(chain["steps"])
        ordered.sort(key=lambda chain: chain["started_at"], reverse=True)
        return ordered[:limit]

    # ------------------------------------------------------------------ #
    # Score global du système                                             #
    # ------------------------------------------------------------------ #

    async def system_score(self, hours: int = 24) -> dict[str, Any]:
        """Score global, agrégé des scores existants.

        Les plugins pèsent le plus lourd : c'est là que se joue la valeur
        du produit. Discovery et Intelligence comptent moins — leur panne
        ralentit l'enrichissement, elle ne fait pas manquer un drop.
        """
        plugins = await self.plugins(hours)
        components: list[dict[str, Any]] = [
            {
                "name": plugin["display_name"],
                "key": plugin["site"],
                "score": plugin["score"],
                "status": plugin["status"],
                "weight": 3,
            }
            for plugin in plugins
        ]

        counts = await self._events.counts_by_source(hours)
        discovery_errors = counts.get("discovery", {}).get(
            EventKind.NETWORK_ERROR.value, 0
        )
        components.append({
            "name": "Discovery", "key": "discovery",
            "score": 100 if discovery_errors == 0 else 80,
            "status": "healthy" if discovery_errors == 0 else "degraded",
            "weight": 1,
        })

        suggestions = await self._catalog.suggestion_stats()
        pending = int(suggestions.get("pending") or 0)
        components.append({
            "name": "Product Intelligence", "key": "intelligence",
            # Des suggestions qui s'accumulent ne sont pas une panne, mais
            # une dette : le catalogue se fragmente en attendant.
            "score": 100 if pending < 10 else max(60, 100 - pending),
            "status": "healthy" if pending < 10 else "degraded",
            "weight": 1,
        })

        total_weight = sum(component["weight"] for component in components)
        overall = round(
            sum(component["score"] * component["weight"] for component in components)
            / total_weight
        ) if total_weight else 100

        return {
            "score": overall,
            "status": (
                "healthy" if overall >= HEALTHY_SCORE
                else "degraded" if overall >= DEGRADED_SCORE
                else "unhealthy"
            ),
            "components": components,
        }

    async def history(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {
                "id": event.id,
                "scope": event.scope,
                "source": event.source,
                "kind": event.kind,
                "label": event.label,
                "severity": event.severity,
                "detail": event.detail,
                "product_uuid": event.product_uuid,
                "created_at": event.created_at,
            }
            for event in await self._events.recent(limit)
        ]

    async def charts(self, hours: int = 48) -> dict[str, Any]:
        """Séries prêtes à tracer. Objectif : voir une dérive, pas décorer."""
        return {
            "checks_per_hour": await self._checks.per_hour(hours),
            "incidents_per_hour": await self._events.per_hour(
                (
                    EventKind.UNKNOWN_STATE,
                    EventKind.HTTP_ERROR,
                    EventKind.BROWSER_FALLBACK,
                    EventKind.BLOCKED,
                ),
                hours,
            ),
            "confidence_per_hour": await self._checks.confidence_per_hour(hours),
            "alerts_per_day": await self._alerts.per_day(14),
            "discoveries_per_day": await self._discoveries.per_day(14),
        }

    # ------------------------------------------------------------------ #
    # Auto-diagnostic                                                     #
    # ------------------------------------------------------------------ #

    async def anomalies(self) -> list[dict[str, Any]]:
        """Compare les dernières 24 h à la semaine écoulée, plugin par plugin.

        Le principe : une valeur absolue ne dit rien (un plugin peut avoir
        toujours utilisé le navigateur). Ce qui compte est la **dérive** par
        rapport à son propre comportement habituel.
        """
        found: list[Anomaly] = []
        for site in sorted(self._registry.known_sites):
            recent = await self._checks.site_window(site, start_hours=24)
            # Référence : les 6 jours PRÉCÉDENTS, fenêtre récente exclue.
            baseline = await self._checks.site_window(
                site, start_hours=24 * 7, end_hours=24
            )
            found.extend(_compare_windows(site, recent, baseline))
            found.extend(await self._confidence_drift(site))

        return [
            {
                "severity": anomaly.severity,
                "source": anomaly.source,
                "title": anomaly.title,
                "detail": anomaly.detail,
            }
            for anomaly in found
        ]

    async def _confidence_drift(self, site: str) -> list[Anomaly]:
        recent = await self._checks.avg_confidence_window(site, start_hours=24)
        baseline = await self._checks.avg_confidence_window(
            site, start_hours=24 * 7, end_hours=24
        )
        if recent is None or baseline is None:
            return []
        if baseline - recent < CONFIDENCE_DROP:
            return []
        return [Anomaly(
            severity="warning",
            source=site,
            title=f"La confiance moyenne du plugin {site.capitalize()} diminue",
            detail=(
                f"{recent:.0f} % sur 24 h contre {baseline:.0f} % la semaine "
                f"précédente. Une confiance qui baisse précède les états "
                f"indéterminés : la structure de la page a probablement changé."
            ),
        )]


def _compare_windows(
    site: str, recent: dict[str, Any], baseline: dict[str, Any]
) -> list[Anomaly]:
    """Dérives d'un plugin entre sa fenêtre récente et sa référence."""
    label = site.capitalize()
    checks = recent["checks"]
    if checks < MIN_SAMPLE_FOR_ANOMALY:
        return []

    found: list[Anomaly] = []

    for key, title, explanation in (
        ("browser_checks",
         f"{label} utilise le navigateur beaucoup plus souvent que d'habitude",
         "Le rendu Chromium n'est déclenché que si la requête HTTP échoue ou "
         "n'aboutit à rien : cette hausse annonce généralement un blocage."),
        ("unknown_states",
         f"{label} génère beaucoup d'états indéterminés",
         "Le plugin ne reconnaît plus l'action d'achat : le vocabulaire ou la "
         "structure de la page a probablement changé."),
        ("http_403",
         f"{label} renvoie de nombreux 403",
         "Le site refuse de servir la page à cette adresse IP. Un hébergeur "
         "est souvent en cause ; surveiller depuis une connexion domestique "
         "règle généralement le problème."),
    ):
        anomaly = _rate_drift(site, key, recent, baseline, title, explanation)
        if anomaly is not None:
            found.append(anomaly)

    recent_ms = recent.get("avg_response_ms")
    baseline_ms = baseline.get("avg_response_ms")
    if (
        recent_ms and baseline_ms
        and baseline.get("checks", 0) >= MIN_SAMPLE_FOR_ANOMALY
        and recent_ms > baseline_ms * SLOWDOWN_FACTOR
    ):
        found.append(Anomaly(
            severity="warning",
            source=site,
            title=f"{label} est plus lent depuis 24 heures",
            detail=(
                f"{recent_ms / 1000:.1f} s en moyenne contre "
                f"{baseline_ms / 1000:.1f} s la semaine précédente."
            ),
        ))

    return found


def _rate_drift(
    site: str,
    key: str,
    recent: dict[str, Any],
    baseline: dict[str, Any],
    title: str,
    explanation: str,
) -> Optional[Anomaly]:
    """Signale un taux qui a franchi le plancher ET doublé par rapport à l'usage."""
    recent_rate = recent[key] / recent["checks"] if recent["checks"] else 0.0
    if recent_rate < DRIFT_FLOOR:
        return None

    baseline_checks = baseline.get("checks", 0)
    baseline_rate = (
        baseline[key] / baseline_checks if baseline_checks >= MIN_SAMPLE_FOR_ANOMALY
        else None
    )
    # Sans référence exploitable, seul un taux vraiment élevé parle.
    if baseline_rate is None:
        if recent_rate < DRIFT_FLOOR * 3:
            return None
        comparison = "aucune référence disponible sur la semaine précédente"
    else:
        if recent_rate < baseline_rate * DRIFT_FACTOR:
            return None
        comparison = f"contre {baseline_rate:.0%} la semaine précédente"

    return Anomaly(
        severity="warning",
        source=site,
        title=title,
        detail=f"{recent_rate:.0%} des vérifications sur 24 h, {comparison}. "
               f"{explanation}",
    )


#: Fenêtre de regroupement d'un cycle, en secondes. Les événements d'une
#: même vérification sont écrits à quelques millisecondes d'intervalle ;
#: 30 s laisse une marge confortable sans mélanger deux cycles.
_CYCLE_WINDOW_SECONDS = 30


def _cycle_key(moment: datetime) -> str:
    """Identifiant de la tranche temporelle à laquelle un événement appartient."""
    return str(int(moment.timestamp()) // _CYCLE_WINDOW_SECONDS)


#: Natures qui closent une chaîne d'incident sur une note positive.
_RECOVERY_KINDS = frozenset({
    EVENT_LABELS[EventKind.BROWSER_FALLBACK], EVENT_LABELS[EventKind.BROWSER_RENDER],
})


def _chain_outcome(steps: list[dict[str, Any]]) -> str:
    """Ce que la chaîne raconte, en un mot."""
    labels = {step["label"] for step in steps}
    unresolved = {
        EVENT_LABELS[EventKind.UNKNOWN_STATE],
        EVENT_LABELS[EventKind.BLOCKED],
        EVENT_LABELS[EventKind.LOCALE_MISMATCH],
    }
    if labels & unresolved:
        return "non résolu"
    if labels & _RECOVERY_KINDS:
        return "rattrapé par le navigateur"
    if EVENT_LABELS[EventKind.NETWORK_ERROR] in labels:
        return "échec réseau"
    return "isolé"


def _success_rate(stats: dict[str, Any]) -> Optional[float]:
    checks = int(stats.get("checks") or 0)
    if not checks:
        return None
    return round(100 * (checks - int(stats.get("errors") or 0)) / checks, 1)


_EMPTY_SITE_STATS: dict[str, Any] = {
    "checks": 0, "errors": 0, "avg_response_ms": None, "unknown_states": 0,
    "browser_checks": 0, "avg_confidence": None, "http_403": 0, "http_429": 0,
    "http_404": 0, "http_5xx": 0, "http_4xx": 0,
}

