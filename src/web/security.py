"""Authentification : identifiants en variables d'environnement, JWT signé
(HS256) posé en cookie httpOnly.

Un seul utilisateur (DASHBOARD_USERNAME / DASHBOARD_PASSWORD) — suffisant
pour un dashboard personnel, extensible plus tard vers une table users.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

COOKIE_NAME = "dm_token"

#: Clé éphémère si SECRET_KEY absent du .env : les sessions ne survivent
#: alors pas à un redémarrage du serveur (un warning est loggé au démarrage).
_EPHEMERAL_SECRET = secrets.token_hex(32)


def effective_secret(configured: str) -> str:
    return configured or _EPHEMERAL_SECRET


def verify_credentials(
    username: str, password: str, expected_username: str, expected_password: str
) -> bool:
    """Comparaison en temps constant (anti timing attack)."""
    user_ok = hmac.compare_digest(username.encode(), expected_username.encode())
    pass_ok = hmac.compare_digest(password.encode(), expected_password.encode())
    return user_ok and pass_ok


def create_token(username: str, secret: str, ttl_hours: int) -> str:
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> Optional[str]:
    """Retourne le username si le token est valide et non expiré, sinon None."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
