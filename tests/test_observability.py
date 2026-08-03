"""Observabilité : diagnostics de vérification, Health Score, anomalies, API.

Ce que ces tests protègent avant tout : le coût. La page Santé ne doit
rien produire de nouveau pendant le cycle de surveillance — elle relit ce
que le cycle écrit déjà.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from src.core.events import Event, EventBus, EventType
from src.db.schema import CheckRow, EngineEventRow
from src.models import (
    Availability,
    CheckDiagnostics,
    EventKind,
    EventScope,
    FetchSource,
    ProductSnapshot,
    PurchaseAction,
)
from src.models.offer import OfferState
from src.monitors.generic import GenericHtmlMonitor
from src.repositories import (
    AlertRepository,
    CheckRepository,
    EngineEventRepository,
    TimelineRepository,
)
from src.services.health import (
    MIN_CHECKS_FOR_SCORE,
    SCORE_WEIGHTS,
    _compare_windows,
    compute_score,
)
from src.services.recorder import EventRecorder
from tests.helpers import make_db, make_product
from tests.test_api import make_settings

asyncio_test = pytest.mark.asyncio

FILLER = "<p>" + "Description détaillée du produit. " * 25 + "</p>"


# ===================================================================== #
# Le Health Score : une formule, pas une impression                      #
# ===================================================================== #

def test_the_score_weights_sum_to_one_hundred():
    """Un plugin qui cumule tous les problèmes doit pouvoir tomber à zéro."""
    assert sum(weight for _, weight, _ in SCORE_WEIGHTS) == 100


def test_a_perfect_plugin_scores_one_hundred():
    result = compute_score({
        "checks": 1000, "errors": 0, "unknown_states": 0, "browser_checks": 0,
        "http_403": 0, "http_429": 0, "blocked": 0,
        "avg_response_ms": 400, "avg_confidence": 95,
    })
    assert result.score == 100
    assert result.status == "healthy"


def test_a_plugin_with_too_few_checks_is_not_judged():
    """Trois vérifications ne font pas une tendance."""
    result = compute_score({"checks": MIN_CHECKS_FOR_SCORE - 1, "errors": 3})
    assert result.status == "observation"
    assert result.score == 100


def test_errors_weigh_more_than_slowness():
    """La hiérarchie de gravité est assumée et doit rester vérifiable."""
    erratic = compute_score({"checks": 100, "errors": 10, "avg_response_ms": 400})
    slow = compute_score({"checks": 100, "errors": 0, "avg_response_ms": 8000})
    assert erratic.score < slow.score


def test_the_score_is_a_rate_not_a_count():
    """10 erreurs sur 10 000 vérifications n'est pas 10 erreurs sur 100."""
    small = compute_score({"checks": 100, "errors": 10})
    large = compute_score({"checks": 10_000, "errors": 10})
    assert large.score > small.score
    assert large.score == 100


def test_a_blocked_plugin_is_reported_unhealthy():
    result = compute_score({
        "checks": 200, "errors": 40, "http_403": 60, "blocked": 20,
        "unknown_states": 80, "browser_checks": 120, "avg_response_ms": 9000,
        "avg_confidence": 20,
    })
    assert result.score <= 5
    assert result.status == "unhealthy"
    # Chaque poste doit avoir contribué : un score effondré doit être
    # explicable poste par poste, pas d'un seul bloc.
    assert all(penalty > 0 for penalty in result.penalties.values())


def test_the_main_issue_names_the_heaviest_penalty():
    result = compute_score({"checks": 200, "errors": 40, "avg_response_ms": 500})
    assert result.main_issue == "erreurs"


# ===================================================================== #
# Auto-diagnostic : la dérive, pas la valeur absolue                     #
# ===================================================================== #

def test_a_stable_plugin_raises_no_anomaly():
    """Un plugin qui a TOUJOURS utilisé le navigateur n'est pas une anomalie."""
    window = {"checks": 100, "browser_checks": 100, "unknown_states": 0,
              "http_403": 0, "avg_response_ms": 1000}
    assert _compare_windows("amazon", window, dict(window)) == []


def test_a_sudden_rise_in_browser_usage_is_reported():
    recent = {"checks": 100, "browser_checks": 60, "unknown_states": 0,
              "http_403": 0, "avg_response_ms": 1000}
    baseline = {"checks": 700, "browser_checks": 70, "unknown_states": 0,
                "http_403": 0, "avg_response_ms": 1000}

    anomalies = _compare_windows("amazon", recent, baseline)
    assert any("navigateur" in anomaly.title for anomaly in anomalies)


def test_a_tiny_relative_rise_is_not_an_anomaly():
    """Passer de 0,5 % à 1,5 % ne mérite pas d'alerte."""
    recent = {"checks": 1000, "browser_checks": 15, "unknown_states": 0,
              "http_403": 0, "avg_response_ms": 1000}
    baseline = {"checks": 1000, "browser_checks": 5, "unknown_states": 0,
                "http_403": 0, "avg_response_ms": 1000}
    assert _compare_windows("amazon", recent, baseline) == []


def test_many_403_are_reported_with_an_actionable_explanation():
    recent = {"checks": 100, "browser_checks": 0, "unknown_states": 0,
              "http_403": 40, "avg_response_ms": 1000}
    baseline = {"checks": 700, "browser_checks": 0, "unknown_states": 0,
                "http_403": 7, "avg_response_ms": 1000}

    anomalies = _compare_windows("micromania", recent, baseline)
    forbidden = next(a for a in anomalies if "403" in a.title)
    assert "Micromania" in forbidden.title
    assert "adresse IP" in forbidden.detail


def test_a_slowdown_is_reported():
    recent = {"checks": 100, "browser_checks": 0, "unknown_states": 0,
              "http_403": 0, "avg_response_ms": 4000}
    baseline = {"checks": 700, "browser_checks": 0, "unknown_states": 0,
                "http_403": 0, "avg_response_ms": 1000}

    anomalies = _compare_windows("fnac", recent, baseline)
    assert any("plus lent" in anomaly.title for anomaly in anomalies)


def test_an_undersampled_window_is_never_judged():
    recent = {"checks": 3, "browser_checks": 3, "unknown_states": 3,
              "http_403": 3, "avg_response_ms": 9000}
    baseline = {"checks": 700, "browser_checks": 0, "unknown_states": 0,
                "http_403": 0, "avg_response_ms": 500}
    assert _compare_windows("cultura", recent, baseline) == []


# ===================================================================== #
# Les diagnostics remontent bien du monitor                              #
# ===================================================================== #

class _Renderer:
    def __init__(self, html: str) -> None:
        self._html = html
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    async def render(self, url, cookie_selectors=(), *, cookies=None,
                     locale=None, timezone=None) -> str:
        self.calls += 1
        return self._html


def _client(status: int, body: str = "") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body,
                              headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


PAGE = (
    "<html lang='fr'><head><title>Fiche</title></head><body>"
    "<main class='product-detail'><h1>Produit</h1>"
    "<span class='price'>189,99 €</span><button>Précommander</button>"
    f"{FILLER}</main></body></html>"
)


@asyncio_test
async def test_a_plain_http_check_reports_its_source():
    monitor = GenericHtmlMonitor(_client(200, PAGE))
    snapshot = await monitor.check(make_product(url="https://example.com/p"))

    assert snapshot.diagnostics.fetch_source is FetchSource.HTTP
    assert snapshot.diagnostics.http_status == 200
    assert not snapshot.diagnostics.browser_fallback


@asyncio_test
async def test_a_403_followed_by_a_render_keeps_the_refusal():
    """Sans cela, un 403 suivi d'un rendu Chromium se lirait comme un 200.

    C'est le signal le plus important pour repérer un site qui commence à
    bloquer : il ne doit jamais disparaître.
    """
    monitor = GenericHtmlMonitor(_client(403, "<html>refusé</html>"), _Renderer(PAGE))
    snapshot = await monitor.check(make_product(url="https://example.com/p"))

    assert snapshot.availability is Availability.PREORDER
    assert snapshot.diagnostics.http_status == 403
    assert snapshot.diagnostics.browser_fallback
    assert snapshot.diagnostics.fetch_source is FetchSource.BROWSER


@asyncio_test
async def test_an_inconclusive_page_then_a_render_is_flagged_as_fallback():
    shell = ("<html lang='fr'><head><title>x</title></head><body>"
             f"<div id='root'></div>{FILLER}</body></html>")
    monitor = GenericHtmlMonitor(_client(200, shell), _Renderer(PAGE))
    snapshot = await monitor.check(make_product(url="https://example.com/p"))

    assert snapshot.diagnostics.browser_fallback
    assert snapshot.diagnostics.fetch_source is FetchSource.BROWSER


@asyncio_test
async def test_a_missing_page_is_not_an_unknown_state():
    monitor = GenericHtmlMonitor(_client(404))
    snapshot = await monitor.check(make_product(url="https://example.com/p"))

    assert snapshot.availability is Availability.NOT_LISTED
    assert snapshot.conclusive
    assert snapshot.diagnostics.http_status == 404


def test_diagnostics_survive_a_round_trip():
    diagnostics = CheckDiagnostics(
        fetch_source=FetchSource.BROWSER, http_status=403,
        browser_fallback=True, confidence=72, blocked_reason="captcha",
        blocked=True,
    )
    snapshot = ProductSnapshot(page_exists=True, diagnostics=diagnostics)
    restored = ProductSnapshot.from_dict(snapshot.to_dict())

    assert restored.diagnostics == diagnostics


def test_diagnostics_never_reach_the_business_hash():
    """Un changement de voie de récupération n'est pas un changement d'offre."""
    offer = OfferState(action=PurchaseAction.ADD_TO_CART, price="10,00 €")
    http = ProductSnapshot(offer=offer, diagnostics=CheckDiagnostics(
        fetch_source=FetchSource.HTTP))
    browser = ProductSnapshot(offer=offer, diagnostics=CheckDiagnostics(
        fetch_source=FetchSource.BROWSER, browser_fallback=True))

    assert http.offer.business_hash() == browser.offer.business_hash()


# ===================================================================== #
# Persistance : le cycle nominal n'écrit aucun incident                  #
# ===================================================================== #

async def _recorder_env(tmp_path):
    db = await make_db(tmp_path)
    checks = CheckRepository(db.session_factory)
    events = EngineEventRepository(db.session_factory)
    recorder = EventRecorder(
        checks, TimelineRepository(db.session_factory),
        AlertRepository(db.session_factory), events,
    )
    bus = EventBus()
    recorder.attach_to(bus)
    return db, checks, events, bus


def _snapshot(**diagnostics) -> ProductSnapshot:
    offer = OfferState(action=PurchaseAction.ADD_TO_CART, price="10,00 €")
    return ProductSnapshot(
        availability=offer.availability, page_exists=True, offer=offer,
        diagnostics=CheckDiagnostics(**diagnostics),
    )


@asyncio_test
async def test_a_nominal_check_writes_no_incident(tmp_path):
    """La table d'incidents doit rester vide quand tout va bien."""
    db, checks, events, bus = await _recorder_env(tmp_path)
    product = make_product(uuid="u1", site="micromania")
    snapshot = _snapshot(fetch_source=FetchSource.HTTP, http_status=200,
                         confidence=95)

    await bus.publish(Event(EventType.CHECK_COMPLETED, {
        "product": product, "snapshot": snapshot, "observed": snapshot,
        "response_time_ms": 420,
    }))

    assert await events.recent() == []
    recorded = await checks.recent("u1")
    assert recorded[0].response_time_ms == 420
    await db.dispose()


@asyncio_test
async def test_the_check_row_carries_the_diagnostics(tmp_path):
    db, _, _, bus = await _recorder_env(tmp_path)
    product = make_product(uuid="u1", site="amazon")
    snapshot = _snapshot(fetch_source=FetchSource.BROWSER, http_status=403,
                         browser_fallback=True, confidence=72)

    await bus.publish(Event(EventType.CHECK_COMPLETED, {
        "product": product, "snapshot": snapshot, "observed": snapshot,
        "response_time_ms": 900,
    }))

    async with db.session_factory() as session:
        row = (await session.execute(CheckRow.__table__.select())).first()
    assert row.fetch_source == "browser"
    assert row.http_status == 403
    assert row.confidence == 72
    await db.dispose()


@asyncio_test
async def test_incidents_are_recorded_with_their_reason(tmp_path):
    db, _, events, bus = await _recorder_env(tmp_path)
    product = make_product(uuid="u1", site="amazon")
    blocked = ProductSnapshot(
        availability=Availability.UNKNOWN, page_exists=True,
        diagnostics=CheckDiagnostics(
            fetch_source=FetchSource.BROWSER, http_status=403,
            browser_fallback=True, blocked=True, blocked_reason="captcha",
        ),
    )

    await bus.publish(Event(EventType.CHECK_COMPLETED, {
        "product": product, "snapshot": blocked, "observed": blocked,
        "response_time_ms": 1500,
    }))

    kinds = {event.kind for event in await events.recent()}
    assert EventKind.BLOCKED.value in kinds
    assert EventKind.BROWSER_FALLBACK.value in kinds
    assert EventKind.HTTP_ERROR.value in kinds
    await db.dispose()


@asyncio_test
async def test_an_inconclusive_read_is_counted_even_though_it_is_hidden(tmp_path):
    """Le dashboard garde l'état précédent, la santé voit la vérité."""
    db, checks, events, bus = await _recorder_env(tmp_path)
    product = make_product(uuid="u1", site="amazon")
    kept = _snapshot(fetch_source=FetchSource.HTTP, http_status=200)
    observed = ProductSnapshot(
        availability=Availability.UNKNOWN, page_exists=True,
        diagnostics=CheckDiagnostics(fetch_source=FetchSource.HTTP,
                                     http_status=200),
    )

    await bus.publish(Event(EventType.CHECK_COMPLETED, {
        "product": product, "snapshot": kept, "observed": observed,
        "response_time_ms": 300,
    }))

    recorded = await checks.recent("u1")
    assert recorded[0].availability == "unknown"
    assert EventKind.UNKNOWN_STATE.value in {
        event.kind for event in await events.recent()
    }
    await db.dispose()


@asyncio_test
async def test_site_aggregation_groups_in_one_query(tmp_path):
    db = await make_db(tmp_path)
    checks = CheckRepository(db.session_factory)

    from src.db.schema import ProductRow
    async with db.session_factory() as session:
        session.add(ProductRow(uuid="u1", name="A", site="amazon", enabled=True))
        session.add(ProductRow(uuid="u2", name="M", site="micromania", enabled=True))
        await session.commit()

    for _ in range(8):
        await checks.add("u1", "ok", availability="in_stock",
                         response_time_ms=1000, fetch_source="http",
                         http_status=200, confidence=90)
    await checks.add("u1", "ok", availability="unknown", response_time_ms=3000,
                     fetch_source="browser", http_status=403, confidence=30)
    await checks.add("u2", "error", error="timeout")

    health = await checks.health_by_site(hours=24)

    assert health["amazon"]["checks"] == 9
    assert health["amazon"]["unknown_states"] == 1
    assert health["amazon"]["browser_checks"] == 1
    assert health["amazon"]["http_403"] == 1
    assert health["micromania"]["errors"] == 1
    await db.dispose()


@asyncio_test
async def test_the_incident_history_is_ordered_and_labelled(tmp_path):
    db = await make_db(tmp_path)
    events = EngineEventRepository(db.session_factory)

    await events.add(EventScope.PLUGIN, "amazon", EventKind.BROWSER_FALLBACK)
    await events.add(EventScope.PLUGIN, "micromania", EventKind.HTTP_ERROR,
                     detail="HTTP 403")

    history = await events.recent(limit=10)
    assert [event.source for event in history] == ["micromania", "amazon"]
    assert history[0].label == "Erreur HTTP"
    assert history[0].severity == "error"
    await db.dispose()


@asyncio_test
async def test_old_incidents_can_be_purged(tmp_path):
    db = await make_db(tmp_path)
    events = EngineEventRepository(db.session_factory)
    await events.add(EventScope.PLUGIN, "amazon", EventKind.BLOCKED)

    async with db.session_factory() as session:
        row = (await session.execute(EngineEventRow.__table__.select())).first()
        await session.execute(
            EngineEventRow.__table__.update()
            .where(EngineEventRow.id == row.id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(days=40))
        )
        await session.commit()

    assert await events.purge_older_than(30) == 1
    assert await events.recent() == []
    await db.dispose()


# ===================================================================== #
# Incidents : l'enchaînement raconte, l'événement isolé n'apprend rien   #
# ===================================================================== #

def test_a_chain_rescued_by_the_browser_is_labelled_as_such():
    from src.services.health import _chain_outcome

    steps = [
        {"label": "Erreur HTTP"},
        {"label": "Bascule sur le navigateur"},
    ]
    assert _chain_outcome(steps) == "rattrapé par le navigateur"


def test_a_chain_that_stays_unknown_is_reported_unresolved():
    from src.services.health import _chain_outcome

    steps = [
        {"label": "Erreur HTTP"},
        {"label": "Bascule sur le navigateur"},
        {"label": "État indéterminé"},
    ]
    assert _chain_outcome(steps) == "non résolu"


def test_events_of_the_same_cycle_are_grouped():
    """La corrélation se fait par proximité temporelle : quelques ms."""
    from src.services.health import _cycle_key

    base = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    assert _cycle_key(base) == _cycle_key(base + timedelta(seconds=5))
    assert _cycle_key(base) != _cycle_key(base + timedelta(minutes=5))


@asyncio_test
async def test_phase_durations_are_averaged_by_kind(tmp_path):
    db = await make_db(tmp_path)
    events = EngineEventRepository(db.session_factory)

    await events.add(EventScope.ENGINE, "amazon", EventKind.SCREENSHOT,
                     duration_ms=1000)
    await events.add(EventScope.ENGINE, "amazon", EventKind.SCREENSHOT,
                     duration_ms=3000)
    await events.add(EventScope.DISCOVERY, "discovery", EventKind.DISCOVERY_SCAN,
                     duration_ms=8000)
    # Un incident sans durée ne doit pas fausser les moyennes.
    await events.add(EventScope.PLUGIN, "amazon", EventKind.BLOCKED)

    durations = await events.average_durations()
    assert durations[EventKind.SCREENSHOT.value] == 2000
    assert durations[EventKind.DISCOVERY_SCAN.value] == 8000
    assert EventKind.BLOCKED.value not in durations
    await db.dispose()


@asyncio_test
async def test_http_and_browser_timings_come_from_the_check_rows(tmp_path):
    """Aucune mesure dédiée : les deux se lisent déjà dans `checks`."""
    db = await make_db(tmp_path)
    checks = CheckRepository(db.session_factory)

    await checks.add("u1", "ok", response_time_ms=200, fetch_source="http")
    await checks.add("u1", "ok", response_time_ms=400, fetch_source="http")
    await checks.add("u1", "ok", response_time_ms=3000, fetch_source="browser")

    http, browser = await checks.avg_by_fetch_source()
    assert http == 300
    assert browser == 3000
    await db.dispose()


# ===================================================================== #
# Migration : une base déjà en service doit survivre                     #
# ===================================================================== #

@asyncio_test
async def test_an_existing_database_gains_the_observability_schema(tmp_path):
    """v3 → v4 sur une base peuplée : rien ne doit être perdu.

    `create_all` n'ajoute jamais de colonne à une table existante : sans
    les ALTER de la migration 4, la base en service resterait muette sur
    la voie de récupération, le statut HTTP et la confiance.
    """
    import sqlite3

    from src.db import Database
    from src.db.migrations import MIGRATIONS

    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY,
                                     applied_at DATETIME);
        CREATE TABLE checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_uuid VARCHAR(32), status VARCHAR(10),
            availability VARCHAR(20), response_time_ms INTEGER,
            error TEXT, checked_at DATETIME NOT NULL
        );
        INSERT INTO schema_version VALUES (1, '2026-01-01'), (2, '2026-01-01'),
                                          (3, '2026-01-01');
        INSERT INTO checks (product_uuid, status, availability, checked_at)
        VALUES ('u1', 'ok', 'in_stock', '2026-08-01T10:00:00');
        """
    )
    legacy.commit()
    legacy.close()

    db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    await db.init()
    await db.dispose()

    check = sqlite3.connect(path)
    columns = {row[1] for row in check.execute("pragma table_info(checks)")}
    tables = {
        row[0] for row in
        check.execute("select name from sqlite_master where type='table'")
    }
    version = check.execute("select max(version) from schema_version").fetchone()[0]
    preserved = check.execute("select count(*) from checks").fetchone()[0]
    check.close()

    assert {"fetch_source", "http_status", "confidence"} <= columns
    assert "engine_events" in tables
    assert version == max(MIGRATIONS)
    assert preserved == 1, "les vérifications existantes doivent être conservées"


def test_a_snapshot_written_before_observability_still_loads():
    """Un état mémorisé par une version antérieure ne bloque pas le démarrage."""
    legacy = {
        "availability": "preorder", "price": "189,99 €", "buttons": [],
        "status_text": "précommande", "page_exists": True,
        "content_hash": "abc", "checked_at": "2026-08-01T10:00:00",
        "details": {},
    }
    restored = ProductSnapshot.from_dict(legacy)

    assert restored.availability is Availability.PREORDER
    assert restored.offer is None
    assert restored.diagnostics == CheckDiagnostics()
    assert restored.conclusive


# ===================================================================== #
# L'API                                                                  #
# ===================================================================== #

@pytest.fixture()
def authed(tmp_path):
    from src.web.app import create_app

    app = create_app(settings=make_settings(tmp_path), config_path=None,
                     run_engine=False)
    with TestClient(app) as client:
        client.post("/api/v1/auth/login",
                    json={"username": "rayan", "password": "s3cret!"})
        yield client


def test_the_public_healthcheck_stays_public_and_minimal(authed):
    """Railway sonde cette route : elle ne doit ni grossir, ni se fermer."""
    response = authed.get("/api/v1/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "version", "uptime_seconds"}


def test_overview_answers_the_global_questions(authed):
    payload = authed.get("/api/v1/health/overview").json()

    for key in ("plugins_active", "products_watched", "offers_total",
                "canonical_products", "discoveries_today", "alerts_today",
                "errors_today", "checks_today", "avg_response_by_plugin"):
        assert key in payload
    assert payload["plugins_active"] >= 2      # amazon + micromania


def test_every_loaded_plugin_gets_a_card(authed):
    cards = authed.get("/api/v1/plugins/health").json()
    sites = {card["site"] for card in cards}

    assert {"amazon", "micromania"} <= sites
    for card in cards:
        assert 0 <= card["score"] <= 100
        assert card["status"] in (
            "healthy", "degraded", "unhealthy", "observation"
        )


def test_diagnostics_returns_every_section(authed):
    payload = authed.get("/api/v1/diagnostics").json()

    assert set(payload) == {
        "overview", "plugins", "discovery", "intelligence", "anomalies",
        "history", "charts", "timings", "incidents", "system",
    }
    assert set(payload["charts"]) == {
        "checks_per_hour", "incidents_per_hour", "confidence_per_hour",
        "alerts_per_day", "discoveries_per_day",
    }


def test_discovery_section_reports_cross_site_searches(authed):
    """Les compteurs de recherche viennent de `search_attempts`, pas d'un calcul."""
    payload = authed.get("/api/v1/diagnostics").json()["discovery"]

    for key in ("searches_total", "searches_found", "searches_empty",
                "searches_retrying"):
        assert key in payload


def test_product_health_is_404_for_an_unknown_product(authed):
    assert authed.get("/api/v1/products/inconnu/health").status_code == 404


def test_product_health_answers_for_a_real_product(authed):
    created = authed.post("/api/v1/products", json={
        "name": "Pokémon Partenaires Série 3", "site": "amazon",
        "url": "https://www.amazon.fr/dp/B0H3PRH89L",
        "check_interval": 60, "enabled": False, "priority": "high", "tags": [],
    }).json()

    payload = authed.get(f"/api/v1/products/{created['uuid']}/health").json()
    assert payload["name"] == "Pokémon Partenaires Série 3"
    assert payload["site"] == "amazon"
    assert payload["checks_total"] == 0
    assert payload["recent_events"] == []


def test_the_system_score_aggregates_every_component(authed):
    system = authed.get("/api/v1/diagnostics").json()["system"]

    keys = {component["key"] for component in system["components"]}
    assert {"amazon", "micromania", "discovery", "intelligence"} <= keys
    assert 0 <= system["score"] <= 100
    # Les plugins pèsent plus lourd : c'est là que se joue la valeur.
    weights = {c["key"]: c["weight"] for c in system["components"]}
    assert weights["amazon"] > weights["discovery"]


def test_phase_timings_are_exposed(authed):
    timings = authed.get("/api/v1/diagnostics").json()["timings"]

    assert set(timings) == {
        "http_ms", "browser_ms", "screenshot_ms", "discovery_scan_ms",
        "intelligence_ms",
    }


def test_product_story_is_404_for_an_unknown_canonical_product(authed):
    assert authed.get("/api/v1/catalog/products/inconnu/story").status_code == 404


@asyncio_test
async def test_a_product_tells_its_own_story(tmp_path):
    """Timeline fusionnée, propagation et métriques d'un produit canonique.

    Le scénario reproduit un drop réel : Amazon publie sa fiche en premier,
    Micromania deux jours plus tard, et chacun vit ses propres événements.
    """
    from src.db.schema import ProductRow
    from src.intelligence.entities import ProductDraft
    from src.models import ChangeType
    from src.repositories import CatalogRepository, OfferRepository
    from src.services.product_story import ProductStoryService

    db = await make_db(tmp_path)
    catalog = CatalogRepository(db.session_factory)
    offers = OfferRepository(db.session_factory)
    timeline = TimelineRepository(db.session_factory)
    events = EngineEventRepository(db.session_factory)

    async with db.session_factory() as session:
        session.add(ProductRow(uuid="m-amazon", name="P", site="amazon",
                               enabled=True))
        session.add(ProductRow(uuid="m-micro", name="P", site="micromania",
                               enabled=True))
        await session.commit()

    product = await catalog.create(ProductDraft(name="Pokémon Partenaires 3"))
    await offers.upsert(product_uuid=product.uuid, site="amazon",
                        url="https://amazon.fr/dp/X", canonical_url="a",
                        monitored_uuid="m-amazon")
    await offers.upsert(product_uuid=product.uuid, site="micromania",
                        url="https://micromania.fr/p/x", canonical_url="m",
                        monitored_uuid="m-micro")

    await timeline.add("m-amazon", ChangeType.INVITATION_OPENED.value,
                       "Invitation ouverte", new_value="preorder")
    await timeline.add("m-micro", ChangeType.PREORDER_OPENED.value,
                       "Précommande ouverte", new_value="preorder")
    await timeline.add("m-amazon", ChangeType.BACK_IN_STOCK.value,
                       "Retour en stock", new_value="in_stock")

    story = await ProductStoryService(
        catalog, offers, timeline, AlertRepository(db.session_factory),
        None, events,
    ).story(product.uuid)

    assert story["name"] == "Pokémon Partenaires 3"

    # La timeline mélange les deux marchands, dans l'ordre chronologique.
    labels = [entry["label"] for entry in story["timeline"]]
    assert "Nouvelle fiche" in labels
    assert "Invitation ouverte" in labels
    assert "Précommande ouverte" in labels
    moments = [entry["at"] for entry in story["timeline"]]
    assert moments == sorted(moments), "l'histoire doit se lire dans l'ordre"

    # La propagation classe les marchands par date de publication.
    assert [step["site"] for step in story["propagation"]] == [
        "amazon", "micromania",
    ]
    assert story["propagation"][0]["rank"] == 1

    metrics = story["metrics"]
    assert metrics["merchants"] == 2
    assert metrics["first_merchant"] == "amazon"
    assert metrics["changes"] == 3
    assert metrics["invitations"] == 1
    assert metrics["preorders"] == 1
    assert metrics["back_in_stock"] == 1
    await db.dispose()


def test_the_story_route_is_404_for_an_unknown_product(authed):
    assert authed.get(
        "/api/v1/catalog/products/inconnu/story"
    ).status_code == 404


def test_the_health_routes_require_authentication(tmp_path):
    from src.web.app import create_app

    app = create_app(settings=make_settings(tmp_path), config_path=None,
                     run_engine=False)
    with TestClient(app) as client:
        for route in ("/api/v1/health/overview", "/api/v1/plugins/health",
                      "/api/v1/diagnostics"):
            assert client.get(route).status_code == 401
