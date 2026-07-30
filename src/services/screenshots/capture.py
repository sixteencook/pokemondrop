"""Rendu d'une page en artefact (image aujourd'hui, PDF/GIF demain).

Architecture d'extension : une `CaptureStrategy` reçoit une page déjà
chargée et stabilisée, et produit un fichier. Le chargement, la fermeture
des bandeaux cookies et la stabilisation visuelle sont mutualisés — une
nouvelle stratégie n'a donc rien à réimplémenter.

Stratégies prévues (l'ossature est en place, voir CaptureRequest) :
  - PdfCaptureStrategy      : request.artifact = "pdf" → page.pdf()
  - RegionCaptureStrategy   : request.clip renseigné → capture d'une zone
  - SeriesCaptureStrategy   : plusieurs captures espacées (base des GIF)
  - DiffCaptureStrategy     : comparaison de deux captures successives
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence

from src.config import ScreenshotSettings
from src.services.screenshots.cookies import dismiss_cookie_banners
from src.utils.logger import get_logger

log = get_logger("screenshots.capture")

#: Neutralise animations, transitions et curseurs pour une image stable.
STABILISE_CSS = """
*, *::before, *::after {
  animation: none !important;
  animation-duration: 0s !important;
  transition: none !important;
  scroll-behavior: auto !important;
  caret-color: transparent !important;
}
html { cursor: none !important; scroll-behavior: auto !important; }
::-webkit-scrollbar { display: none !important; }
video, [class*='carousel'], [class*='slider'] { animation-play-state: paused !important; }
"""


@dataclass(frozen=True)
class CaptureRequest:
    """Tout ce qu'il faut pour produire un artefact depuis une URL."""

    url: str
    target: Path                        # chemin absolu du fichier à écrire
    artifact: str = "image"             # "image" (aujourd'hui) | "pdf" | "series"
    full_page: bool = True
    clip: Optional[dict[str, float]] = None   # zone {x, y, width, height}
    cookie_selectors: Sequence[str] = ()


@dataclass(frozen=True)
class CaptureResult:
    """Issue d'une tentative de capture."""

    success: bool
    path: Optional[Path] = None
    size_bytes: int = 0
    duration_ms: int = 0
    cookie_banner_closed: bool = False
    error: Optional[str] = None


class CaptureStrategy(Protocol):
    """Produit un fichier à partir d'une page déjà chargée et stabilisée."""

    async def run(self, page: Any, request: CaptureRequest) -> Path: ...


class ImageCaptureStrategy:
    """Capture PNG (ou JPEG) — pleine page ou zone délimitée."""

    def __init__(self, settings: ScreenshotSettings) -> None:
        self._settings = settings

    async def run(self, page: Any, request: CaptureRequest) -> Path:
        options: dict[str, Any] = {
            "path": str(request.target),
            "type": self._settings.image_format,
            "animations": "disabled",   # Playwright fige les animations CSS
            "caret": "hide",            # aucun curseur de saisie visible
            "scale": "device",          # respecte le device_scale_factor du contexte
            "timeout": self._settings.timeout_ms,
        }
        if request.clip is not None:
            options["clip"] = request.clip
        else:
            options["full_page"] = request.full_page
        if self._settings.image_format == "jpeg":
            options["quality"] = self._settings.quality  # sans effet en PNG (sans perte)

        await page.screenshot(**options)
        return request.target


class PageCapturer:
    """Orchestre chargement → cookies → stabilisation → stratégie."""

    def __init__(self, settings: ScreenshotSettings) -> None:
        self._settings = settings
        self._strategy: CaptureStrategy = ImageCaptureStrategy(settings)

    async def capture(self, page: Any, request: CaptureRequest) -> CaptureResult:
        """Exécute la capture sur une page fournie par le BrowserPool."""
        import time

        started = time.perf_counter()
        timeout = self._settings.timeout_ms

        # 1. Navigation : on n'exige que le DOM, le réseau vient après.
        await page.goto(request.url, wait_until="domcontentloaded", timeout=timeout)

        # 2. État réseau raisonnable — best effort, jamais bloquant.
        try:
            await page.wait_for_load_state("networkidle", timeout=min(timeout // 2, 8000))
        except Exception:  # noqa: BLE001 — sites à polling permanent
            log.check("Réseau jamais au repos sur %s — capture malgré tout.", request.url)

        # 3. Bandeau de consentement.
        banner = await dismiss_cookie_banners(page, request.cookie_selectors)

        # 4. Stabilisation visuelle.
        try:
            await page.add_style_tag(content=STABILISE_CSS)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(self._settings.settle_delay_ms)

        # 5. Artefact.
        path = await self._strategy.run(page, request)
        size = path.stat().st_size if path.exists() else 0
        return CaptureResult(
            success=size > 0,
            path=path,
            size_bytes=size,
            duration_ms=int((time.perf_counter() - started) * 1000),
            cookie_banner_closed=banner is not None,
            error=None if size > 0 else "Fichier de capture vide",
        )
