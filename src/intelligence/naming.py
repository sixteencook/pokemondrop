"""Normalisation et comparaison des noms de produits.

Un même produit s'appelle rarement pareil chez deux marchands :

    « Pokémon 30 Ans — Ultra Premium Collection (UPC) »
    « POKEMON 30 ANS ULTRA PREMIUM COLLECTION - Neuf »

La clé normalisée les ramène à la même chaîne. La similarité, elle,
sert aux rapprochements de moindre confiance (« nom proche »).

Aucune dépendance externe : recouvrement de jetons combiné à une
similarité de séquence, ce qui reste prévisible et testable.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from src.monitors.generic import normalise

#: Mots sans valeur discriminante, retirés de la clé.
_STOP_WORDS: frozenset[str] = frozenset({
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "the", "of",
    "neuf", "nouveau", "new", "edition", "ed", "version", "pack", "lot",
    "officiel", "official", "fr", "france", "francais", "vf",
    "produit", "article", "reference", "ref",
})

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    """Nom réduit à sa forme comparable, ordre des mots conservé."""
    folded = normalise(name)
    folded = _PUNCTUATION.sub(" ", folded)
    tokens = [
        token for token in _SPACES.split(folded)
        if token and token not in _STOP_WORDS
    ]
    return " ".join(tokens)


def name_key(name: str) -> str:
    """Clé d'égalité : jetons normalisés, dédoublonnés et triés.

    L'ordre des mots ne compte pas — « UPC Pokémon 30 ans » et
    « Pokémon 30 ans UPC » produisent la même clé.
    """
    tokens = sorted(set(normalise_name(name).split()))
    return " ".join(tokens)


def similarity(left: str, right: str) -> float:
    """Proximité de deux noms, entre 0 et 1.

    Moyenne du recouvrement de jetons (robuste aux mots ajoutés) et de la
    similarité de séquence (sensible aux fautes de frappe).
    """
    left_norm, right_norm = normalise_name(left), normalise_name(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0

    left_tokens, right_tokens = set(left_norm.split()), set(right_norm.split())
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    return (jaccard + sequence) / 2
