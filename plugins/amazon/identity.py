"""Stratégie d'identité Amazon — découverte automatiquement.

Le cœur charge `plugins/<site>/identity.py` sans rien savoir du site.
Cette stratégie complète l'extraction générique (JSON-LD, microdata, meta)
par ce qui est propre à Amazon :

    ASIN            dans l'URL, le HTML ou les caractéristiques
    UPC / EAN       tableau « Informations sur le produit »
    Numéro de modèle, fabricant, marque, date de sortie

Elle s'exécute APRÈS la stratégie générique (priorité inférieure) : elle
comble les trous sans jamais écraser une information mieux établie.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from src.intelligence.identifiers import normalise_code, normalise_ean, normalise_upc
from src.intelligence.identity import ProductIdentity
from src.intelligence.strategies import IdentityContext
from src.monitors.generic import normalise

from .parser import extract_asin

#: Libellés de lignes du tableau de caractéristiques → champ d'identité.
_DETAIL_LABELS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("upc",), "upc"),
    (("ean", "code ean"), "ean"),
    (("gtin",), "gtin"),
    (("numero du modele", "numero de modele", "item model number",
      "model number", "modele"), "model_number"),
    (("reference fabricant", "numero de piece fabricant",
      "manufacturer part number", "mpn"), "mpn"),
    (("fabricant", "manufacturer"), "manufacturer"),
    (("marque", "brand"), "brand"),
    (("date de sortie", "date de mise en vente", "release date",
      "date de disponibilite"), "release_date"),
    (("collection", "serie", "series"), "collection"),
    (("edition", "format"), "edition"),
)

_ASIN_IN_HTML = re.compile(r'"?\bASIN"?\s*[:=]\s*"?([A-Z0-9]{10})\b')
_DATE_RE = re.compile(r"(\d{1,2})[/\s.-](\d{1,2})[/\s.-](\d{4})")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class AmazonIdentityStrategy:
    """Enrichit l'identité avec ce qu'Amazon expose de spécifique."""

    name = "amazon_details"
    priority = 90          # après la lecture des données structurées (100)

    async def enrich(
        self, identity: ProductIdentity, context: IdentityContext
    ) -> Optional[ProductIdentity]:
        if not _is_amazon(context):
            return None

        result = identity
        source = context.site or "amazon"

        asin = extract_asin(context.url or "")
        if not asin and context.html:
            match = _ASIN_IN_HTML.search(context.html)
            asin = match.group(1) if match else None
        if asin:
            result = result.with_field("asin", asin, 98, source)

        if not context.html:
            return result

        for label, value in _iter_detail_rows(context.html):
            field = _field_for(label)
            if field is None:
                continue
            result = _apply(result, field, value, source)

        return result


def _is_amazon(context: IdentityContext) -> bool:
    """La stratégie ne s'applique qu'aux pages Amazon."""
    if (context.site or "").lower() == "amazon":
        return True
    return "amazon." in (context.url or "").lower()


def _iter_detail_rows(html: str):
    """Paires (libellé, valeur) des tableaux de caractéristiques Amazon."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        return

    # Tableaux classiques : <tr><th>Libellé</th><td>Valeur</td></tr>
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            label = normalise(cells[0].get_text(" ", strip=True))
            value = " ".join(cells[1].get_text(" ", strip=True).split())
            if label and value:
                yield label, value

    # Listes à puces : <li><span>Libellé :</span><span>Valeur</span></li>
    for item in soup.select("#detailBullets_feature_div li, .detail-bullet-list li"):
        spans = item.find_all("span")
        if len(spans) >= 2:
            label = normalise(spans[0].get_text(" ", strip=True)).strip(" :‎")
            value = " ".join(spans[1].get_text(" ", strip=True).split())
            if label and value:
                yield label, value


def _field_for(label: str) -> Optional[str]:
    cleaned = label.strip(" :‎")
    for candidates, field in _DETAIL_LABELS:
        if any(cleaned == candidate or cleaned.startswith(candidate)
               for candidate in candidates):
            return field
    return None


def _apply(
    identity: ProductIdentity, field: str, value: str, source: str
) -> ProductIdentity:
    """Normalise puis pose la valeur, avec la confiance de sa nature."""
    if field == "ean":
        normalised = normalise_ean(value)
        return identity.with_field("ean", normalised, 100, source) if normalised else identity
    if field == "upc":
        normalised = normalise_upc(value) or normalise_ean(value)
        if not normalised:
            return identity
        # Un UPC-A vaut aussi comme EAN-13 : on renseigne les deux.
        identity = identity.with_field("upc", normalise_upc(value), 100, source)
        return identity.with_field("ean", normalise_ean(value), 100, source)
    if field == "gtin":
        normalised = normalise_ean(value)
        return identity.with_field("gtin", normalised, 100, source) if normalised else identity
    if field in ("model_number", "mpn"):
        return identity.with_field(field, normalise_code(value), 92, source)
    if field == "release_date":
        iso = _to_iso_date(value)
        return identity.with_field("release_date", iso, 88, source) if iso else identity
    return identity.with_field(field, value[:120], 85, source)


def _to_iso_date(value: str) -> Optional[str]:
    """« 21/08/2026 » ou « 2026-08-21 » → « 2026-08-21 »."""
    iso = _ISO_DATE_RE.search(value)
    if iso:
        return iso.group(0)
    match = _DATE_RE.search(value)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"
