"""Tests du seed YAML→SQLite et de l'EventRecorder (bus → base)."""

import pytest

from src.core.events import Event, EventBus, EventType
from src.db import import_products_from_yaml
from src.models import Availability, ChangeEvent, ChangeType, ProductSnapshot
from src.repositories import (
    AlertRepository,
    CheckRepository,
    ProductRepository,
    TimelineRepository,
)
from src.services import EventRecorder
from tests.helpers import make_db, make_product

pytestmark = pytest.mark.asyncio


async def test_yaml_seed_only_when_empty(tmp_path):
    db = await make_db(tmp_path)
    repo = ProductRepository(db.session_factory)

    products = [make_product(name="A"), make_product(name="B")]
    assert await import_products_from_yaml(repo, products) == 2
    # Second démarrage : la base n'est plus vide, aucun ré-import.
    assert await import_products_from_yaml(repo, products) == 0
    assert await repo.count() == 2
    await db.dispose()


async def _make_recorder_env(tmp_path):
    db = await make_db(tmp_path)
    checks = CheckRepository(db.session_factory)
    timeline = TimelineRepository(db.session_factory)
    alerts = AlertRepository(db.session_factory)
    bus = EventBus()
    EventRecorder(checks, timeline, alerts).attach_to(bus)
    return db, checks, timeline, alerts, bus


async def test_recorder_persists_checks_and_baseline(tmp_path):
    db, checks, timeline, _, bus = await _make_recorder_env(tmp_path)
    product = make_product(uuid="u1")
    snap = ProductSnapshot(availability=Availability.UNAVAILABLE, page_exists=True)

    await bus.publish(Event(EventType.CHECK_COMPLETED,
                            {"product": product, "snapshot": snap, "response_time_ms": 321}))
    await bus.publish(Event(EventType.BASELINE_RECORDED,
                            {"product": product, "snapshot": snap}))
    await bus.publish(Event(EventType.CHECK_FAILED,
                            {"product": product, "error": "Timeout"}))

    records = await checks.recent("u1")
    assert {r.status for r in records} == {"ok", "error"}
    assert records[-1].response_time_ms == 321

    entries = await timeline.for_product("u1")
    assert entries[0].event_type == "baseline"
    await db.dispose()


async def test_recorder_persists_change_and_alert(tmp_path):
    db, _, timeline, alerts, bus = await _make_recorder_env(tmp_path)
    product = make_product(uuid="u1")
    snap = ProductSnapshot(availability=Availability.PREORDER, price="119,99 €",
                           page_exists=True)
    change = ChangeEvent(product=product, change_type=ChangeType.PREORDER_OPENED,
                         old_value="unavailable", new_value="preorder", snapshot=snap)

    payload = {"product": product, "change": change, "snapshot": snap}
    await bus.publish(Event(EventType.CHANGE_DETECTED, payload))

    entries = await timeline.for_product("u1")
    assert entries[0].label == "Précommande ouverte"

    stored = await alerts.list("u1")
    assert len(stored) == 1
    assert not stored[0].notified
    assert "alert_id" in payload  # id reporté pour NOTIFICATION_SENT

    # Simule le NotificationManager après envoi réussi.
    await bus.publish(Event(EventType.NOTIFICATION_SENT, dict(payload)))
    assert (await alerts.list("u1"))[0].notified
    await db.dispose()


async def test_recorder_labels_rupture(tmp_path):
    """La timeline parle métier : « Rupture de stock », pas un code technique."""
    db, _, timeline, _, bus = await _make_recorder_env(tmp_path)
    product = make_product(uuid="u1")
    snap = ProductSnapshot(availability=Availability.UNAVAILABLE, page_exists=True)
    change = ChangeEvent(product=product, change_type=ChangeType.WENT_OUT_OF_STOCK,
                         old_value="in_stock", new_value="unavailable", snapshot=snap)

    await bus.publish(Event(EventType.CHANGE_DETECTED,
                            {"product": product, "change": change, "snapshot": snap}))
    entries = await timeline.for_product("u1")
    assert entries[0].label == "Rupture de stock"
    await db.dispose()


async def test_recorder_names_the_merchant_on_seller_events(tmp_path):
    """« Amazon devient vendeur » se lit sans traduction."""
    db, _, timeline, _, bus = await _make_recorder_env(tmp_path)
    product = make_product(site="amazon", uuid="u1")
    snap = ProductSnapshot(availability=Availability.IN_STOCK, page_exists=True)
    change = ChangeEvent(
        product=product, change_type=ChangeType.SELLER_BECAME_OFFICIAL,
        old_value="Boutique Tierce", new_value="Amazon.fr", snapshot=snap,
    )

    await bus.publish(Event(EventType.CHANGE_DETECTED,
                            {"product": product, "change": change, "snapshot": snap}))
    entries = await timeline.for_product("u1")
    assert entries[0].label == "Amazon devient vendeur"
    await db.dispose()
