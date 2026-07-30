"""Tests de la couche Repository (SQLite jetable par test)."""

import pytest

from src.models import Availability, Priority, ProductSnapshot
from src.repositories import (
    AlertRepository,
    CheckRepository,
    ProductRepository,
    SnapshotRepository,
    TimelineRepository,
)
from tests.helpers import make_db, make_product

pytestmark = pytest.mark.asyncio


async def test_product_crud_roundtrip(tmp_path):
    db = await make_db(tmp_path)
    repo = ProductRepository(db.session_factory)

    created = await repo.create(make_product())
    assert created.uuid  # uuid immuable généré
    assert created.priority is Priority.HIGH
    assert created.tags == ("pokemon", "etb")

    loaded = await repo.get(created.uuid)
    assert loaded == created

    updated = await repo.update(created.uuid, url="https://example.com/new", enabled=False)
    assert updated.url == "https://example.com/new"
    assert not updated.enabled
    assert updated.uuid == created.uuid  # l'uuid ne change jamais

    assert await repo.delete(created.uuid)
    assert await repo.get(created.uuid) is None
    await db.dispose()


async def test_product_update_rejects_uuid(tmp_path):
    db = await make_db(tmp_path)
    repo = ProductRepository(db.session_factory)
    created = await repo.create(make_product())
    with pytest.raises(ValueError):
        await repo.update(created.uuid, uuid="autre")
    await db.dispose()


async def test_snapshot_roundtrip(tmp_path):
    db = await make_db(tmp_path)
    repo = SnapshotRepository(db.session_factory)

    assert await repo.load("p1") is None  # premier lancement
    snap = ProductSnapshot(availability=Availability.PREORDER, price="119,99 €",
                           page_exists=True, content_hash="abc")
    await repo.save("p1", snap)
    loaded = await repo.load("p1")
    assert loaded.availability is Availability.PREORDER
    assert loaded.price == "119,99 €"

    # Écrasement (update, pas insert en double).
    await repo.save("p1", ProductSnapshot(availability=Availability.IN_STOCK))
    assert (await repo.load("p1")).availability is Availability.IN_STOCK
    await db.dispose()


async def test_checks_add_recent_and_purge(tmp_path):
    db = await make_db(tmp_path)
    repo = CheckRepository(db.session_factory)

    await repo.add("p1", status="ok", availability="preorder", response_time_ms=250)
    await repo.add("p1", status="error", error="Timeout")
    recent = await repo.recent("p1")
    assert len(recent) == 2
    assert recent[0].status == "error"  # tri décroissant

    assert await repo.purge_older_than(30) == 0  # rien d'assez ancien
    await db.dispose()


async def test_timeline_per_product(tmp_path):
    db = await make_db(tmp_path)
    repo = TimelineRepository(db.session_factory)

    await repo.add("p1", "baseline", "Surveillance démarrée")
    await repo.add("p1", "price_appeared", "Prix détecté", new_value="119,99 €")
    await repo.add("p2", "baseline", "Surveillance démarrée")

    entries = await repo.for_product("p1")
    assert [e.event_type for e in entries] == ["price_appeared", "baseline"]
    assert len(await repo.recent()) == 3
    await db.dispose()


async def test_alerts_add_and_mark_notified(tmp_path):
    db = await make_db(tmp_path)
    repo = AlertRepository(db.session_factory)

    alert_id = await repo.add("p1", "preorder_opened", "unavailable", "preorder",
                              "119,99 €", "https://example.com")
    alerts = await repo.list("p1")
    assert len(alerts) == 1
    assert not alerts[0].notified

    await repo.mark_notified(alert_id)
    assert (await repo.list("p1"))[0].notified

    await repo.set_screenshot(alert_id, "screenshots/2026-08-18/x.png")
    assert (await repo.list("p1"))[0].screenshot_path == "screenshots/2026-08-18/x.png"
    await db.dispose()
