"""Découverte automatique des plugins de sites.

Parcourt le paquet `plugins/`, importe chaque sous-paquet et enregistre
sa classe monitor dans le registre. L'isolation est totale : un plugin
qui ne s'importe pas (erreur de syntaxe, dépendance manquante…) est
loggé puis ignoré — les autres plugins et le cœur continuent de tourner.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from src.monitors.base import BaseMonitor
from src.monitors.registry import MonitorRegistry
from src.utils.logger import get_logger

log = get_logger("plugins")


def discover_plugins(registry: MonitorRegistry, package_name: str = "plugins") -> list[str]:
    """Charge tous les plugins trouvés dans `package_name` et retourne leurs noms.

    Chaque plugin doit exposer, dans son module `monitor`, une sous-classe
    de BaseMonitor avec un `site_name` non vide.
    """
    loaded: list[str] = []
    try:
        package = importlib.import_module(package_name)
    except ImportError as exc:
        log.error("Paquet de plugins « %s » introuvable : %s", package_name, exc)
        return loaded

    for info in pkgutil.iter_modules(package.__path__):
        if not info.ispkg:
            continue
        module_name = f"{package_name}.{info.name}.monitor"
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 — isolation volontaire par plugin
            log.error("Plugin « %s » ignoré (import impossible) : %s", info.name, exc)
            continue

        monitor_class = _find_monitor_class(module)
        if monitor_class is None:
            log.error(
                "Plugin « %s » ignoré : aucune classe BaseMonitor avec site_name "
                "trouvée dans %s", info.name, module_name,
            )
            continue

        metadata = None
        try:
            meta_module = importlib.import_module(f"{package_name}.{info.name}.metadata")
            metadata = getattr(meta_module, "METADATA", None)
        except ImportError:
            pass  # metadata.py optionnel

        try:
            registry.register(monitor_class, metadata)
            loaded.append(monitor_class.site_name)
            log.ok("Plugin chargé : %s (%s)", monitor_class.display_name, info.name)
        except Exception as exc:  # noqa: BLE001
            log.error("Plugin « %s » ignoré (enregistrement impossible) : %s", info.name, exc)

    return loaded


def _find_monitor_class(module: object) -> type[BaseMonitor] | None:
    """Première sous-classe concrète de BaseMonitor définie dans le module."""
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(obj, BaseMonitor)
            and obj is not BaseMonitor
            and getattr(obj, "site_name", "")
            and not inspect.isabstract(obj)
            and obj.__module__ == module.__name__
        ):
            return obj
    return None
