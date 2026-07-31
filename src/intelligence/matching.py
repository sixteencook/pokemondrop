"""Moteur de corrélation produit.

Le principe : une LISTE DE STRATÉGIES ordonnée par confiance décroissante.
La première qui répond gagne — pas de cascade de `if`, et surtout pas de
règle codée en dur dans le moteur.

    100  EAN identique
     98  UPC identique
     96  ISBN identique
     95  MPN identique
     93  ASIN identique
     92  SKU constructeur identique
     90  Référence constructeur identique
     88  Numéro de modèle identique
     85  Nom normalisé + marque
     80  Nom + date de sortie
     75  Nom + collection
     70  Nom normalisé seul

AJOUTER UNE MÉTHODE PLUS TARD (OCR, embeddings, similarité visuelle,
recherche inversée d'image, comparaison de packaging) revient à écrire une
classe respectant `MatchStrategy` et à l'insérer dans la liste, à sa place
dans l'échelle de confiance. Rien d'autre ne bouge : ni le moteur, ni les
plugins, ni la base.

Une stratégie peut être asynchrone et faire des appels réseau ou GPU :
`find()` est une coroutine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence, runtime_checkable

from src.intelligence.entities import CanonicalProduct, ProductDraft
from src.intelligence.naming import name_key, similarity

#: Similarité minimale pour considérer deux noms comme « proches ».
NAME_SIMILARITY_FLOOR = 0.82


@dataclass(frozen=True)
class MatchResult:
    """Rapprochement proposé par une stratégie."""

    product: CanonicalProduct
    score: int
    method: str
    reason: str


@runtime_checkable
class MatchStrategy(Protocol):
    """Contrat d'une méthode de corrélation."""

    name: str
    score: int

    async def find(
        self, draft: ProductDraft, candidates: Sequence[CanonicalProduct]
    ) -> Optional[MatchResult]: ...


class _IdentifierStrategy:
    """Égalité stricte sur un identifiant fort."""

    def __init__(self, name: str, score: int, attribute: str, label: str) -> None:
        self.name = name
        self.score = score
        self._attribute = attribute
        self._label = label

    async def find(
        self, draft: ProductDraft, candidates: Sequence[CanonicalProduct]
    ) -> Optional[MatchResult]:
        value = getattr(draft.identifiers, self._attribute, None)
        if not value:
            return None
        for product in candidates:
            if getattr(product.identifiers, self._attribute, None) == value:
                return MatchResult(
                    product=product, score=self.score, method=self.name,
                    reason=f"{self._label} identique ({value})",
                )
        return None


class _IdentityFieldStrategy:
    """Égalité stricte sur un champ du profil d'identité v2.

    Couvre les clés qui n'existent pas dans `identifiers` : ASIN, numéro de
    modèle, et toute clé ajoutée plus tard — il suffit de nommer le champ.
    """

    def __init__(self, name: str, score: int, field: str, label: str) -> None:
        self.name = name
        self.score = score
        self._field = field
        self._label = label

    async def find(
        self, draft: ProductDraft, candidates: Sequence[CanonicalProduct]
    ) -> Optional[MatchResult]:
        value = draft.identity.get(self._field)
        if not value:
            return None
        for product in candidates:
            if product.identity.get(self._field) == value:
                return MatchResult(
                    product=product, score=self.score, method=self.name,
                    reason=f"{self._label} identique ({value})",
                )
        return None


class NameBrandStrategy:
    """Nom normalisé identique ET même marque."""

    name = "name_brand"
    score = 85

    async def find(
        self, draft: ProductDraft, candidates: Sequence[CanonicalProduct]
    ) -> Optional[MatchResult]:
        brand = (draft.attributes.brand or "").strip().lower()
        if not brand:
            return None
        key = name_key(draft.name)
        for product in candidates:
            if product.name_key != key:
                continue
            if (product.attributes.brand or "").strip().lower() == brand:
                return MatchResult(
                    product=product, score=self.score, method=self.name,
                    reason=f"nom normalisé et marque identiques ({brand})",
                )
        return None


class NameReleaseDateStrategy:
    """Nom proche ET même date de sortie."""

    name = "name_release_date"
    score = 80

    async def find(
        self, draft: ProductDraft, candidates: Sequence[CanonicalProduct]
    ) -> Optional[MatchResult]:
        release = draft.attributes.release_date
        if not release:
            return None
        for product in candidates:
            if product.attributes.release_date != release:
                continue
            if similarity(draft.name, product.name) >= NAME_SIMILARITY_FLOOR:
                return MatchResult(
                    product=product, score=self.score, method=self.name,
                    reason=f"nom proche et sortie identique ({release})",
                )
        return None


class NameCollectionStrategy:
    """Nom proche ET même collection."""

    name = "name_collection"
    score = 75

    async def find(
        self, draft: ProductDraft, candidates: Sequence[CanonicalProduct]
    ) -> Optional[MatchResult]:
        collection = (draft.attributes.collection or "").strip().lower()
        if not collection:
            return None
        for product in candidates:
            if (product.attributes.collection or "").strip().lower() != collection:
                continue
            if similarity(draft.name, product.name) >= NAME_SIMILARITY_FLOOR:
                return MatchResult(
                    product=product, score=self.score, method=self.name,
                    reason=f"nom proche et collection identique ({collection})",
                )
        return None


class NameOnlyStrategy:
    """Dernier recours : clé de nom identique, sans autre confirmation.

    Score volontairement bas — sous le seuil de fusion par défaut, ce
    rapprochement part en validation manuelle.
    """

    name = "name_only"
    score = 70

    async def find(
        self, draft: ProductDraft, candidates: Sequence[CanonicalProduct]
    ) -> Optional[MatchResult]:
        key = name_key(draft.name)
        for product in candidates:
            if product.name_key == key:
                return MatchResult(
                    product=product, score=self.score, method=self.name,
                    reason="nom normalisé identique, aucun identifiant commun",
                )
        return None


def default_strategies() -> list[MatchStrategy]:
    """Échelle de confiance par défaut, du plus sûr au plus incertain."""
    return [
        _IdentifierStrategy("ean", 100, "ean", "EAN"),
        _IdentifierStrategy("upc", 98, "upc", "UPC"),
        _IdentifierStrategy("isbn", 96, "isbn", "ISBN"),
        _IdentifierStrategy("mpn", 95, "mpn", "MPN"),
        _IdentityFieldStrategy("asin", 93, "asin", "ASIN"),
        _IdentifierStrategy("manufacturer_sku", 92, "manufacturer_sku",
                            "SKU constructeur"),
        _IdentifierStrategy("manufacturer_ref", 90, "manufacturer_ref",
                            "référence constructeur"),
        _IdentityFieldStrategy("model_number", 88, "model_number",
                               "numéro de modèle"),
        NameBrandStrategy(),
        NameReleaseDateStrategy(),
        NameCollectionStrategy(),
        NameOnlyStrategy(),
    ]


class MatchingEngine:
    """Applique les stratégies dans l'ordre et rend le meilleur résultat."""

    def __init__(self, strategies: Optional[Sequence[MatchStrategy]] = None) -> None:
        self._strategies = sorted(
            strategies if strategies is not None else default_strategies(),
            key=lambda strategy: strategy.score,
            reverse=True,
        )

    @property
    def methods(self) -> list[str]:
        return [f"{s.name} ({s.score})" for s in self._strategies]

    async def match(
        self, draft: ProductDraft, candidates: Sequence[CanonicalProduct]
    ) -> Optional[MatchResult]:
        """Premier rapprochement trouvé, par confiance décroissante."""
        for strategy in self._strategies:
            result = await strategy.find(draft, candidates)
            if result is not None:
                return result
        return None
