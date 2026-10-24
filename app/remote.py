"""Connexion à distance au compte JobTeaser de chaque utilisateur.

Le navigateur du robot tourne sur la machine qui héberge CandiPilot. Pour que chaque utilisateur
connecte SON compte (SSO de son école, double authentification…), l'application lui montre l'écran
de ce navigateur et lui transmet ses clics et sa saisie. Une fois connecté, les cookies restent dans
le profil du navigateur de l'utilisateur (data/navigateurs/<id>) ; CandiPilot ne voit ni ne stocke
son mot de passe.

Une session par utilisateur, fermée après connexion réussie, annulation ou 10 minutes d'inactivité.
"""
from __future__ import annotations

import os
import queue
import threading
import time

from . import db, robot

IDLE_TIMEOUT_S = 600
VIEWPORT = {"width": 1100, "height": 760}
# Robot simulé (tests) : navigateur invisible, aucun vrai site.
HEADLESS = os.environ.get("CANDIPILOT_FAKE_ROBOT") == "1"

_sessions: dict = {}
_lock = threading.Lock()


class Session(threading.Thread):
    def __init__(self, user_id: str, profile: str, start_url: str):
        super().__init__(daemon=True, name=f"jobteaser-{user_id[:8]}")
        self.user_id, self.profile, self.start_url = user_id, profile, start_url
        self.commands: queue.Queue = queue.Queue()
        self.status, self.error, self.url, self.title = "demarrage", "", "", ""
        self.screen = b""
        self.last_activity = time.time()

    def info(self) -> dict:
        return {"status": self.status, "url": self.url, "title": self.title, "error": self.error}

    def send(self, cmd: dict) -> None:
        self.last_activity = time.time()
        self.commands.put(cmd)

    def run(self) -> None:
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(self.profile, headless=HEADLESS, viewport=VIEWPORT)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(self.start_url, wait_until="domcontentloaded")
                self.status = "en_cours"
                last_check = 0.0
                while self.status == "en_cours":
                    try:
                        cmd = self.commands.get(timeout=0.4)
                        self._apply(page, cmd)
                    except queue.Empty:
                        pass
                    if self.status != "en_cours":
                        break
                    try:
                        self.screen = page.screenshot(type="jpeg", quality=60)
                        self.url, self.title = page.url, page.title()
                    except Exception:
                        pass                                    # page en cours de chargement
                    if time.time() - last_check > 2.5:
                        last_check = time.time()
                        if robot.logged_in(page):
                            self.status = "connecte"
                            _mark(self.user_id, "connected")
                    if time.time() - self.last_activity > IDLE_TIMEOUT_S:
                        self.status = "expiree"
                ctx.close()
        except Exception as e:
            self.status, self.error = "erreur", f"{type(e).__name__}: {e}"[:300]
        finally:
            with _lock:
                if _sessions.get(self.user_id) is self:
                    del _sessions[self.user_id]

    def _apply(self, page, cmd: dict) -> None:
        kind = cmd.get("action")
        if kind == "click":
            page.mouse.click(float(cmd["x"]) * VIEWPORT["width"], float(cmd["y"]) * VIEWPORT["height"])
        elif kind == "type":
            page.keyboard.type(str(cmd.get("text", ""))[:500], delay=20)
        elif kind == "key" and cmd.get("key") in ("Enter", "Tab", "Backspace", "Escape", "ArrowDown", "ArrowUp",
                                                  "ArrowLeft", "ArrowRight", "Delete", "Shift+Tab"):
            page.keyboard.press(cmd["key"])
        elif kind == "scroll":
            page.mouse.wheel(0, max(-2000, min(2000, float(cmd.get("dy", 0)))))
        elif kind == "back":
            page.go_back()
        elif kind == "reload":
            page.goto(self.start_url, wait_until="domcontentloaded")
        elif kind == "stop":
            self.status = "annulee"
        page.wait_for_timeout(250)


def _mark(user_id: str, status: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE preferences SET jobteaser_status=?, jobteaser_at=? WHERE user_id=?", (status, db.now(), user_id))
        if status == "connected":
            db.add_event(c, user_id, "success", "Compte JobTeaser connecté : le robot travaillera avec ton compte.")


def start(user_id: str) -> Session:
    with db.tx() as c:
        profile = str(robot.profile_dir(c, user_id))
        start_url = robot.portal(c, user_id) + "/fr/job-offers"
    with _lock:
        s = _sessions.get(user_id)
        if s and s.is_alive() and s.status in ("demarrage", "en_cours"):
            return s
        s = Session(user_id, profile, start_url)
        _sessions[user_id] = s
    s.start()
    return s


def get(user_id: str):
    with _lock:
        return _sessions.get(user_id)


def active_users() -> set:
    """Utilisateurs dont le navigateur est ouvert pour la connexion : le robot attend avant de s'en servir."""
    with _lock:
        return {u for u, s in _sessions.items() if s.is_alive()}


def stop(user_id: str) -> None:
    s = get(user_id)
    if s:
        s.send({"action": "stop"})
