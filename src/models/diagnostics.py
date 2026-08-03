"""Métadonnées techniques d'une vérification — la matière de l'observabilité.

POURQUOI, ET SURTOUT À QUEL COÛT
--------------------------------
Le moteur écrit **déjà** une ligne dans `checks` à chaque cycle. Y ajouter
quelques colonnes nullables ne coûte donc rien : ni requête, ni écriture
supplémentaire, ni ralentissement du cycle de surveillance. C'est ce qui
permet de connaître, sans instrumentation dédiée :

  * la part de vérifications passées par le navigateur (fallback) ;
  * la distribution des statuts HTTP (403, 429, 5xx…) ;
  * la tendance de la confiance d'analyse.

Les événements réellement exceptionnels (captcha, page d'interception,
confiance trop basse, découverte, fusion) sont eux consignés dans
`engine_events` — rares par nature, donc peu coûteux à écrire.

Rien ici n'entre dans le hash métier : ce sont des informations sur
*l'analyse*, pas sur *l'offre*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class FetchSource(str, Enum):
    """Par quelle voie la page a été obtenue."""

    HTTP = "http"
    BROWSER = "browser"
    UNKNOWN = "unknown"


class EventScope(str, Enum):
    """Quelle partie du moteur a produit l'événement technique."""

    PLUGIN = "plugin"
    DISCOVERY = "discovery"
    INTELLIGENCE = "intelligence"
    ENGINE = "engine"


class EventKind(str, Enum):
    """Événements techniques consignés dans `engine_events`.

    Volontairement peu nombreux : chacun doit être *actionnable* au
    débogage. Une statistique décorative n'a pas sa place ici.
    """

    # --- Récupération de page ---
    HTTP_ERROR = "http_error"            # 4xx / 5xx renvoyé par le site
    TIMEOUT = "timeout"                  # délai dépassé
    NETWORK_ERROR = "network_error"      # échec réseau après retries
    BROWSER_RENDER = "browser_render"    # rendu Chromium utilisé
    BROWSER_FALLBACK = "browser_fallback"  # rendu Chromium APRÈS un refus
    BLOCKED = "blocked"                  # captcha, mur anti-robot, Cloudflare
    PAGE_MISSING = "page_missing"        # 404 : fiche absente

    # --- Qualité d'analyse ---
    UNKNOWN_STATE = "unknown_state"      # aucune action d'achat identifiée
    LOW_CONFIDENCE = "low_confidence"    # analyse déclassée faute de confiance
    LOCALE_MISMATCH = "locale_mismatch"  # page servie pour un autre pays
    UNSTABLE = "unstable"                # deux lectures contradictoires

    # --- Découverte et intelligence ---
    DISCOVERY_SCAN = "discovery_scan"        # balayage complet (porte sa durée)
    DISCOVERY_FOUND = "discovery_found"
    DISCOVERY_IMPORTED = "discovery_imported"
    CATALOG_CREATED = "catalog_created"      # produit canonique inédit
    CATALOG_MERGED = "catalog_merged"        # offre rattachée à un produit
    CATALOG_PENDING = "catalog_pending"      # fusion sous le seuil, à valider
    SCREENSHOT = "screenshot"                # capture terminée (porte sa durée)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


#: Gravité par défaut de chaque événement, pour colorer l'historique sans
#: que l'appelant ait à y penser.
DEFAULT_SEVERITY: dict[EventKind, Severity] = {
    EventKind.HTTP_ERROR: Severity.ERROR,
    EventKind.TIMEOUT: Severity.ERROR,
    EventKind.NETWORK_ERROR: Severity.ERROR,
    EventKind.BLOCKED: Severity.ERROR,
    EventKind.BROWSER_RENDER: Severity.INFO,
    EventKind.BROWSER_FALLBACK: Severity.WARNING,
    EventKind.PAGE_MISSING: Severity.WARNING,
    EventKind.UNKNOWN_STATE: Severity.WARNING,
    EventKind.LOW_CONFIDENCE: Severity.WARNING,
    EventKind.LOCALE_MISMATCH: Severity.WARNING,
    EventKind.UNSTABLE: Severity.WARNING,
    EventKind.DISCOVERY_SCAN: Severity.INFO,
    EventKind.DISCOVERY_FOUND: Severity.INFO,
    EventKind.DISCOVERY_IMPORTED: Severity.INFO,
    EventKind.CATALOG_CREATED: Severity.INFO,
    EventKind.CATALOG_MERGED: Severity.INFO,
    EventKind.CATALOG_PENDING: Severity.INFO,
    EventKind.SCREENSHOT: Severity.INFO,
}

#: Phases dont on suit le temps moyen sur la page Santé.
TIMED_KINDS: tuple[EventKind, ...] = (
    EventKind.DISCOVERY_SCAN,
    EventKind.CATALOG_MERGED,
    EventKind.SCREENSHOT,
)

#: Libellés lisibles, affichés tels quels dans l'historique du dashboard.
EVENT_LABELS: dict[EventKind, str] = {
    EventKind.HTTP_ERROR: "Erreur HTTP",
    EventKind.TIMEOUT: "Délai dépassé",
    EventKind.NETWORK_ERROR: "Échec réseau",
    EventKind.BROWSER_RENDER: "Rendu navigateur",
    EventKind.BROWSER_FALLBACK: "Bascule sur le navigateur",
    EventKind.BLOCKED: "Page d'interception",
    EventKind.PAGE_MISSING: "Fiche introuvable",
    EventKind.UNKNOWN_STATE: "État indéterminé",
    EventKind.LOW_CONFIDENCE: "Confiance insuffisante",
    EventKind.LOCALE_MISMATCH: "Contexte de livraison incorrect",
    EventKind.UNSTABLE: "Lectures contradictoires",
    EventKind.DISCOVERY_SCAN: "Balayage Discovery",
    EventKind.DISCOVERY_FOUND: "Nouvelle fiche repérée",
    EventKind.DISCOVERY_IMPORTED: "Fiche importée",
    EventKind.CATALOG_CREATED: "Produit canonique créé",
    EventKind.CATALOG_MERGED: "Offre rattachée",
    EventKind.CATALOG_PENDING: "Fusion à valider",
    EventKind.SCREENSHOT: "Capture d'écran",
}


@dataclass
class CheckDiagnostics:
    """Ce qu'une vérification apprend sur elle-même.

    Rempli en deux temps, sans que ni le cœur ni les plugins n'aient à se
    coordonner : `BaseMonitor` renseigne la partie récupération, le plugin
    la partie analyse.
    """

    #: Voie empruntée pour obtenir la page.
    fetch_source: FetchSource = FetchSource.UNKNOWN
    #: Statut HTTP de la requête (None si le navigateur a servi directement).
    http_status: Optional[int] = None
    #: Le navigateur a été utilisé APRÈS un refus ou une analyse infructueuse.
    browser_fallback: bool = False
    #: Score de confiance de l'analyse, quand le plugin en calcule un.
    confidence: Optional[int] = None
    #: Raison pour laquelle la page n'a pas permis de conclure.
    blocked_reason: Optional[str] = None
    #: Interception détectée (captcha, mur anti-robot, Cloudflare).
    blocked: bool = False

    @property
    def used_browser(self) -> bool:
        return self.fetch_source is FetchSource.BROWSER

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetch_source": self.fetch_source.value,
            "http_status": self.http_status,
            "browser_fallback": self.browser_fallback,
            "confidence": self.confidence,
            "blocked_reason": self.blocked_reason,
            "blocked": self.blocked,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "CheckDiagnostics":
        if not data:
            return cls()
        try:
            source = FetchSource(data.get("fetch_source", "unknown"))
        except ValueError:
            source = FetchSource.UNKNOWN
        return cls(
            fetch_source=source,
            http_status=data.get("http_status"),
            browser_fallback=bool(data.get("browser_fallback", False)),
            confidence=data.get("confidence"),
            blocked_reason=data.get("blocked_reason"),
            blocked=bool(data.get("blocked", False)),
        )
