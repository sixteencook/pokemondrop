"""Moteur de règles d'inclusion / exclusion.

Entièrement piloté par la configuration (config/discovery.yaml) : aucune
règle n'est codée en dur, aucun terme métier n'apparaît dans le cœur.

Ordre d'évaluation, volontairement simple et prévisible :

    1. une règle d'EXCLUSION correspond      → rejeté (motif conservé)
    2. aucune règle d'inclusion configurée   → accepté
    3. une règle d'INCLUSION correspond      → accepté (motif conservé)
    4. sinon                                 → non retenu

L'exclusion prime toujours : « Pokémon » + « occasion » reste rejeté.

La comparaison réutilise `normalise()` du parser : accents repliés, casse
et espaces uniformisés — « Pokémon » et « pokemon » sont équivalents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from src.discovery.contracts import DiscoveredProduct
from src.monitors.generic import normalise


@dataclass(frozen=True)
class RuleMatch:
    """Décision motivée, exploitable dans le dashboard et les logs."""

    accepted: bool
    excluded: bool = False
    reason: str = ""
    matched: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleSet:
    """Jeu de règles appliqué au titre et à l'URL d'une fiche."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    url_include: tuple[str, ...] = ()
    url_exclude: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, raw: dict | None) -> "RuleSet":
        raw = raw or {}
        return cls(
            include=_clean(raw.get("include")),
            exclude=_clean(raw.get("exclude")),
            url_include=_clean(raw.get("url_include")),
            url_exclude=_clean(raw.get("url_exclude")),
        )

    @property
    def has_inclusion(self) -> bool:
        return bool(self.include or self.url_include)

    def evaluate(self, product: DiscoveredProduct) -> RuleMatch:
        title = normalise(product.title)
        url = normalise(product.url)

        hits = _hits(self.exclude, title)
        if hits:
            return RuleMatch(False, True, f"titre exclu : {', '.join(hits)}", hits)

        hits = _hits(self.url_exclude, url)
        if hits:
            return RuleMatch(False, True, f"URL exclue : {', '.join(hits)}", hits)

        if not self.has_inclusion:
            return RuleMatch(True, reason="aucune règle d'inclusion configurée")

        hits = _hits(self.include, title) + _hits(self.url_include, url)
        if hits:
            return RuleMatch(True, reason=f"correspond à : {', '.join(hits)}",
                             matched=hits)

        return RuleMatch(False, reason="ne correspond à aucune règle d'inclusion")


def _clean(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    cleaned = [normalise(str(value)) for value in values if str(value).strip()]
    return tuple(dict.fromkeys(cleaned))


def _hits(patterns: Sequence[str], haystack: str) -> tuple[str, ...]:
    return tuple(pattern for pattern in patterns if pattern in haystack)
