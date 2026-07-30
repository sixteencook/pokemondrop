"""Chargement et validation du fichier config/products.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.models import GlobalSettings, Priority, ProductConfig


class ConfigError(Exception):
    """Erreur de configuration (fichier absent, champ manquant, valeur invalide)."""


def _require(entry: dict[str, Any], field: str, index: int) -> Any:
    if field not in entry or entry[field] is None:
        raise ConfigError(f"Produit #{index + 1} : champ obligatoire manquant « {field} »")
    return entry[field]


def _parse_priority(entry: dict[str, Any], name: str) -> Priority:
    raw = str(entry.get("priority", Priority.NORMAL.value)).strip().lower()
    try:
        return Priority(raw)
    except ValueError:
        allowed = ", ".join(p.value for p in Priority)
        raise ConfigError(
            f"Produit « {name} » : priority « {raw} » invalide (valeurs : {allowed})"
        ) from None


def _parse_tags(entry: dict[str, Any], name: str) -> tuple[str, ...]:
    raw = entry.get("tags") or []
    if not isinstance(raw, list):
        raise ConfigError(f"Produit « {name} » : tags doit être une liste")
    tags = [str(tag).strip().lower() for tag in raw if str(tag).strip()]
    return tuple(dict.fromkeys(tags))  # dédoublonné, ordre conservé


def load_config(path: Path) -> tuple[GlobalSettings, list[ProductConfig]]:
    """Lit le YAML et retourne (paramètres globaux, liste de produits).

    Lève ConfigError si le fichier est absent ou invalide.
    """
    if not path.exists():
        raise ConfigError(f"Fichier de configuration introuvable : {path}")

    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ConfigError("La racine du YAML doit être un objet (defaults / products).")

    defaults_raw = raw.get("defaults") or {}
    defaults = GlobalSettings(
        check_interval=int(defaults_raw.get("check_interval", 60)),
        request_timeout=int(defaults_raw.get("request_timeout", 15)),
        max_retries=int(defaults_raw.get("max_retries", 3)),
        retry_backoff=int(defaults_raw.get("retry_backoff", 5)),
    )

    products_raw = raw.get("products") or []
    if not isinstance(products_raw, list):
        raise ConfigError("« products » doit être une liste.")

    products: list[ProductConfig] = []
    seen_keys: set[str] = set()
    for i, entry in enumerate(products_raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"Produit #{i + 1} : entrée invalide (objet attendu).")

        name = str(_require(entry, "name", i)).strip()
        product = ProductConfig(
            name=name,
            site=str(_require(entry, "site", i)).strip().lower(),
            url=str(entry.get("url") or "").strip(),
            check_interval=int(entry.get("check_interval", defaults.check_interval)),
            enabled=bool(entry.get("enabled", False)),
            group=(str(entry["group"]).strip() or None) if entry.get("group") else None,
            priority=_parse_priority(entry, name),
            tags=_parse_tags(entry, name),
        )
        if product.check_interval < 10:
            raise ConfigError(
                f"Produit « {product.name} » : check_interval minimal de 10 s "
                "(éviter de spammer les sites)."
            )
        if product.key in seen_keys:
            raise ConfigError(f"Produit en double dans la configuration : « {product.name} »")
        seen_keys.add(product.key)
        products.append(product)

    return defaults, products
