"""Contrat des plugins de sites.

Chaque plugin vit dans son propre paquet sous plugins/ :

    plugins/
    └── micromania/
        ├── __init__.py      # expose MONITOR (et METADATA)
        ├── metadata.py      # identité du plugin (PluginMetadata)
        ├── keywords.py      # mots-clés propres au site
        ├── selectors.py     # sélecteurs CSS propres au site
        ├── parser.py        # analyse HTML spécifique (optionnelle)
        └── monitor.py       # la classe monitor (hérite de GenericHtmlMonitor)

Le cœur du projet ne contient AUCUNE connaissance des sites : il découvre
les plugins automatiquement (voir loader.py). Un changement du HTML de
Micromania ne touche que plugins/micromania/ ; un plugin défectueux est
ignoré au chargement sans impacter les autres.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PluginMetadata:
    """Carte d'identité d'un plugin de site."""

    site_name: str        # identifiant utilisé dans la configuration (minuscules)
    display_name: str     # nom affiché (logs, alertes, dashboard)
    version: str          # version du plugin, indépendante du cœur
    base_url: str         # racine du site surveillé
    description: str = ""
