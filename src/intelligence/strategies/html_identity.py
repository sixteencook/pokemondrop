"""Stratégie fournie : identité extraite du HTML de la fiche.

S'appuie sur ce que les marchands publient déjà pour les moteurs de
recherche — JSON-LD schema.org, microdata, balises meta. Aucune
connaissance d'un site particulier.

Les confiances reflètent la fiabilité de la source : un GTIN validé par sa
clé de contrôle vaut 100, une marque déclarée 90, un nom de page 70.
"""

from __future__ import annotations

import re
from typing import Optional

from src.intelligence.identifiers import extract, normalise_code
from src.intelligence.identity import ProductIdentity
from src.intelligence.strategies import IdentityContext

#: ASIN Amazon : 10 caractères alphanumériques, présents dans l'URL.
_ASIN_RE = re.compile(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})(?:[/?]|$)", re.I)


class HtmlIdentityStrategy:
    """Lit les données structurées de la page produit."""

    name = "html_structured_data"
    priority = 100

    async def enrich(
        self, identity: ProductIdentity, context: IdentityContext
    ) -> Optional[ProductIdentity]:
        result = identity

        # L'identifiant marchand se lit parfois dans l'URL elle-même.
        if context.url:
            match = _ASIN_RE.search(context.url)
            if match:
                result = result.with_field(
                    "asin", match.group(1).upper(), 95, context.site or "url"
                )

        if context.title:
            result = result.with_field(
                "canonical_name", context.title, 70, context.site
            ).with_alias(context.title)

        if not context.html:
            return result

        identifiers, attributes = extract(context.html)
        source = context.site or self.name

        for name, value, confidence in (
            ("ean", identifiers.ean, 100),
            ("upc", identifiers.upc, 100),
            ("isbn", identifiers.isbn, 100),
            ("mpn", identifiers.mpn, 95),
            ("sku", identifiers.manufacturer_sku, 92),
            ("manufacturer_part_number", identifiers.manufacturer_ref, 90),
            ("brand", attributes.brand, 90),
            ("collection", attributes.collection, 80),
            ("edition", attributes.edition, 80),
            ("release_date", attributes.release_date, 90),
            ("primary_image", attributes.image_url, 85),
        ):
            result = result.with_field(name, value, confidence, source)

        # Le GTIN générique complète les cas où seul un code long est publié.
        if identifiers.ean and not result.get("gtin"):
            result = result.with_field("gtin", identifiers.ean, 100, source)

        model = _model_from_html(context.html)
        if model:
            result = result.with_field("model_number", model, 85, source)

        if attributes.image_url:
            result = result.with_images(attributes.image_url)

        return result


_MODEL_RE = re.compile(
    r'itemprop=["\']model["\'][^>]*content=["\']([^"\']{2,60})["\']', re.I
)


def _model_from_html(html: str) -> Optional[str]:
    match = _MODEL_RE.search(html)
    return normalise_code(match.group(1)) if match else None
