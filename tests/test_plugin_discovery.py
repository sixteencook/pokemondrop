"""Tests de la découverte automatique des plugins."""

import httpx
import pytest

from src.monitors import UnknownSiteError, create_registry
from src.monitors.generic import GenericHtmlMonitor


@pytest.fixture
def registry():
    return create_registry(httpx.AsyncClient())


def test_micromania_plugin_is_discovered(registry):
    monitor = registry.get("micromania")
    assert monitor.display_name == "Micromania"
    assert isinstance(monitor, GenericHtmlMonitor)  # hérite de l'analyse générique


def test_micromania_plugin_extends_keywords(registry):
    monitor = registry.get("micromania")
    assert "réserver" in monitor.preorder_keywords
    assert "précommander" in monitor.preorder_keywords  # défauts conservés


def test_generic_monitor_always_available(registry):
    assert registry.get("generic") is not None


def test_unknown_site_raises(registry):
    with pytest.raises(UnknownSiteError):
        registry.get("site-inexistant")


def test_known_sites_lists_plugins(registry):
    assert "micromania" in registry.known_sites
    assert "generic" in registry.known_sites
