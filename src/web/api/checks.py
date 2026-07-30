"""Route checks (historique des vérifications)."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from src.web.deps import get_ctx
from src.web.schemas import Page, PageParams, SortParams, page_params, sort_params
from src.web.schemas.monitoring import CheckOut
from src.web.state import AppContext

router = APIRouter(prefix="/checks", tags=["Checks"])


@router.get(
    "",
    response_model=Page[CheckOut],
    summary="Historique des vérifications",
    description="Chaque check effectué (statut, disponibilité, temps de réponse). "
                "Tri : checked_at, response_time_ms, status.",
)
async def list_checks(
    ctx: AppContext = Depends(get_ctx),
    pagination: PageParams = Depends(page_params),
    sorting: SortParams = Depends(sort_params),
    product_uuid: Optional[str] = Query(None),
    status: Optional[Literal["ok", "error"]] = Query(None),
) -> Page[CheckOut]:
    items, total = await ctx.checks.list_page(
        page=pagination.page, page_size=pagination.page_size,
        sort=sorting.sort, order=sorting.order,
        product_uuid=product_uuid, status=status,
    )
    return Page.build([CheckOut.from_domain(c) for c in items], total, pagination)
