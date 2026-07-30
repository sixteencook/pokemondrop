"""Identité du plugin Micromania."""

from src.monitors.plugin import PluginMetadata

METADATA = PluginMetadata(
    site_name="micromania",
    display_name="Micromania",
    version="1.0.0",
    base_url="https://www.micromania.fr",
    description="Surveillance des fiches produit Micromania (précommandes, stock).",
)
