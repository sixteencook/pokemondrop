"""Recherche inter-sites multi-clés, mémoire des échecs et relance.

Scénario directeur : Amazon découvre un UPC. Micromania ne connaît pas
encore la fiche ; l'échec est mémorisé et retenté plus tard. Quand la page
apparaît enfin, la relance la trouve — sans repartir de zéro.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.discovery.loader import DiscoveryRegistry
from src.intelligence.candidates import OfferCandidate
from src.intelligence.crosssite import CrossSiteIntelligence, CrossSiteSettings
from src.intelligence.identity import ProductIdentity
from src.intelligence.keys import SearchKey
from src.repositories.search_attempts import (
    STATUS_FOUND,
    STATUS_NOT_FOUND,
    SearchAttemptRepository,
    next_retry,
)
from tests.helpers import make_db

pytestmark = pytest.mark.asyncio

EAN = "4006381333931"
UPC = "196214141612"
PRODUCT = "prod-0001"


class FakeMerchant:
    """Plugin scriptable : répond selon la clé qu'on lui présente."""

    def __init__(self, site: str, answers: dict[str, list[OfferCandidate]] | None = None,
                 boom: bool = False) -> None:
        self.site_name = site
        self.display_name = site.capitalize()
        self.answers = answers or {}
        self.boom = boom
        self.calls: list[str] = []

    async def search(self, identity, ctx, key=None):
        if self.boom:
            raise RuntimeError("site injoignable")
        self.calls.append(key.kind if key else "?")
        return self.answers.get(key.kind if key else "", [])


def candidate(confidence: int = 96, **kwargs) -> OfferCandidate:
    base = dict(
        url="https://micromania.fr/p/upc.html", title="Pokémon 30 Ans UPC",
        confidence=confidence, matched_fields=("upc", "brand"),
        reason="UPC identique",
    )
    base.update(kwargs)
    return OfferCandidate(**base)


def identity() -> ProductIdentity:
    return ProductIdentity.build(
        source="amazon", upc=UPC, asin="B0H3PRH89L", mpn="10-10410-102",
        brand="Pokémon", canonical_name="Pokémon 30 Ans UPC",
    )


async def build(tmp_path, *plugins, **overrides):
    db = await make_db(tmp_path)
    attempts = SearchAttemptRepository(db.session_factory)
    registry = DiscoveryRegistry()
    for plugin in plugins:
        registry.register(plugin)
    settings = CrossSiteSettings(**{"enabled": True, **overrides})
    engine = CrossSiteIntelligence(
        settings, registry, attempts, context_factory=lambda options: object()
    )
    return db, engine, attempts


# --------------------------------------------------------------------- #
# Recherche multi-clés                                                   #
# --------------------------------------------------------------------- #

async def test_all_keys_are_tried_until_one_answers(tmp_path):
    """Les clés sont essayées par ordre de confiance décroissante."""
    merchant = FakeMerchant("micromania", {"mpn": [candidate(95)]})
    db, engine, _ = await build(tmp_path, merchant)

    candidates, report = await engine.search_everywhere(PRODUCT, identity())

    # upc (98) et asin (93) essayés avant mpn (92) : l'ordre est respecté.
    assert merchant.calls[:3] == ["upc", "asin", "mpn"]
    assert len(candidates) == 1
    assert report.keys_tried == 3
    await db.dispose()


async def test_search_stops_once_confidence_is_high_enough(tmp_path):
    merchant = FakeMerchant("micromania", {"upc": [candidate(96)]})
    db, engine, _ = await build(tmp_path, merchant, stop_confidence=90)

    await engine.search_everywhere(PRODUCT, identity())

    assert merchant.calls == ["upc"]      # inutile d'essayer les clés faibles
    await db.dispose()


async def test_weak_result_keeps_trying_other_keys(tmp_path):
    merchant = FakeMerchant("micromania", {"upc": [candidate(60)]})
    db, engine, _ = await build(tmp_path, merchant, stop_confidence=90)

    await engine.search_everywhere(PRODUCT, identity())

    assert len(merchant.calls) > 1
    await db.dispose()


async def test_several_merchants_are_queried_in_parallel(tmp_path):
    first = FakeMerchant("micromania", {"upc": [candidate()]})
    second = FakeMerchant("fnac", {"upc": [candidate(url="https://fnac.com/p")]})
    db, engine, _ = await build(tmp_path, first, second)

    candidates, report = await engine.search_everywhere(PRODUCT, identity())

    assert report.sites_queried == 2
    assert {c.site for c in candidates} == {"micromania", "fnac"}
    await db.dispose()


async def test_known_sites_are_excluded(tmp_path):
    merchant = FakeMerchant("micromania", {"upc": [candidate()]})
    db, engine, _ = await build(tmp_path, merchant)

    _, report = await engine.search_everywhere(
        PRODUCT, identity(), exclude_sites=("micromania",)
    )
    assert report.sites_queried == 0
    assert merchant.calls == []
    await db.dispose()


async def test_a_failing_merchant_never_blocks_the_others(tmp_path):
    broken = FakeMerchant("kingjouet", boom=True)
    healthy = FakeMerchant("fnac", {"upc": [candidate()]})
    db, engine, _ = await build(tmp_path, broken, healthy)

    candidates, report = await engine.search_everywhere(PRODUCT, identity())

    assert len(candidates) == 1
    assert report.errors                     # l'échec est rapporté
    await db.dispose()


async def test_identity_without_keys_searches_nothing(tmp_path):
    merchant = FakeMerchant("micromania")
    db, engine, _ = await build(tmp_path, merchant)

    candidates, report = await engine.search_everywhere(PRODUCT, ProductIdentity())

    assert candidates == []
    assert report.sites_queried == 0
    await db.dispose()


# --------------------------------------------------------------------- #
# Mémoire des recherches                                                 #
# --------------------------------------------------------------------- #

async def test_a_successful_search_is_recorded_with_its_reason(tmp_path):
    merchant = FakeMerchant("micromania", {"upc": [candidate(96)]})
    db, engine, attempts = await build(tmp_path, merchant)

    await engine.search_everywhere(PRODUCT, identity())

    found = [a for a in await attempts.for_product(PRODUCT) if a.succeeded]
    assert len(found) == 1
    assert found[0].confidence == 96
    assert found[0].matched_fields == ("upc", "brand")
    assert found[0].reason == "UPC identique"
    assert found[0].next_retry_at is None     # plus rien à relancer
    await db.dispose()


async def test_failures_are_remembered_with_a_retry_time(tmp_path):
    """Le cœur de la demande : un échec n'est pas perdu."""
    merchant = FakeMerchant("micromania")          # ne trouve rien
    db, engine, attempts = await build(tmp_path, merchant)

    await engine.search_everywhere(PRODUCT, identity())

    recorded = await attempts.for_product(PRODUCT)
    assert recorded
    assert all(a.status == STATUS_NOT_FOUND for a in recorded)
    assert all(a.next_retry_at is not None for a in recorded)
    await db.dispose()


async def test_retry_delay_grows_with_attempts(tmp_path):
    base = next_retry(1, 1800, 1.5, 21600)
    later = next_retry(4, 1800, 1.5, 21600)
    capped = next_retry(50, 1800, 1.5, 21600)
    now = datetime.now(timezone.utc)

    assert (base - now) < (later - now)
    assert (capped - now) <= timedelta(seconds=21600 + 5)


async def test_due_for_retry_returns_only_ripe_attempts(tmp_path):
    db = await make_db(tmp_path)
    attempts = SearchAttemptRepository(db.session_factory)
    now = datetime.now(timezone.utc)

    await attempts.record(PRODUCT, "micromania", "upc", UPC, STATUS_NOT_FOUND,
                          next_retry_at=now - timedelta(minutes=1))
    await attempts.record(PRODUCT, "fnac", "upc", UPC, STATUS_NOT_FOUND,
                          next_retry_at=now + timedelta(hours=2))
    await attempts.record(PRODUCT, "cultura", "upc", UPC, STATUS_FOUND)

    due = await attempts.due_for_retry()
    assert [a.site for a in due] == ["micromania"]
    await db.dispose()


async def test_every_tried_key_is_recorded_even_in_parallel(tmp_path):
    """Les sites sont interrogés en parallèle : aucune trace ne doit se perdre."""
    first = FakeMerchant("micromania")
    second = FakeMerchant("kingjouet")
    db, engine, attempts = await build(tmp_path, first, second)

    _, report = await engine.search_everywhere(PRODUCT, identity())

    recorded = await attempts.for_product(PRODUCT)
    assert len(recorded) == report.keys_tried
    assert len(recorded) == len(first.calls) + len(second.calls)
    await db.dispose()


async def test_repeated_failures_update_the_same_row(tmp_path):
    merchant = FakeMerchant("micromania")
    db, engine, attempts = await build(tmp_path, merchant)

    await engine.search_everywhere(PRODUCT, identity(), only_keys=[
        SearchKey("upc", UPC, 98)
    ])
    await engine.search_everywhere(PRODUCT, identity(), only_keys=[
        SearchKey("upc", UPC, 98)
    ])

    recorded = await attempts.for_product(PRODUCT)
    assert len(recorded) == 1          # une seule ligne, pas deux
    assert recorded[0].attempts == 2
    await db.dispose()


async def test_a_late_listing_is_caught_by_the_retry(tmp_path):
    """Micromania publie sa page quelques heures après Amazon."""
    merchant = FakeMerchant("micromania")          # rien aujourd'hui
    db, engine, attempts = await build(tmp_path, merchant)

    await engine.search_everywhere(PRODUCT, identity(), only_keys=[
        SearchKey("upc", UPC, 98)
    ])
    assert not (await attempts.for_product(PRODUCT))[0].succeeded

    # La fiche apparaît enfin.
    merchant.answers = {"upc": [candidate(96)]}
    await engine.search_everywhere(PRODUCT, identity(), only_keys=[
        SearchKey("upc", UPC, 98)
    ])

    recorded = await attempts.for_product(PRODUCT)
    assert recorded[0].succeeded
    assert recorded[0].found_url == "https://micromania.fr/p/upc.html"
    assert recorded[0].next_retry_at is None
    await db.dispose()


async def test_already_found_short_circuits_further_retries(tmp_path):
    db = await make_db(tmp_path)
    attempts = SearchAttemptRepository(db.session_factory)
    await attempts.record(PRODUCT, "micromania", "upc", UPC, STATUS_FOUND)

    assert await attempts.already_found(PRODUCT, "micromania")
    assert not await attempts.already_found(PRODUCT, "fnac")
    await db.dispose()


async def test_pending_retries_are_counted(tmp_path):
    merchant = FakeMerchant("micromania")
    db, engine, attempts = await build(tmp_path, merchant)
    await engine.search_everywhere(PRODUCT, identity())

    assert await attempts.pending_retries() > 0
    await db.dispose()


async def test_disabled_engine_reports_itself(tmp_path):
    merchant = FakeMerchant("micromania")
    db, engine, _ = await build(tmp_path, merchant, enabled=False)
    assert not engine.enabled
    await db.dispose()


async def test_engine_without_capable_plugin_is_disabled(tmp_path):
    class Silent:
        site_name = "muet"
        display_name = "Muet"

    db, engine, _ = await build(tmp_path, Silent())
    assert not engine.enabled
    await db.dispose()
