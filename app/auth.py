"""Authentification : mots de passe PBKDF2-SHA256, sessions en base, cookie HttpOnly."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, HTTPException, Request

from . import db

COOKIE = "cp_session"
SESSION_DAYS = 14
ITERATIONS = 240_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Limite simple anti-force brute : 10 tentatives / 10 min par IP + e-mail.
_attempts: dict = defaultdict(deque)
WINDOW_S, MAX_ATTEMPTS = 600, 10


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(test, digest)
    except (ValueError, AttributeError):
        return False


def check_credentials_format(email: str, password: str) -> None:
    if not EMAIL_RE.match(email or ""):
        raise HTTPException(422, "Adresse e-mail invalide.")
    if len(password or "") < 8:
        raise HTTPException(422, "Le mot de passe doit contenir au moins 8 caractères.")


def rate_limit(key: str) -> None:
    q = _attempts[key]
    t = time.time()
    while q and t - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        raise HTTPException(429, "Trop de tentatives. Réessaie dans quelques minutes.")
    q.append(t)


def create_session(conn, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO sessions(token, user_id, expires_at) VALUES (?,?,?)", (token, user_id, expires))
    return token


def set_cookie(response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=False, path="/")


def current_user(request: Request, cp_session: str = Cookie(default=None)) -> dict:
    """Dépendance FastAPI : renvoie l'utilisateur connecté ou 401."""
    if not cp_session:
        raise HTTPException(401, "Non connecté.")
    with db.tx() as c:
        r = c.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                         WHERE s.token = ? AND s.expires_at > ?""", (cp_session, db.now())).fetchone()
    if r is None:
        raise HTTPException(401, "Session expirée.")
    user = db.row(r)
    user.pop("password_hash", None)
    return user
