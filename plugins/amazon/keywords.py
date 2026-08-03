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
    # Formulation réellement observée dans le bloc de disponibilité d'une
    # fiche en drop (ASIN B0H3PRH89L, août 2026).
    "disponible sur invitation",
    "request an invite",
    "request invite",
    "available by invitation",
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
#:
#: ⚠️ « date de sortie » et « sortie prévue » ont été RETIRÉS : ce sont des
#: mentions factuelles présentes sur toute fiche de précommande, y compris
#: quand le bouton « Précommander » est parfaitement actif. Les traiter
#: comme un état masquait l'ouverture d'une précommande — le faux négatif
#: le plus coûteux du projet.
COMING_SOON: tuple[str, ...] = (
    "bientot disponible",
    "prochainement disponible",
    "coming soon",
    "pas encore disponible a la vente",
)

#: Mise au panier.
ADD_TO_CART: tuple[str, ...] = (
    "ajouter au panier",
    "ajouter dans le panier",
    "add to cart",
    "add to basket",
)

#: Achat immédiat.
BUY_NOW: tuple[str, ...] = (
    "acheter maintenant",
    "acheter cet article",
    "buy now",
)

#: Mentions de stock : elles confirment une disponibilité, sans être des
#: boutons. Elles ne servent qu'en second rideau.
IN_STOCK: tuple[str, ...] = (
    "en stock",
    "in stock",
    "il ne reste plus que",
    "only",
)

#: Achat immédiat possible — union des trois familles ci-dessus.
AVAILABLE: tuple[str, ...] = ADD_TO_CART + BUY_NOW + IN_STOCK

#: Alerte de retour en stock : le produit existe, mais ne s'achète pas.
NOTIFY_ME: tuple[str, ...] = (
    "prevenez-moi",
    "previens-moi",
    "me prevenir",
    "m'alerter",
    "alertez-moi",
    "email me when available",
    "notify me",
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

# --------------------------------------------------------------------- #
# Libellés à ne JAMAIS considérer comme une action d'achat               #
# --------------------------------------------------------------------- #
#
# Une fiche Amazon porte des dizaines de boutons. Un seul décrit ce que
# l'acheteur peut faire du produit ; tous les autres sont du bruit, et
# c'est de ce bruit que naissaient les fausses alertes. Chaque entrée
# porte son motif, affiché tel quel dans les logs.

EXCLUDED_LABELS: tuple[tuple[str, str], ...] = (
    # Liste d'envies / registres
    ("ajouter a votre liste", "liste d'envies"),
    ("ajouter a une liste", "liste d'envies"),
    ("liste d'envies", "liste d'envies"),
    ("liste de souhaits", "liste d'envies"),
    ("add to list", "liste d'envies"),
    ("add to wish list", "liste d'envies"),
    ("liste de naissance", "liste d'envies"),
    # Livraison / adresse
    ("adresse de livraison", "adresse de livraison"),
    ("mettre a jour la position", "adresse de livraison"),
    ("mettre a jour l'emplacement", "adresse de livraison"),
    ("choisir le lieu de livraison", "adresse de livraison"),
    ("livrer a", "adresse de livraison"),
    ("select delivery location", "adresse de livraison"),
    # Prime
    ("essayez prime", "Prime"),
    ("essai gratuit de prime", "Prime"),
    ("adherer a prime", "Prime"),
    ("try prime", "Prime"),
    # Financement
    ("payer en plusieurs fois", "financement"),
    ("paiement en plusieurs fois", "financement"),
    ("en 4 fois", "financement"),
    ("financement", "financement"),
    # Garanties et services
    ("garantie", "garantie / assurance"),
    ("plan de protection", "garantie / assurance"),
    ("assurance", "garantie / assurance"),
    ("installation", "service annexe"),
    # Partage, comparaison, avis
    ("partager", "partage"),
    ("comparer", "comparaison"),
    ("voir les avis", "avis clients"),
    ("poser une question", "questions/réponses"),
    ("signaler", "signalement"),
    # Renvois vers d'autres offres : informatif, pas une action d'achat
    ("voir les options d'achat", "renvoi vers les autres vendeurs"),
    ("autres vendeurs sur amazon", "renvoi vers les autres vendeurs"),
    ("see all buying options", "renvoi vers les autres vendeurs"),
    # Retours et politiques
    ("retours gratuits", "politique de retour"),
    ("retourner cet article", "politique de retour"),
    ("en savoir plus", "lien d'information"),
    ("politique de confidentialite", "mentions légales"),
)
