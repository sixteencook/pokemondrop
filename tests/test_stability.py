"""Stabilisation : anti-instabilité, confirmation, hashes invisibles, preuve.

Objectif de la phase : mieux vaut manquer une alerte qu'en produire une
fausse. Ces tests vérifient chacun des garde-fous.
"""

import asyncio
import re

import httpx
import pytest

from src.core import evidence
from src.core.detector import describe_buttons, describe_state, detect_changes
from src.core.engine import MonitorEngine
from src.core.events import Event, EventBus, EventType
from src.models import (
    Availability,
    ChangeType,
    GlobalSettings,
    ProductSnapshot,
)
from src.monitors import MonitorRegistry
from src.monitors.base import BaseMonitor
from src.repositories import SnapshotRepository
from tests.helpers import make_db, make_product

asyncio_test = pytest.mark.asyncio

#: Un hash tel que produit par le projet : 16 caractères hexadécimaux.
HASH_RE = re.compile(r"^[0-9a-f]{16}$")


def snap(**kwargs) -> ProductSnapshot:
    defaults = dict(page_exists=True, availability=Availability.UNAVAILABLE)
    defaults.update(kwargs)
    return ProductSnapshot(**defaults)


# --------------------------------------------------------------------- #
# Problème 1 — aucun hash ne doit être visible                           #
# --------------------------------------------------------------------- #

def test_button_change_shows_readable_labels():
    old = snap(buttons=["Demande d'invitation"], content_hash="aaaaaaaaaaaaaaaa")
    new = snap(buttons=["Ajouter au panier"], content_hash="bbbbbbbbbbbbbbbb")

    events = detect_changes(make_product(), old, new)
    change = next(e for e in events if e.change_type is ChangeType.BUTTON_CHANGED)

    assert change.old_value == "« Demande d'invitation »"
    assert change.new_value == "« Ajouter au panier »"
    assert not HASH_RE.match(change.old_value or "")


def test_page_change_never_exposes_a_hash():
    """Le hash reste un outil interne : jamais affiché."""
    old = snap(price="189,99 €", content_hash="aaaaaaaaaaaaaaaa")
    new = snap(price="189,99 €", content_hash="bbbbbbbbbbbbbbbb")

    events = detect_changes(make_product(), old, new)
    change = next(e for e in events if e.change_type is ChangeType.PAGE_CHANGED)

    for value in (change.old_value, change.new_value):
        assert not HASH_RE.match(value or "")
        assert "aaaaaaaa" not in (value or "")
    assert "unavailable" in change.old_value
    assert "189,99 €" in change.old_value


def test_no_change_type_ever_carries_a_hash():
    """Balayage complet : aucun événement ne doit transporter un hash."""
    old = snap(price="10,00 €", buttons=["Indisponible"],
               content_hash="0123456789abcdef")
    new = snap(availability=Availability.IN_STOCK, price="12,00 €",
               buttons=["Ajouter au panier"], content_hash="fedcba9876543210")

    for change in detect_changes(make_product(), old, new):
        for value in (change.old_value, change.new_value):
            assert not HASH_RE.match(value or "")


def test_button_description_is_bounded():
    many = [f"Bouton {index}" for index in range(10)]
    rendered = describe_buttons(many)
    assert rendered.count("«") == 3
    assert "(+7)" in rendered
    assert describe_buttons([]) == "aucun bouton"


def test_state_description_is_human_readable():
    described = describe_state(snap(price="189,99 €", buttons=["Ajouter au panier"]))
    assert "unavailable" in described and "189,99 €" in described


# --------------------------------------------------------------------- #
# Problèmes 2 et 7 — instabilité et confirmation                         #
# --------------------------------------------------------------------- #

class ScriptedMonitor(BaseMonitor):
    """Monitor rendant une suite d'analyses prédéfinie."""

    site_name = "scripted"
    display_name = "Scripted"

    def __init__(self, snapshots: list[ProductSnapshot]) -> None:
        super().__init__(httpx.AsyncClient())
        self.snapshots = snapshots
        self.calls = 0

    async def check(self, product):
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return self.snapshots[index]

    def parse(self, html, product):  # pragma: no cover — non utilisé
        raise NotImplementedError


async def build_engine(tmp_path, monitor, **settings):
    db = await make_db(tmp_path)
    registry = MonitorRegistry(httpx.AsyncClient())
    registry.register(type(monitor))
    registry._instances["scripted"] = monitor      # instance scriptée

    bus = EventBus()
    engine = MonitorEngine(
        registry, bus, SnapshotRepository(db.session_factory),
        GlobalSettings(confirmation_delay=0, **settings),
        product_provider=lambda: [],
        evidence_dir=tmp_path / "evidence",
    )
    return db, engine, bus


def collect(bus: EventBus, *types: EventType) -> list[Event]:
    captured: list[Event] = []

    async def handler(event: Event) -> None:
        captured.append(event)

    bus.subscribe(handler, set(types))
    return captured


@asyncio_test
async def test_a_confirmed_change_is_notified(tmp_path):
    stable = snap(availability=Availability.IN_STOCK, content_hash="aaaaaaaaaaaaaaaa")
    monitor = ScriptedMonitor([
        snap(content_hash="0000000000000000"),   # baseline : indisponible
        stable, stable,                          # changement, puis confirmation
    ])
    db, engine, bus = await build_engine(tmp_path, monitor)
    changes = collect(bus, EventType.CHANGE_DETECTED)

    product = make_product(site="scripted", uuid="u1")
    await engine._check_once(product)     # baseline
    await engine._check_once(product)     # changement + confirmation

    assert changes, "un changement confirmé doit être notifié"
    assert monitor.calls == 3             # 1 baseline + 1 lecture + 1 confirmation
    await db.dispose()


@asyncio_test
async def test_an_unstable_state_is_never_notified(tmp_path):
    """unknown → unavailable → unknown : aucune notification."""
    monitor = ScriptedMonitor([
        snap(content_hash="0000000000000000"),                    # baseline
        snap(availability=Availability.UNKNOWN,
             content_hash="1111111111111111"),                    # lecture 1
        snap(availability=Availability.UNAVAILABLE,
             content_hash="2222222222222222"),                    # confirmation ≠
    ])
    db, engine, bus = await build_engine(tmp_path, monitor)
    changes = collect(bus, EventType.CHANGE_DETECTED)
    unstable = collect(bus, EventType.CHECK_UNSTABLE)

    product = make_product(site="scripted", uuid="u1")
    await engine._check_once(product)
    await engine._check_once(product)

    assert changes == [], "aucune alerte sur un état instable"
    assert len(unstable) == 1
    assert "→" in unstable[0].payload["observed"]
    await db.dispose()


@asyncio_test
async def test_the_previous_state_is_kept_when_unstable(tmp_path):
    monitor = ScriptedMonitor([
        snap(availability=Availability.UNAVAILABLE, content_hash="aaaaaaaaaaaaaaaa"),
        snap(availability=Availability.IN_STOCK, content_hash="bbbbbbbbbbbbbbbb"),
        snap(availability=Availability.UNKNOWN, content_hash="cccccccccccccccc"),
    ])
    db, engine, _ = await build_engine(tmp_path, monitor)
    product = make_product(site="scripted", uuid="u1")

    await engine._check_once(product)
    result = await engine._check_once(product)

    assert result.availability is Availability.UNAVAILABLE   # état conservé
    stored = await engine._snapshots.load("u1")
    assert stored.availability is Availability.UNAVAILABLE
    await db.dispose()


@asyncio_test
async def test_confirmation_can_be_disabled(tmp_path):
    monitor = ScriptedMonitor([
        snap(content_hash="0000000000000000"),
        snap(availability=Availability.IN_STOCK, content_hash="1111111111111111"),
    ])
    db, engine, bus = await build_engine(tmp_path, monitor, confirm_changes=False)
    changes = collect(bus, EventType.CHANGE_DETECTED)

    product = make_product(site="scripted", uuid="u1")
    await engine._check_once(product)
    await engine._check_once(product)

    assert changes                    # notifié sans seconde lecture
    assert monitor.calls == 2
    await db.dispose()


@asyncio_test
async def test_baseline_is_never_confirmed(tmp_path):
    """Le premier passage n'entraîne aucune lecture supplémentaire."""
    monitor = ScriptedMonitor([snap(availability=Availability.IN_STOCK)])
    db, engine, _ = await build_engine(tmp_path, monitor)

    await engine._check_once(make_product(site="scripted", uuid="u1"))

    assert monitor.calls == 1
    await db.dispose()


# --------------------------------------------------------------------- #
# Problème 9 — preuve archivée                                           #
# --------------------------------------------------------------------- #

def test_evidence_is_stored_for_important_changes(tmp_path):
    from src.models import ChangeEvent

    product = make_product(site="amazon")
    change = ChangeEvent(
        product=product, change_type=ChangeType.BACK_IN_STOCK,
        old_value="unavailable", new_value="in_stock",
        snapshot=snap(availability=Availability.IN_STOCK),
    )
    relative = evidence.store(tmp_path, product, change, "<html>preuve</html>")

    assert relative and relative.endswith(".html")
    stored = evidence.resolve(tmp_path, relative)
    assert stored is not None
    content = stored.read_text(encoding="utf-8")
    assert "preuve" in content
    assert "back_in_stock" in content        # en-tête explicatif
    assert product.url in content


def test_routine_changes_are_not_archived(tmp_path):
    from src.models import ChangeEvent

    product = make_product()
    change = ChangeEvent(
        product=product, change_type=ChangeType.PAGE_CHANGED,
        old_value="a", new_value="b", snapshot=snap(),
    )
    assert evidence.store(tmp_path, product, change, "<html></html>") is None


def test_evidence_resolution_refuses_path_traversal(tmp_path):
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    root = tmp_path / "evidence"
    root.mkdir()
    assert evidence.resolve(root, "../secret.txt") is None


def test_snapshot_html_is_never_persisted():
    """Le HTML vit en mémoire le temps du cycle, jamais en base."""
    snapshot = snap(raw_html="<html>volumineux</html>")
    payload = snapshot.to_dict()

    assert "raw_html" not in payload
    assert ProductSnapshot.from_dict(payload).raw_html is None
