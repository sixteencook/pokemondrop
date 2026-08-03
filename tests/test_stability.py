"""Stabilisation : anti-instabilité, confirmation, hashes invisibles, preuve.

Objectif de la phase : mieux vaut manquer une alerte qu'en produire une
fausse. Ces tests vérifient chacun des garde-fous.
"""

import re

import httpx
import pytest

from src.core import evidence
from src.core.detector import describe_state, detect_changes
from src.core.engine import MonitorEngine
from src.core.events import Event, EventBus, EventType
from src.models import (
    RETIRED_CHANGE_TYPES,
    Availability,
    ChangeType,
    GlobalSettings,
    OfferState,
    ProductSnapshot,
    PurchaseAction,
)
from src.monitors import MonitorRegistry
from src.monitors.base import BaseMonitor
from src.repositories import SnapshotRepository
from tests.helpers import make_db, make_product

asyncio_test = pytest.mark.asyncio

#: Un hash tel que produit par le projet : 16 caractères hexadécimaux.
HASH_RE = re.compile(r"^[0-9a-f]{16}$")


def offer(action: PurchaseAction = PurchaseAction.CURRENTLY_UNAVAILABLE,
          **kwargs) -> OfferState:
    return OfferState(action=action, native_state=action.value, **kwargs)


def snap(**kwargs) -> ProductSnapshot:
    """Snapshot cohérent : la disponibilité découle toujours de l'action."""
    state = kwargs.pop("offer", None) or offer(
        kwargs.pop("action", PurchaseAction.CURRENTLY_UNAVAILABLE),
        price=kwargs.get("price"),
    )
    defaults = dict(
        page_exists=True,
        availability=state.availability,
        offer=state,
        content_hash=state.business_hash(),
    )
    defaults.update(kwargs)
    return ProductSnapshot(**defaults)


# --------------------------------------------------------------------- #
# Le moteur ne surveille plus le HTML                                    #
# --------------------------------------------------------------------- #

def test_retired_change_types_are_never_emitted():
    """« Bouton modifié » et « Page modifiée » ont disparu des émissions."""
    old = snap(buttons=["Demande d'invitation"], content_hash="aaaaaaaaaaaaaaaa")
    new = snap(buttons=["Ajouter au panier"], content_hash="bbbbbbbbbbbbbbbb")

    events = detect_changes(make_product(), old, new)

    assert events == [], "un libellé de bouton n'est pas un événement métier"
    assert RETIRED_CHANGE_TYPES == {
        ChangeType.BUTTON_CHANGED, ChangeType.PAGE_CHANGED,
    }


def test_a_hash_difference_alone_produces_nothing():
    """Le hash ne déclenche plus rien : seul l'état métier compte."""
    old = snap(price="189,99 €", content_hash="aaaaaaaaaaaaaaaa")
    new = snap(price="189,99 €", content_hash="bbbbbbbbbbbbbbbb")

    assert detect_changes(make_product(), old, new) == []


def test_no_change_type_ever_carries_a_hash():
    """Balayage complet : aucun événement ne doit transporter un hash."""
    old = snap(action=PurchaseAction.CURRENTLY_UNAVAILABLE, price="10,00 €")
    new = snap(action=PurchaseAction.ADD_TO_CART, price="12,00 €")

    changes = detect_changes(make_product(), old, new)
    assert changes
    for change in changes:
        for value in (change.old_value, change.new_value):
            assert not HASH_RE.match(value or "")


def test_state_description_is_human_readable():
    described = describe_state(snap(action=PurchaseAction.ADD_TO_CART,
                                    price="189,99 €"))
    assert "Ajouter au panier" in described and "189,99 €" in described


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
    stable = snap(action=PurchaseAction.ADD_TO_CART)
    monitor = ScriptedMonitor([
        snap(),                                  # baseline : indisponible
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
async def test_two_contradictory_readings_are_never_notified(tmp_path):
    """La seconde lecture ne confirme pas : rien ne part, l'état est conservé."""
    monitor = ScriptedMonitor([
        snap(),                                       # baseline : indisponible
        snap(action=PurchaseAction.ADD_TO_CART),      # lecture 1 : en stock
        snap(action=PurchaseAction.PREORDER),         # confirmation : autre chose
    ])
    db, engine, bus = await build_engine(tmp_path, monitor)
    changes = collect(bus, EventType.CHANGE_DETECTED)
    unstable = collect(bus, EventType.CHECK_UNSTABLE)

    product = make_product(site="scripted", uuid="u1")
    await engine._check_once(product)
    await engine._check_once(product)

    assert changes == [], "aucune alerte sur deux lectures contradictoires"
    assert len(unstable) == 1
    stored = await engine._snapshots.load("u1")
    assert stored.availability is Availability.UNAVAILABLE   # état conservé
    await db.dispose()


@asyncio_test
async def test_an_inconclusive_reading_keeps_the_business_memory(tmp_path):
    """C'est la règle qui supprime l'oscillation invitation → inconnu.

    Une lecture qui ne conclut rien ne produit aucun événement, ne
    déclenche aucune seconde lecture, et n'écrase pas l'état mémorisé.
    """
    monitor = ScriptedMonitor([
        snap(action=PurchaseAction.REQUEST_INVITE),
        snap(action=PurchaseAction.NONE),        # page illisible
        snap(action=PurchaseAction.NONE),
    ])
    db, engine, bus = await build_engine(tmp_path, monitor)
    changes = collect(bus, EventType.CHANGE_DETECTED)
    product = make_product(site="scripted", uuid="u1")

    await engine._check_once(product)             # baseline : invitation
    result = await engine._check_once(product)    # lecture non concluante

    assert changes == []
    assert monitor.calls == 2, "aucune confirmation à demander"
    assert result.availability is Availability.PREORDER
    stored = await engine._snapshots.load("u1")
    assert stored.availability is Availability.PREORDER
    assert stored.offer.action is PurchaseAction.REQUEST_INVITE
    await db.dispose()


@asyncio_test
async def test_recovering_from_an_inconclusive_reading_is_silent(tmp_path):
    """invitation → inconnu → invitation ne doit produire AUCUNE alerte."""
    invitation = snap(action=PurchaseAction.REQUEST_INVITE)
    monitor = ScriptedMonitor([
        invitation,
        snap(action=PurchaseAction.NONE),
        invitation, invitation,
    ])
    db, engine, bus = await build_engine(tmp_path, monitor)
    changes = collect(bus, EventType.CHANGE_DETECTED)
    product = make_product(site="scripted", uuid="u1")

    for _ in range(3):
        await engine._check_once(product)

    assert changes == [], "l'oscillation ne doit produire aucune alerte"
    await db.dispose()


@asyncio_test
async def test_confirmation_can_be_disabled(tmp_path):
    monitor = ScriptedMonitor([
        snap(),
        snap(action=PurchaseAction.ADD_TO_CART),
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
    monitor = ScriptedMonitor([snap(action=PurchaseAction.ADD_TO_CART)])
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
        snapshot=snap(action=PurchaseAction.ADD_TO_CART),
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
