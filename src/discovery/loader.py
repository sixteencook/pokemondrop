"""Découverte automatique des plugins de découverte.

Symétrique de src/monitors/loader.py : le cœur parcourt `plugins/`, importe
`plugins/<site>/discovery.py` et y cherche une classe respectant le contrat
DiscoveryPlugin.

Un plugin est TOTALEMENT optionnel : un site peut n'avoir qu'un monitor
(surveillance d'URL connues), qu'un plugin de découverte, ou les deux.
Un plugin défectueux est ignoré sans impacter les autres ni le moteur.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from src.discovery.contracts import DiscoveryPlugin
from src.utils.logger import get_logger

log = get_logger("discovery.plugins")


class DiscoveryRegistry:
    """Plugins de découverte chargés, indexés par site."""

    def __init__(self) -> None:
        self._plugins: dict[str, DiscoveryPlugin] = {}

    def register(self, plugin: DiscoveryPlugin) -> None:
        if not getattr(plugin, "site_name", ""):
            raise ValueError(f"{plugin.__class__.__name__} : site_name manquant")
        self._plugins[plugin.site_name.lower()] = plugin

    def get(self, site: str) -> DiscoveryPlugin | None:
        return self._plugins.get(site.lower())

    @property
    def sites(self) -> list[str]:
        return sorted(self._plugins)

    def all(self) -> list[DiscoveryPlugin]:
        return [self._plugins[site] for site in self.sites]

    def __len__(self) -> int:
        return len(self._plugins)


def discover_discovery_plugins(package_name: str = "plugins") -> DiscoveryRegistry:
    """Charge tous les plugins de découverte trouvés dans `package_name`."""
    registry = DiscoveryRegistry()
    try:
        package = importlib.import_module(package_name)
    except ImportError as exc:
        log.error("Paquet de plugins « %s » introuvable : %s", package_name, exc)
        return registry

    for info in pkgutil.iter_modules(package.__path__):
        if not info.ispkg:
            continue
        module_name = f"{package_name}.{info.name}.discovery"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue  # site sans découverte : parfaitement normal
        except Exception as exc:  # noqa: BLE001 — isolation par plugin
            log.error("Plugin de découverte « %s » ignoré : %s", info.name, exc)
            continue

        plugin_class = _find_plugin_class(module)
        if plugin_class is None:
            log.error(
                "Plugin de découverte « %s » ignoré : aucune classe conforme "
                "dans %s", info.name, module_name,
            )
            continue

        try:
            registry.register(plugin_class())
            log.ok("Plugin de découverte chargé : %s", plugin_class.display_name)
        except Exception as exc:  # noqa: BLE001
            log.error("Plugin de découverte « %s » ignoré : %s", info.name, exc)

    return registry


def _find_plugin_class(module: object) -> type | None:
    """Première classe du module exposant site_name et scan()."""
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        if getattr(obj, "site_name", "") and hasattr(obj, "scan"):
            return obj
    return None
