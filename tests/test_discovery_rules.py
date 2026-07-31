"""Règles d'inclusion / exclusion, entièrement configurables."""

from src.discovery.contracts import DiscoveredProduct
from src.discovery.rules import RuleSet


def product(title: str, url: str = "https://example.com/p/x") -> DiscoveredProduct:
    return DiscoveredProduct(url=url, title=title, site="micromania")


def test_exclusion_wins_over_inclusion():
    rules = RuleSet.from_config({"include": ["pokemon"], "exclude": ["occasion"]})
    match = rules.evaluate(product("Pokémon 30 Ans UPC — Occasion"))
    assert not match.accepted
    assert match.excluded
    assert "occasion" in match.reason


def test_accents_and_case_are_ignored():
    rules = RuleSet.from_config({"include": ["pokémon"]})
    assert rules.evaluate(product("POKEMON 30 ANS")).accepted
    assert rules.evaluate(product("Pokémon 30 Ans")).accepted


def test_without_inclusion_rules_everything_passes():
    rules = RuleSet.from_config({"exclude": ["occasion"]})
    assert rules.evaluate(product("Console rétro")).accepted
    assert not rules.evaluate(product("Console rétro occasion")).accepted


def test_non_matching_product_is_not_accepted():
    rules = RuleSet.from_config({"include": ["pokemon", "booster"]})
    match = rules.evaluate(product("Manette sans fil"))
    assert not match.accepted
    assert not match.excluded  # écarté, mais pas exclu


def test_url_rules_apply():
    rules = RuleSet.from_config({"url_exclude": ["/occasion/"]})
    assert not rules.evaluate(
        product("Pokémon UPC", "https://example.com/occasion/upc")
    ).accepted


def test_matched_terms_are_reported():
    rules = RuleSet.from_config({"include": ["pokemon", "upc"]})
    match = rules.evaluate(product("Pokémon 30 Ans UPC"))
    assert set(match.matched) == {"pokemon", "upc"}


def test_empty_ruleset_accepts_everything():
    assert RuleSet().evaluate(product("N'importe quoi")).accepted
