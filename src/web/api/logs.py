"""Route logs (buffer mémoire des dernières lignes)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from src.utils import get_log_entries
from src.web.schemas import Page, PageParams, page_params
from src.web.schemas.monitoring import LogEntryOut

router = APIRouter(prefix="/logs", tags=["Logs"])


@router.get(
    "",
    response_model=Page[LogEntryOut],
    summary="Logs récents",
    description="Les ~2000 dernières lignes de log (buffer mémoire), du plus "
                "récent au plus ancien. Filtres : niveau (INFO, CHECK, WARN, "
                "ALERTE, ERROR) et recherche plein-texte. L'historique durable "
                "reste dans les fichiers logs/.",
)
async def list_logs(
    pagination: PageParams = Depends(page_params),
    level: Optional[str] = Query(None, description="INFO, CHECK, WARN, ALERTE, ERROR"),
    q: Optional[str] = Query(None, description="Recherche dans le message"),
) -> Page[LogEntryOut]:
    entries = list(reversed(get_log_entries()))  # plus récent d'abord
    if level:
        wanted = level.strip().upper()
        entries = [e for e in entries if e.level == wanted]
    if q:
        needle = q.lower()
        entries = [e for e in entries if needle in e.message.lower()
                   or needle in e.logger.lower()]

    total = len(entries)
    start = (pagination.page - 1) * pagination.page_size
    page_items = entries[start:start + pagination.page_size]
    return Page.build([LogEntryOut.from_domain(e) for e in page_items], total, pagination)
