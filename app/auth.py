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
# Adresse publique (liens des e-mails) ; Render la fournit dans RENDER_EXTERNAL_URL.
PUBLIC_URL = (os.environ.get("CANDIPILOT_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
TRUST_PROXY = os.environ.get("CANDIPILOT_TRUST_PROXY") == "1"    # derrière le proxy de l'hébergeur
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
    if TRUST_PROXY and request.headers.get("x-forwarded-for"):
        return request.headers["x-forwarded-for"].split(",")[0].strip()
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


def mail_config() -> dict:
    """Réglages d'envoi réellement utilisés, sans les secrets (panneau du propriétaire)."""
    env = os.environ.get
    key, pwd = env("CANDIPILOT_BREVO_API_KEY", "").strip(), env("CANDIPILOT_SMTP_PASSWORD", "")
    mask = lambda v: (v[:8] + "…" + f" ({len(v)} caractères)") if v else "(vide)"
    name, sender = _sender()
    return {"method": "API Brevo" if key else "SMTP" if env("CANDIPILOT_SMTP_HOST") else "aucune (liens écrits dans le journal)",
            "brevo_api_key": mask(key), "smtp_host": env("CANDIPILOT_SMTP_HOST", ""), "smtp_port": env("CANDIPILOT_SMTP_PORT", "587"),
            "smtp_user": env("CANDIPILOT_SMTP_USER", ""), "smtp_password": mask(pwd) if pwd else "(vide)",
            "smtp_password_spaces": pwd != pwd.strip() or " " in pwd, "sender": f"{name} <{sender}>"}


def send_mail_logged(to: str, subject: str, body: str) -> None:
    """Envoie et écrit le résultat dans le journal du serveur (onglet Logs de Render)."""
    try:
        if send_mail(to, subject, body):
            print(f"[e-mail] envoyé à {to} : {subject}", flush=True)
    except Exception as e:
        print(f"[e-mail] ÉCHEC de l'envoi à {to} : {type(e).__name__}: {e}", flush=True)


def _sender() -> tuple:
    """(nom, adresse) de l'expéditeur, depuis CANDIPILOT_SMTP_FROM (« Nom <adresse> » ou « adresse »)."""
    raw = os.environ.get("CANDIPILOT_SMTP_FROM") or os.environ.get("CANDIPILOT_SMTP_USER", "")
    m = re.match(r"\s*(.*?)\s*<([^>]+)>\s*$", raw)
    return (m.group(1) or "CandiPilot", m.group(2)) if m else ("CandiPilot", raw.strip())


def _send_brevo(key: str, to: str, subject: str, body: str) -> None:
    """API transactionnelle de Brevo (HTTPS : jamais bloquée par les hébergeurs, contrairement au SMTP)."""
    import json as _json
    import urllib.request
    name, email = _sender()
    payload = {"sender": {"name": name, "email": email}, "to": [{"email": to}], "subject": subject, "textContent": body}
    req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=_json.dumps(payload).encode(), method="POST",
                                 headers={"api-key": key, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Brevo {e.code} : {e.read().decode(errors='ignore')[:300]}") from None


def send_mail(to: str, subject: str, body: str) -> bool:
    """Envoie par l'API Brevo (CANDIPILOT_BREVO_API_KEY) ou par SMTP (CANDIPILOT_SMTP_HOST) ;
    sinon écrit le message dans le journal du serveur."""
    brevo = os.environ.get("CANDIPILOT_BREVO_API_KEY", "").strip()
    if brevo:
        _send_brevo(brevo, to, subject, body)
        return True
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
