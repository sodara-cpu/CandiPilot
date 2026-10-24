"""Authentification : mots de passe PBKDF2-SHA256, sessions en base, cookie HttpOnly,
réinitialisation du mot de passe par lien à usage unique."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, HTTPException, Request

from . import db

COOKIE = "cp_session"
SESSION_DAYS = 14
ITERATIONS = 240_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Mise en ligne (deploy.sh) : cookie HTTPS uniquement, adresse publique pour les liens envoyés.
SECURE_COOKIE = os.environ.get("CANDIPILOT_SECURE_COOKIE") == "1"
PUBLIC_URL = os.environ.get("CANDIPILOT_PUBLIC_URL", "").rstrip("/")
RESET_MINUTES = 60

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
    check_password_strength(password)


def check_password_strength(password: str) -> None:
    if len(password or "") < 8:
        raise HTTPException(422, "Le mot de passe doit contenir au moins 8 caractères.")
    if not (re.search(r"[A-Za-zÀ-ÿ]", password) and re.search(r"\d", password)):
        raise HTTPException(422, "Le mot de passe doit contenir au moins une lettre et un chiffre.")


def client_ip(request: Request) -> str:
    """IP du visiteur ; derrière le tunnel Cloudflare (local), elle est dans CF-Connecting-IP."""
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1"):
        return request.headers.get("cf-connecting-ip", host)
    return host


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
                        samesite="lax", secure=SECURE_COOKIE, path="/")


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


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/")


# ---------- mot de passe oublié ----------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset(conn, user_id: str) -> str:
    """Lien à usage unique valable une heure ; seule son empreinte est stockée."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=RESET_MINUTES)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM password_resets WHERE user_id=? OR expires_at<=?", (user_id, db.now()))
    conn.execute("INSERT INTO password_resets(token_hash, user_id, expires_at) VALUES (?,?,?)",
                 (_token_hash(token), user_id, expires))
    return token


def consume_reset(conn, token: str):
    """Renvoie l'utilisateur du lien s'il est valide, et l'invalide."""
    r = conn.execute("SELECT * FROM password_resets WHERE token_hash=?", (_token_hash(token or ""),)).fetchone()
    if not r or r["used"] or r["expires_at"] <= db.now():
        raise HTTPException(400, "Lien invalide ou expiré. Refais une demande de réinitialisation.")
    conn.execute("UPDATE password_resets SET used=1 WHERE token_hash=?", (r["token_hash"],))
    return r["user_id"]


def send_mail(to: str, subject: str, body: str) -> bool:
    """Envoie par SMTP si CANDIPILOT_SMTP_HOST est configuré ; sinon écrit le message dans le journal du serveur."""
    host = os.environ.get("CANDIPILOT_SMTP_HOST")
    if not host:
        print(f"[e-mail non envoyé : SMTP non configuré] À : {to}\nObjet : {subject}\n{body}", flush=True)
        return False
    msg = EmailMessage()
    msg["From"] = os.environ.get("CANDIPILOT_SMTP_FROM") or os.environ.get("CANDIPILOT_SMTP_USER", "")
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("CANDIPILOT_SMTP_PORT", "587")), timeout=20) as smtp:
        smtp.starttls()
        if os.environ.get("CANDIPILOT_SMTP_USER"):
            smtp.login(os.environ["CANDIPILOT_SMTP_USER"], os.environ.get("CANDIPILOT_SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    return True
