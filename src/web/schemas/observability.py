"""Schémas de la couche d'observabilité (page Santé et API externes).

Volontairement plats et sans surprise : ces réponses sont destinées à être
consommées plus tard par une application mobile ou un dashboard tiers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EngineOverviewOut(BaseModel):
    """Vue globale : le moteur va-t-il bien, en un coup d'œil ?"""

    window_hours: int = Field(description="Fenêtre d'observation, en heures.")
    engine_running: bool = Field(description="Au moins une boucle active.")
    plugins_active: int = Field(description="Plugins de surveillance chargés.")
    products_watched: int = Field(description="Produits réellement surveillés.")
    products_total: int
    offers_total: int = Field(description="Offres marchandes connues.")
    canonical_products: int = Field(description="Produits canoniques du catalogue.")
    discoveries_today: int
    discoveries_pending: int = Field(description="Fiches en attente de validation.")
    alerts_today: int
    errors_today: int
    checks_today: int
    avg_response_ms: Optional[float] = Field(
        default=None, description="Temps d'analyse moyen, pondéré par le volume."
    )
    avg_response_by_plugin: dict[str, Optional[float]] = Field(
        default_factory=dict, description="Temps moyen par plugin, en ms."
    )


class PluginHealthOut(BaseModel):
    """Carte de santé d'un plugin."""

    site: str
    display_name: str
    version: Optional[str] = None
    score: int = Field(description="Health Score 0-100 (formule documentée).")
    status: str = Field(description="healthy | degraded | unhealthy | observation")
    penalties: dict[str, int] = Field(
        default_factory=dict, description="Points retirés, par poste."
    )
    main_issue: Optional[str] = Field(
        default=None, description="Poste de pénalité le plus lourd."
    )

    products_watched: int = 0
    products_total: int = 0
    last_check_at: Optional[datetime] = None
    success_rate: Optional[float] = Field(
        default=None, description="Pourcentage de vérifications sans erreur."
    )

    checks: int = 0
    errors: int = 0
    avg_response_ms: Optional[float] = None
    avg_confidence: Optional[float] = None
    unknown_states: int = 0
    browser_checks: int = 0

    http_403: int = 0
    http_404: int = 0
    http_429: int = 0
    http_4xx: int = 0
    http_5xx: int = 0

    browser_renders: int = 0
    browser_fallbacks: int = 0
    captchas: int = Field(default=0, description="Interceptions : captcha, mur, Cloudflare.")
    timeouts: int = 0
    network_errors: int = 0
    low_confidence: int = 0
    locale_mismatch: int = 0
    pages_missing: int = 0

    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None


class ProductEventOut(BaseModel):
    kind: str
    label: str
    severity: str
    detail: str
    created_at: datetime


class ProductHealthOut(BaseModel):
    """Onglet Santé d'un produit : est-il stable, et depuis quand ?"""

    uuid: str
    name: str
    site: str
    url: str

    last_check_at: Optional[datetime] = None
    last_availability: Optional[str] = None
    last_alert_at: Optional[datetime] = None
    last_alert_type: Optional[str] = None
    last_screenshot: Optional[str] = Field(
        default=None, description="Chemin relatif de la dernière capture."
    )
    last_evidence: Optional[str] = Field(
        default=None, description="Chemin relatif du dernier HTML archivé."
    )

    score: int = Field(default=100, description="Health Score du produit.")
    status: str = "observation"
    main_issue: Optional[str] = None

    checks_window: int = 0
    checks_total: int = 0
    errors: int = 0
    avg_response_ms: Optional[float] = None
    avg_confidence: Optional[float] = None
    unknown_states: int = 0
    browser_checks: int = 0
    browser_fallbacks: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None

    confidence_history: list[dict[str, Any]] = Field(
        default_factory=list, description="Confiance des dernières analyses."
    )
    recent_events: list[ProductEventOut] = Field(default_factory=list)


class DiscoveryHealthOut(BaseModel):
    found_today: int
    found_this_week: int
    imported: int
    pending: int
    ignored: int
    blocked: int
    last_discovery_at: Optional[datetime] = None
    per_day: list[dict[str, Any]] = Field(default_factory=list)

    searches_total: int = Field(
        default=0, description="Recherches inter-sites lancées."
    )
    searches_found: int = Field(default=0, description="Recherches abouties.")
    searches_empty: int = Field(default=0, description="Recherches sans résultat.")
    searches_retrying: int = Field(
        default=0, description="Recherches en attente de relance (backoff actif)."
    )


class IntelligenceHealthOut(BaseModel):
    canonical_products: int
    offers: int
    merged_automatically: int
    pending_validation: int
    rejected: int
    avg_confidence: Optional[float] = Field(
        default=None, description="Score moyen des rapprochements proposés."
    )
    identifiers: dict[str, int] = Field(
        default_factory=dict,
        description="Nombre de produits canoniques portant chaque identifiant fort.",
    )


class AnomalyOut(BaseModel):
    """Problème détecté par le moteur lui-même."""

    severity: str = Field(description="warning | error")
    source: str = Field(description="Plugin ou module concerné.")
    title: str
    detail: str


class EngineEventOut(BaseModel):
    id: int
    scope: str
    source: str
    kind: str
    label: str
    severity: str
    detail: str
    product_uuid: Optional[str] = None
    created_at: datetime


class PhaseTimingsOut(BaseModel):
    """Temps moyen de chaque étage du moteur, en millisecondes."""

    http_ms: Optional[float] = Field(
        default=None, description="Requête HTTP simple."
    )
    browser_ms: Optional[float] = Field(
        default=None, description="Rendu Chromium (Playwright)."
    )
    screenshot_ms: Optional[float] = Field(default=None, description="Capture d'écran.")
    discovery_scan_ms: Optional[float] = Field(
        default=None, description="Balayage Discovery complet."
    )
    intelligence_ms: Optional[float] = Field(
        default=None, description="Corrélation Product Intelligence."
    )


class IncidentStepOut(BaseModel):
    label: str
    detail: str
    severity: str
    at: datetime


class IncidentOut(BaseModel):
    """Un enchaînement « problème → réaction → issue »."""

    source: str
    product_uuid: Optional[str] = None
    started_at: datetime
    outcome: str = Field(
        description="non résolu | rattrapé par le navigateur | échec réseau | isolé"
    )
    steps: list[IncidentStepOut] = Field(default_factory=list)


class ScoreComponentOut(BaseModel):
    name: str
    key: str
    score: int
    status: str
    weight: int


class SystemScoreOut(BaseModel):
    """Score global du système, agrégé des scores de chaque composant."""

    score: int
    status: str
    components: list[ScoreComponentOut] = Field(default_factory=list)


class DiagnosticsOut(BaseModel):
    """Tout ce qu'il faut pour comprendre l'état du moteur en 30 secondes."""

    overview: EngineOverviewOut
    system: SystemScoreOut
    plugins: list[PluginHealthOut]
    discovery: DiscoveryHealthOut
    intelligence: IntelligenceHealthOut
    anomalies: list[AnomalyOut]
    incidents: list[IncidentOut]
    timings: PhaseTimingsOut
    history: list[EngineEventOut]
    charts: dict[str, Any] = Field(
        default_factory=dict,
        description="Séries prêtes à tracer : checks/heure, incidents/heure, "
                    "confiance/heure, alertes/jour, découvertes/jour.",
    )


# --------------------------------------------------------------------- #
# Histoire d'un produit canonique                                        #
# --------------------------------------------------------------------- #

class StoryEntryOut(BaseModel):
    at: datetime
    site: str
    label: str
    detail: str = ""
    origin: str = Field(description="monitoring | discovery | intelligence")


class PropagationStepOut(BaseModel):
    """Ordre de publication d'une fiche entre marchands."""

    site: str
    first_seen_at: datetime
    rank: int
    delay_hours: float = Field(description="Retard sur le premier marchand.")
    url: str
    price: Optional[str] = None
    availability: Optional[str] = None


class ProductMetricsOut(BaseModel):
    merchants: int
    first_merchant: Optional[str] = None
    first_seen_at: Optional[datetime] = None
    last_merchant: Optional[str] = None
    last_merchant_at: Optional[datetime] = None
    changes: int = 0
    notifications: int = 0
    screenshots: int = 0
    price_changes: int = 0
    back_in_stock: int = 0
    out_of_stock: int = 0
    preorders: int = 0
    invitations: int = 0


class SearchAttemptOut(BaseModel):
    site: str
    key_kind: str
    key_value: str
    status: str
    attempts: int
    confidence: int
    reason: str = ""
    found_url: Optional[str] = None
    last_attempt_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = Field(
        default=None, description="Prochaine relance (backoff actif)."
    )


class ProductStoryOut(BaseModel):
    """L'histoire complète d'un produit canonique, tous marchands confondus."""

    uuid: str
    name: str
    brand: Optional[str] = None
    timeline: list[StoryEntryOut] = Field(default_factory=list)
    propagation: list[PropagationStepOut] = Field(default_factory=list)
    metrics: ProductMetricsOut
    identity: dict[str, str] = Field(
        default_factory=dict, description="Clés fortes connues du produit."
    )
    searches: list[SearchAttemptOut] = Field(default_factory=list)
