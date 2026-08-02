"""Cycle de vie du navigateur Chromium (Playwright).

Le navigateur est lancé UNE fois puis réutilisé : chaque capture obtient
un contexte neuf (isolation des cookies, du cache et du stockage), fermé
juste après. Lancer Chromium coûte ~1 s, ouvrir un contexte ~30 ms.

Playwright est importé paresseusement : l'application démarre et
fonctionne parfaitement sans lui (captures simplement désactivées).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

from src.config import ScreenshotSettings
from src.utils.logger import get_logger

log = get_logger("screenshots.browser")

#: Drapeaux limitant l'empreinte mémoire (conteneur Railway).
CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-features=TranslateUI,BackForwardCache",
    "--mute-audio",
]

#: Localisation par défaut du contexte : celle de l'utilisateur du projet.
#: Un monitor peut la remplacer page par page.
DEFAULT_LOCALE = "fr-FR"
DEFAULT_TIMEZONE = "Europe/Paris"


class BrowserUnavailable(RuntimeError):
    """Playwright n'est pas installé, ou Chromium n'a pas pu démarrer."""


class BrowserPool:
    """Détenteur unique du navigateur, protégé par un verrou de démarrage."""

    def __init__(self, settings: ScreenshotSettings) -> None:
        self._settings = settings
        self._playwright: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def _ensure_browser(self) -> Any:
        """Démarre Chromium au premier besoin ; le relance s'il a crashé."""
        async with self._lock:
            if self.is_running:
                return self._browser

            if self._browser is not None:  # navigateur mort : on nettoie
                log.error("Chromium s'est arrêté de façon inattendue — relance.")
                await self._shutdown_locked()

            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise BrowserUnavailable(
                    "Playwright n'est pas installé. "
                    "pip install playwright && playwright install chromium"
                ) from exc

            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True, args=CHROMIUM_ARGS
                )
            except Exception as exc:  # noqa: BLE001
                await self._shutdown_locked()
                raise BrowserUnavailable(
                    f"Chromium n'a pas pu démarrer ({exc}). "
                    "Exécutez « playwright install chromium »."
                ) from exc

            log.ok("Chromium démarré (captures d'écran actives).")
            return self._browser

    @asynccontextmanager
    async def page(
        self,
        locale: str | None = None,
        timezone_id: str | None = None,
        cookies: Sequence[dict[str, Any]] = (),
    ) -> AsyncIterator[Any]:
        """Fournit une page neuve dans un contexte isolé, fermé à la sortie.

        `locale`, `timezone_id` et `cookies` permettent à un monitor de
        demander une version localisée précise d'une page : sans eux, le
        site choisit lui-même une langue et un pays de livraison, et la
        page rendue n'est pas celle que l'utilisateur voit.
        """
        browser = await self._ensure_browser()
        context = await browser.new_context(
            viewport={
                "width": self._settings.viewport_width,
                "height": self._settings.viewport_height,
            },
            device_scale_factor=self._settings.device_scale_factor,
            locale=locale or DEFAULT_LOCALE,
            timezone_id=timezone_id or DEFAULT_TIMEZONE,
            reduced_motion="reduce",
            java_script_enabled=True,
        )
        context.set_default_timeout(self._settings.timeout_ms)
        if cookies:
            try:
                await context.add_cookies(list(cookies))
            except Exception as exc:  # noqa: BLE001 — préférence, pas blocage
                log.error("Cookies de préférence refusés : %s", exc)
        page = await context.new_page()
        try:
            yield page
        finally:
            # Fermeture systématique : aucune page ni contexte ne fuit.
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        async with self._lock:
            await self._shutdown_locked()

    async def _shutdown_locked(self) -> None:
        """Arrêt propre (le verrou doit déjà être détenu)."""
        for closer, label in (
            (getattr(self._browser, "close", None), "navigateur"),
            (getattr(self._playwright, "stop", None), "Playwright"),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception as exc:  # noqa: BLE001
                log.error("Arrêt du %s en échec : %s", label, exc)
        self._browser = None
        self._playwright = None
