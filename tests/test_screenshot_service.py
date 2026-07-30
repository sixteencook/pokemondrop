"""ScreenshotService : découplage du moteur, file, retries, publication.

Playwright n'est pas sollicité : le pool et le capteur sont remplacés par
des doubles, ce qui permet de tester le CONTRAT (ne jamais bloquer, ne
jamais perdre une alerte) sans navigateur.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.config import ScreenshotSettings
from src.core.events import Event, EventBus, EventType
from src.models import Availability, ChangeEvent, ChangeType, ProductSnapshot
from src.services.screenshots import PENDING_FLAG, ScreenshotService
from src.services.screenshots.browser import BrowserUnavailable
from src.services.screenshots.capture import CaptureResult
from tests.helpers import make_product

pytestmark = pytest.mark.asyncio


class FakePool:
    """Pool sans navigateur ; peut simuler une indisponibilité."""

    def __init__(self, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.pages_opened = 0
        self.closed = False

    @asynccontextmanager
    async def page(self):
        if self.unavailable:
            raise BrowserUnavailable("Chromium absent (test)")
        self.pages_opened += 1
        yield object()

    async def close(self) -> None:
        self.closed = True


class FakeCapturer:
    """Capteur scriptable : succès, échecs, ou lenteur volontaire."""

    def __init__(self, failures: int = 0, delay: float = 0.0) -> None:
        self.failures = failures
        self.delay = delay
        self.calls = 0

    async def capture(self, page, request) -> CaptureResult:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.calls <= self.failures:
            raise RuntimeError("échec simulé")
        request.target.parent.mkdir(parents=True, exist_ok=True)
        request.target.write_bytes(b"\x89PNG fake")
        return CaptureResult(
            success=True, path=request.target,
            size_bytes=request.target.stat().st_size, duration_ms=5,
        )


def make_settings(tmp_path, **overrides) -> ScreenshotSettings:
    base = dict(
        enabled=True, timeout_ms=1000, max_concurrent=1, max_attempts=2,
        settle_delay_ms=0, retention_days=0, directory=tmp_path / "screenshots",
    )
    base.update(overrides)
    return ScreenshotSettings(**base)


def make_change(change_type=ChangeType.PREORDER_OPENED) -> ChangeEvent:
    return ChangeEvent(
        product=make_product(uuid="u1"),
        change_type=change_type,
        old_value="unavailable",
        new_value="preorder",
        snapshot=ProductSnapshot(availability=Availability.PREORDER,
                                 price="119,99 €", page_exists=True),
    )


async def build_service(tmp_path, capturer=None, pool=None, **overrides):
    bus = EventBus()
    service = ScreenshotService(
        make_settings(tmp_path, **overrides), bus,
        capturer=capturer or FakeCapturer(), pool=pool or FakePool(),
    )
    service.attach_to(bus)
    await service.start()
    return bus, service


def change_payload(change=None):
    change = change or make_change()
    return {"product": change.product, "change": change,
            "snapshot": change.snapshot, "alert_id": 42}


async def wait_for(predicate, timeout: float = 3.0) -> None:
    """Attend qu'une condition devienne vraie (les workers sont asynchrones)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition jamais satisfaite")


async def test_handler_never_blocks_the_engine(tmp_path):
    """Le handler enfile et rend la main : une capture lente n'attend pas."""
    capturer = FakeCapturer(delay=2.0)  # capture volontairement très lente
    bus, service = await build_service(tmp_path, capturer=capturer)

    started = asyncio.get_running_loop().time()
    await bus.publish(Event(EventType.CHANGE_DETECTED, change_payload()))
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.2, "la publication a attendu la capture"
    assert service.pending_count >= 0
    await service.stop()


async def test_capture_publishes_completion_with_relative_path(tmp_path):
    bus, service = await build_service(tmp_path)
    completions = []
    bus.subscribe(lambda e: completions.append(e) or asyncio.sleep(0),
                  {EventType.SCREENSHOT_COMPLETED})

    payload = change_payload()
    await bus.publish(Event(EventType.CHANGE_DETECTED, payload))
    assert payload[PENDING_FLAG] is True  # les notifications doivent patienter

    await wait_for(lambda: len(completions) == 1)
    result = completions[0].payload
    assert result["alert_id"] == 42
    assert result["screenshot_path"].endswith(".png")
    assert "\\" not in result["screenshot_path"]  # POSIX : portable Windows → Linux
    assert PENDING_FLAG not in result
    assert (service._settings.directory / result["screenshot_path"]).is_file()
    await service.stop()


async def test_routine_change_is_ignored(tmp_path):
    bus, service = await build_service(tmp_path)
    payload = change_payload(make_change(ChangeType.PAGE_CHANGED))
    await bus.publish(Event(EventType.CHANGE_DETECTED, payload))

    assert PENDING_FLAG not in payload  # notification immédiate, sans capture
    assert service.pending_count == 0
    await service.stop()


async def test_product_without_url_is_ignored(tmp_path):
    bus, service = await build_service(tmp_path)
    change = ChangeEvent(
        product=make_product(uuid="u2", url=""),
        change_type=ChangeType.PREORDER_OPENED,
        old_value=None, new_value="preorder",
        snapshot=ProductSnapshot(page_exists=True),
    )
    payload = {"product": change.product, "change": change, "alert_id": 1}
    await bus.publish(Event(EventType.CHANGE_DETECTED, payload))

    assert PENDING_FLAG not in payload
    await service.stop()


async def test_retry_then_success(tmp_path):
    capturer = FakeCapturer(failures=1)  # premier essai KO, second OK
    bus, service = await build_service(tmp_path, capturer=capturer)
    completions = []
    bus.subscribe(lambda e: completions.append(e) or asyncio.sleep(0),
                  {EventType.SCREENSHOT_COMPLETED})

    await bus.publish(Event(EventType.CHANGE_DETECTED, change_payload()))
    await wait_for(lambda: len(completions) == 1, timeout=8)

    assert capturer.calls == 2
    assert completions[0].payload["screenshot_path"] is not None
    await service.stop()


async def test_permanent_failure_still_publishes_completion(tmp_path):
    """Une capture définitivement en échec ne doit JAMAIS perdre l'alerte."""
    capturer = FakeCapturer(failures=99)
    bus, service = await build_service(tmp_path, capturer=capturer, max_attempts=1)
    completions = []
    bus.subscribe(lambda e: completions.append(e) or asyncio.sleep(0),
                  {EventType.SCREENSHOT_COMPLETED})

    await bus.publish(Event(EventType.CHANGE_DETECTED, change_payload()))
    await wait_for(lambda: len(completions) == 1)

    assert completions[0].payload["screenshot_path"] is None
    assert completions[0].payload["alert_id"] == 42  # Telegram partira en texte
    await service.stop()


async def test_browser_unavailable_disables_service(tmp_path):
    """Playwright absent : le service se désactive, sans bloquer le reste."""
    bus, service = await build_service(tmp_path, pool=FakePool(unavailable=True))
    completions = []
    bus.subscribe(lambda e: completions.append(e) or asyncio.sleep(0),
                  {EventType.SCREENSHOT_COMPLETED})

    await bus.publish(Event(EventType.CHANGE_DETECTED, change_payload()))
    await wait_for(lambda: len(completions) == 1)

    assert completions[0].payload["screenshot_path"] is None
    assert not service.enabled  # plus aucune capture ne sera tentée
    await service.stop()


async def test_disabled_service_ignores_events(tmp_path):
    bus = EventBus()
    service = ScreenshotService(
        make_settings(tmp_path, enabled=False), bus,
        capturer=FakeCapturer(), pool=FakePool(),
    )
    service.attach_to(bus)
    await service.start()

    payload = change_payload()
    await bus.publish(Event(EventType.CHANGE_DETECTED, payload))
    assert PENDING_FLAG not in payload
    await service.stop()


async def test_one_capture_shared_by_simultaneous_changes(tmp_path):
    """Un même check détecte prix + précommande : UNE capture, DEUX alertes."""
    capturer = FakeCapturer()
    bus, service = await build_service(tmp_path, capturer=capturer)
    completions = []
    bus.subscribe(lambda e: completions.append(e) or asyncio.sleep(0),
                  {EventType.SCREENSHOT_COMPLETED})

    snapshot = ProductSnapshot(availability=Availability.PREORDER,
                               price="189,99 €", page_exists=True,
                               checked_at="2026-08-21T14:03:11+00:00")
    product = make_product(uuid="u1")
    for change_type, alert_id in (
        (ChangeType.PRICE_APPEARED, 1), (ChangeType.PREORDER_OPENED, 2)
    ):
        change = ChangeEvent(product=product, change_type=change_type,
                             old_value=None, new_value="preorder", snapshot=snapshot)
        await bus.publish(Event(EventType.CHANGE_DETECTED, {
            "product": product, "change": change,
            "snapshot": snapshot, "alert_id": alert_id,
        }))

    await wait_for(lambda: len(completions) == 2)
    assert capturer.calls == 1, "la page a été capturée deux fois"
    paths = {event.payload["screenshot_path"] for event in completions}
    assert len(paths) == 1  # même fichier partagé
    assert {event.payload["alert_id"] for event in completions} == {1, 2}
    await service.stop()


async def test_separate_checks_produce_separate_captures(tmp_path):
    """Deux vérifications distinctes → deux captures distinctes."""
    capturer = FakeCapturer()
    bus, service = await build_service(tmp_path, capturer=capturer)
    completions = []
    bus.subscribe(lambda e: completions.append(e) or asyncio.sleep(0),
                  {EventType.SCREENSHOT_COMPLETED})

    product = make_product(uuid="u1")
    for stamp in ("2026-08-21T14:03:11+00:00", "2026-08-21T14:04:11+00:00"):
        snapshot = ProductSnapshot(availability=Availability.PREORDER,
                                   page_exists=True, checked_at=stamp)
        change = ChangeEvent(product=product, change_type=ChangeType.PREORDER_OPENED,
                             old_value="unavailable", new_value="preorder",
                             snapshot=snapshot)
        await bus.publish(Event(EventType.CHANGE_DETECTED, {
            "product": product, "change": change, "snapshot": snapshot, "alert_id": 1,
        }))
        await wait_for(lambda: len(completions) >= 1 if stamp.endswith("03:11") else True)

    await wait_for(lambda: len(completions) == 2, timeout=5)
    assert capturer.calls == 2
    await service.stop()


async def test_stop_closes_browser(tmp_path):
    pool = FakePool()
    _, service = await build_service(tmp_path, pool=pool)
    await service.stop()
    assert pool.closed
