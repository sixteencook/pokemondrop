"""Schémas Pydantic de l'API v1.

Contrat public de l'API : les modèles SQLAlchemy et les dataclasses
internes ne sont JAMAIS exposés directement. Toute évolution interne
(schéma SQL, moteur) reste invisible du frontend tant que ces schémas
sont stables.
"""

from .common import Page, PageParams, SortParams, page_params, sort_params
from .products import CheckNowOut, ProductCreate, ProductOut, ProductUpdate

__all__ = [
    "CheckNowOut",
    "Page",
    "PageParams",
    "ProductCreate",
    "ProductOut",
    "ProductUpdate",
    "SortParams",
    "page_params",
    "sort_params",
]
