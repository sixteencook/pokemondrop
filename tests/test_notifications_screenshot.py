"""Chaîne notification : report de l'envoi, photo, repli texte."""

from pathlib import Path
from typing import Optional

import pytest

from src.core.events import SCREENSHOT_PENDING_KEY, Event, EventBus, EventType
from src.models import Availability, ChangeEvent, ChangeType, ProductSnapshot
from src.notifications import NotificationManager
from src.notifications.base import BaseNotifier
from tests.helpers import make_product

pytestmark = pytest.mark.asyncio


class RecordingNotifier(BaseNotifier):
    channel_name = "test"

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.calls: list[Optional[Path]] = []

    async def send(self, event: ChangeEvent, screenshot: Optional[Path] = None) -> bool:
        self.calls.append(screenshot)
        return self.succeed


def make_change(change_type=ChangeType.PREORDER_OPENED) -> ChangeEvent:
    return ChangeEvent(
        product=make_product(uuid="u1"),
        change_type=change_type,
        old_value="unavailable",
        new_value="preorder",
        snapshot=ProductSnapshot(availability=Availability.PREORDER,
                                 price="119,99 €", page_exists=True),
    )


def build(tmp_path, succeed: bool = True):
    bus = EventBus()
    notifier = RecordingNotifier(succeed)
    manager = NotificationManager(screenshots_dir=tmp_path)
    manager.register(notifier)
    manager.attach_to(bus)
    return bus, manager, notifier


async def test_sends_immediately_when_no_screenshot_pending(tmp_path):
    bus, _, notifier = build(tmp_path)
    change = make_change()
    await bus.publish(Event(EventType.CHANGE_DETECTED,
                            {"product": change.product, "change": change}))
    assert notifier.calls == [None]  # un envoi, sans capture


async def test_defers_when_screenshot_pending(tmp_path):
    """Une capture est en file : aucun envoi tant qu'elle n'est pas terminée."""
    bus, _, notifier = build(tmp_path)
    change = make_change()
    await bus.publish(Event(EventType.CHANGE_DETECTED, {
        "product": change.product, "change": change, SCREENSHOT_PENDING_KEY: True,
    }))
    assert notifier.calls == []  # rien n'est parti


async def test_sends_with_screenshot_on_completion(tmp_path):
    bus, _, notifier = build(tmp_path)
    capture = tmp_path / "2026-08-21" / "shot.png"
    capture.parent.mkdir(parents=True)
    capture.write_bytes(b"\x89PNG")

    change = make_change()
    await bus.publish(Event(EventType.SCREENSHOT_COMPLETED, {
        "product": change.product, "change": change,
        "screenshot_path": "2026-08-21/shot.png", "alert_id": 7,
    }))
    assert notifier.calls == [capture]


async def test_falls_back_to_text_when_capture_failed(tmp_path):
    """Capture en échec (path=None) : l'alerte part quand même."""
    bus, _, notifier = build(tmp_path)
    change = make_change()
    await bus.publish(Event(EventType.SCREENSHOT_COMPLETED, {
        "product": change.product, "change": change,
        "screenshot_path": None, "alert_id": 7,
    }))
    assert notifier.calls == [None]


async def test_missing_file_falls_back_to_text(tmp_path):
    """Le chemin existe en base mais le fichier a disparu du disque."""
    bus, _, notifier = build(tmp_path)
    change = make_change()
    await bus.publish(Event(EventType.SCREENSHOT_COMPLETED, {
        "product": change.product, "change": change,
        "screenshot_path": "2026-08-21/disparu.png", "alert_id": 7,
    }))
    assert notifier.calls == [None]


async def test_publishes_notification_sent_with_alert_id(tmp_path):
    bus, _, _ = build(tmp_path)
    results: list[Event] = []
    bus.subscribe(
        lambda event: results.append(event) or _noop(),
        {EventType.NOTIFICATION_SENT, EventType.NOTIFICATION_FAILED},
    )
    change = make_change()
    await bus.publish(Event(EventType.SCREENSHOT_COMPLETED, {
        "product": change.product, "change": change,
        "screenshot_path": None, "alert_id": 7,
    }))
    assert len(results) == 1
    assert results[0].type is EventType.NOTIFICATION_SENT
    assert results[0].payload["alert_id"] == 7


async def test_publishes_notification_failed_when_channel_fails(tmp_path):
    bus, _, _ = build(tmp_path, succeed=False)
    results: list[Event] = []
    bus.subscribe(lambda event: results.append(event) or _noop(),
                  {EventType.NOTIFICATION_FAILED})
    change = make_change()
    await bus.publish(Event(EventType.CHANGE_DETECTED,
                            {"product": change.product, "change": change}))
    assert len(results) == 1


async def _noop() -> None:
    return None
