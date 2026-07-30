"""Routes paramètres : lecture de la configuration, diagnostic Telegram."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.services import send_test_alert, telegram_status
from src.web.deps import get_ctx
from src.web.schemas.system import (
    ScreenshotSettingsOut,
    SettingsOut,
    TelegramSettingsOut,
    TelegramStatusOut,
    TelegramTestOut,
)
from src.web.state import AppContext

router = APIRouter(prefix="/settings", tags=["Paramètres"])


@router.get(
    "",
    response_model=SettingsOut,
    summary="Configuration courante",
    description="Vue en lecture seule de la configuration (les valeurs sensibles "
                "sont masquées). La configuration se modifie via les variables "
                "d'environnement.",
)
async def get_settings(ctx: AppContext = Depends(get_ctx)) -> SettingsOut:
    token = ctx.settings.telegram_bot_token
    shots = ctx.settings.screenshots
    return SettingsOut(
        telegram=TelegramSettingsOut(
            configured=ctx.settings.telegram_configured,
            chat_count=len(ctx.settings.telegram_chat_ids),
            token_preview=f"…{token[-4:]}" if token else None,
        ),
        screenshots=ScreenshotSettingsOut(
            enabled=shots.enabled,
            available=ctx.screenshots.enabled,
            timeout_ms=shots.timeout_ms,
            quality=shots.quality,
            max_concurrent=shots.max_concurrent,
            retention_days=shots.retention_days,
            image_format=shots.image_format,
            full_page=shots.full_page,
            directory=str(shots.directory),
            pending=ctx.screenshots.pending_count,
        ),
        log_level=ctx.settings.log_level,
        database=ctx.settings.database_url.split("+")[0].split(":")[0],
        data_dir=str(ctx.settings.data_dir),
        auth_configured=ctx.settings.auth_configured,
    )


@router.get(
    "/telegram/status",
    response_model=TelegramStatusOut,
    summary="État du bot Telegram",
    description="Vérifie que le bot répond (getMe) et que chaque Chat ID est "
                "joignable (getChat), sans envoyer de message.",
)
async def get_telegram_status(ctx: AppContext = Depends(get_ctx)) -> TelegramStatusOut:
    return TelegramStatusOut(**await telegram_status(ctx.settings, ctx.client))


@router.post(
    "/telegram/test",
    response_model=TelegramTestOut,
    summary="Envoyer une notification de test",
    description="Envoie une fausse alerte à tous les destinataires configurés.",
)
async def post_telegram_test(ctx: AppContext = Depends(get_ctx)) -> TelegramTestOut:
    if not ctx.settings.telegram_configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram non configuré : définissez TELEGRAM_BOT_TOKEN et "
                   "TELEGRAM_CHAT_ID(S).",
        )
    sent = await send_test_alert(ctx.settings, ctx.client)
    return TelegramTestOut(sent=sent, recipients=len(ctx.settings.telegram_chat_ids))
