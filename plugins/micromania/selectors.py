"""Sélecteurs CSS propres à Micromania.

À affiner en observant le HTML réel des fiches produit ; les valeurs
actuelles élargissent les sélecteurs génériques sans rien supposer d'une
structure précise.

COOKIE_SELECTORS est utilisé à la fois par le service de captures et par
le rendu navigateur de secours.
"""

from src.monitors.generic import DEFAULT_BUTTON_SELECTORS, DEFAULT_PRICE_SELECTORS

PRICE_SELECTORS: str = (
    f'{DEFAULT_PRICE_SELECTORS}, [class*="Price"], [class*="prix"], '
    '[data-product-price], .product-price, .price-sales'
)

BUTTON_SELECTORS: str = (
    f'{DEFAULT_BUTTON_SELECTORS}, [class*="Button"], [class*="addToCart"], '
    '[class*="add-to-cart"], [data-action], [class*="product-action"], '
    '[class*="availability"], [class*="stock"]'
)

COOKIE_SELECTORS: tuple[str, ...] = (
    "#onetrust-accept-btn-handler",
    ".onetrust-close-btn-handler",
    "#didomi-notice-agree-button",
    'button[id*="accept"]',
    'button[class*="cookie"]',
    'button[data-testid*="accept"]',
)
