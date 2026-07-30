"""Route timeline globale (flux d'activité tous produits)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.web.deps import get_ctx
from src.web.schemas import Page, PageParams, SortParams, page_params, sort_params
from src.web.schemas.monitoring import TimelineEntryOut
from src.web.state import AppContext

router = APIRouter(prefix="/timeline", tags=["Timeline"])


@router.get(
    "",
    response_model=Page[TimelineEntryOut],
    summary="Flux d'activité global",
    description="Tous les événements de tous les produits (la timeline d'un "
                "produit précis est sur /products/{uuid}/timeline).",
)
async def list_timeline(
    ctx: AppContext = Depends(get_ctx),
    pagination: PageParams = Depends(page_params),
    sorting: SortParams = Depends(sort_params),
    product_uuid: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
) -> Page[TimelineEntryOut]:
    items, total = await ctx.timeline.list_page(
        page=pagination.page, page_size=pagination.page_size,
        sort=sorting.sort, order=sorting.order,
        product_uuid=product_uuid, event_type=event_type,
    )
    return Page.build([TimelineEntryOut.from_domain(e) for e in items], total, pagination)
