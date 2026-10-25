"""Découverte multi-sources (lot 2) : plateformes de recrutement et pages carrières, sans Internet.

    .venv/bin/python tests/test_discovery.py <port>

Lance son propre serveur (robot simulé) dont les API de plateformes pointent vers les fausses API
de app/testportal.py, puis vérifie : reconnaissance des adresses, filtrage selon le profil, doublons
entre plateformes, offres expirées, sources par utilisateur.
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8830
BASE = f"http://127.0.0.1:{PORT}"
sys.argv = [sys.argv[0], BASE]
import test_api  # noqa: E402
from test_api import Client  # noqa: E402
from app.discovery import contract_of, detect_source, fingerprint  # noqa: E402

passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        raise SystemExit(1)
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


# 1. Reconnaissance des adresses, contrats, doublons (sans serveur)
check(detect_source("https://boards.greenhouse.io/adyen/jobs/123") == ("greenhouse", "adyen"), "adresse Greenhouse reconnue")
check(detect_source("https://job-boards.eu.greenhouse.io/doctolib") == ("greenhouse", "doctolib"), "adresse Greenhouse (Europe) reconnue")
check(detect_source("https://jobs.lever.co/qonto/abc") == ("lever", "qonto"), "adresse Lever reconnue")
check(detect_source("https://jobs.ashbyhq.com/alan") == ("ashby", "alan"), "adresse Ashby reconnue")
check(detect_source("https://jobs.smartrecruiters.com/Ubisoft2/7444") == ("smartrecruiters", "ubisoft2"), "adresse SmartRecruiters reconnue")
check(detect_source("https://apply.workable.com/huggingface/") == ("workable", "huggingface"), "adresse Workable reconnue")
check(detect_source("https://www.entreprise.fr/carrieres")[0] == "jsonld", "autre site : lecture des données structurées de la page")
check(contract_of({"title": "Internal Control Specialist", "contract": ""}) != "stage", "« Internal » n'est pas un stage")
check(contract_of({"title": "Finance Intern", "contract": ""}) == "stage", "« Intern » est un stage")
check(fingerprint("Acme", "Finance Intern (H/F)", "Paris, France") == fingerprint("ACME", "Finance intern", "Paris"),
      "même offre sur deux plateformes = même empreinte")

# 2. Serveur avec de fausses API de plateformes
env = {**{k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "ANTHROPIC_API_KEY")}, "CANDIPILOT_FAKE_ROBOT": "1",
       "CANDIPILOT_FAKE_LLM": "1",
       "CANDIPILOT_DATA": tempfile.mkdtemp(), "CANDIPILOT_ATS_BASE": BASE + "/fake-ats"}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], env=env, cwd=str(ROOT),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def up():
    try:
        return urllib.request.urlopen(BASE + "/healthz").status == 200
    except Exception:
        return False


try:
    until(up, 30)
    test_api.BASE = BASE
    c = Client()
    c.req("POST", "/api/auth/register", {"email": f"d{uuid.uuid4().hex[:6]}@example.com", "password": "motdepasse1"})
    c.req("PUT", "/api/preferences", {"metiers": ["finance", "analyste", "contrôle de gestion"], "zones": [], "contrats": ["stage"], "min_score": 30})
    s, r = c.req("POST", "/api/sources", {"url": "https://boards.greenhouse.io/acme"})
    check(s == 201 and r["platform"] == "greenhouse" and r["published"] == 3, "source ajoutée par simple adresse (3 offres publiées)")
    check(c.req("POST", "/api/sources", {"url": "https://boards.greenhouse.io/acme"})[0] == 409, "pas deux fois la même source")
    for body in ({"platform": "lever", "token": "acme", "company": "Acme"}, {"platform": "ashby", "token": "acme", "company": "Acme"},
                 {"platform": "smartrecruiters", "token": "ubi", "company": ""}, {"platform": "workable", "token": "hug", "company": ""}):
        check(c.req("POST", "/api/sources", body)[0] == 201, f"source {body['platform']} ajoutée")
    s, r = c.req("POST", "/api/sources", {"url": BASE + "/fake-ats/page/maisonx"})
    check(s == 201 and r["platform"] == "jsonld" and r["published"] == 1, "page carrières lue via ses données structurées (JobPosting)")
    check(c.req("POST", "/api/sources", {"url": "pas une adresse"})[0] == 422, "adresse invalide refusée")

    check(c.req("POST", "/api/search")[0] == 202, "découverte lancée")
    offers = until(lambda: (lambda o: o if len(o) >= 6 else None)(c.req("GET", "/api/offers")[1]), 40) or c.req("GET", "/api/offers")[1]
    titles = sorted(o["poste"] for o in offers)
    check("Senior Backend Engineer" not in titles, "offre hors profil écartée (ingénieur backend)")
    check(sum(t.startswith("Finance Intern") for t in titles) == 1, f"doublon Greenhouse / Lever fusionné : {titles}")
    check({"Corporate Finance Intern", "Stage Analyste Financier", "Finance Operations Intern", "Stage Contrôle de gestion"} <= set(titles),
          "offres des 5 autres sources gardées")
    ats = [o for o in offers if o["source"] in ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "jsonld")]
    check(len(ats) == 6 and all(o["apply_type"] == "external" for o in ats), "offres des plateformes marquées « site de l'entreprise »")
    rated = until(lambda: (lambda o: o if all(x["match_reason"] for x in o) else None)(c.req("GET", "/api/offers")[1]), 30)
    check(rated and all(x["match_reason"].startswith("(simulation)") and x["rule_score"] >= 0 for x in rated),
          "chaque offre relue par l'IA : note et explication (Claude simulé)")
    check(c.req("GET", "/api/offers")[1][0]["score"] == 90, "offres classées par la note de l'IA")
    srcs = {x["platform"]: x for x in c.req("GET", "/api/sources")[1]["sources"]}
    check(srcs["greenhouse"]["last_status"].startswith("ok") and srcs["workable"]["company"] == "Hug Test", "état de chaque source et nom de l'entreprise")

    # 3. Offre retirée par l'entreprise : expirée, plus proposée
    urllib.request.urlopen(urllib.request.Request(BASE + "/fake-ats/_expire/gh3", method="POST"))
    c.req("POST", "/api/search")
    check(until(lambda: not any(o["poste"] == "M&A Analyst Internship" for o in c.req("GET", "/api/offers")[1]), 30),
          "offre retirée de la plateforme : marquée expirée et masquée")

    # 4. Sources propres à chaque utilisateur
    other = Client()
    other.req("POST", "/api/auth/register", {"email": f"o{uuid.uuid4().hex[:6]}@example.com", "password": "motdepasse1"})
    check(other.req("GET", "/api/sources")[1]["sources"] == [] and other.req("GET", "/api/offers")[1] == [], "les sources et offres d'un autre ne sont pas visibles")
    sid = next(iter(srcs.values()))["id"]
    check(other.req("DELETE", f"/api/sources/{sid}")[0] == 404, "impossible de retirer la source d'un autre")
    check(c.req("DELETE", f"/api/sources/{sid}")[0] == 200, "retrait de sa propre source")
finally:
    srv.terminate()

print(f"\n{passed} vérifications réussies.")
