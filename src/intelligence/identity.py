"""Identité produit — profil enrichi progressivement par tous les marchands.

Chaque champ porte sa propre CONFIANCE et sa SOURCE : un EAN lu dans un
JSON-LD schema.org vaut mieux qu'une marque devinée depuis un titre, et il
faut pouvoir le savoir.

L'identité est immuable : l'enrichir produit une nouvelle instance. Une
information de meilleure confiance remplace la précédente ; à confiance
égale, la première trouvée est conservée (on ne fait pas osciller une
identité entre deux sources qui se valent).

C'est cette identité — et non plus une clé unique — que le moteur donne
aux plugins pour chercher le produit chez les autres enseignes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator, Mapping, Optional

#: Champs d'identité reconnus, avec la confiance par défaut de leur source.
IDENTITY_FIELDS: tuple[str, ...] = (
    "ean", "upc", "isbn", "gtin", "asin",
    "sku", "mpn", "manufacturer_part_number", "model_number",
    "brand", "manufacturer", "collection", "edition", "release_date",
    "canonical_name", "primary_image",
)


@dataclass(frozen=True)
class IdentityField:
    """Une information, sa confiance et son origine."""

    value: str
    confidence: int = 100
    source: str = ""          # site ou stratégie ayant fourni l'information

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", max(0, min(100, self.confidence)))


@dataclass(frozen=True)
class ProductIdentity:
    """Profil d'identité d'un produit, indépendant de tout marchand."""

    fields: Mapping[str, IdentityField] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    additional_images: tuple[str, ...] = ()

    # ------------------------------------------------------------------ #
    # Lecture                                                             #
    # ------------------------------------------------------------------ #

    def get(self, name: str) -> Optional[str]:
        entry = self.fields.get(name)
        return entry.value if entry else None

    def confidence_of(self, name: str) -> int:
        entry = self.fields.get(name)
        return entry.confidence if entry else 0

    def source_of(self, name: str) -> str:
        entry = self.fields.get(name)
        return entry.source if entry else ""

    def __iter__(self) -> Iterator[tuple[str, IdentityField]]:
        return iter(self.fields.items())

    @property
    def is_empty(self) -> bool:
        return not self.fields and not self.aliases

    @property
    def known_fields(self) -> tuple[str, ...]:
        return tuple(name for name in IDENTITY_FIELDS if name in self.fields)

    # Accès direct aux champs les plus utilisés.
    ean = property(lambda self: self.get("ean"))
    upc = property(lambda self: self.get("upc"))
    isbn = property(lambda self: self.get("isbn"))
    gtin = property(lambda self: self.get("gtin"))
    asin = property(lambda self: self.get("asin"))
    sku = property(lambda self: self.get("sku"))
    mpn = property(lambda self: self.get("mpn"))
    manufacturer_part_number = property(
        lambda self: self.get("manufacturer_part_number")
    )
    model_number = property(lambda self: self.get("model_number"))
    brand = property(lambda self: self.get("brand"))
    manufacturer = property(lambda self: self.get("manufacturer"))
    collection = property(lambda self: self.get("collection"))
    edition = property(lambda self: self.get("edition"))
    release_date = property(lambda self: self.get("release_date"))
    canonical_name = property(lambda self: self.get("canonical_name"))
    primary_image = property(lambda self: self.get("primary_image"))

    # ------------------------------------------------------------------ #
    # Enrichissement                                                      #
    # ------------------------------------------------------------------ #

    def with_field(
        self, name: str, value: Optional[str], confidence: int = 100, source: str = ""
    ) -> "ProductIdentity":
        """Ajoute ou améliore un champ. Retourne une nouvelle identité."""
        if not value or not str(value).strip():
            return self
        candidate = IdentityField(str(value).strip(), confidence, source)
        existing = self.fields.get(name)
        if existing is not None and existing.confidence >= candidate.confidence:
            return self          # l'information connue vaut mieux ou autant
        return replace(self, fields={**self.fields, name: candidate})

    def with_alias(self, *names: str) -> "ProductIdentity":
        """Ajoute des noms alternatifs (titres vus chez d'autres marchands)."""
        cleaned = [" ".join(str(name).split()) for name in names if name and str(name).strip()]
        if not cleaned:
            return self
        merged = tuple(dict.fromkeys((*self.aliases, *cleaned)))
        return replace(self, aliases=merged)

    def with_images(self, *urls: str) -> "ProductIdentity":
        cleaned = [url for url in urls if url and url.startswith("http")]
        if not cleaned:
            return self
        merged = tuple(dict.fromkeys((*self.additional_images, *cleaned)))
        return replace(self, additional_images=merged)

    def merged_with(self, other: "ProductIdentity") -> "ProductIdentity":
        """Fusionne deux identités, champ par champ, confiance la plus haute."""
        result = self
        for name, entry in other.fields.items():
            result = result.with_field(name, entry.value, entry.confidence, entry.source)
        result = result.with_alias(*other.aliases)
        return result.with_images(*other.additional_images)

    # ------------------------------------------------------------------ #
    # Sérialisation                                                       #
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {
            "fields": {
                name: {
                    "value": entry.value,
                    "confidence": entry.confidence,
                    "source": entry.source,
                }
                for name, entry in self.fields.items()
            },
            "aliases": list(self.aliases),
            "additional_images": list(self.additional_images),
        }

    @classmethod
    def from_dict(cls, payload: dict | None) -> "ProductIdentity":
        payload = payload or {}
        fields = {
            name: IdentityField(
                value=str(entry.get("value", "")),
                confidence=int(entry.get("confidence", 100)),
                source=str(entry.get("source", "")),
            )
            for name, entry in (payload.get("fields") or {}).items()
            if entry.get("value")
        }
        return cls(
            fields=fields,
            aliases=tuple(payload.get("aliases") or ()),
            additional_images=tuple(payload.get("additional_images") or ()),
        )

    @classmethod
    def build(cls, source: str = "", **values: Optional[str]) -> "ProductIdentity":
        """Raccourci de construction : ProductIdentity.build(ean=…, brand=…)."""
        identity = cls()
        for name, value in values.items():
            identity = identity.with_field(name, value, source=source)
        return identity


def aliases_from(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(" ".join(name.split()) for name in names if name))
