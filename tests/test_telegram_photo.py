"""TelegramNotifier : sendPhoto, légende, repli texte, multi-destinations."""

import httpx
import pytest

from src.models import Availability, ChangeEvent, ChangeType, ProductSnapshot
from src.notifications import TelegramNotifier
from tests.helpers import make_product

pytestmark = pytest.mark.asyncio


def make_change() -> ChangeEvent:
    return ChangeEvent(
        product=make_product(uuid="u1", name="Pokémon 30 Ans UPC Jour"),
        change_type=ChangeType.PREORDER_OPENED,
        old_value="unavailable",
        new_value="preorder",
        snapshot=ProductSnapshot(availability=Availability.PREORDER,
                                 price="189,99 €", page_exists=True),
    )


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_sends_photo_with_caption(tmp_path):
    capture = tmp_path / "shot.png"
    capture.write_bytes(b"\x89PNG" + b"0" * 500)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler) as client:
        notifier = TelegramNotifier("token", ["123"], client)
        assert await notifier.send(make_change(), capture)

    assert len(seen) == 1
    assert seen[0].url.path.endswith("/sendPhoto")
    body = seen[0].content.decode("utf-8", errors="ignore")
    assert "ALERTE DROP" in body
    assert "Pokémon 30 Ans UPC Jour" in body
    assert "189,99" in body
    assert "Capture d'écran jointe" in body


async def test_falls_back_to_text_when_photo_rejected(tmp_path):
    capture = tmp_path / "shot.png"
    capture.write_bytes(b"\x89PNG")
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(400, json={"ok": False, "description": "refusé"})
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler) as client:
        notifier = TelegramNotifier("token", ["123"], client)
        assert await notifier.send(make_change(), capture)  # délivré malgré tout

    assert paths[0].endswith("/sendPhoto")
    assert paths[1].endswith("/sendMessage")  # repli automatique


async def test_text_only_when_no_screenshot():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler) as client:
        notifier = TelegramNotifier("token", ["123"], client)
        assert await notifier.send(make_change())

    assert paths == [paths[0]]
    assert paths[0].endswith("/sendMessage")


async def test_missing_capture_file_uses_text(tmp_path):
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler) as client:
        notifier = TelegramNotifier("token", ["123"], client)
        assert await notifier.send(make_change(), tmp_path / "inexistant.png")

    assert paths[0].endswith("/sendMessage")


async def test_photo_sent_to_every_chat(tmp_path):
    capture = tmp_path / "shot.png"
    capture.write_bytes(b"\x89PNG")
    chats: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8", errors="ignore")
        for chat in ("111", "-100222"):
            if chat in body:
                chats.append(chat)
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler) as client:
        notifier = TelegramNotifier("token", ["111", "-100222"], client)
        assert await notifier.send(make_change(), capture)

    assert set(chats) == {"111", "-100222"}


async def test_partial_delivery_still_counts_as_delivered(tmp_path):
    """Un destinataire injoignable n'empêche pas les autres."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8", errors="ignore")
        if "999" in body:
            return httpx.Response(403, json={"ok": False, "description": "bloqué"})
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler) as client:
        notifier = TelegramNotifier("token", ["111", "999"], client)
        assert await notifier.send(make_change())
