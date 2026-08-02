"""Contrat de rendu HTML par navigateur.

Le cœur (src/monitors/) ne dépend PAS de Playwright : il ne connaît que ce
protocole. L'implémentation réelle vit dans src/services/browser_renderer.py
et réutilise le navigateur déjà piloté par le service de captures.

Cela permet :
  - de tester les monitors avec un rendu factice ;
  - de désactiver totalement le navigateur (renderer = None) sans que le
    cœur ne s'en aperçoive ;
  - de remplacer un jour Playwright par autre chose sans toucher aux plugins.
"""

from __future__ import annotations

from typing import Mapping, Optional, Protocol, Sequence, runtime_checkable


class RenderError(Exception):
    """Le rendu par navigateur a échoué (indisponible, timeout, erreur page)."""


@runtime_checkable
class HtmlRenderer(Protocol):
    """Récupère le HTML d'une page tel qu'un navigateur le voit.

    Contrairement à une requête HTTP simple, le résultat contient le DOM
    APRÈS exécution du JavaScript — indispensable pour les fiches produit
    dont le bouton d'achat est injecté côté client.
    """

    async def render(
        self,
        url: str,
        cookie_selectors: Sequence[str] = (),
        *,
        cookies: Optional[Mapping[str, str]] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> str:
        """Rend `url` et retourne le DOM obtenu.

        `cookies`, `locale` et `timezone` transportent la localisation
        demandée par le monitor (langue, devise, pays de livraison). Le
        contexte navigateur est construit avec ces valeurs afin que la page
        rendue soit bien celle que l'utilisateur verrait.
        """
        ...

    @property
    def available(self) -> bool: ...
