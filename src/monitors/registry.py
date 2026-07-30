"""Registre des monitors disponibles.

Associe l'identifiant `site` de la configuration à la classe de monitor.
Le registre est peuplé automatiquement par la découverte de plugins
(src/monitors/loader.py) — le cœur ne référence aucun site en dur.
"""

from __future__ import annotations

from typing import Optional

import httpx

from src.monitors.base import BaseMonitor
from src.monitors.generic import GenericHtmlMonitor
from src.monitors.plugin import PluginMetadata


class UnknownSiteError(Exception):
    """Le champ `site` de la configuration ne correspond à aucun plugin chargé."""


class MonitorRegistry:
    """Instancie et met en cache un monitor par site."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._classes: dict[str, type[BaseMonitor]] = {}
        self._instances: dict[str, BaseMonitor] = {}
        self._metadata: dict[str, PluginMetadata] = {}

    def register(
        self,
        monitor_class: type[BaseMonitor],
        metadata: Optional[PluginMetadata] = None,
    ) -> None:
        if not monitor_class.site_name:
            raise ValueError(f"{monitor_class.__name__} : site_name manquant")
        self._classes[monitor_class.site_name] = monitor_class
        if metadata is not None:
            self._metadata[monitor_class.site_name] = metadata

    def get_metadata(self, site: str) -> Optional[PluginMetadata]:
        return self._metadata.get(site.lower())

    def get(self, site: str) -> BaseMonitor:
        site = site.lower()
        if site not in self._instances:
            if site not in self._classes:
                known = ", ".join(sorted(self._classes)) or "(aucun)"
                raise UnknownSiteError(
                    f"Site inconnu « {site} ». Plugins chargés : {known}"
                )
            self._instances[site] = self._classes[site](self._client)
        return self._instances[site]

    @property
    def known_sites(self) -> list[str]:
        return sorted(self._classes)


def create_registry(client: httpx.AsyncClient) -> MonitorRegistry:
    """Registre peuplé par la découverte automatique des plugins.

    Le monitor générique est toujours disponible (site: generic) pour
    surveiller un site sans plugin dédié.
    """
    # Import local pour éviter le cycle registry ↔ loader.
    from src.monitors.loader import discover_plugins

    registry = MonitorRegistry(client)
    registry.register(GenericHtmlMonitor)
    discover_plugins(registry)
    return registry
