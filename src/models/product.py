"""Modèles de données du Drop Monitor.

Tous les objets échangés entre les couches (config, monitors, notifications)
sont définis ici sous forme de dataclasses immuables ou quasi immuables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

# `diagnostics` ne dépend d'aucun autre module du projet : import direct.
from src.models.diagnostics import CheckDiagnostics

if TYPE_CHECKING:  # évite un import circulaire : offer.py importe Availability
    from src.models.offer import OfferState


class Availability(str, Enum):
    """Statut de disponibilité d'un produit."""

    UNKNOWN = "unknown"            # page non encore analysée / illisible
    NOT_LISTED = "not_listed"      # page inexistante (404) ou produit absent
    UNAVAILABLE = "unavailable"    # fiche présente mais indisponible
    PREORDER = "preorder"          # bouton Précommander détecté
    IN_STOCK = "in_stock"          # bouton Ajouter au panier détecté


class Priority(str, Enum):
    """Priorité d'un produit — servira à adapter automatiquement la
    fréquence des vérifications (un produit critique pourra être vérifié
    plus souvent qu'un produit de fond de catalogue)."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ChangeType(str, Enum):
    """Type de changement métier détecté entre deux états d'offre.

    Chaque valeur répond à « qu'est-ce qui a changé **pour l'acheteur** ? ».
    Aucune ne décrit le HTML : le détecteur ne compare plus ni libellés de
    boutons, ni texte de page.
    """

    PRODUCT_APPEARED = "product_appeared"
    PRODUCT_DELISTED = "product_delisted"
    PRICE_APPEARED = "price_appeared"
    PRICE_CHANGED = "price_changed"
    PREORDER_OPENED = "preorder_opened"
    INVITATION_OPENED = "invitation_opened"
    BACK_IN_STOCK = "back_in_stock"
    WENT_OUT_OF_STOCK = "went_out_of_stock"
    SELLER_BECAME_OFFICIAL = "seller_became_official"
    SELLER_LEFT_BUYBOX = "seller_left_buybox"
    STATUS_CHANGED = "status_changed"

    # --- Hérités, PLUS JAMAIS ÉMIS --------------------------------------
    # Conservés uniquement pour relire les lignes déjà écrites en base et
    # les afficher dans le dashboard. Ils décrivaient le HTML, pas le
    # produit : c'est exactement la source de bruit que la version 1.0
    # supprime.
    BUTTON_CHANGED = "button_changed"
    PAGE_CHANGED = "page_changed"


#: Types que le détecteur n'émet plus. Un test verrouille cette promesse.
RETIRED_CHANGE_TYPES: frozenset[ChangeType] = frozenset({
    ChangeType.BUTTON_CHANGED, ChangeType.PAGE_CHANGED,
})


@dataclass(frozen=True)
class GlobalSettings:
    """Valeurs par défaut issues du bloc `defaults` du YAML."""

    check_interval: int = 60
    request_timeout: int = 15
    max_retries: int = 3
    retry_backoff: int = 5
    #: Confirmer tout changement par une seconde analyse avant d'alerter.
    #: Coûte une requête supplémentaire UNIQUEMENT quand un changement est
    #: détecté ; évite les fausses alertes dues à une page temporairement
    #: différente (bannière, test A/B, rendu partiel).
    confirm_changes: bool = True
    #: Délai avant la seconde analyse, en secondes.
    confirmation_delay: int = 4
    #: Archiver le HTML ayant motivé une alerte importante.
    keep_evidence: bool = True


@dataclass(frozen=True)
class ProductConfig:
    """Un produit à surveiller, tel que déclaré dans config/products.yaml.

    `group` est une clé de regroupement optionnelle : le même produit
    surveillé sur plusieurs sites partage le même group (ex. deux entrées
    « Pokémon UPC Jour » chez Micromania et à la Fnac avec
    group: pokemon-30-upc-jour). C'est la fondation du futur tableau
    comparatif multi-sites du dashboard.

    `uuid` est l'identifiant interne immuable (généré à la création en
    base ; vide tant que le produit n'est pas persisté).
    `priority` servira à moduler automatiquement la fréquence des checks.
    `tags` est une liste libre pour les filtres du dashboard
    (pokemon, upc, etb, collector, one-piece, …).
    """

    name: str
    site: str
    url: str
    check_interval: int
    enabled: bool
    group: Optional[str] = None
    uuid: str = ""
    priority: Priority = Priority.NORMAL
    tags: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Identifiant stable utilisé pour la persistance de l'état."""
        slug = "".join(c if c.isalnum() else "-" for c in self.name.lower())
        while "--" in slug:
            slug = slug.replace("--", "-")
        return f"{self.site}--{slug.strip('-')}"

    @property
    def is_monitorable(self) -> bool:
        """Un produit n'est surveillable que s'il est activé ET possède une URL."""
        return self.enabled and bool(self.url.strip())


@dataclass
class ProductSnapshot:
    """Photographie de l'état d'une fiche produit à un instant donné.

    C'est l'objet produit par les monitors et comparé entre deux checks
    pour détecter les changements.
    """

    availability: Availability = Availability.UNKNOWN
    price: Optional[str] = None
    #: Libellés relevés sur la page. **Diagnostic uniquement** : ils
    #: n'entrent ni dans le hash, ni dans la détection de changement, ni
    #: dans les notifications. Le moteur ne surveille pas des boutons.
    buttons: list[str] = field(default_factory=list)
    status_text: Optional[str] = None
    page_exists: bool = False
    content_hash: Optional[str] = None
    #: État métier de l'offre — c'est LUI que le moteur compare. Absent
    #: seulement pour une page inexistante ou un plugin qui n'a rien pu
    #: conclure.
    offer: Optional["OfferState"] = None
    #: Métadonnées techniques de la vérification (voie de récupération,
    #: statut HTTP, confiance). Alimente la page Santé ; n'entre jamais
    #: dans le hash ni dans la détection de changement.
    diagnostics: CheckDiagnostics = field(default_factory=CheckDiagnostics)
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    #: Informations libres relevées par le plugin (vendeur, expédition,
    #: état natif du marchand, variation…). Volontairement HORS du hash :
    #: un vendeur qui tourne ne doit pas déclencher d'alerte à lui seul.
    details: dict[str, str] = field(default_factory=dict)
    #: HTML analysé, conservé en mémoire le temps du cycle pour pouvoir
    #: archiver la preuve d'une décision importante. JAMAIS persisté en
    #: base : il n'apparaît ni dans to_dict(), ni dans le hash.
    raw_html: Optional[str] = field(
        default=None, repr=False, compare=False
    )

    @property
    def conclusive(self) -> bool:
        """La lecture a-t-elle abouti à un état métier exploitable ?

        Une lecture non concluante (page d'interception, confiance
        insuffisante, contexte de localisation incorrect) ne doit produire
        AUCUN événement et ne doit pas effacer le dernier état connu :
        c'est ce qui empêche l'oscillation « invitation → inconnu →
        invitation ».
        """
        return self.availability is not Availability.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability.value,
            "price": self.price,
            "buttons": self.buttons,
            "status_text": self.status_text,
            "page_exists": self.page_exists,
            "content_hash": self.content_hash,
            "offer": self.offer.to_dict() if self.offer else None,
            "diagnostics": self.diagnostics.to_dict(),
            "checked_at": self.checked_at,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProductSnapshot":
        from src.models.offer import OfferState

        raw_offer = data.get("offer")
        return cls(
            availability=Availability(data.get("availability", "unknown")),
            price=data.get("price"),
            buttons=list(data.get("buttons", [])),
            status_text=data.get("status_text"),
            page_exists=bool(data.get("page_exists", False)),
            content_hash=data.get("content_hash"),
            offer=OfferState.from_dict(raw_offer) if raw_offer else None,
            diagnostics=CheckDiagnostics.from_dict(data.get("diagnostics")),
            checked_at=data.get("checked_at", ""),
            details=dict(data.get("details") or {}),
        )


@dataclass(frozen=True)
class ChangeEvent:
    """Un changement détecté entre deux snapshots d'un même produit."""

    product: ProductConfig
    change_type: ChangeType
    old_value: Optional[str]
    new_value: Optional[str]
    snapshot: ProductSnapshot

    @property
    def is_alert_worthy(self) -> bool:
        """Tout événement métier mérite d'être notifié.

        Le détecteur n'en produit plus aucun qui décrive le HTML : il n'y a
        donc plus rien à filtrer ici. Les types hérités restent écartés au
        cas où une ligne ancienne serait rejouée.
        """
        return self.change_type not in RETIRED_CHANGE_TYPES

    @property
    def is_important(self) -> bool:
        """Événement « important » : celui qui mérite une preuve visuelle.

        Sert de socle au service de captures (un check de routine ou une
        simple variation de page ne déclenche jamais de screenshot).
        """
        return self.change_type in IMPORTANT_CHANGE_TYPES


#: Changements considérés comme importants (voir ChangeEvent.is_important).
IMPORTANT_CHANGE_TYPES: frozenset[ChangeType] = frozenset({
    ChangeType.PRODUCT_APPEARED,
    ChangeType.PRODUCT_DELISTED,
    ChangeType.PRICE_APPEARED,
    ChangeType.PREORDER_OPENED,
    ChangeType.INVITATION_OPENED,
    ChangeType.BACK_IN_STOCK,
    ChangeType.WENT_OUT_OF_STOCK,
    ChangeType.SELLER_BECAME_OFFICIAL,
    ChangeType.SELLER_LEFT_BUYBOX,
    ChangeType.STATUS_CHANGED,
})
