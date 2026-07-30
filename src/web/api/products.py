"""Routes produits : CRUD à chaud, vérification immédiate, timeline."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.models import Priority, ProductConfig
from src.web.deps import get_ctx
from src.web.schemas import (
    CheckNowOut,
    Page,
    PageParams,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    SortParams,
    page_params,
    sort_params,
)
from src.web.schemas.monitoring import TimelineEntryOut
from src.web.state import AppContext

router = APIRouter(prefix="/products", tags=["Produits"])


def _ensure_known_site(ctx: AppContext, site: str) -> None:
    if site not in ctx.registry.known_sites:
        raise HTTPException(
            status_code=422,
            detail=f"Site inconnu « {site} ». Plugins chargés : "
                   f"{', '.join(ctx.registry.known_sites)}",
        )


async def _get_or_404(ctx: AppContext, uuid: str) -> ProductConfig:
    product = await ctx.products.get(uuid)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Produit introuvable.")
    return product


@router.get(
    "",
    response_model=Page[ProductOut],
    summary="Lister les produits",
    description="Liste paginée, filtrable par site, activation, tag et recherche "
                "plein-texte sur le nom. Tri : name, site, priority, check_interval, "
                "created_at, updated_at.",
)
async def list_products(
    ctx: AppContext = Depends(get_ctx),
    pagination: PageParams = Depends(page_params),
    sorting: SortParams = Depends(sort_params),
    site: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
    tag: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Recherche sur le nom"),
) -> Page[ProductOut]:
    items, total = await ctx.products.list_page(
        page=pagination.page, page_size=pagination.page_size,
        sort=sorting.sort, order=sorting.order,
        site=site, enabled=enabled, tag=tag, search=search,
    )
    snapshots = await ctx.snapshots.load_many([p.uuid for p in items])
    return Page.build(
        [ProductOut.from_domain(p, snapshots.get(p.uuid)) for p in items],
        total, pagination,
    )


@router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un produit",
    description="Le produit est pris en compte À CHAUD : si activé avec une URL, "
                "sa surveillance démarre en quelques secondes, sans redémarrage.",
)
async def create_product(
    body: ProductCreate, ctx: AppContext = Depends(get_ctx)
) -> ProductOut:
    _ensure_known_site(ctx, body.site)
    created = await ctx.products.create(ProductConfig(
        name=body.name,
        site=body.site,
        url=body.url.strip(),
        check_interval=body.check_interval,
        enabled=body.enabled,
        group=body.group,
        priority=body.priority,
        tags=tuple(body.tags),
    ))
    return ProductOut.from_domain(created)


@router.get("/{uuid}", response_model=ProductOut, summary="Détail d'un produit")
async def get_product(uuid: str, ctx: AppContext = Depends(get_ctx)) -> ProductOut:
    product = await _get_or_404(ctx, uuid)
    return ProductOut.from_domain(product, await ctx.snapshots.load(uuid))


@router.patch(
    "/{uuid}",
    response_model=ProductOut,
    summary="Modifier un produit",
    description="Modification partielle (URL, intervalle, activation, site, "
                "priorité, tags…). Appliquée à chaud par le moteur. "
                "L'uuid est immuable.",
)
async def update_product(
    uuid: str, body: ProductUpdate, ctx: AppContext = Depends(get_ctx)
) -> ProductOut:
    await _get_or_404(ctx, uuid)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="Aucun champ à modifier.")
    if "site" in fields:
        _ensure_known_site(ctx, fields["site"])
    if "priority" in fields and isinstance(fields["priority"], Priority):
        fields["priority"] = fields["priority"].value
    updated = await ctx.products.update(uuid, **fields)
    return ProductOut.from_domain(updated)


@router.delete(
    "/{uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un produit",
    description="Supprime le produit ET tout son historique (checks, timeline, "
                "alertes, snapshot). Sa surveillance s'arrête à chaud.",
)
async def delete_product(uuid: str, ctx: AppContext = Depends(get_ctx)) -> None:
    await _get_or_404(ctx, uuid)
    await ctx.products.delete(uuid)
    await ctx.snapshots.delete(uuid)
    await ctx.checks.purge_product(uuid)
    await ctx.timeline.purge_product(uuid)
    await ctx.alerts.purge_product(uuid)


@router.post(
    "/{uuid}/check",
    response_model=CheckNowOut,
    summary="Vérifier maintenant",
    description="Force une vérification immédiate, hors cycle. Les changements "
                "détectés déclenchent les alertes normalement.",
)
async def check_now(uuid: str, ctx: AppContext = Depends(get_ctx)) -> CheckNowOut:
    product = await _get_or_404(ctx, uuid)
    if not product.url.strip():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="URL vide : la fiche produit n'est pas encore publiée.")
    snapshot = await ctx.engine.check_now(product)
    if snapshot is None:
        return CheckNowOut(status="error")
    return CheckNowOut(
        status="ok",
        availability=snapshot.availability.value,
        price=snapshot.price,
        page_exists=snapshot.page_exists,
        checked_at=snapshot.checked_at,
    )


@router.get(
    "/{uuid}/timeline",
    response_model=Page[TimelineEntryOut],
    summary="Timeline d'un produit",
    description="Historique complet du produit (baseline, prix, précommande, "
                "ruptures, retours en stock…), du plus récent au plus ancien.",
)
async def product_timeline(
    uuid: str,
    ctx: AppContext = Depends(get_ctx),
    pagination: PageParams = Depends(page_params),
    sorting: SortParams = Depends(sort_params),
    event_type: Optional[str] = Query(None),
) -> Page[TimelineEntryOut]:
    await _get_or_404(ctx, uuid)
    items, total = await ctx.timeline.list_page(
        page=pagination.page, page_size=pagination.page_size,
        sort=sorting.sort, order=sorting.order,
        product_uuid=uuid, event_type=event_type,
    )
    return Page.build([TimelineEntryOut.from_domain(e) for e in items], total, pagination)
