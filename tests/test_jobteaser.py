"""Compte JobTeaser par utilisateur : connexion à distance (faux portail) et isolation des comptes.

    .venv/bin/python tests/test_jobteaser.py http://127.0.0.1:8765
(serveur en robot simulé, CANDIPILOT_DATA exporté ; un second serveur en mode réel est lancé
par le test lui-même, sans jamais démarrer le robot.)
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from test_api import Client  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
DATA = Path(os.environ.get("CANDIPILOT_DATA", ""))
passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        sys.exit(1)
    passed += 1
    print("✓", label)


def user():
    c = Client()
    c.opener.addheaders.append(("CF-Connecting-IP", f"10.9.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"))
    s, r = c.req("POST", "/api/auth/register", {"email": f"jt{uuid.uuid4().hex[:6]}@example.com", "password": "motdepasse1"})
    assert s == 201, s
    return c, r["id"]


def until(fn, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.5)
    return None


a, a_id = user()
b, b_id = user()
st = a.req("GET", "/api/jobteaser")[1]
check(st["status"] == "" and st["portal_url"] == "https://www.jobteaser.com", "nouveau compte : JobTeaser non connecté, portail par défaut")
check(a.req("PUT", "/api/jobteaser", {"portal_url": "https://evil.example.com"})[0] == 422, "adresse qui n'est pas JobTeaser refusée")
check(a.req("PUT", "/api/jobteaser", {"portal_url": "skema.jobteaser.com/fr/job-offers"})[1]["portal_url"] == "https://skema.jobteaser.com",
      "adresse d'école normalisée (https://skema.jobteaser.com)")
a.req("PUT", "/api/jobteaser", {"portal_url": BASE + "/fake-jobteaser"})
check(a.req("POST", "/api/jobteaser/input", {"action": "key", "key": "Tab"})[0] == 409, "saisie refusée sans connexion ouverte")

# Connexion à distance : écran, clavier, mauvais puis bon mot de passe.
check(a.req("POST", "/api/jobteaser/connect")[0] == 202, "ouverture du navigateur de connexion")
img = until(lambda: (lambda r: r[1] if r[0] == 200 else None)(a.req("GET", "/api/jobteaser/screen")))
check(img and img[:2] == b"\xff\xd8", "l'écran du navigateur est transmis (image)")
check(b.req("GET", "/api/jobteaser/screen")[0] == 404, "un autre utilisateur ne voit pas cet écran")
check(a.req("POST", "/api/jobteaser/input", {"action": "click", "x": 2, "y": 0.5})[0] == 422, "clic hors écran refusé")


def login(password):
    for step in ({"action": "key", "key": "Tab"}, {"action": "type", "text": "alex@ecole.fr"}, {"action": "key", "key": "Tab"},
                 {"action": "type", "text": password}, {"action": "key", "key": "Enter"}):
        a.req("POST", "/api/jobteaser/input", step)
        time.sleep(0.4)


until(lambda: (a.req("GET", "/api/jobteaser")[1]["session"] or {}).get("title") == "Connexion")
login("mauvais")
time.sleep(4)
check(a.req("GET", "/api/jobteaser")[1]["status"] == "", "mauvais mot de passe : toujours non connecté")
login("bonmotdepasse")
check(until(lambda: a.req("GET", "/api/jobteaser")[1]["status"] == "connected"), "bon mot de passe : compte JobTeaser connecté (détecté seul)")
check(until(lambda: a.req("GET", "/api/jobteaser")[1]["session"] is None, 15), "navigateur de connexion refermé")
check(b.req("GET", "/api/jobteaser")[1]["status"] == "", "le compte d'un autre utilisateur n'est pas connecté pour autant")
if DATA.name:
    check((DATA / "navigateurs" / a_id).is_dir() and not (DATA / "navigateurs" / b_id).exists(), "un dossier de navigateur par utilisateur")
check(any("JobTeaser connecté" in e["message"] for e in a.req("GET", "/api/events")[1]), "notification « Compte JobTeaser connecté »")

# La session est gardée : une nouvelle ouverture arrive directement sur les offres.
a.req("POST", "/api/jobteaser/connect")
check(until(lambda: a.req("GET", "/api/jobteaser")[1]["session"] is None, 30) and
      a.req("GET", "/api/jobteaser")[1]["status"] == "connected", "session gardée : reconnexion immédiate sans mot de passe")
check(a.req("DELETE", "/api/jobteaser")[1]["status"] == "", "déconnexion : session oubliée")
if DATA.name:
    check(not any((DATA / "navigateurs" / a_id).iterdir()), "déconnexion : cookies effacés")

# Mode réel (sans robot simulé) : rien ne part sans le compte JobTeaser de la personne connectée.
port = int(BASE.rsplit(":", 1)[1]) + 1
real = f"http://127.0.0.1:{port}"
tmp = tempfile.mkdtemp()
env = {**{k: v for k, v in os.environ.items() if k != "CANDIPILOT_FAKE_ROBOT"}, "CANDIPILOT_DATA": tmp}
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], env=env,
                          cwd=str(Path(__file__).resolve().parent.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ok(url):
    try:
        urllib.request.urlopen(url + "/api/openapi.json")
        return True
    except Exception:
        return False


try:
    until(lambda: _ok(real), 30)
    import test_api
    test_api.BASE = real
    user()                          # premier compte (propriétaire), non utilisé
    r, _ = user()
    r.req("PUT", "/api/preferences", {"metiers": ["finance"], "zones": [], "contrats": ["stage"]})
    check(r.req("GET", "/api/jobteaser")[1]["required"], "mode réel : compte JobTeaser obligatoire")
    s, body = r.req("POST", "/api/search")
    check(s == 422 and "JobTeaser" in body["detail"], "mode réel : recherche refusée sans compte JobTeaser connecté")
    g = r.req("GET", "/api/guide")[1]
    check(g["current"] <= 2 and "JobTeaser" in g["steps"][2]["help"], "parcours : « ton compte JobTeaser connecté » demandé à l'étape 3")
    r.req("PUT", "/api/workflow", {"submit_mode": "auto"})
    s, body = r.req("POST", "/api/launch")
    check(s == 422 and "JobTeaser" in body["detail"], "mode réel : lancement refusé sans compte JobTeaser")
    check(r.req("GET", "/api/jobs")[1] == [], "aucune tâche du robot créée")
finally:
    server.terminate()
    test_api.BASE = BASE

print(f"\n{passed} vérifications réussies.")
