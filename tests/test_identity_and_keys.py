"""Identité produit, confiances, et construction des clés de recherche."""

import pytest

from src.intelligence.identity import IdentityField, ProductIdentity
from src.intelligence.keys import KEY_PRIORITIES, build_search_keys
from src.intelligence.strategies import (
    IdentityContext,
    IdentityStrategyRegistry,
    discover_identity_strategies,
)
from src.intelligence.strategies.html_identity import HtmlIdentityStrategy

asyncio_test = pytest.mark.asyncio

EAN = "4006381333931"


# --------------------------------------------------------------------- #
# Identité : confiance et enrichissement progressif                      #
# --------------------------------------------------------------------- #

def test_field_carries_value_confidence_and_source():
    identity = ProductIdentity().with_field("ean", EAN, 100, "amazon")
    assert identity.ean == EAN
    assert identity.confidence_of("ean") == 100
    assert identity.source_of("ean") == "amazon"


def test_higher_confidence_replaces_lower():
    identity = (
        ProductIdentity()
        .with_field("brand", "pokemon", 60, "titre")
        .with_field("brand", "Pokémon", 90, "json-ld")
    )
    assert identity.brand == "Pokémon"
    assert identity.confidence_of("brand") == 90


def test_lower_confidence_never_overwrites():
    identity = (
        ProductIdentity()
        .with_field("brand", "Pokémon", 90, "json-ld")
        .with_field("brand", "Autre", 50, "titre")
    )
    assert identity.brand == "Pokémon"


def test_empty_values_are_ignored():
    identity = ProductIdentity().with_field("ean", None).with_field("upc", "  ")
    assert identity.is_empty


def test_identity_is_immutable():
    original = ProductIdentity().with_field("ean", EAN)
    enriched = original.with_field("upc", "036000291452")
    assert original.upc is None          # l'originale n'a pas bougé
    assert enriched.ean == EAN


def test_aliases_accumulate_without_duplicates():
    identity = (
        ProductIdentity()
        .with_alias("Pokémon 30 Ans UPC")
        .with_alias("POKEMON 30 ANS UPC", "Pokémon 30 Ans UPC")
    )
    assert identity.aliases == ("Pokémon 30 Ans UPC", "POKEMON 30 ANS UPC")


def test_merge_keeps_the_best_of_both():
    amazon = ProductIdentity.build(source="amazon", upc="196214141612",
                                   asin="B0H3PRH89L")
    micromania = ProductIdentity.build(source="micromania", ean=EAN,
                                       brand="Pokémon")
    merged = amazon.merged_with(micromania)
    assert merged.upc == "196214141612"
    assert merged.asin == "B0H3PRH89L"
    assert merged.ean == EAN
    assert merged.brand == "Pokémon"


def test_round_trip_serialisation():
    identity = (
        ProductIdentity.build(source="amazon", ean=EAN, brand="Pokémon")
        .with_alias("Autre titre")
        .with_images("https://cdn/x.jpg")
    )
    restored = ProductIdentity.from_dict(identity.to_dict())
    assert restored.ean == EAN
    assert restored.aliases == ("Autre titre",)
    assert restored.additional_images == ("https://cdn/x.jpg",)
    assert restored.source_of("ean") == "amazon"


# --------------------------------------------------------------------- #
# Clés de recherche multi-critères                                       #
# --------------------------------------------------------------------- #

def test_all_available_keys_become_searches():
    """Le cas Amazon de l'énoncé : ASIN, UPC, MPN, marque, modèle, nom."""
    identity = ProductIdentity.build(
        source="amazon",
        asin="B0H3PRH89L", upc="196214141612", mpn="10-10410-102",
        brand="Pokémon", model_number="Premiers Partenaires Série 3",
        canonical_name="Coffret Pokémon Premiers Partenaires",
    )
    keys = build_search_keys(identity)
    kinds = [key.kind for key in keys]

    assert "upc" in kinds and "asin" in kinds and "mpn" in kinds
    assert "model_number" in kinds and "brand_model" in kinds
    assert "canonical_name" in kinds
    # Ordre : du plus discriminant au plus vague.
    priorities = [key.priority for key in keys]
    assert priorities == sorted(priorities, reverse=True)


def test_identical_values_are_searched_once_across_kinds():
    """Si « marque + modèle » redonne le nom canonique, une seule recherche."""
    identity = ProductIdentity.build(
        brand="Pokémon", model_number="UPC 30 Ans",
        canonical_name="Pokémon UPC 30 Ans",
    )
    values = [key.value.lower() for key in build_search_keys(identity)]
    assert values.count("pokémon upc 30 ans") == 1


def test_ean_comes_first():
    identity = ProductIdentity.build(ean=EAN, canonical_name="Un produit")
    assert build_search_keys(identity)[0].kind == "ean"


def test_brand_alone_is_not_a_key():
    """Une marque seule ne désigne aucun produit."""
    identity = ProductIdentity.build(brand="Pokémon")
    assert build_search_keys(identity) == []


def test_brand_and_model_combine():
    """Le modèle seul reste plus discriminant, mais la combinaison existe."""
    identity = ProductIdentity.build(brand="Pokémon", model_number="SV-10")
    keys = {key.kind: key for key in build_search_keys(identity)}
    assert keys["model_number"].value == "SV-10"
    assert keys["brand_model"].value == "Pokémon SV-10"
    assert keys["model_number"].priority > keys["brand_model"].priority


def test_duplicate_values_are_not_searched_twice():
    identity = ProductIdentity.build(ean=EAN, gtin=EAN)
    values = [key.value for key in build_search_keys(identity)]
    assert values.count(EAN) == 1


def test_strong_keys_are_flagged():
    identity = ProductIdentity.build(ean=EAN, canonical_name="Un produit")
    keys = {key.kind: key for key in build_search_keys(identity)}
    assert keys["ean"].is_strong
    assert not keys["canonical_name"].is_strong


def test_empty_identity_produces_no_key():
    assert build_search_keys(ProductIdentity()) == []


def test_priorities_match_the_documented_scale():
    assert KEY_PRIORITIES["ean"] > KEY_PRIORITIES["upc"] > KEY_PRIORITIES["isbn"]
    assert KEY_PRIORITIES["mpn"] > KEY_PRIORITIES["sku"]
    assert KEY_PRIORITIES["canonical_name"] > KEY_PRIORITIES["alias"]


# --------------------------------------------------------------------- #
# Stratégies d'identité : extensibilité                                  #
# --------------------------------------------------------------------- #

def test_builtin_strategy_is_discovered():
    registry = discover_identity_strategies()
    assert any("html_structured_data" in name for name in registry.names)


@asyncio_test
async def test_html_strategy_extracts_identity_and_asin():
    page = f"""
    <html><head><script type="application/ld+json">
    {{"@type": "Product", "name": "X", "gtin13": "{EAN}",
      "brand": {{"name": "Pokémon"}}, "mpn": "10-10410-102"}}
    </script></head><body></body></html>
    """
    identity = await HtmlIdentityStrategy().enrich(
        ProductIdentity(),
        IdentityContext(site="amazon", url="https://amazon.fr/dp/B0H3PRH89L",
                        title="Pokémon Premiers Partenaires", html=page),
    )
    assert identity.ean == EAN
    assert identity.asin == "B0H3PRH89L"      # lu dans l'URL
    assert identity.brand == "Pokémon"
    assert identity.mpn == "10-10410-102"
    assert identity.canonical_name == "Pokémon Premiers Partenaires"


@asyncio_test
async def test_a_new_strategy_plugs_in_without_touching_the_engine():
    """OCR, code-barres, CLIP, LLM… : une classe suffit."""

    class BarcodeStrategy:
        name = "barcode_ocr"
        priority = 95

        async def enrich(self, identity, context):
            if not context.image_url:
                return None
            return identity.with_field("ean", EAN, 88, "barcode_ocr")

    registry = IdentityStrategyRegistry([BarcodeStrategy()])
    identity = await registry.enrich(
        ProductIdentity(), IdentityContext(image_url="https://cdn/box.jpg")
    )
    assert identity.ean == EAN
    assert identity.source_of("ean") == "barcode_ocr"


@asyncio_test
async def test_a_failing_strategy_never_loses_the_identity():
    class BrokenStrategy:
        name = "broken"
        priority = 50

        async def enrich(self, identity, context):
            raise RuntimeError("modèle indisponible")

    registry = IdentityStrategyRegistry([BrokenStrategy()])
    identity = await registry.enrich(
        ProductIdentity.build(ean=EAN), IdentityContext()
    )
    assert identity.ean == EAN          # l'acquis est préservé
