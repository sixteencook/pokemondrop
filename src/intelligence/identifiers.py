"""Extraction et normalisation des identifiants produit.

Aucune connaissance des sites : on s'appuie sur les standards que les
marchands publient déjà pour les moteurs de recherche.

    1. JSON-LD schema.org/Product  → gtin13, gtin12, gtin, sku, mpn, brand,
                                      releaseDate, category, image
    2. Microdata itemprop           → gtin13, sku, mpn, brand…
    3. Balises meta                 → product:*, og:*

Les GTIN sont validés par leur clé de contrôle, puis ramenés à une forme
canonique : un UPC-A (12 chiffres) devient l'EAN-13 équivalent, si bien
qu'un produit trouvé chez un marchand américain et chez un marchand
français porte le MÊME identifiant.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup

from src.intelligence.entities import ProductAttributes, ProductIdentifiers
from src.utils.logger import get_logger

log = get_logger("intelligence.identifiers")

_DIGITS = re.compile(r"\D")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


# --------------------------------------------------------------------- #
# Normalisation                                                          #
# --------------------------------------------------------------------- #

def _digits_only(value: str | None) -> str:
    return _DIGITS.sub("", value or "")


def gtin_check_digit(body: str) -> int:
    """Clé de contrôle GTIN (EAN-13, UPC-A, EAN-8) — pondération 3/1."""
    total = 0
    for index, char in enumerate(reversed(body)):
        weight = 3 if index % 2 == 0 else 1
        total += int(char) * weight
    return (10 - total % 10) % 10


def is_placeholder_gtin(digits: str) -> bool:
    """Code factice du type « 0000000000000 » ou « 1111111111116 ».

    Ces valeurs passent la clé de contrôle mais sont émises par des sites
    qui n'ont pas la vraie référence. Les accepter reviendrait à fusionner
    à confiance 100 tous les produits qui les portent — le pire scénario
    possible pour un moteur de corrélation.

    Le test porte sur le CORPS du code : « 1111111111116 » se termine par
    sa clé de contrôle (6) mais reste un code bidon.
    """
    body = digits[:-1] if len(digits) > 1 else digits
    return len(set(body)) <= 1


def is_valid_gtin(value: str) -> bool:
    digits = _digits_only(value)
    if len(digits) not in (8, 12, 13, 14):
        return False
    if is_placeholder_gtin(digits):
        return False
    return gtin_check_digit(digits[:-1]) == int(digits[-1])


def normalise_ean(value: str | None) -> Optional[str]:
    """EAN-13 canonique. Un UPC-A valide est converti (préfixe « 0 »)."""
    digits = _digits_only(value)
    if not digits or not is_valid_gtin(digits):
        return None
    if len(digits) == 12:
        digits = "0" + digits          # UPC-A → EAN-13
    if len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]            # GTIN-14 sans indicateur d'emballage
    return digits if len(digits) == 13 else digits


def normalise_upc(value: str | None) -> Optional[str]:
    digits = _digits_only(value)
    if len(digits) == 12 and is_valid_gtin(digits):
        return digits
    # Un EAN-13 commençant par 0 EST un UPC-A.
    if len(digits) == 13 and digits.startswith("0") and is_valid_gtin(digits):
        return digits[1:]
    return None


def normalise_isbn(value: str | None) -> Optional[str]:
    raw = re.sub(r"[^0-9Xx]", "", value or "").upper()
    if len(raw) == 13 and is_valid_gtin(raw):
        return raw
    if len(raw) == 10:
        return raw
    return None


def normalise_code(value: str | None) -> Optional[str]:
    """Référence libre (SKU, MPN) : casse et séparateurs uniformisés."""
    if not value:
        return None
    cleaned = re.sub(r"[\s_]+", "-", str(value).strip().upper())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or None


# --------------------------------------------------------------------- #
# Extraction                                                             #
# --------------------------------------------------------------------- #

def extract(html: str) -> tuple[ProductIdentifiers, ProductAttributes]:
    """Identifiants et attributs déduits d'une page produit.

    Ne lève jamais : une page mal formée rend simplement des champs vides.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        return ProductIdentifiers(), ProductAttributes()

    identifiers = ProductIdentifiers()
    attributes = ProductAttributes()

    for source in (_from_json_ld(soup), _from_microdata(soup), _from_meta(soup)):
        found_ids, found_attrs = source
        identifiers = identifiers.merged_with(found_ids)
        attributes = attributes.merged_with(found_attrs)

    return identifiers, attributes


def _build(raw: dict[str, Any]) -> tuple[ProductIdentifiers, ProductAttributes]:
    """Construit les entités depuis un dictionnaire de champs bruts."""
    identifiers = ProductIdentifiers(
        ean=normalise_ean(raw.get("gtin13") or raw.get("gtin") or raw.get("ean")),
        upc=normalise_upc(raw.get("gtin12") or raw.get("upc")
                          or raw.get("gtin13") or raw.get("gtin")),
        isbn=normalise_isbn(raw.get("isbn")),
        mpn=normalise_code(raw.get("mpn")),
        manufacturer_sku=normalise_code(raw.get("sku")),
        manufacturer_ref=normalise_code(raw.get("productID") or raw.get("model")),
    )
    release = raw.get("releaseDate") or raw.get("datePublished")
    match = _ISO_DATE.search(str(release)) if release else None
    attributes = ProductAttributes(
        brand=_clean_text(raw.get("brand")),
        collection=_clean_text(raw.get("isPartOf") or raw.get("collection")),
        edition=_clean_text(raw.get("edition") or raw.get("bookEdition")),
        category=_clean_text(raw.get("category")),
        release_date=match.group(0) if match else None,
        image_url=_first_url(raw.get("image")),
    )
    return identifiers, attributes


def _clean_text(value: Any) -> Optional[str]:
    """Aplati les formes schema.org (« Brand » imbriqué, listes…)."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id")
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get("name")
    if not value:
        return None
    text = " ".join(str(value).split())
    return text[:120] or None


def _first_url(value: Any) -> Optional[str]:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    if isinstance(value, str) and value.startswith("http"):
        return value
    return None


def _from_json_ld(soup: BeautifulSoup) -> tuple[ProductIdentifiers, ProductAttributes]:
    identifiers = ProductIdentifiers()
    attributes = ProductAttributes()

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _iter_product_nodes(payload):
            found_ids, found_attrs = _build(node)
            identifiers = identifiers.merged_with(found_ids)
            attributes = attributes.merged_with(found_attrs)

    return identifiers, attributes


def _iter_product_nodes(payload: Any, depth: int = 0) -> Iterable[dict[str, Any]]:
    """Parcourt un JSON-LD à la recherche des nœuds de type Product."""
    if depth > 6:
        return
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_product_nodes(item, depth + 1)
        return
    if not isinstance(payload, dict):
        return

    node_type = payload.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    if any(str(kind).lower().endswith("product") for kind in types if kind):
        yield payload

    for key in ("@graph", "mainEntity", "itemListElement", "hasVariant"):
        if key in payload:
            yield from _iter_product_nodes(payload[key], depth + 1)


def _from_microdata(soup: BeautifulSoup) -> tuple[ProductIdentifiers, ProductAttributes]:
    wanted = (
        "gtin13", "gtin12", "gtin", "ean", "upc", "isbn",
        "sku", "mpn", "productID", "model", "brand", "category", "releaseDate",
    )
    raw: dict[str, Any] = {}
    for prop in wanted:
        tag = soup.find(attrs={"itemprop": prop})
        if tag is None:
            continue
        value = tag.get("content") or tag.get("value") or tag.get_text(" ", strip=True)
        if value:
            raw[prop] = value
    return _build(raw)


def _from_meta(soup: BeautifulSoup) -> tuple[ProductIdentifiers, ProductAttributes]:
    mapping = {
        "product:retailer_item_id": "sku",
        "product:mfr_part_no": "mpn",
        "product:ean": "gtin13",
        "product:upc": "gtin12",
        "product:brand": "brand",
        "og:brand": "brand",
        "og:image": "image",
    }
    raw: dict[str, Any] = {}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        target = mapping.get(key)
        if target and tag.get("content"):
            raw.setdefault(target, tag["content"])
    return _build(raw)
