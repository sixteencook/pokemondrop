"""Product Intelligence Engine : ingestion, corrélation, offres, fusion."""

import pytest

from src.core.events import Event, EventBus, EventType
from src.discovery.contracts import DiscoveredProduct
from src.intelligence import (
    IntelligenceSettings,
    OfferStatus,
    OfferSyncService,
    ProductIntelligenceEngine,
)
from src.models import Availability, ProductSnapshot
from src.repositories import CatalogRepository, OfferRepository, ProductRepository
from tests.helpers import make_db, make_product

pytestmark = pytest.mark.asyncio

EAN_UPC = "4006381333931"
EAN_ETB = "0036000291452"


def found(
    title: str = "Pokémon 30 Ans Ultra Premium Collection",
    site: str = "micromania",
    url: str = "https://micromania.fr/p/upc.html",
    **kwargs,
) -> DiscoveredProduct:
    return DiscoveredProduct(url=url, title=title, site=site, **kwargs)


async def build(tmp_path, **overrides):
    db = await make_db(tmp_path)
    settings = IntelligenceSettings(**{
        "enabled": True, "merge_threshold": 90, "suggestion_floor": 70, **overrides
    })
    bus = EventBus()
    engine = ProductIntelligenceEngine(
        settings,
        CatalogRepository(db.session_factory),
        OfferRepository(db.session_factory),
        ProductRepository(db.session_factory),
        bus,
    )
    return db, engine, bus


def collect(bus: EventBus, *types: EventType) -> list[Event]:
    captured: list[Event] = []

    async def handler(event: Event) -> None:
        captured.append(event)

    bus.subscribe(handler, set(types))
    return captured


# --------------------------------------------------------------------- #
# Corrélation multi-sites                                                #
# --------------------------------------------------------------------- #

async def test_same_ean_on_two_sites_yields_one_product_two_offers(tmp_path):
    """Le cœur de la phase : deux marchands, un seul produit."""
    db, engine, _ = await build(tmp_path)

    first = await engine.ingest(found(site="micromania", ean=EAN_UPC))
    second = await engine.ingest(found(
        title="POKEMON 30 ANS UPC — Ultra Premium",   # nom différent
        site="fnac", url="https://fnac.com/a123/upc", ean=EAN_UPC,
    ))

    assert second.product.uuid == first.product.uuid   # même produit canonique
    assert not second.created_product
    assert second.match.score == 100
    assert second.match.method == "ean"

    offers = await OfferRepository(db.session_factory).for_product(first.product.uuid)
    assert {offer.site for offer in offers} == {"micromania", "fnac"}
    await db.dispose()


async def test_different_products_stay_separate(tmp_path):
    db, engine, _ = await build(tmp_path)
    first = await engine.ingest(found(title="Pokémon UPC", ean=EAN_UPC))
    second = await engine.ingest(found(
        title="Manette PS5 sans fil", site="fnac",
        url="https://fnac.com/p/manette", ean=EAN_ETB,
    ))
    assert first.product.uuid != second.product.uuid
    await db.dispose()


async def test_product_has_no_url_only_offers_do(tmp_path):
    """Vérification structurelle : le produit canonique ignore les URL."""
    db, engine, _ = await build(tmp_path)
    outcome = await engine.ingest(found(ean=EAN_UPC))

    assert not hasattr(outcome.product, "url")
    assert outcome.offer.url == "https://micromania.fr/p/upc.html"
    await db.dispose()


async def test_second_sighting_of_same_url_updates_the_offer(tmp_path):
    db, engine, _ = await build(tmp_path)
    await engine.ingest(found(ean=EAN_UPC, price="189,99 €"))
    again = await engine.ingest(found(ean=EAN_UPC, price="179,99 €"))

    assert not again.created_offer
    assert again.offer.price == "179,99 €"

    history = await OfferRepository(db.session_factory).history(again.offer.uuid)
    assert len(history) == 2          # création + changement de prix
    await db.dispose()


async def test_tracking_parameters_do_not_duplicate_offers(tmp_path):
    db, engine, _ = await build(tmp_path)
    await engine.ingest(found(ean=EAN_UPC))
    again = await engine.ingest(found(
        url="https://www.micromania.fr/p/upc.html?utm_source=mail", ean=EAN_UPC,
    ))
    assert not again.created_offer
    await db.dispose()


# --------------------------------------------------------------------- #
# Seuil de confiance et file de validation                               #
# --------------------------------------------------------------------- #

async def test_low_confidence_creates_a_suggestion_not_a_merge(tmp_path):
    """Nom identique sans identifiant : 70 < 90 → validation manuelle."""
    db, engine, bus = await build(tmp_path)
    pending = collect(bus, EventType.CATALOG_MATCH_PENDING)

    first = await engine.ingest(found())
    second = await engine.ingest(found(
        site="fnac", url="https://fnac.com/p/upc",
    ))

    assert second.created_product                 # rien n'est fusionné à tort
    assert second.product.uuid != first.product.uuid
    assert second.suggestion_id is not None
    assert len(pending) == 1

    suggestions = await CatalogRepository(db.session_factory).list_suggestions()
    assert suggestions[0].score == 70
    await db.dispose()


async def test_threshold_is_configurable(tmp_path):
    """Abaisser le seuil à 70 fusionne ce qui était en attente."""
    db, engine, _ = await build(tmp_path, merge_threshold=70)
    first = await engine.ingest(found())
    second = await engine.ingest(found(site="fnac", url="https://fnac.com/p/upc"))

    assert second.product.uuid == first.product.uuid
    assert second.suggestion_id is None
    await db.dispose()


async def test_below_suggestion_floor_no_noise(tmp_path):
    db, engine, bus = await build(tmp_path, suggestion_floor=95)
    pending = collect(bus, EventType.CATALOG_MATCH_PENDING)

    await engine.ingest(found())
    await engine.ingest(found(site="fnac", url="https://fnac.com/p/upc"))

    assert pending == []          # ni fusion, ni suggestion : deux produits
    await db.dispose()


# --------------------------------------------------------------------- #
# Enrichissement                                                         #
# --------------------------------------------------------------------- #

async def test_a_second_merchant_enriches_the_product(tmp_path):
    """Micromania donne l'EAN, la Fnac la marque : le produit gagne les deux."""
    from src.intelligence.entities import ProductAttributes

    db, engine, _ = await build(tmp_path)
    await engine.ingest(found(ean=EAN_UPC))
    await engine.ingest(
        found(site="fnac", url="https://fnac.com/p/upc", ean=EAN_UPC),
        attributes=ProductAttributes(brand="Pokémon", release_date="2026-08-21"),
    )

    product = (await CatalogRepository(db.session_factory).list_page())[0][0]
    assert product.identifiers.ean == EAN_UPC
    assert product.attributes.brand == "Pokémon"
    assert product.attributes.release_date == "2026-08-21"
    await db.dispose()


async def test_fields_extracted_by_the_plugin_reach_the_product(tmp_path):
    """Enrichissement : marque, MPN et date de sortie remontés par le plugin."""
    db, engine, _ = await build(tmp_path)
    outcome = await engine.ingest(found(
        ean=EAN_UPC, sku="mm-998877", mpn="pok-30-upc",
        brand="Pokémon", release_date="2026-08-21",
        image_url="https://cdn.example.com/upc.jpg",
    ))

    product = outcome.product
    assert product.identifiers.ean == EAN_UPC
    assert product.identifiers.manufacturer_sku == "MM-998877"
    assert product.identifiers.mpn == "POK-30-UPC"
    assert product.attributes.brand == "Pokémon"
    assert product.attributes.release_date == "2026-08-21"
    assert product.attributes.image_url == "https://cdn.example.com/upc.jpg"
    await db.dispose()


async def test_enrichment_never_overwrites_known_values(tmp_path):
    from src.intelligence.entities import ProductAttributes

    db, engine, _ = await build(tmp_path)
    await engine.ingest(found(ean=EAN_UPC),
                        attributes=ProductAttributes(brand="Pokémon"))
    await engine.ingest(
        found(site="fnac", url="https://fnac.com/p/upc", ean=EAN_UPC),
        attributes=ProductAttributes(brand="Marque erronée"),
    )
    product = (await CatalogRepository(db.session_factory).list_page())[0][0]
    assert product.attributes.brand == "Pokémon"
    await db.dispose()


# --------------------------------------------------------------------- #
# Offres : cycle de vie et regroupement automatique                      #
# --------------------------------------------------------------------- #

async def test_offer_is_never_deleted_only_transitioned(tmp_path):
    db, engine, _ = await build(tmp_path)
    outcome = await engine.ingest(found(ean=EAN_UPC))
    offers = OfferRepository(db.session_factory)

    await offers.set_status(outcome.offer.uuid, OfferStatus.NOT_FOUND)
    still_there = await offers.get(outcome.offer.uuid)
    assert still_there is not None
    assert still_there.status is OfferStatus.NOT_FOUND

    history = await offers.history(outcome.offer.uuid)
    assert any(entry.status is OfferStatus.NOT_FOUND for entry in history)
    await db.dispose()


async def test_group_of_the_monitored_product_becomes_automatic(tmp_path):
    """« group » n'est plus saisi à la main : c'est l'UUID du produit."""
    db, engine, _ = await build(tmp_path)
    monitored = await ProductRepository(db.session_factory).create(
        make_product(name="Pokémon UPC", url="https://micromania.fr/p/upc.html")
    )

    outcome = await engine.ingest(found(ean=EAN_UPC), monitored_uuid=monitored.uuid)

    refreshed = await ProductRepository(db.session_factory).get(monitored.uuid)
    assert refreshed.group == outcome.product.uuid
    assert outcome.offer.monitored_uuid == monitored.uuid
    await db.dispose()


async def test_offer_sync_reflects_live_checks(tmp_path):
    """Une vérification met à jour l'offre : la vue Produit est vivante."""
    db, engine, bus = await build(tmp_path)
    monitored = await ProductRepository(db.session_factory).create(
        make_product(name="Pokémon UPC", url="https://micromania.fr/p/upc.html")
    )
    outcome = await engine.ingest(found(ean=EAN_UPC), monitored_uuid=monitored.uuid)

    offers = OfferRepository(db.session_factory)
    OfferSyncService(offers).attach_to(bus)

    await bus.publish(Event(EventType.CHECK_COMPLETED, {
        "product": monitored,
        "snapshot": ProductSnapshot(
            availability=Availability.PREORDER, price="189,99 €", page_exists=True
        ),
        "response_time_ms": 120,
    }))

    refreshed = await offers.get(outcome.offer.uuid)
    assert refreshed.availability == "preorder"
    assert refreshed.price == "189,99 €"
    assert refreshed.status is OfferStatus.ACTIVE
    await db.dispose()


async def test_offer_sync_marks_missing_page_not_found(tmp_path):
    db, engine, bus = await build(tmp_path)
    monitored = await ProductRepository(db.session_factory).create(
        make_product(name="Pokémon UPC", url="https://micromania.fr/p/upc.html")
    )
    outcome = await engine.ingest(found(ean=EAN_UPC), monitored_uuid=monitored.uuid)

    offers = OfferRepository(db.session_factory)
    OfferSyncService(offers).attach_to(bus)
    await bus.publish(Event(EventType.CHECK_COMPLETED, {
        "product": monitored,
        "snapshot": ProductSnapshot(page_exists=False),
    }))

    assert (await offers.get(outcome.offer.uuid)).status is OfferStatus.NOT_FOUND
    await db.dispose()


# --------------------------------------------------------------------- #
# Fusion manuelle                                                        #
# --------------------------------------------------------------------- #

async def test_merge_moves_offers_and_keeps_history(tmp_path):
    db, engine, _ = await build(tmp_path)
    first = await engine.ingest(found())
    second = await engine.ingest(found(site="fnac", url="https://fnac.com/p/upc"))

    merged = await engine.merge(second.product.uuid, first.product.uuid)
    assert merged.uuid == first.product.uuid

    offers = OfferRepository(db.session_factory)
    assert len(await offers.for_product(first.product.uuid)) == 2
    assert await offers.for_product(second.product.uuid) == []
    await db.dispose()


async def test_merge_with_unknown_product_returns_none(tmp_path):
    db, engine, _ = await build(tmp_path)
    outcome = await engine.ingest(found())
    assert await engine.merge(outcome.product.uuid, "inexistant") is None
    await db.dispose()


# --------------------------------------------------------------------- #
# Événements                                                             #
# --------------------------------------------------------------------- #

async def test_events_are_published_for_the_rest_of_the_platform(tmp_path):
    db, engine, bus = await build(tmp_path)
    created = collect(bus, EventType.CATALOG_PRODUCT_CREATED)
    linked = collect(bus, EventType.CATALOG_OFFER_LINKED)

    await engine.ingest(found(ean=EAN_UPC))
    await engine.ingest(found(site="fnac", url="https://fnac.com/p/upc", ean=EAN_UPC))

    assert len(created) == 1      # un seul produit canonique
    assert len(linked) == 2       # mais deux offres
    await db.dispose()


async def test_disabled_engine_reports_itself(tmp_path):
    db, engine, _ = await build(tmp_path, enabled=False)
    assert not engine.enabled
    await db.dispose()
