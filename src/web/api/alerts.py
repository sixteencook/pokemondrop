"""Routes alertes (historique paginé, filtrable)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from src.services.screenshots import storage
from src.web.deps import get_ctx
from src.web.schemas import Page, PageParams, SortParams, page_params, sort_params
from src.web.schemas.monitoring import AlertOut
from src.web.state import AppContext

router = APIRouter(prefix="/alerts", tags=["Alertes"])


@router.get(
    "",
    response_model=Page[AlertOut],
    summary="Historique des alertes",
    description="Alertes envoyées, enrichies du nom et du site du produit. "
                "Filtres : produit, type d'événement, site, statut d'envoi. "
                "Tri : created_at, change_type.",
)
async def list_alerts(
    ctx: AppContext = Depends(get_ctx),
    pagination: PageParams = Depends(page_params),
    sorting: SortParams = Depends(sort_params),
    product_uuid: Optional[str] = Query(None),
    change_type: Optional[str] = Query(None, description="Ex. preorder_opened, back_in_stock"),
    site: Optional[str] = Query(None),
    notified: Optional[bool] = Query(None),
) -> Page[AlertOut]:
    rows, total = await ctx.alerts.list_page(
        page=pagination.page, page_size=pagination.page_size,
        sort=sorting.sort, order=sorting.order,
        product_uuid=product_uuid, change_type=change_type,
        site=site, notified=notified,
    )
    items = [AlertOut.from_domain(record, name, site_) for record, name, site_ in rows]
    return Page.build(items, total, pagination)


@router.get(
    "/{alert_id}/screenshot",
    summary="Capture d'écran d'une alerte",
    description="Renvoie l'image PNG capturée au moment de l'alerte. "
                "Ajouter `?download=true` pour forcer le téléchargement.",
    responses={
        200: {"content": {"image/png": {}}, "description": "Image de la capture"},
        404: {"description": "Alerte inconnue, ou aucune capture disponible"},
    },
    response_class=FileResponse,
)
async def alert_screenshot(
    alert_id: int,
    ctx: AppContext = Depends(get_ctx),
    download: bool = Query(False, description="Forcer le téléchargement"),
) -> FileResponse:
    record = await ctx.alerts.get(alert_id)
    if record is None or not record.screenshot_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune capture disponible pour cette alerte.",
        )

    # `resolve` refuse toute cible hors du dossier des captures.
    path = storage.resolve(ctx.settings.screenshots.directory, record.screenshot_path)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fichier de capture introuvable (supprimé ou purgé).",
        )
    return FileResponse(
        path,
        media_type="image/png",
        filename=path.name if download else None,
        content_disposition_type="attachment" if download else "inline",
    )
