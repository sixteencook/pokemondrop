"""Construction des clés de recherche multi-critères.

Le moteur ne cherche plus avec UNE clé mais avec TOUTES celles que
l'identité permet de former, par ordre de pouvoir discriminant :

    EAN · UPC · ISBN · GTIN · ASIN · MPN · SKU · Model Number ·
    Manufacturer Part Number · Brand + Model · Nom canonique · Alias

Un plugin reçoit la liste ordonnée et s'arrête dès qu'il obtient un
résultat suffisamment sûr : inutile d'interroger un site dix fois quand la
première clé a répondu.

Ajouter une clé (numéro de série, référence interne, code-barres lu par
OCR…) revient à ajouter une entrée dans KEY_PRIORITIES — le moteur, lui,
ne change pas.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.intelligence.identity import ProductIdentity

#: Pouvoir discriminant de chaque type de clé (100 = certitude).
KEY_PRIORITIES: dict[str, int] = {
    "ean": 100,
    "upc": 98,
    "isbn": 96,
    "gtin": 94,
    "asin": 93,
    "mpn": 92,
    "sku": 90,
    "model_number": 88,
    "manufacturer_part_number": 86,
    "brand_model": 80,
    "canonical_name": 70,
    "alias": 60,
}

#: Nombre maximal d'alias transformés en clés (les titres se ressemblent).
MAX_ALIAS_KEYS = 3


@dataclass(frozen=True)
class SearchKey:
    """Une recherche à tenter, et ce qu'elle vaut si elle aboutit."""

    kind: str
    value: str
    priority: int
    fields: tuple[str, ...] = ()

    @property
    def is_strong(self) -> bool:
        """Clé désignant le produit sans ambiguïté (identifiant normalisé)."""
        return self.priority >= KEY_PRIORITIES["sku"]

    def __str__(self) -> str:
        return f"{self.kind}={self.value}"


def build_search_keys(
    identity: ProductIdentity, max_keys: int = 12
) -> list[SearchKey]:
    """Toutes les recherches possibles, de la plus sûre à la plus vague."""
    keys: list[SearchKey] = []

    def add(kind: str, value: str | None, *fields: str) -> None:
        if not value or not str(value).strip():
            return
        keys.append(SearchKey(
            kind=kind,
            value=str(value).strip(),
            priority=KEY_PRIORITIES.get(kind, 50),
            fields=fields or (kind,),
        ))

    # Identifiants forts, dans l'ordre du barème.
    for kind in ("ean", "upc", "isbn", "gtin", "asin", "mpn", "sku",
                 "model_number", "manufacturer_part_number"):
        add(kind, identity.get(kind))

    # Combinaisons : une marque seule ne dit rien, mais marque + modèle si.
    brand = identity.brand or identity.manufacturer
    model = identity.model_number or identity.mpn
    if brand and model:
        add("brand_model", f"{brand} {model}", "brand", "model_number")

    add("canonical_name", identity.canonical_name, "canonical_name")

    for alias in identity.aliases[:MAX_ALIAS_KEYS]:
        add("alias", alias, "canonical_name")

    # Tri par priorité décroissante, doublons de valeur écartés.
    keys.sort(key=lambda key: key.priority, reverse=True)
    seen: set[str] = set()
    unique: list[SearchKey] = []
    for key in keys:
        marker = key.value.lower()
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(key)

    return unique[:max_keys]
