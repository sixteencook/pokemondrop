"""Identité stable des fiches : c'est elle qui évite les redécouvertes."""

from src.discovery.fingerprint import canonical_url, compute, product_slug


def test_tracking_parameters_are_stripped():
    assert canonical_url(
        "https://www.example.com/p/produit-x.html?utm_source=newsletter&gclid=abc"
    ) == "https://example.com/p/produit-x.html"


def test_case_www_slash_and_fragment_are_normalised():
    a = canonical_url("HTTPS://WWW.Example.com/p/Produit/#avis")
    b = canonical_url("https://example.com/p/Produit")
    assert a == b


def test_meaningful_query_is_preserved_and_sorted():
    assert canonical_url("https://example.com/p?b=2&a=1&utm_medium=x") == (
        "https://example.com/p?a=1&b=2"
    )


def test_same_product_different_tracking_yields_same_fingerprint():
    first = compute("micromania", "https://example.com/p/x.html?utm_source=a")
    second = compute("micromania", "https://www.example.com/p/x.html#tab")
    assert first.value == second.value
    assert first.basis == "url"


def test_ean_wins_over_url():
    info = compute("micromania", "https://example.com/p/x.html", ean="3760000000001")
    assert info.basis == "ean"
    # Le même EAN sur un autre site donne la même empreinte : c'est le but.
    other = compute("fnac", "https://autre.fr/produit/y", ean="3760000000001")
    assert info.value == other.value


def test_sku_is_scoped_to_the_site():
    micromania = compute("micromania", "https://a.fr/p/x", sku="SKU-1")
    fnac = compute("fnac", "https://b.fr/p/y", sku="SKU-1")
    assert micromania.basis == "sku"
    assert micromania.value != fnac.value  # même SKU, sites différents


def test_product_slug_extraction():
    assert product_slug("https://example.com/p/pokemon-30-ans-upc.html") == (
        "pokemon-30-ans-upc"
    )
    assert product_slug("https://example.com/") == ""
