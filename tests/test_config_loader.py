"""Tests du chargement de la configuration YAML."""

from pathlib import Path

import pytest

from src.config import ConfigError, load_config


def write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "products.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_valid_config(tmp_path):
    path = write_yaml(tmp_path, """
defaults:
  check_interval: 30
products:
  - name: "Pokémon 30 Ans ETB"
    site: micromania
    url: ""
    enabled: false
""")
    defaults, products = load_config(path)
    assert defaults.check_interval == 30
    assert len(products) == 1
    assert products[0].check_interval == 30  # hérite du défaut
    assert not products[0].is_monitorable    # URL vide → non surveillable


def test_product_with_url_and_enabled_is_monitorable(tmp_path):
    path = write_yaml(tmp_path, """
products:
  - name: "Test"
    site: micromania
    url: "https://example.com/p"
    enabled: true
""")
    _, products = load_config(path)
    assert products[0].is_monitorable


def test_group_is_optional_and_parsed(tmp_path):
    path = write_yaml(tmp_path, """
products:
  - name: "UPC Jour Micromania"
    site: micromania
    group: pokemon-30-upc-jour
  - name: "Sans groupe"
    site: micromania
""")
    _, products = load_config(path)
    assert products[0].group == "pokemon-30-upc-jour"
    assert products[1].group is None


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yaml")


def test_missing_name_raises(tmp_path):
    path = write_yaml(tmp_path, """
products:
  - site: micromania
""")
    with pytest.raises(ConfigError):
        load_config(path)


def test_interval_too_short_raises(tmp_path):
    path = write_yaml(tmp_path, """
products:
  - name: "Test"
    site: micromania
    check_interval: 2
""")
    with pytest.raises(ConfigError):
        load_config(path)


def test_duplicate_product_raises(tmp_path):
    path = write_yaml(tmp_path, """
products:
  - name: "Test"
    site: micromania
  - name: "Test"
    site: micromania
""")
    with pytest.raises(ConfigError):
        load_config(path)
