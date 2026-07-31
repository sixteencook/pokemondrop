"""Stratégies d'enrichissement d'identité — découvertes automatiquement.

Une stratégie observe une fiche produit et en tire des informations
d'identité. Le moteur ne connaît que le protocole ci-dessous : ajouter une
méthode n'exige AUCUNE modification du moteur.

Extensions prévues, toutes réalisables sans toucher au cœur :

    OCR                    lire l'EAN imprimé sur la boîte
    Barcode / QR Code      décoder un code-barres depuis l'image produit
    Image Embeddings       vecteur visuel du packaging
    CLIP / ViT             similarité image-texte
    Reverse Image Search   retrouver la même photo chez un autre marchand
    Packaging Similarity   comparaison de boîtes
    LLM Matching           « ces deux fiches désignent-elles le même produit ? »

Écrire une stratégie :

    class BarcodeStrategy:
        name = "barcode"
        priority = 90

        async def enrich(self, identity, context):
            ...  # retourne une ProductIdentity enrichie, ou None

Puis la déposer dans ce paquet (ou dans plugins/<site>/identity.py) : elle
est trouvée et appliquée toute seule, dans l'ordre de sa priorité.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from src.intelligence.identity import ProductIdentity
from src.utils.logger import get_logger

log = get_logger("intelligence.strategies")


@dataclass
class IdentityContext:
    """Matière première offerte aux stratégies.

    Toutes les entrées sont optionnelles : une stratégie n'utilise que ce
    dont elle a besoin et s'abstient sinon.
    """

    site: str = ""
    url: str = ""
    title: str = ""
    html: Optional[str] = None
    image_url: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class IdentityStrategy(Protocol):
    """Contrat d'une méthode d'enrichissement d'identité."""

    name: str
    priority: int

    async def enrich(
        self, identity: ProductIdentity, context: IdentityContext
    ) -> Optional[ProductIdentity]: ...


class IdentityStrategyRegistry:
    """Applique les stratégies connues, de la plus prioritaire à la moins."""

    def __init__(self, strategies: Optional[Sequence[IdentityStrategy]] = None) -> None:
        self._strategies: list[IdentityStrategy] = sorted(
            strategies or [], key=lambda item: item.priority, reverse=True
        )

    def register(self, strategy: IdentityStrategy) -> None:
        self._strategies.append(strategy)
        self._strategies.sort(key=lambda item: item.priority, reverse=True)

    @property
    def names(self) -> list[str]:
        return [f"{item.name} ({item.priority})" for item in self._strategies]

    def __len__(self) -> int:
        return len(self._strategies)

    async def enrich(
        self, identity: ProductIdentity, context: IdentityContext
    ) -> ProductIdentity:
        """Fait passer l'identité par toutes les stratégies.

        Une stratégie en échec est journalisée puis ignorée : l'identité
        déjà acquise n'est jamais perdue.
        """
        result = identity
        for strategy in self._strategies:
            try:
                enriched = await strategy.enrich(result, context)
            except Exception as exc:  # noqa: BLE001 — isolation par stratégie
                log.error("Stratégie « %s » en échec : %s", strategy.name, exc)
                continue
            if enriched is not None:
                result = result.merged_with(enriched)
        return result


def discover_identity_strategies(
    packages: Sequence[str] = ("src.intelligence.strategies", "plugins"),
) -> IdentityStrategyRegistry:
    """Charge les stratégies trouvées dans les paquets indiqués.

    - `src.intelligence.strategies` : stratégies génériques fournies ;
    - `plugins/<site>/identity.py`  : stratégies propres à un marchand.
    """
    registry = IdentityStrategyRegistry()
    for package_name in packages:
        if package_name == "plugins":
            _load_plugin_strategies(registry, package_name)
        else:
            _load_package_strategies(registry, package_name)
    return registry


def _load_package_strategies(registry: IdentityStrategyRegistry, name: str) -> None:
    try:
        package = importlib.import_module(name)
    except ImportError as exc:
        log.error("Paquet de stratégies « %s » introuvable : %s", name, exc)
        return

    for info in pkgutil.iter_modules(package.__path__):
        if info.ispkg or info.name.startswith("_"):
            continue
        _register_from_module(registry, f"{name}.{info.name}")


def _load_plugin_strategies(registry: IdentityStrategyRegistry, name: str) -> None:
    try:
        package = importlib.import_module(name)
    except ImportError:
        return
    for info in pkgutil.iter_modules(package.__path__):
        if not info.ispkg:
            continue
        _register_from_module(registry, f"{name}.{info.name}.identity", quiet=True)


def _register_from_module(
    registry: IdentityStrategyRegistry, module_name: str, quiet: bool = False
) -> None:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        if not quiet:
            log.error("Module de stratégie introuvable : %s", module_name)
        return
    except Exception as exc:  # noqa: BLE001 — isolation par module
        log.error("Stratégie « %s » ignorée : %s", module_name, exc)
        return

    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        if not (getattr(obj, "name", "") and hasattr(obj, "enrich")):
            continue
        try:
            registry.register(obj())
            log.ok("Stratégie d'identité chargée : %s", obj.name)
        except Exception as exc:  # noqa: BLE001
            log.error("Stratégie « %s » non instanciable : %s", obj.name, exc)
