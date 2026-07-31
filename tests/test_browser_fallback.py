"""Bascule automatique sur le navigateur : blocage HTTP et pages JavaScript."""

import httpx
import pytest

from src.models import Availability
from src.monitors import FetchError
from src.monitors.generic import GenericHtmlMonitor
from src.monitors.renderer import RenderError
from tests.helpers import make_product

pytestmark = pytest.mark.asyncio

BLOCKED_PAGE = "<html><head><title>Forbidden</title></head><body>403</body></html>"

STATIC_SHELL = (
    "<html lang='fr'><head><title>Pokémon UPC</title></head>"
    "<body><div id='root'></div>"
    "<p>" + "Chargement de la fiche produit. " * 20 + "</p></body></html>"
)

RENDERED_PAGE = (
    "<html lang='fr'><head><title>Pokémon UPC</title></head><body>"
    "<h1>Pokémon 30 Ans UPC</h1>"
    '<span class="price">189,99 €</span>'
    "<button>Précommander</button>"
    "<p>" + "Description complète du produit. " * 20 + "</p></body></html>"
)


class FakeRenderer:
    """Renderer scriptable, sans Playwright."""

    def __init__(self, html: str = RENDERED_PAGE, fail: bool = False,
                 available: bool = True) -> None:
        self._html = html
        self._fail = fail
        self._available = available
        self.calls: list[str] = []
        self.cookie_selectors_seen: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self._available

    async def render(self, url: str, cookie_selectors=()) -> str:
        self.calls.append(url)
        self.cookie_selectors_seen = tuple(cookie_selectors)
        if self._fail:
            raise RenderError("échec simulé")
        return self._html


def client_returning(status: int, body: str = "") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body,
                              headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_monitor(status: int, body: str = "", renderer=None) -> GenericHtmlMonitor:
    return GenericHtmlMonitor(client_returning(status, body), renderer)


async def test_403_escalates_to_browser():
    renderer = FakeRenderer()
    monitor = make_monitor(403, BLOCKED_PAGE, renderer)
    snapshot = await monitor.check(make_product(url="https://example.com/p"))

    assert renderer.calls == ["https://example.com/p"]
    assert snapshot.availability is Availability.PREORDER
    assert snapshot.price == "189,99 €"


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_all_blocking_statuses_escalate(status):
    renderer = FakeRenderer()
    monitor = make_monitor(status, BLOCKED_PAGE, renderer)
    snapshot = await monitor.check(make_product(url="https://example.com/p"))
    assert len(renderer.calls) == 1
    assert snapshot.availability is Availability.PREORDER


async def test_403_without_renderer_raises_actionable_error():
    monitor = make_monitor(403, BLOCKED_PAGE, renderer=None)
    with pytest.raises(FetchError) as exc_info:
        await monitor.check(make_product(url="https://example.com/p"))
    assert "403" in str(exc_info.value)
    assert "BROWSER_FALLBACK_ENABLED" in str(exc_info.value)


async def test_403_then_render_failure_raises():
    renderer = FakeRenderer(fail=True)
    monitor = make_monitor(403, BLOCKED_PAGE, renderer)
    with pytest.raises(FetchError):
        await monitor.check(make_product(url="https://example.com/p"))


async def test_unknown_status_escalates_to_browser():
    """HTTP 200 mais coquille vide : le rendu navigateur tranche."""
    renderer = FakeRenderer()
    monitor = make_monitor(200, STATIC_SHELL, renderer)
    snapshot = await monitor.check(make_product(url="https://example.com/p"))

    assert len(renderer.calls) == 1
    assert snapshot.availability is Availability.PREORDER


async def test_conclusive_http_response_does_not_use_browser():
    """Économie : pas de navigateur quand le HTML statique suffit."""
    renderer = FakeRenderer()
    monitor = make_monitor(200, RENDERED_PAGE, renderer)
    snapshot = await monitor.check(make_product(url="https://example.com/p"))

    assert renderer.calls == []  # aucun rendu déclenché
    assert snapshot.availability is Availability.PREORDER


async def test_unknown_stays_unknown_when_render_also_inconclusive():
    renderer = FakeRenderer(html=STATIC_SHELL)
    monitor = make_monitor(200, STATIC_SHELL, renderer)
    snapshot = await monitor.check(make_product(url="https://example.com/p"))
    assert snapshot.availability is Availability.UNKNOWN


async def test_unavailable_renderer_is_skipped():
    renderer = FakeRenderer(available=False)
    monitor = make_monitor(200, STATIC_SHELL, renderer)
    snapshot = await monitor.check(make_product(url="https://example.com/p"))
    assert renderer.calls == []
    assert snapshot.availability is Availability.UNKNOWN


async def test_404_never_escalates():
    renderer = FakeRenderer()
    monitor = make_monitor(404, renderer=renderer)
    snapshot = await monitor.check(make_product(url="https://example.com/p"))
    assert renderer.calls == []
    assert not snapshot.page_exists


async def test_plugin_cookie_selectors_reach_the_renderer():
    """Le rendu doit fermer les bandeaux propres au site."""
    from plugins.micromania.monitor import MicromaniaMonitor

    renderer = FakeRenderer()
    monitor = MicromaniaMonitor(client_returning(403, BLOCKED_PAGE), renderer)
    await monitor.check(make_product(site="micromania", url="https://example.com/p"))
    assert "#onetrust-accept-btn-handler" in renderer.cookie_selectors_seen


async def test_requires_javascript_skips_http_entirely():
    class JsMonitor(GenericHtmlMonitor):
        site_name = "js-site"
        requires_javascript = True

    renderer = FakeRenderer()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("aucune requête HTTP ne devait partir")

    monitor = JsMonitor(httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                        renderer)
    snapshot = await monitor.check(make_product(url="https://example.com/p"))
    assert snapshot.availability is Availability.PREORDER
    assert len(renderer.calls) == 1
