"""Utilitaires de texte partagés.

Volontairement sans dépendance : ce module est importable depuis n'importe
quelle couche sans créer de cycle.
"""

from __future__ import annotations

import re
import unicodedata

_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_length: int = 60) -> str:
    """« Pokémon 30 Ans UPC Jour » → « pokemon_30_ans_upc_jour »."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = _SLUG_CLEAN.sub("_", folded.lower()).strip("_")
    return (slug[:max_length].rstrip("_")) or "produit"
