"""Garde-fous : rythme poli, détection login/CAPTCHA (pause), confirmation."""
from __future__ import annotations

import random
import time

# Indices textuels d'un mur de connexion / création de compte / anti-bot.
# Si l'un apparaît sur la page, on NE contourne rien : on rend la main.
LOGIN_WALL_HINTS = [
    "se connecter", "connexion", "sign in", "log in", "login",
    "créer un compte", "create an account", "create profile", "créer un profil",
    "register", "s'inscrire",
]
CAPTCHA_HINTS = [
    "captcha", "recaptcha", "hcaptcha", "i'm not a robot",
    "je ne suis pas un robot", "cloudflare", "verify you are human",
    "vérifier que vous êtes humain",
]


def polite_delay(settings: dict) -> None:
    lo = float(settings.get("min_delay_seconds", 2))
    hi = float(settings.get("max_delay_seconds", 5))
    time.sleep(random.uniform(lo, max(lo, hi)))


def page_blob(page) -> str:
    try:
        return (page.content() or "").lower()
    except Exception:
        return ""


def detect_block(page) -> str | None:
    """Renvoie 'captcha' ou 'login' si un mur est détecté, sinon None.

    On NE résout jamais un CAPTCHA et on NE crée jamais de compte :
    on signale pour laisser l'humain agir.
    """
    blob = page_blob(page)
    if any(h in blob for h in CAPTCHA_HINTS):
        return "captcha"
    # Un simple lien "Se connecter" en header ne suffit pas ; on exige que le
    # contenu principal demande explicitement une auth avant de postuler.
    strong_login = ["créer un profil", "create profile", "create an account",
                    "vous devez vous connecter", "please sign in", "please log in"]
    if any(h in blob for h in strong_login):
        return "login"
    return None


def confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [o/N] ").strip().lower() in ("o", "oui", "y", "yes")
    except EOFError:
        return False
