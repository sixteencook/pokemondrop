"""Pagination, tri et enveloppes communes de l'API v1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Enveloppe standard de toute réponse paginée."""

    items: list[T] = Field(description="Éléments de la page courante")
    total: int = Field(description="Nombre total d'éléments (tous filtres appliqués)")
    page: int = Field(description="Numéro de page (1-indexé)")
    page_size: int = Field(description="Taille de page demandée")
    pages: int = Field(description="Nombre total de pages")

    @classmethod
    def build(cls, items: list[T], total: int, params: "PageParams") -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=max(1, math.ceil(total / params.page_size)),
        )


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int


@dataclass(frozen=True)
class SortParams:
    sort: str
    order: Literal["asc", "desc"]


def page_params(
    page: int = Query(1, ge=1, description="Numéro de page (1-indexé)"),
    page_size: int = Query(25, ge=1, le=200, description="Éléments par page (max 200)"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


def sort_params(
    sort: str = Query("created_at", description="Colonne de tri (whitelist par ressource ; "
                                                "valeur inconnue → tri par défaut)"),
    order: Literal["asc", "desc"] = Query("desc", description="Sens du tri"),
) -> SortParams:
    return SortParams(sort=sort, order=order)
