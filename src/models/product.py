"""Modèles de données du Drop Monitor.

Tous les objets échangés entre les couches (config, monitors, notifications)
sont définis ici sous forme de dataclasses immuables ou quasi immuables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


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
    """Type de changement détecté entre deux snapshots."""

    PRODUCT_APPEARED = "product_appeared"
    PRICE_APPEARED = "price_appeared"
    PRICE_CHANGED = "price_changed"
    PREORDER_OPENED = "preorder_opened"
    BACK_IN_STOCK = "back_in_stock"
    BUTTON_CHANGED = "button_changed"
    STATUS_CHANGED = "status_changed"
    PAGE_CHANGED = "page_changed"


@dataclass(frozen=True)
class GlobalSettings:
    """Valeurs par défaut issues du bloc `defaults` du YAML."""

    check_interval: int = 60
    request_timeout: int = 15
    max_retries: int = 3
    retry_backoff: int = 5


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
    buttons: list[str] = field(default_factory=list)
    status_text: Optional[str] = None
    page_exists: bool = False
    content_hash: Optional[str] = None
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    #: Informations libres relevées par le plugin (vendeur, expédition,
    #: état natif du marchand, variation…). Volontairement HORS du hash :
    #: un vendeur qui tourne ne doit pas déclencher d'alerte à lui seul.
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability.value,
            "price": self.price,
            "buttons": self.buttons,
            "status_text": self.status_text,
            "page_exists": self.page_exists,
            "content_hash": self.content_hash,
            "checked_at": self.checked_at,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProductSnapshot":
        return cls(
            availability=Availability(data.get("availability", "unknown")),
            price=data.get("price"),
            buttons=list(data.get("buttons", [])),
            status_text=data.get("status_text"),
            page_exists=bool(data.get("page_exists", False)),
            content_hash=data.get("content_hash"),
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
        """Les changements de hash seuls ne déclenchent pas d'alerte Telegram
        (trop bruyants : bannières, dates, contenus dynamiques)."""
        return self.change_type is not ChangeType.PAGE_CHANGED

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
    ChangeType.PRICE_APPEARED,
    ChangeType.PREORDER_OPENED,
    ChangeType.BACK_IN_STOCK,
    ChangeType.STATUS_CHANGED,
})
