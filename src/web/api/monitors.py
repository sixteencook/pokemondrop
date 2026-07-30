"""Route monitors (plugins chargés et leurs agrégats)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.web.deps import get_ctx
from src.web.schemas.system import MonitorOut
from src.web.state import AppContext

router = APIRouter(prefix="/monitors", tags=["Monitors"])


@router.get(
    "",
    response_model=list[MonitorOut],
    summary="Plugins de sites chargés",
    description="Un élément par plugin découvert au démarrage : identité, "
                "produits rattachés, dernier check, dernière erreur, temps de "
                "réponse moyen (24 h).",
)
async def list_monitors(ctx: AppContext = Depends(get_ctx)) -> list[MonitorOut]:
    return [MonitorOut(**entry) for entry in await ctx.stats.monitors()]
