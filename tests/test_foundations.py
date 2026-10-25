"""Lot 1 « fondations » : migrations, journal d'audit, consentement, registre des sites, limite quotidienne,
anti-CSRF, idempotence des envois (robot simulé ; un serveur en mode extension est lancé pour l'idempotence).

    CANDIPILOT_FAKE_ROBOT=1 CANDIPILOT_DATA=/tmp/cp … uvicorn app.main:app --port 8765
    .venv/bin/python tests/test_foundations.py http://127.0.0.1:8765
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import test_api  # noqa: E402
from test_api import Client, PDF  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        sys.exit(1)
    passed += 1
    print("✓", label)


def until(fn, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.5)
    return None


def user():
    c = Client()
    c.opener.addheaders.append(("CF-Connecting-IP", f"10.5.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"))
    c.req("POST", "/api/auth/register", {"email": f"f{uuid.uuid4().hex[:6]}@example.com", "password": "motdepasse1", "first_name": "Ana"})
    c.req("PUT", "/api/profile", {"last_name": "Lopez", "phone": "+33600000000", "availability": "Mars 2027", "gender": "Femme"})
    c.req("POST", "/api/documents", {"type": "cv"}, files={"file": ("cv.pdf", PDF)})
    c.req("PUT", "/api/workflow", {"letter_source": "template"})
    c.req("PUT", "/api/preferences", {"metiers": ["analyste"], "zones": [], "contrats": [], "min_score": 0})
    return c


def add_offer(c, url, company):
    c.req("POST", "/api/offers", {"url": url, "poste": "Analyste", "entreprise": company})
    o = next(o for o in c.req("GET", "/api/offers")[1] if o["url"] == url)
    c.req("POST", f"/api/applications/{o['application_id']}/select", {"selected": True})
    return o["application_id"]


def status(c, aid):
    return c.req("GET", f"/api/applications/{aid}")[1]["statut"]


# 1. Registre des sites et migrations
owner = user()                     # premier compte de la base = propriétaire
c = user()
sites = {s["domain"]: s for s in c.req("GET", "/api/admin/sites")[1]}
check(sites["linkedin.com"]["mode_max"] == "manual" and sites["jobteaser.com"]["mode_max"] == "auto", "registre des sites : niveaux par défaut")
check(c.req("PUT", "/api/admin/sites/jobteaser.com", {"mode_max": "manual"})[0] == 403, "registre modifiable seulement par le propriétaire")
if owner.req("GET", "/api/auth/me")[1].get("owner"):          # seulement si la base était vide au départ
    check(owner.req("PUT", "/api/admin/sites/greenhouse.io", {"mode_max": "assisted", "notes": "vérifié"})[0] == 200, "le propriétaire peut le modifier")

# 2. Consentement explicite
check(c.req("PUT", "/api/workflow", {"submit_mode": "auto"})[0] == 422, "mode automatique refusé sans consentement")
check(c.req("PUT", "/api/workflow", {"submit_mode": "auto", "auto_consent": True})[1]["auto_consent"], "consentement enregistré")

# 3. Plafonnement par le registre : une offre LinkedIn ne part jamais automatiquement
ok_id = add_offer(c, "https://example.invalid/offre-ok", "Acme Datas")
li_id = add_offer(c, "https://www.linkedin.com/jobs/view/123", "Société LinkedIn")
until(lambda: status(c, ok_id) == "a_valider" and status(c, li_id) == "a_valider")
c.req("PUT", "/api/workflow", {"submit_mode": "auto", "auto_consent": True})
s, r = c.req("POST", "/api/launch")
check(s == 202 and r["started"] == 1 and r["capped"] == 1 and r["manual"] == 1, "LinkedIn plafonné à « semi-assisté » : pas d'envoi par le robot")
check(c.req("GET", f"/api/applications/{li_id}")[1]["mode"] == "manual", "mode de l'offre LinkedIn abaissé")
until(lambda: status(c, ok_id) == "confirmee")

# 4. Journal d'audit et historique
h = c.req("GET", f"/api/applications/{ok_id}/history")[1]
steps = [(x["from_status"], x["to_status"]) for x in h if x["action"] == "statut"]
check(("a_valider", "soumission") in steps and ("soumission", "confirmee") in steps, f"historique des statuts enregistré par la base : {steps}")
check(any(x["action"] == "mode_plafonné" for x in c.req("GET", f"/api/applications/{li_id}/history")[1]), "décision de plafonnement tracée")
other = user()
check(other.req("GET", f"/api/applications/{ok_id}/history")[0] == 404, "historique invisible pour un autre utilisateur")

# 5. Limite quotidienne
c2 = user()
ids = [add_offer(c2, f"https://example.invalid/lim{i}", f"Societe{i}") for i in range(3)]
until(lambda: all(status(c2, i) == "a_valider" for i in ids))
c2.req("PUT", "/api/workflow", {"submit_mode": "auto", "auto_consent": True, "daily_limit": 2})
s, r = c2.req("POST", "/api/launch")
check(r["started"] == 2 and r["postponed"] == 1, "limite quotidienne : 2 envoyées, 1 reportée")

# 6. Anti-CSRF : une modification venant d'un autre site est refusée
c3 = user()
c3.opener.addheaders.append(("Origin", "https://site-malveillant.example"))
check(c3.req("PUT", "/api/profile", {"phone": "+33699999999"})[0] == 403, "modification depuis un autre site refusée")

# 7. Idempotence : un résultat d'envoi qui ne correspond pas à l'essai en cours est ignoré (serveur en mode extension)
port = int(BASE.rsplit(":", 1)[1]) + 3
real = f"http://127.0.0.1:{port}"
env = {**{k: v for k, v in os.environ.items() if k not in ("CANDIPILOT_FAKE_ROBOT", "DATABASE_URL")},
       "CANDIPILOT_ROBOT": "extension", "CANDIPILOT_TEST_PORTAL": "1", "CANDIPILOT_DATA": tempfile.mkdtemp()}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], env=env, cwd=str(ROOT),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    def _ok():
        try:
            return urllib.request.urlopen(real + "/healthz").status == 200
        except Exception:
            return False
    until(_ok, 30)
    test_api.BASE = real
    owner = user()                                   # premier compte
    u = user()
    token = u.req("POST", "/api/agent/link", {"version": "test"})[1]["token"]
    agent = lambda m, p, b=None: (lambda r: (r.status, json.loads(r.read() or b"null")))(urllib.request.urlopen(
        urllib.request.Request(real + p, method=m, data=json.dumps(b).encode() if b is not None else None,
                               headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})))
    agent("PUT", "/api/agent/sites", {"sites": {"jobteaser": {"connected": True}}})
    aid = add_offer(u, "https://skema.jobteaser.com/fr/job-offers/42", "Banque Test")
    job = agent("GET", "/api/agent/next")[1]
    agent("POST", f"/api/agent/jobs/{job['id']}/result", {"ok": True, "data": {"info": {"kind": "easy", "letter": False, "questions": []}}})
    u.req("PUT", "/api/workflow", {"submit_mode": "auto", "auto_consent": True})
    u.req("POST", "/api/launch")
    job = agent("GET", "/api/agent/next")[1]
    check(job["type"] == "submit" and job["key"], "la tâche d'envoi porte une clé d'idempotence")
    agent("POST", f"/api/agent/jobs/{job['id']}/result", {"ok": True, "data": {"status": "submitted", "detail": "x", "key": "ancienne-cle"}})
    check(status(u, aid) == "soumission", "résultat avec une autre clé ignoré : pas de « envoyée » sans preuve de CET envoi")
finally:
    srv.terminate()
    test_api.BASE = BASE

print(f"\n{passed} vérifications réussies.")
