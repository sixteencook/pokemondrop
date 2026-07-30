"""Monitor Micromania — assemble metadata, keywords et selectors du plugin."""

from __future__ import annotations

from typing import ClassVar

from src.monitors.generic import GenericHtmlMonitor

from . import keywords, selectors
from .metadata import METADATA


class MicromaniaMonitor(GenericHtmlMonitor):
    site_name: ClassVar[str] = METADATA.site_name
    display_name: ClassVar[str] = METADATA.display_name

    preorder_keywords: ClassVar[tuple[str, ...]] = keywords.PREORDER_KEYWORDS
    add_to_cart_keywords: ClassVar[tuple[str, ...]] = keywords.ADD_TO_CART_KEYWORDS
    unavailable_keywords: ClassVar[tuple[str, ...]] = keywords.UNAVAILABLE_KEYWORDS
    price_selectors: ClassVar[str] = selectors.PRICE_SELECTORS
    cookie_selectors: ClassVar[tuple[str, ...]] = selectors.COOKIE_SELECTORS
