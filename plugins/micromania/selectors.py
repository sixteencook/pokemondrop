"""Sélecteurs CSS propres à Micromania.

À préciser lorsque les fiches produit existeront ; en attendant, on
reprend les sélecteurs génériques (classes contenant « price », etc.).

COOKIE_SELECTORS sera utilisé par le futur service de captures Playwright
pour fermer les popups de consentement avant le screenshot.
"""

from src.monitors.generic import DEFAULT_PRICE_SELECTORS

PRICE_SELECTORS: str = DEFAULT_PRICE_SELECTORS

COOKIE_SELECTORS: tuple[str, ...] = (
    "#onetrust-accept-btn-handler",
    'button[id*="accept"]',
    'button[class*="cookie"]',
)
