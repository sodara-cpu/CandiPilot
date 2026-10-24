"""Session navigateur : trois modes, au choix dans config/profile.yaml (browser.mode).

- "persistent"   : un Chromium isolé dédié à l'outil (profil ./.user-profile).
                   Tu te connectes dedans une fois ; rien ne touche ton Chrome perso.
- "system_chrome": lance TON Google Chrome avec TON profil (tes sessions + le
                   gestionnaire de mots de passe de Chrome). >>> Chrome doit être
                   FERMÉ avant de lancer l'outil (le profil est verrouillé sinon).
- "cdp"          : se connecte à un Chrome que TU as déjà démarré avec le débogage
                   distant. Pratique si tu veux garder Chrome ouvert.
                   Démarrage (une fois), Chrome fermé au préalable :
                     macOS :
                       "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
                         --remote-debugging-port=9222 \\
                         --user-data-dir="$HOME/Library/Application Support/Google/Chrome"
                   puis mets browser.cdp_url: "http://localhost:9222".

Aucun mot de passe ne transite par l'outil : c'est TON navigateur / TON
gestionnaire qui gère l'authentification.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import ROOT

PROFILE_DIR = ROOT / ".user-profile"


def _expand(p: str) -> str:
    return os.path.expanduser(os.path.expandvars((p or "").strip("<> ")))


@contextmanager
def browser_session(settings: dict):
    """Ouvre la session selon settings['browser']. Yield un *context* Playwright
    exposant .pages et .new_page()."""
    bconf = settings.get("browser", {}) or {}
    mode = (bconf.get("mode") or "persistent").lower()
    headless = bool(settings.get("headless", False))

    with sync_playwright() as p:
        if mode == "cdp":
            url = bconf.get("cdp_url") or "http://localhost:9222"
            browser = p.chromium.connect_over_cdp(url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            context.set_default_timeout(20000)
            try:
                yield context
            finally:
                # On ne ferme PAS le Chrome de l'utilisateur ; on se détache.
                browser.close()
            return

        if mode == "system_chrome":
            user_data = _expand(bconf.get("chrome_user_data_dir", ""))
            if not user_data or not Path(user_data).exists():
                raise RuntimeError(
                    "browser.chrome_user_data_dir introuvable. Renseigne le chemin "
                    "du dossier de données Chrome (voir config/profile.yaml). "
                    "Et FERME Google Chrome avant de lancer l'outil."
                )
            profile_dir = bconf.get("chrome_profile_directory", "Default")
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data,
                channel="chrome",                      # utilise Google Chrome installé
                headless=headless,
                viewport={"width": 1366, "height": 900},
                accept_downloads=True,
                args=[f"--profile-directory={profile_dir}"],
            )
            context.set_default_timeout(20000)
            try:
                yield context
            finally:
                context.close()
            return

        # mode "persistent" (défaut)
        PROFILE_DIR.mkdir(exist_ok=True)
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            viewport={"width": 1366, "height": 900},
            accept_downloads=True,
        )
        context.set_default_timeout(20000)
        try:
            yield context
        finally:
            context.close()


def login_hint(settings: dict) -> str:
    mode = ((settings.get("browser") or {}).get("mode") or "persistent").lower()
    if mode == "system_chrome":
        return ("Mode system_chrome : FERME Google Chrome avant de lancer l'outil. "
                "Il rouvrira TON profil Chrome, avec tes sessions et ton gestionnaire "
                "de mots de passe déjà connus.")
    if mode == "cdp":
        return ("Mode cdp : démarre d'abord Chrome avec --remote-debugging-port=9222 "
                "(voir src/browser.py), puis lance l'outil.")
    return ("Mode persistent : une fenêtre Chromium dédiée s'ouvre. Connecte-toi "
            "dedans une fois ; tes sessions seront mémorisées pour la suite.")
