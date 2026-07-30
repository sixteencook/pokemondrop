"""Dépendances FastAPI : accès au contexte applicatif et authentification."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from src.web.security import COOKIE_NAME, decode_token, effective_secret
from src.web.state import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


async def require_auth(request: Request, ctx: AppContext = Depends(get_ctx)) -> str:
    """Garde d'authentification : cookie httpOnly ou en-tête Bearer.

    Toutes les routes v1 en dépendent, sauf /auth/login et /health.
    """
    if not ctx.settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification non configurée : définissez DASHBOARD_USERNAME "
                   "et DASHBOARD_PASSWORD dans les variables d'environnement.",
        )

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentification requise.")

    username = decode_token(token, effective_secret(ctx.settings.secret_key))
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Session invalide ou expirée.")
    return username
