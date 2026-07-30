"""Test d'intégration réel : Chromium capture une page locale.

Aucune requête vers un site marchand : la page est servie depuis un
fichier local (file://) contenant un faux bandeau cookies et un bouton
« Précommander ». Ignoré automatiquement si Chromium n'est pas installé.
"""

import pytest

from src.config import ScreenshotSettings
from src.services.screenshots.browser import BrowserPool, BrowserUnavailable
from src.services.screenshots.capture import CaptureRequest, PageCapturer

pytestmark = pytest.mark.asyncio

PAGE_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Fiche produit</title>
<style>
  body { font-family: sans-serif; padding: 40px; background: #fff; }
  .banner { position: fixed; inset: auto 0 0 0; background: #222; color: #fff;
            padding: 20px; display: flex; gap: 12px; justify-content: center; }
  .pulse { animation: blink 0.4s infinite alternate; }
  @keyframes blink { from { opacity: 1 } to { opacity: 0 } }
  button { padding: 10px 18px; font-size: 16px; }
</style></head>
<body>
  <h1 class="pulse">Pokémon 30 Ans — Ultra Premium Collection</h1>
  <p class="product-price">189,99 €</p>
  <button id="buy">Précommander</button>
  <div class="banner" id="cookie-banner">
    <span>Nous utilisons des cookies.</span>
    <button onclick="document.getElementById('cookie-banner').remove()">Tout accepter</button>
  </div>
</body></html>
"""


@pytest.fixture
def settings(tmp_path) -> ScreenshotSettings:
    return ScreenshotSettings(
        enabled=True, timeout_ms=15000, settle_delay_ms=100,
        max_concurrent=1, retention_days=0, directory=tmp_path,
    )


async def test_real_chromium_capture(tmp_path, settings):
    page_file = tmp_path / "fiche.html"
    page_file.write_text(PAGE_HTML, encoding="utf-8")
    target = tmp_path / "capture.png"

    pool = BrowserPool(settings)
    capturer = PageCapturer(settings)
    try:
        async with pool.page() as page:
            result = await capturer.capture(page, CaptureRequest(
                url=page_file.as_uri(), target=target,
            ))
    except BrowserUnavailable as exc:
        pytest.skip(f"Chromium indisponible : {exc}")
    finally:
        await pool.close()

    assert result.success
    assert target.is_file()
    assert result.size_bytes > 1000            # une vraie image, pas un fichier vide
    assert target.read_bytes()[:4] == b"\x89PNG"
    assert result.cookie_banner_closed          # bandeau fermé automatiquement


async def test_browser_is_reused_between_captures(tmp_path, settings):
    """Le navigateur est lancé une seule fois pour plusieurs captures."""
    page_file = tmp_path / "fiche.html"
    page_file.write_text(PAGE_HTML, encoding="utf-8")

    pool = BrowserPool(settings)
    capturer = PageCapturer(settings)
    try:
        async with pool.page() as page:
            await capturer.capture(page, CaptureRequest(
                url=page_file.as_uri(), target=tmp_path / "a.png"))
        first_browser = pool._browser

        async with pool.page() as page:
            await capturer.capture(page, CaptureRequest(
                url=page_file.as_uri(), target=tmp_path / "b.png"))
        assert pool._browser is first_browser  # même instance réutilisée
    except BrowserUnavailable as exc:
        pytest.skip(f"Chromium indisponible : {exc}")
    finally:
        await pool.close()

    assert (tmp_path / "a.png").is_file()
    assert (tmp_path / "b.png").is_file()
    assert not pool.is_running  # fermeture propre
