"""Vocabulaire Amazon, par état — aucune logique ici.

Séparer le vocabulaire de la logique permet d'ajuster un libellé sans
toucher au parser : c'est ce fichier que l'on modifie quand Amazon change
une formulation, ou quand on ajoute une place de marché (amazon.de, .co.uk).

Toutes les comparaisons sont faites sur du texte normalisé (accents
repliés, casse et espaces uniformisés) : inutile de dupliquer les
variantes accentuées.
"""

from __future__ import annotations

#: Demande d'invitation (drops très demandés) — l'achat n'est pas direct,
#: mais la page est vivante : c'est un signal fort.
INVITATION: tuple[str, ...] = (
    "demande d'invitation",
    "demander une invitation",
    "request an invite",
    "request invite",
    "vous avez ete invite",
    "invitation acceptee",
)

#: Précommande.
PREORDER: tuple[str, ...] = (
    "precommander",
    "precommande",
    "pre-commander",
    "pre-order",
    "preorder",
    "reserver maintenant",
)

#: Sortie annoncée mais rien à faire pour l'instant.
COMING_SOON: tuple[str, ...] = (
    "bientot disponible",
    "prochainement disponible",
    "coming soon",
    "date de sortie",
    "sortie prevue",
)

#: Achat immédiat possible.
AVAILABLE: tuple[str, ...] = (
    "ajouter au panier",
    "ajouter dans le panier",
    "add to cart",
    "add to basket",
    "acheter maintenant",
    "acheter cet article",
    "buy now",
    "en stock",
    "in stock",
    "il ne reste plus que",
    "only",
)

#: Rupture — le produit existe mais ne peut pas être commandé.
OUT_OF_STOCK: tuple[str, ...] = (
    "temporairement en rupture de stock",
    "temporairement indisponible",
    "actuellement indisponible",
    "rupture de stock",
    "en rupture",
    "currently unavailable",
    "temporarily out of stock",
    "out of stock",
    "nous ne savons pas quand cet article sera de nouveau approvisionne",
)

#: Indisponible durablement / plus vendu.
UNAVAILABLE: tuple[str, ...] = (
    "cet article n'est pas disponible",
    "produit non disponible",
    "n'est plus disponible",
    "no longer available",
    "this item is not available",
    "indisponible",
)

#: Marqueurs d'une page d'interception (robot, connexion, captcha).
#: Ils déclenchent l'escalade vers le navigateur plutôt qu'une conclusion.
BOT_WALL: tuple[str, ...] = (
    "saisissez les caracteres",
    "enter the characters you see",
    "pour continuer, veuillez",
    "type the characters",
    "sorry, we just need to make sure you're not a robot",
    "desole, il faut que nous nous assurions",
    "api-services-support@amazon.com",
    "identifiez-vous pour continuer",
)

#: Libellés de la ligne « vendu par / expédié par » de la buy box.
SOLD_BY: tuple[str, ...] = ("vendu par", "sold by", "vendeur")
SHIPPED_BY: tuple[str, ...] = ("expedie par", "ships from", "livre par")
