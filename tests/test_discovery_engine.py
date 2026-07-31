"""Discovery Engine : modes d'approbation, anti-doublon, import à chaud."""

import pytest

from src.core.events import Event, EventBus, EventType
from src.discovery.config import (
    ApprovalMode,
    DiscoverySettings,
    ImportDefaults,
    SiteDiscoveryConfig,
)
from src.discovery.contracts import DiscoveredProduct, DiscoveryContext, ScanResult
from src.discovery.engine import DiscoveryEngine
from src.discovery.loader import DiscoveryRegistry
from src.discovery.rules import RuleSet
from src.models import DiscoveryStatus, Priority
from src.repositories import DiscoveryRepository, ProductRepository
from tests.helpers import make_db

pytestmark = pytest.mark.asyncio


class FakePlugin:
    """Plugin scriptable : rend ce qu'on lui donne, ou lève."""

    site_name = "boutique"
    display_name = "Boutique de test"

    def __init__(self, products=None, complete: bool = True, boom: bool = False):
        self.products = list(products or [])
        self.complete = complete
        self.boom = boom
        self.scans = 0

    async def scan(self, ctx: DiscoveryContext) -> ScanResult:
        self.scans += 1
        if self.boom:
            raise RuntimeError("plugin cassé")
        return ScanResult(products=self.products, complete=self.complete,
                          sources_scanned=1)


def item(title: str, url: str = "https://boutique.test/p/x") -> DiscoveredProduct:
    return DiscoveredProduct(url=url, title=title, price="189,99 €",
                             image_url="https://boutique.test/img.png")


def settings(mode: ApprovalMode, **overrides) -> DiscoverySettings:
    base = dict(
        enabled=True, mode=mode, scan_interval=900, max_new_per_scan=50,
        rules=RuleSet.from_config({"include": ["pokemon"], "exclude": ["occasion"]}),
        defaults=ImportDefaults(check_interval=45, priority=Priority.CRITICAL,
                                tags=("auto-découvert",)),
        sites={"boutique": SiteDiscoveryConfig(enabled=True)},
    )
    base.update(overrides)
    return DiscoverySettings(**base)


async def build(tmp_path, plugin, mode=ApprovalMode.RULES, **overrides):
    db = await make_db(tmp_path)
    registry = DiscoveryRegistry()
    registry.register(plugin)
    bus = EventBus()
    engine = DiscoveryEngine(
        settings(mode, **overrides), registry,
        DiscoveryRepository(db.session_factory),
        ProductRepository(db.session_factory), bus,
        context_factory=lambda options: DiscoveryContext(client=None, options=options),
    )
    return db, engine, bus


def collect(bus: EventBus, event_type: EventType) -> list[Event]:
    captured: list[Event] = []

    async def handler(event: Event) -> None:
        captured.append(event)

    bus.subscribe(handler, {event_type})
    return captured


# --------------------------------------------------------------------- #
# Anti-doublon                                                           #
# --------------------------------------------------------------------- #

async def test_same_product_is_discovered_only_once(tmp_path):
    plugin = FakePlugin([item("Pokémon 30 Ans UPC")])
    db, engine, bus = await build(tmp_path, plugin)
    events = collect(bus, EventType.NEW_PRODUCT_DISCOVERED)

    first = await engine.scan_all()
    second = await engine.scan_all()

    assert first.new_products == 1
    assert second.new_products == 0      # déjà connue
    assert len(events) == 1
    await db.dispose()


async def test_tracking_parameters_do_not_create_duplicates(tmp_path):
    plugin = FakePlugin([item("Pokémon UPC", "https://boutique.test/p/upc")])
    db, engine, bus = await build(tmp_path, plugin)
    await engine.scan_all()

    plugin.products = [item("Pokémon UPC",
                            "https://www.boutique.test/p/upc?utm_source=mail")]
    report = await engine.scan_all()

    assert report.new_products == 0
    await db.dispose()


# --------------------------------------------------------------------- #
# Modes d'approbation                                                    #
# --------------------------------------------------------------------- #

async def test_auto_mode_imports_everything_not_excluded(tmp_path):
    plugin = FakePlugin([item("Manette sans fil")])  # ne matche aucune règle
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.AUTO)

    report = await engine.scan_all()
    assert report.imported == 1
    assert report.pending == 0
    await db.dispose()


async def test_review_mode_never_imports(tmp_path):
    plugin = FakePlugin([item("Pokémon 30 Ans UPC")])  # matche pourtant les règles
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.REVIEW)

    report = await engine.scan_all()
    assert report.imported == 0
    assert report.pending == 1
    await db.dispose()


async def test_rules_mode_imports_only_matching(tmp_path):
    plugin = FakePlugin([
        item("Pokémon 30 Ans UPC", "https://boutique.test/p/a"),
        item("Manette sans fil", "https://boutique.test/p/b"),
    ])
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.RULES)

    report = await engine.scan_all()
    assert report.imported == 1
    assert report.pending == 1
    await db.dispose()


async def test_exclusion_applies_in_every_mode(tmp_path):
    plugin = FakePlugin([item("Pokémon 30 Ans UPC Occasion")])
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.AUTO)

    report = await engine.scan_all()
    assert report.excluded == 1
    assert report.imported == 0
    await db.dispose()


# --------------------------------------------------------------------- #
# Import automatique                                                     #
# --------------------------------------------------------------------- #

async def test_import_creates_a_monitorable_product(tmp_path):
    plugin = FakePlugin([item("Pokémon 30 Ans UPC")])
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.AUTO)
    await engine.scan_all()

    products = await ProductRepository(db.session_factory).list_all()
    assert len(products) == 1
    created = products[0]
    assert created.uuid                       # identité immuable
    assert created.site == "boutique"
    assert created.check_interval == 45        # valeurs par défaut de la config
    assert created.priority is Priority.CRITICAL
    assert "auto-découvert" in created.tags
    assert created.is_monitorable              # surveillé dès le prochain cycle
    await db.dispose()


async def test_discovery_event_carries_everything_needed(tmp_path):
    plugin = FakePlugin([item("Pokémon 30 Ans UPC")])
    db, engine, bus = await build(tmp_path, plugin, mode=ApprovalMode.AUTO)
    events = collect(bus, EventType.NEW_PRODUCT_DISCOVERED)

    await engine.scan_all()
    payload = events[0].payload
    assert payload["imported"] is True
    assert payload["product_uuid"]
    assert payload["site_label"] == "Boutique de test"
    assert payload["discovery"].title == "Pokémon 30 Ans UPC"
    await db.dispose()


async def test_manual_approval_imports_a_pending_record(tmp_path):
    plugin = FakePlugin([item("Manette sans fil")])
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.REVIEW)
    await engine.scan_all()

    repo = DiscoveryRepository(db.session_factory)
    pending, _ = await repo.list_page(status=DiscoveryStatus.PENDING.value)
    uuid = await engine.import_product(pending[0])

    refreshed = await repo.get(pending[0].fingerprint)
    assert refreshed.status is DiscoveryStatus.IMPORTED
    assert refreshed.product_uuid == uuid
    await db.dispose()


# --------------------------------------------------------------------- #
# Décisions durables et fiches disparues                                 #
# --------------------------------------------------------------------- #

async def test_blocked_record_is_never_reimported(tmp_path):
    plugin = FakePlugin([item("Pokémon 30 Ans UPC")])
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.AUTO)
    repo = DiscoveryRepository(db.session_factory)

    await engine.scan_all()
    records, _ = await repo.list_page()
    await repo.set_status(records[0].fingerprint, DiscoveryStatus.BLOCKED, "test")

    report = await engine.scan_all()   # le site la propose toujours
    assert report.new_products == 0
    assert (await repo.get(records[0].fingerprint)).status is DiscoveryStatus.BLOCKED
    await db.dispose()


async def test_missing_products_are_marked_gone_after_complete_scan(tmp_path):
    plugin = FakePlugin([
        item("Pokémon A", "https://boutique.test/p/a"),
        item("Pokémon B", "https://boutique.test/p/b"),
    ])
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.REVIEW)
    await engine.scan_all()

    plugin.products = [item("Pokémon A", "https://boutique.test/p/a")]
    report = await engine.scan_all()

    assert report.gone == 1
    await db.dispose()


async def test_partial_scan_never_marks_products_gone(tmp_path):
    """Un scan incomplet (timeout, page en erreur) ne doit rien effacer."""
    plugin = FakePlugin([
        item("Pokémon A", "https://boutique.test/p/a"),
        item("Pokémon B", "https://boutique.test/p/b"),
    ])
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.REVIEW)
    await engine.scan_all()

    plugin.products = [item("Pokémon A", "https://boutique.test/p/a")]
    plugin.complete = False
    report = await engine.scan_all()

    assert report.gone == 0
    await db.dispose()


# --------------------------------------------------------------------- #
# Robustesse                                                             #
# --------------------------------------------------------------------- #

async def test_failing_plugin_is_isolated(tmp_path):
    broken = FakePlugin(boom=True)
    db, engine, _ = await build(tmp_path, broken, mode=ApprovalMode.AUTO)

    report = await engine.scan_all()   # ne doit pas lever
    assert report.errors
    assert report.sites_scanned == 0
    await db.dispose()


async def test_disabled_site_is_skipped(tmp_path):
    plugin = FakePlugin([item("Pokémon 30 Ans UPC")])
    db, engine, _ = await build(
        tmp_path, plugin, mode=ApprovalMode.AUTO,
        sites={"boutique": SiteDiscoveryConfig(enabled=False)},
    )
    report = await engine.scan_all()
    assert plugin.scans == 0
    assert report.products_seen == 0
    await db.dispose()


async def test_max_new_per_scan_is_enforced(tmp_path):
    plugin = FakePlugin([
        item(f"Pokémon {index}", f"https://boutique.test/p/{index}")
        for index in range(10)
    ])
    db, engine, _ = await build(tmp_path, plugin, mode=ApprovalMode.AUTO,
                                max_new_per_scan=3)
    report = await engine.scan_all()
    assert report.new_products == 3
    await db.dispose()


async def test_engine_disabled_when_no_plugin(tmp_path):
    db = await make_db(tmp_path)
    engine = DiscoveryEngine(
        settings(ApprovalMode.AUTO), DiscoveryRegistry(),
        DiscoveryRepository(db.session_factory),
        ProductRepository(db.session_factory), EventBus(),
        context_factory=lambda options: DiscoveryContext(client=None, options=options),
    )
    assert not engine.enabled
    await engine.run()   # doit rendre la main immédiatement
    await db.dispose()
