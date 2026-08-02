"""Rendu HTML par navigateur (implémentation du contrat HtmlRenderer).

Réutilise le BrowserPool DÉJÀ créé pour les captures d'écran : un seul
Chromium sert à la fois les captures et le rendu de secours. Les rendus
simultanés sont bornés par un sémaphore pour maîtriser la mémoire.

Ce que fait ce renderer : afficher la page comme un navigateur ordinaire,
puis lire le DOM obtenu. Il n'usurpe aucune identité et ne contourne
aucune protection — si un site refuse l'accès à une adresse IP, il le
refusera aussi au navigateur.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional, Sequence

from src.monitors.renderer import RenderError
from src.services.screenshots.browser import BrowserPool, BrowserUnavailable
from src.services.screenshots.cookies import dismiss_cookie_banners
from src.utils.logger import get_logger

log = get_logger("renderer")


class PlaywrightRenderer:
    """Récupère le DOM d'une page après exécution du JavaScript."""

    def __init__(
        self,
        pool: BrowserPool,
        timeout_ms: int = 20000,
        max_concurrent: int = 2,
        enabled: bool = True,
    ) -> None:
        self._pool = pool
        self._timeout_ms = timeout_ms
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._enabled = enabled
        self._unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self._enabled and self._unavailable_reason is None

    async def render(
        self,
        url: str,
        cookie_selectors: Sequence[str] = (),
        *,
        cookies: Optional[Mapping[str, str]] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> str:
        """Retourne le HTML rendu. Lève RenderError en cas d'échec."""
        if not self.available:
            raise RenderError(
                self._unavailable_reason or "rendu navigateur désactivé"
            )

        async with self._semaphore:
            try:
                async with self._pool.page(
                    locale=locale,
                    timezone_id=timezone,
                    cookies=_playwright_cookies(cookies, url),
                ) as page:
                    return await asyncio.wait_for(
                        self._render_page(page, url, cookie_selectors),
                        timeout=(self._timeout_ms / 1000) + 15,
                    )
            except BrowserUnavailable as exc:
                # Chromium absent : inutile de réessayer à chaque check.
                self._unavailable_reason = str(exc)
                log.error("Rendu navigateur désactivé — %s", exc)
                raise RenderError(str(exc)) from exc
            except asyncio.TimeoutError as exc:
                raise RenderError(f"délai dépassé sur {url}") from exc
            except Exception as exc:  # noqa: BLE001 — remonté proprement
                raise RenderError(f"{exc.__class__.__name__}: {exc}") from exc

    async def _render_page(
        self, page: object, url: str, cookie_selectors: Sequence[str]
    ) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        # Laisse au JavaScript le temps d'injecter le bloc d'achat ;
        # best effort, jamais bloquant sur les sites à polling permanent.
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=min(self._timeout_ms // 2, 8000)
            )
        except Exception:  # noqa: BLE001
            pass
        await dismiss_cookie_banners(page, cookie_selectors)
        return await page.content()


def _playwright_cookies(
    cookies: Optional[Mapping[str, str]], url: str
) -> tuple[dict[str, Any], ...]:
    """Traduit des cookies de préférence au format attendu par Playwright.

    La forme `url=` évite d'avoir à deviner domaine et chemin : Playwright
    les déduit de l'adresse visitée.
    """
    if not cookies:
        return ()
    return tuple(
        {"name": name, "value": value, "url": url}
        for name, value in cookies.items()
    )
