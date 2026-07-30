"""Mots-clés propres à Micromania.

Les fiches Pokémon TCG 30e anniversaire n'étant pas encore publiées,
ces listes partent des mots-clés génériques, enrichis du vocabulaire
habituel de Micromania. À affiner en observant les vraies pages le
jour venu — SEUL ce fichier (et selectors.py) sera à ajuster.
"""

from src.monitors.generic import (
    DEFAULT_ADD_TO_CART_KEYWORDS,
    DEFAULT_PREORDER_KEYWORDS,
    DEFAULT_UNAVAILABLE_KEYWORDS,
)

PREORDER_KEYWORDS: tuple[str, ...] = DEFAULT_PREORDER_KEYWORDS + (
    "réserver",
    "réservation",
)

ADD_TO_CART_KEYWORDS: tuple[str, ...] = DEFAULT_ADD_TO_CART_KEYWORDS + (
    "retrait en magasin",
)

UNAVAILABLE_KEYWORDS: tuple[str, ...] = DEFAULT_UNAVAILABLE_KEYWORDS + (
    "offre momentanément indisponible",
    "produit indisponible en ligne",
)
