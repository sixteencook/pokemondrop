"""Diagnostic Telegram : test d'envoi et validation bot / chat IDs.

Utilisé par la page Paramètres du dashboard et par `python main.py
--test-telegram`.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.config import AppSettings
from src.models import Availability, ChangeEvent, ChangeType, ProductConfig, ProductSnapshot
from src.notifications import TelegramNotifier


def _test_event() -> ChangeEvent:
    product = ProductConfig(
        name="Message de test",
        site="micromania",
        url="https://www.micromania.fr",
        check_interval=60,
        enabled=True,
    )
    return ChangeEvent(
        product=product,
        change_type=ChangeType.PREORDER_OPENED,
        old_value="unavailable",
        new_value="preorder",
        snapshot=ProductSnapshot(availability=Availability.PREORDER, price="119,99 €"),
    )


async def send_test_alert(settings: AppSettings, client: httpx.AsyncClient) -> bool:
    """Envoie une fausse alerte à tous les destinataires configurés."""
    notifier = TelegramNotifier(
        settings.telegram_bot_token, settings.telegram_chat_ids, client
    )
    return await notifier.send(_test_event())


async def telegram_status(
    settings: AppSettings, client: httpx.AsyncClient
) -> dict[str, Any]:
    """Vérifie que le bot répond (getMe) et que chaque Chat ID est joignable
    (getChat). Ne poste aucun message."""
    if not settings.telegram_configured:
        return {"configured": False, "bot_ok": False, "bot_username": None, "chats": []}

    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    bot_ok, bot_username = False, None
    try:
        response = await client.get(f"{base}/getMe", timeout=10)
        data = response.json()
        bot_ok = bool(data.get("ok"))
        if bot_ok:
            bot_username = data["result"].get("username")
    except (httpx.HTTPError, ValueError):
        pass

    chats: list[dict[str, Any]] = []
    for chat_id in settings.telegram_chat_ids:
        entry: dict[str, Any] = {"chat_id": chat_id, "ok": False, "title": None}
        try:
            response = await client.get(
                f"{base}/getChat", params={"chat_id": chat_id}, timeout=10
            )
            data = response.json()
            if data.get("ok"):
                result = data["result"]
                entry["ok"] = True
                entry["title"] = (
                    result.get("title")
                    or result.get("username")
                    or result.get("first_name")
                )
        except (httpx.HTTPError, ValueError):
            pass
        chats.append(entry)

    return {
        "configured": True,
        "bot_ok": bot_ok,
        "bot_username": bot_username,
        "chats": chats,
    }
