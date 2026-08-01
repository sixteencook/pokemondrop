"""Interface commune à tous les monitors de sites.

Pour ajouter un nouveau site :
  1. créer un paquet plugins/<site>/ ;
  2. hériter de BaseMonitor (ou de GenericHtmlMonitor pour une analyse
     HTML standard) ;
  3. définir `site_name` et, si besoin, surcharger `parse()` ;
  4. c'est tout : la découverte est automatique.

Le cœur de l'application (scheduler, diff, notifications, persistance)
n'a JAMAIS besoin d'être modifié.

RÉCUPÉRATION EN DEUX TEMPS
--------------------------
1. Requête HTTP classique (rapide, ~200 ms).
2. Escalade vers un vrai navigateur (Playwright) si — et seulement si :
     - le site répond par un statut de blocage (403, 429, 503…) ;
     - ou la page est récupérée mais l'analyse reste inconclusive
       (`unknown`), signe d'une fiche rendue en JavaScript ou d'une page
       d'attente anti-robot servie en HTTP 200.

L'escalade se contente d'afficher la page comme le ferait un navigateur
ordinaire : aucune protection n'est contournée.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import ClassVar, Optional

import httpx

from src.models import Availability, ProductConfig, ProductSnapshot
from src.monitors.renderer import HtmlRenderer, RenderError
from src.utils.logger import get_logger

log = get_logger("monitors")

# En-têtes réalistes et honnêtes : un navigateur standard, sans usurpation
# d'identité exotique ni contournement de protections.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
}

#: Statuts trahissant un refus de servir la page à un client non-navigateur.
BLOCKED_STATUSES = frozenset({401, 403, 429, 503})


class FetchError(Exception):
    """Erreur réseau ou HTTP lors de la récupération d'une page."""


@dataclass(frozen=True)
class FetchResult:
    """Page récupérée, avec la voie empruntée (« http » ou « browser »)."""

    status_code: int
    html: Optional[str]
    source: str


class BaseMonitor(ABC):
    """Contrat que chaque monitor de site doit respecter."""

    #: Identifiant utilisé dans le champ `site` de la configuration (minuscules).
    site_name: ClassVar[str] = ""

    #: Nom affiché dans les logs et les alertes.
    display_name: ClassVar[str] = ""

    #: Sélecteurs CSS de fermeture des popups cookies, propres au site.
    #: Utilisés par le service de captures ET par le rendu navigateur.
    cookie_selectors: ClassVar[tuple[str, ...]] = ()

    #: Fiche produit rendue côté client : passer directement au navigateur,
    #: sans perdre de temps sur une requête HTTP qui ne donnera rien.
    requires_javascript: ClassVar[bool] = False

    def __init__(
        self, client: httpx.AsyncClient, renderer: Optional[HtmlRenderer] = None
    ) -> None:
        self._client = client
        self._renderer = renderer

    @property
    def can_render(self) -> bool:
        return self._renderer is not None and self._renderer.available

    async def check(self, product: ProductConfig) -> ProductSnapshot:
        """Récupère la page du produit et retourne un snapshot de son état."""
        result = await self._fetch(product.url)
        if result.status_code == 404 or not result.html:
            return ProductSnapshot(page_exists=False)

        snapshot = self.parse(result.html, product)
        if snapshot.raw_html is None:
            # Le HTML reste disponible pour archiver la preuve d'une
            # décision importante ; il n'est jamais persisté en base.
            snapshot = replace(snapshot, raw_html=result.html)

        # Analyse inconclusive après une simple requête HTTP : la page est
        # probablement rendue en JavaScript. On refait un tour avec le
        # navigateur avant de conclure.
        if (
            snapshot.availability is Availability.UNKNOWN
            and result.source == "http"
            and self.can_render
        ):
            log.check(
                "Statut indéterminé pour %s — nouvelle tentative avec le navigateur.",
                product.name,
            )
            rendered = await self._render(product.url)
            if rendered is not None:
                snapshot = self.parse(rendered, product)
                if snapshot.availability is not Availability.UNKNOWN:
                    log.ok(
                        "Rendu navigateur concluant pour %s : statut « %s ».",
                        product.name, snapshot.availability.value,
                    )
        return snapshot

    # ------------------------------------------------------------------ #
    # Récupération                                                        #
    # ------------------------------------------------------------------ #

    async def _fetch(self, url: str) -> FetchResult:
        """HTTP d'abord, navigateur si le site refuse de servir la page."""
        if self.requires_javascript and self.can_render:
            html = await self._render(url)
            if html is None:
                raise FetchError(f"Rendu navigateur impossible : {url}")
            return FetchResult(200, html, "browser")

        try:
            response = await self._client.get(
                url, headers=DEFAULT_HEADERS, follow_redirects=True
            )
        except httpx.TimeoutException as exc:
            raise FetchError(f"Timeout : {url}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"Erreur réseau ({exc.__class__.__name__}) : {url}") from exc

        if response.status_code == 404:
            return FetchResult(404, None, "http")

        if response.status_code in BLOCKED_STATUSES:
            if not self.can_render:
                raise FetchError(
                    f"HTTP {response.status_code} (accès refusé) : {url} — "
                    "activez le rendu navigateur (BROWSER_FALLBACK_ENABLED) "
                    "pour réessayer via Chromium."
                )
            log.check(
                "HTTP %s sur %s — bascule automatique sur le navigateur.",
                response.status_code, url,
            )
            html = await self._render(url)
            if html is None:
                raise FetchError(
                    f"HTTP {response.status_code} puis échec du rendu navigateur : {url}"
                )
            return FetchResult(200, html, "browser")

        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code} : {url}")

        return FetchResult(response.status_code, response.text, "http")

    async def _render(self, url: str) -> Optional[str]:
        """Rendu navigateur ; retourne None en cas d'échec (jamais d'exception)."""
        if not self.can_render:
            return None
        try:
            html = await self._renderer.render(url, self.cookie_selectors)
        except RenderError as exc:
            log.error("Rendu navigateur en échec (%s) : %s", url, exc)
            return None
        log.check("Rendu navigateur : %s (%d caractères)", url, len(html))
        return html

    @abstractmethod
    def parse(self, html: str, product: ProductConfig) -> ProductSnapshot:
        """Analyse le HTML d'une fiche produit et en extrait l'état.

        Chaque site peut fournir sa propre implémentation ; l'implémentation
        générique (GenericHtmlMonitor) couvre la majorité des cas.
        """
