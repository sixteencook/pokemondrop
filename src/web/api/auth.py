"""Routes d'authentification."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.web.deps import get_ctx, require_auth
from src.web.schemas.system import LoginRequest, UserOut
from src.web.security import COOKIE_NAME, create_token, effective_secret, verify_credentials
from src.web.state import AppContext

router = APIRouter(prefix="/auth", tags=["Authentification"])


@router.post(
    "/login",
    response_model=UserOut,
    summary="Connexion",
    description="Vérifie les identifiants (variables d'environnement) et pose "
                "un cookie httpOnly contenant le jeton de session (JWT).",
)
async def login(
    body: LoginRequest, response: Response, ctx: AppContext = Depends(get_ctx)
) -> UserOut:
    if not ctx.settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentification non configurée : définissez DASHBOARD_USERNAME "
                   "et DASHBOARD_PASSWORD.",
        )
    if not verify_credentials(
        body.username, body.password,
        ctx.settings.dashboard_username, ctx.settings.dashboard_password,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Identifiants invalides.")

    token = create_token(
        body.username,
        effective_secret(ctx.settings.secret_key),
        ctx.settings.token_ttl_hours,
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=bool(os.getenv("RAILWAY_ENVIRONMENT")),  # https en production
        max_age=ctx.settings.token_ttl_hours * 3600,
        path="/",
    )
    return UserOut(username=body.username)


@router.post("/logout", summary="Déconnexion", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, _: str = Depends(require_auth)) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut, summary="Utilisateur connecté")
async def me(username: str = Depends(require_auth)) -> UserOut:
    return UserOut(username=username)
