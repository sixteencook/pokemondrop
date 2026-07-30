"""Tests de l'event bus : filtrage par type et isolation des abonnés."""

import pytest

from src.core.events import Event, EventBus, EventType

pytestmark = pytest.mark.asyncio


async def test_subscriber_receives_matching_events():
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(handler, {EventType.CHANGE_DETECTED})
    await bus.publish(Event(EventType.CHANGE_DETECTED, {"x": 1}))
    await bus.publish(Event(EventType.CHECK_COMPLETED))  # filtré

    assert len(received) == 1
    assert received[0].type is EventType.CHANGE_DETECTED
    assert received[0].payload == {"x": 1}


async def test_subscriber_without_filter_receives_everything():
    bus = EventBus()
    received: list[EventType] = []

    async def handler(event: Event) -> None:
        received.append(event.type)

    bus.subscribe(handler)
    await bus.publish(Event(EventType.ENGINE_STARTED))
    await bus.publish(Event(EventType.CHECK_FAILED))

    assert received == [EventType.ENGINE_STARTED, EventType.CHECK_FAILED]


async def test_failing_subscriber_does_not_block_others():
    """Isolation : un abonné qui plante n'empêche ni les autres ni l'appelant."""
    bus = EventBus()
    received: list[str] = []

    async def broken(event: Event) -> None:
        raise RuntimeError("boom")

    async def healthy(event: Event) -> None:
        received.append("ok")

    bus.subscribe(broken)
    bus.subscribe(healthy)

    await bus.publish(Event(EventType.CHANGE_DETECTED))  # ne doit pas lever

    assert received == ["ok"]


async def test_publish_without_subscribers_is_noop():
    bus = EventBus()
    await bus.publish(Event(EventType.ENGINE_STOPPED))  # ne doit pas lever
