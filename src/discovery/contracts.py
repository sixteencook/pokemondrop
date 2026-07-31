"""Contrats de la couche Découverte.

Le cœur ne connaît que ces types. Il ignore totalement Micromania, Amazon
ou tout autre site : il sait seulement qu'un plugin de découverte expose
une méthode `scan()` qui lui rend une liste de fiches trouvées.

Un plugin choisit librement SA stratégie (HTTP, Playwright, API, RSS, XML,
navigation) : le moteur n'en sait rien et n'a pas à le savoir.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Protocol, Sequence, runtime_checkable

import httpx

from src.monitors.renderer import HtmlRenderer


@dataclass(frozen=True)
class DiscoveredProduct:
    """Une fiche produit trouvée sur un site, avant toute décision.

    Seuls `url` et `title` sont obligatoires. Les identifiants forts
    (`sku`, `ean`) améliorent la qualité du fingerprint quand le site les
    expose — voir src/discovery/fingerprint.py.
    """

    url: str
    title: str
    site: str = ""                     # renseigné par le moteur
    image_url: Optional[str] = None
    price: Optional[str] = None
    sku: Optional[str] = None
    ean: Optional[str] = None
    brand: Optional[str] = None
    mpn: Optional[str] = None
    release_date: Optional[str] = None
    source: str = ""                   # d'où vient la fiche (sitemap, listing…)
    tags: tuple[str, ...] = ()

    def with_site(self, site: str) -> "DiscoveredProduct":
        return replace(self, site=site)


@dataclass(frozen=True)
class ScanResult:
    """Retour d'un plugin après exploration.

    `complete` indique que le balayage a couvert l'intégralité du
    périmètre du plugin. Le moteur ne conclut à une fiche SUPPRIMÉE que
    dans ce cas : un scan partiel (timeout, page en erreur) ne doit jamais
    faire disparaître des produits à tort.
    """

    products: Sequence[DiscoveredProduct] = field(default_factory=tuple)
    complete: bool = True
    sources_scanned: int = 0
    note: str = ""


@dataclass
class DiscoveryContext:
    """Dépendances mises à disposition des plugins par le moteur.

    Le plugin ne construit jamais son propre client HTTP ni son propre
    navigateur : il reçoit ceux de l'application (connexions mutualisées,
    timeouts cohérents, un seul Chromium).
    """

    client: httpx.AsyncClient
    renderer: Optional[HtmlRenderer] = None
    #: Réglages libres venant de config/discovery.yaml (section du site).
    options: dict = field(default_factory=dict)

    @property
    def can_render(self) -> bool:
        return self.renderer is not None and self.renderer.available


@runtime_checkable
class DiscoveryPlugin(Protocol):
    """Contrat d'un plugin de découverte.

    À placer dans plugins/<site>/discovery.py ; la découverte des plugins
    est automatique (voir src/discovery/loader.py).
    """

    site_name: str
    display_name: str

    async def scan(self, ctx: DiscoveryContext) -> ScanResult: ...
