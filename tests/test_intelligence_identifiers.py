"""Extraction et normalisation des identifiants produit."""


from src.intelligence.identifiers import (
    extract,
    is_valid_gtin,
    normalise_code,
    normalise_ean,
    normalise_upc,
)

# EAN-13 valide (clé de contrôle correcte).
EAN = "4006381333931"
# UPC-A valide, et son équivalent EAN-13 (préfixe 0).
UPC = "036000291452"
UPC_AS_EAN = "0036000291452"


def test_gtin_validation():
    assert is_valid_gtin(EAN)
    assert is_valid_gtin(UPC)
    assert not is_valid_gtin("4006381333930")   # clé fausse
    assert not is_valid_gtin("12345")


def test_placeholder_gtins_are_refused():
    """« 0000000000000 » passe la clé de contrôle mais ne désigne rien.

    L'accepter fusionnerait à confiance 100 tous les produits dont le
    marchand n'a pas renseigné le vrai code.
    """
    assert not is_valid_gtin("0000000000000")
    assert not is_valid_gtin("1111111111116")
    assert normalise_ean("0000000000000") is None


def test_upc_is_normalised_to_ean13():
    """Un produit américain et son équivalent européen doivent se rejoindre."""
    assert normalise_ean(UPC) == UPC_AS_EAN
    assert normalise_ean(f" {EAN} ") == EAN
    assert normalise_ean("pas un code") is None


def test_ean13_starting_with_zero_yields_the_upc():
    assert normalise_upc(UPC_AS_EAN) == UPC
    assert normalise_upc(EAN) is None       # 400… n'est pas un UPC


def test_code_normalisation():
    assert normalise_code(" sku_123-a ") == "SKU-123-A"
    assert normalise_code("") is None


JSON_LD_PAGE = f"""
<html><head>
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Pokémon 30 Ans Ultra Premium Collection",
  "gtin13": "{EAN}",
  "sku": "mm-998877",
  "mpn": "POK-30-UPC",
  "brand": {{"@type": "Brand", "name": "Pokémon"}},
  "category": "Cartes à collectionner",
  "releaseDate": "2026-08-21",
  "image": ["https://cdn.example.com/upc.jpg"]
}}
</script></head><body></body></html>
"""


def test_extraction_from_json_ld():
    identifiers, attributes = extract(JSON_LD_PAGE)
    assert identifiers.ean == EAN
    assert identifiers.manufacturer_sku == "MM-998877"
    assert identifiers.mpn == "POK-30-UPC"
    assert attributes.brand == "Pokémon"
    assert attributes.release_date == "2026-08-21"
    assert attributes.category == "Cartes à collectionner"
    assert attributes.image_url == "https://cdn.example.com/upc.jpg"


def test_extraction_from_graph_json_ld():
    """Beaucoup de sites imbriquent le produit dans un @graph."""
    page = (
        '<html><head><script type="application/ld+json">'
        f'{{"@graph": [{{"@type": "WebPage"}}, '
        f'{{"@type": "Product", "name": "X", "gtin13": "{EAN}"}}]}}'
        "</script></head><body></body></html>"
    )
    identifiers, _ = extract(page)
    assert identifiers.ean == EAN


def test_extraction_from_microdata():
    page = (
        '<html><body itemscope itemtype="https://schema.org/Product">'
        f'<meta itemprop="gtin13" content="{EAN}">'
        '<span itemprop="sku">REF-42</span>'
        '<span itemprop="brand">Nintendo</span>'
        "</body></html>"
    )
    identifiers, attributes = extract(page)
    assert identifiers.ean == EAN
    assert identifiers.manufacturer_sku == "REF-42"
    assert attributes.brand == "Nintendo"


def test_extraction_from_meta_tags():
    page = (
        "<html><head>"
        f'<meta property="product:ean" content="{EAN}">'
        '<meta property="product:mfr_part_no" content="MPN-9">'
        "</head><body></body></html>"
    )
    identifiers, _ = extract(page)
    assert identifiers.ean == EAN
    assert identifiers.mpn == "MPN-9"


def test_invalid_gtin_is_discarded_not_stored():
    """Un code faux ne doit jamais devenir une clé de corrélation."""
    page = (
        '<html><head><script type="application/ld+json">'
        '{"@type": "Product", "name": "X", "gtin13": "0000000000000"}'
        "</script></head><body></body></html>"
    )
    identifiers, _ = extract(page)
    assert identifiers.ean is None


def test_broken_page_never_raises():
    identifiers, attributes = extract("<html><script type='application/ld+json'>{{{")
    assert identifiers.is_empty
    assert attributes.brand is None
