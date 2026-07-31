"""Mots-clés propres à Micromania.

La comparaison est faite sur du texte normalisé (accents repliés, casse et
espaces uniformisés) : inutile de dupliquer les variantes accentuées, mais
les variantes de vocabulaire, elles, doivent figurer ici.

C'est LE fichier à enrichir si un statut reste « unknown » alors que la
page est bien récupérée : les logs de diagnostic listent les libellés de
boutons réellement présents sur la page.
"""

from src.monitors.generic import (
    DEFAULT_ADD_TO_CART_KEYWORDS,
    DEFAULT_PREORDER_KEYWORDS,
    DEFAULT_UNAVAILABLE_KEYWORDS,
)

PREORDER_KEYWORDS: tuple[str, ...] = DEFAULT_PREORDER_KEYWORDS + (
    "réserver",
    "réservation",
    "je précommande",
    "précommandez",
)

ADD_TO_CART_KEYWORDS: tuple[str, ...] = DEFAULT_ADD_TO_CART_KEYWORDS + (
    "retrait en magasin",
    "ajouter au panier",
    "j'achète",
    "commander",
    "disponible en ligne",
)

UNAVAILABLE_KEYWORDS: tuple[str, ...] = DEFAULT_UNAVAILABLE_KEYWORDS + (
    "offre momentanément indisponible",
    "produit indisponible en ligne",
    "actuellement indisponible",
    "me prévenir",
    "alertez-moi",
    "non disponible en ligne",
)
