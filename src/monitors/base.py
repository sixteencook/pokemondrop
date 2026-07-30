"""Interface commune à tous les monitors de sites.

Pour ajouter un nouveau site :
  1. créer un fichier src/monitors/<site>.py ;
  2. hériter de BaseMonitor (ou de GenericHtmlMonitor pour une analyse
     HTML standard) ;
  3. définir `site_name` et, si besoin, surcharger `parse()` ;
  4. l'enregistrer dans src/monitors/registry.py.

Le cœur de l'application (scheduler, diff, notifications, persistance)
n'a JAMAIS besoin d'être modifié.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Optional

import httpx

from src.models import ProductConfig, ProductSnapshot

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


class FetchError(Exception):
    """Erreur réseau ou HTTP lors de la récupération d'une page."""


class BaseMonitor(ABC):
    """Contrat que chaque monitor de site doit respecter."""

    #: Identifiant utilisé dans le champ `site` du YAML (minuscules).
    site_name: ClassVar[str] = ""

    #: Nom affiché dans les logs et les alertes.
    display_name: ClassVar[str] = ""

    #: Sélecteurs CSS de fermeture des popups cookies, propres au site.
    #: Utilisés par le service de captures, en complément des sélecteurs
    #: universels (OneTrust, Didomi, Cookiebot…).
    cookie_selectors: ClassVar[tuple[str, ...]] = ()

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def check(self, product: ProductConfig) -> ProductSnapshot:
        """Récupère la page du produit et retourne un snapshot de son état."""
        status_code, html = await self._fetch(product.url)
        if status_code == 404 or not html:
            return ProductSnapshot(page_exists=False)
        return self.parse(html, product)

    async def _fetch(self, url: str) -> tuple[int, Optional[str]]:
        """Télécharge la page. Lève FetchError en cas de problème réseau."""
        try:
            response = await self._client.get(
                url, headers=DEFAULT_HEADERS, follow_redirects=True
            )
        except httpx.TimeoutException as exc:
            raise FetchError(f"Timeout : {url}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"Erreur réseau ({exc.__class__.__name__}) : {url}") from exc

        if response.status_code == 404:
            return 404, None
        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code} : {url}")
        return response.status_code, response.text

    @abstractmethod
    def parse(self, html: str, product: ProductConfig) -> ProductSnapshot:
        """Analyse le HTML d'une fiche produit et en extrait l'état.

        Chaque site peut fournir sa propre implémentation ; l'implémentation
        générique (GenericHtmlMonitor) couvre la majorité des cas.
        """
