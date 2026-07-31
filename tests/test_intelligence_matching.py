"""Moteur de corrélation : échelle de confiance et extensibilité."""

from datetime import datetime, timezone

import pytest

from src.intelligence.entities import (
    CanonicalProduct,
    ProductAttributes,
    ProductDraft,
    ProductIdentifiers,
)
from src.intelligence.matching import MatchingEngine, MatchResult, default_strategies
from src.intelligence.naming import name_key, normalise_name, similarity
from src.models import Priority

#: Seuls les tests du moteur sont asynchrones — pas de marqueur global.
asyncio_test = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)


def product(
    name: str = "Pokémon 30 Ans Ultra Premium Collection",
    **kwargs,
) -> CanonicalProduct:
    identifiers = ProductIdentifiers(**{
        key: kwargs.pop(key) for key in list(kwargs)
        if key in ("ean", "upc", "isbn", "mpn", "manufacturer_sku", "manufacturer_ref")
    })
    attributes = ProductAttributes(**kwargs)
    return CanonicalProduct(
        uuid=f"u-{abs(hash(name + str(identifiers))) % 10**8}",
        name=name, name_key=name_key(name),
        identifiers=identifiers, attributes=attributes,
        tags=(), priority=Priority.NORMAL, created_at=NOW, updated_at=NOW,
    )


def draft(name: str = "Pokemon 30 ans ultra premium collection", **kwargs) -> ProductDraft:
    identifiers = ProductIdentifiers(**{
        key: kwargs.pop(key) for key in list(kwargs)
        if key in ("ean", "upc", "isbn", "mpn", "manufacturer_sku", "manufacturer_ref")
    })
    return ProductDraft(name=name, identifiers=identifiers,
                        attributes=ProductAttributes(**kwargs))


# --------------------------------------------------------------------- #
# Normalisation des noms                                                 #
# --------------------------------------------------------------------- #

def test_name_normalisation_ignores_accents_case_and_filler():
    assert normalise_name("Pokémon 30 Ans — Édition Collector (Neuf)") == (
        "pokemon 30 ans collector"
    )


def test_name_key_ignores_word_order():
    assert name_key("UPC Pokémon 30 ans") == name_key("Pokémon 30 ans UPC")


def test_similarity_scale():
    assert similarity("Pokémon 30 Ans UPC", "POKEMON 30 ANS UPC") == 1.0
    assert similarity("Pokémon 30 Ans UPC", "Manette PS5") < 0.4


# --------------------------------------------------------------------- #
# Échelle de confiance                                                   #
# --------------------------------------------------------------------- #

@asyncio_test
async def test_ean_wins_with_maximum_confidence():
    known = product(ean="4006381333931")
    engine = MatchingEngine()
    result = await engine.match(draft(name="Autre nom", ean="4006381333931"), [known])
    assert result.score == 100
    assert result.method == "ean"
    assert result.product.uuid == known.uuid


@pytest.mark.parametrize("field,expected_score,expected_method", [
    ("upc", 98, "upc"),
    ("isbn", 96, "isbn"),
    ("mpn", 95, "mpn"),
    ("manufacturer_sku", 92, "manufacturer_sku"),
    ("manufacturer_ref", 90, "manufacturer_ref"),
])
@asyncio_test
async def test_identifier_scale(field, expected_score, expected_method):
    known = product(**{field: "CODE-1"})
    result = await MatchingEngine().match(
        draft(name="Nom différent", **{field: "CODE-1"}), [known]
    )
    assert (result.score, result.method) == (expected_score, expected_method)


@asyncio_test
async def test_name_and_brand_scores_85():
    known = product(brand="Pokémon")
    result = await MatchingEngine().match(
        draft(name="Pokémon 30 ans Ultra Premium Collection", brand="Pokémon"), [known]
    )
    assert result.score == 85
    assert result.method == "name_brand"


@asyncio_test
async def test_name_and_release_date_scores_80():
    known = product(release_date="2026-08-21")
    result = await MatchingEngine().match(
        draft(name="Pokemon 30 ans ultra premium collection",
              release_date="2026-08-21"), [known]
    )
    assert result.score == 80


@asyncio_test
async def test_name_and_collection_scores_75():
    known = product(collection="30 Ans")
    result = await MatchingEngine().match(
        draft(name="Pokemon 30 ans ultra premium collection",
              collection="30 Ans"), [known]
    )
    assert result.score == 75


@asyncio_test
async def test_name_only_stays_below_the_merge_threshold():
    known = product()
    result = await MatchingEngine().match(draft(), [known])
    assert result.score == 70
    assert result.method == "name_only"


@asyncio_test
async def test_no_match_returns_none():
    assert await MatchingEngine().match(
        draft(name="Manette PS5 sans fil"), [product()]
    ) is None


@asyncio_test
async def test_different_identifiers_never_match():
    known = product(ean="4006381333931")
    assert await MatchingEngine().match(
        draft(name="Nom totalement autre", ean="0036000291452"), [known]
    ) is None


@asyncio_test
async def test_strategies_are_ordered_by_confidence():
    scores = [strategy.score for strategy in default_strategies()]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------- #
# Extensibilité : OCR, embeddings, similarité visuelle…                  #
# --------------------------------------------------------------------- #

@asyncio_test
async def test_a_custom_strategy_plugs_in_without_touching_the_engine():
    """Ajouter une méthode = une classe et une entrée dans la liste."""

    class VisualSimilarityStrategy:
        name = "visual_similarity"
        score = 88

        async def find(self, candidate, candidates):
            if not candidate.attributes.image_url:
                return None
            for known in candidates:
                if known.attributes.image_url == candidate.attributes.image_url:
                    return MatchResult(known, self.score, self.name,
                                       "packaging identique")
            return None

    known = product(image_url="https://cdn/x.jpg")
    engine = MatchingEngine([*default_strategies(), VisualSimilarityStrategy()])

    result = await engine.match(
        draft(name="Nom sans rapport", image_url="https://cdn/x.jpg"), [known]
    )
    assert result.method == "visual_similarity"
    assert result.score == 88
    # La nouvelle méthode s'insère à sa place dans l'échelle.
    assert engine.methods[0].startswith("ean")
