"""Schémas produits (requêtes et réponses)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from src.models import Priority, ProductConfig, ProductSnapshot


class ProductOut(BaseModel):
    """Représentation publique d'un produit surveillé, enrichie de son
    dernier état connu (disponibilité, prix, dernier check)."""

    uuid: str = Field(description="Identifiant interne immuable")
    name: str
    site: str = Field(description="Identifiant du plugin (micromania, fnac…)")
    url: str = Field(description="URL de la fiche produit (vide si pas encore publiée)")
    group: Optional[str] = Field(None, description="Clé de regroupement multi-sites")
    check_interval: int = Field(description="Intervalle de vérification (secondes)")
    enabled: bool
    priority: Priority
    tags: list[str]
    monitorable: bool = Field(description="True si le produit est activé ET possède une URL")
    availability: Optional[str] = Field(None, description="Dernier statut connu "
                                                          "(preorder, in_stock, unavailable…)")
    price: Optional[str] = Field(None, description="Dernier prix connu")
    last_checked_at: Optional[str] = Field(None, description="Horodatage du dernier check")

    @classmethod
    def from_domain(
        cls, product: ProductConfig, snapshot: Optional[ProductSnapshot] = None
    ) -> "ProductOut":
        return cls(
            uuid=product.uuid,
            name=product.name,
            site=product.site,
            url=product.url,
            group=product.group,
            check_interval=product.check_interval,
            enabled=product.enabled,
            priority=product.priority,
            tags=list(product.tags),
            monitorable=product.is_monitorable,
            availability=snapshot.availability.value if snapshot else None,
            price=snapshot.price if snapshot else None,
            last_checked_at=snapshot.checked_at if snapshot else None,
        )


class _ProductFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    site: str = Field(min_length=1, max_length=50,
                      description="Doit correspondre à un plugin chargé")
    url: str = Field("", max_length=2000)
    group: Optional[str] = Field(None, max_length=100)
    check_interval: int = Field(60, ge=10, le=86400,
                                description="Secondes (min 10 : ne pas spammer les sites)")
    enabled: bool = False
    priority: Priority = Priority.NORMAL
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("site")
    @classmethod
    def _lower_site(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        cleaned = [tag.strip().lower() for tag in value if tag.strip()]
        return list(dict.fromkeys(cleaned))


class ProductCreate(_ProductFields):
    """Corps de POST /products."""


class ProductUpdate(BaseModel):
    """Corps de PATCH /products/{uuid} — tous les champs optionnels."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    site: Optional[str] = Field(None, min_length=1, max_length=50)
    url: Optional[str] = Field(None, max_length=2000)
    group: Optional[str] = Field(None, max_length=100)
    check_interval: Optional[int] = Field(None, ge=10, le=86400)
    enabled: Optional[bool] = None
    priority: Optional[Priority] = None
    tags: Optional[list[str]] = Field(None, max_length=20)

    @field_validator("site")
    @classmethod
    def _lower_site(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().lower() if value else value

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        cleaned = [tag.strip().lower() for tag in value if tag.strip()]
        return list(dict.fromkeys(cleaned))


class CheckNowOut(BaseModel):
    """Résultat de POST /products/{uuid}/check."""

    status: str = Field(description="ok | error")
    availability: Optional[str] = None
    price: Optional[str] = None
    page_exists: Optional[bool] = None
    checked_at: Optional[str] = None
