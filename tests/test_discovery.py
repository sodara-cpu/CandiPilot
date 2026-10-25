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
from app.discovery import contract_of, detect_source, fingerprint, in_zone  # noqa: E402
from app.matching import score  # noqa: E402

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

check(in_zone("Rennes, FR", ["France"]) and in_zone("Levallois-Perret, FR", ["Paris"]) and in_zone("London, UK", ["londres"])
      and not in_zone("Barcelona", ["France"]) and not in_zone("Frankfurt, DE", ["France"]), "zones : codes pays, Île-de-France, Londres")
fin = {"metiers": ["corporate finance", "M&A"], "contrats": ["stage"], "zones": ["France"]}
o = lambda t: {"poste": t, "localisation": "Paris, FR", "contrat": "stage"}
check(score(o("Stagiaire Modélisation financière"), fin, {}) > score(o("Stage Talent Acquisition"), fin, {}),
      "« financière » proche de « finance » ; « Talent Acquisition » n'est pas du M&A")
check(score(o("Stage Fusions & Acquisitions"), fin, {}) > score(o("Growth Marketing Intern"), fin, {}), "« Fusions » = M&A ; le marketing n'est pas de la finance")

# 2. Serveur avec de fausses API de plateformes
env = {**{k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "ANTHROPIC_API_KEY")}, "CANDIPILOT_FAKE_ROBOT": "1",
       "CANDIPILOT_FAKE_LLM": "1", "CANDIPILOT_FT_BASE": BASE + "/fake-ft", "FRANCE_TRAVAIL_CLIENT_ID": "PAR_test", "FRANCE_TRAVAIL_CLIENT_SECRET": "secret-test",
       "CANDIPILOT_ADZUNA_BASE": BASE + "/fake-adzuna", "ADZUNA_APP_ID": "id-test", "ADZUNA_APP_KEY": "cle-test", "CANDIPILOT_DEFAULT_SOURCES": "0",
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
    c.req("PUT", "/api/preferences", {"metiers": ["finance", "analyste", "contrôle de gestion"], "zones": [], "contrats": ["stage", "alternance"], "min_score": 30})
    s, r = c.req("POST", "/api/sources", {"url": "https://boards.greenhouse.io/acme"})
    check(s == 201 and r["platform"] == "greenhouse" and r["published"] == 3, "source ajoutée par simple adresse (3 offres publiées)")
    check(c.req("POST", "/api/sources", {"url": "https://boards.greenhouse.io/acme"})[0] == 409, "pas deux fois la même source")
    for body in ({"platform": "lever", "token": "acme", "company": "Acme"}, {"platform": "ashby", "token": "acme", "company": "Acme"},
                 {"platform": "smartrecruiters", "token": "ubi", "company": ""}, {"platform": "workable", "token": "hug", "company": ""}):
        check(c.req("POST", "/api/sources", body)[0] == 201, f"source {body['platform']} ajoutée")
    s, r = c.req("POST", "/api/sources", {"url": BASE + "/fake-ats/page/maisonx"})
    check(s == 201 and r["platform"] == "jsonld" and r["published"] == 1, "page carrières lue via ses données structurées (JobPosting)")
    check(c.req("POST", "/api/sources", {"url": "pas une adresse"})[0] == 422, "adresse invalide refusée")
    s, r = c.req("POST", "/api/sources", {"platform": "sitemap", "token": BASE + "/fake-careers", "company": "Maison Y"})
    check(s == 201 and r["published"] == 2, "site carrières (Workday, site maison) lu comme un moteur de recherche : 2 offres en France")


    check(c.req("POST", "/api/search")[0] == 202, "découverte lancée")
    offers = until(lambda: (lambda o: o if len(o) >= 6 else None)(c.req("GET", "/api/offers")[1]), 40) or c.req("GET", "/api/offers")[1]
    titles = sorted(o["poste"] for o in offers)
    check("Senior Backend Engineer" not in titles, "offre hors profil écartée (ingénieur backend)")
    check(sum(t.startswith("Finance Intern") for t in titles) == 1, f"doublon Greenhouse / Lever fusionné : {titles}")
    check({"Corporate Finance Intern", "Stage Analyste Financier", "Finance Operations Intern", "Stage Contrôle de gestion"} <= set(titles),
          "offres des 5 autres sources gardées")
    ft = {o["poste"]: o for o in c.req("GET", "/api/offers")[1] if o["source"] == "francetravail"}
    check("Alternance - Assistant contrôle de gestion (H/F)" in ft and ft["Alternance - Assistant contrôle de gestion (H/F)"]["url"].startswith("https://candidat.francetravail.fr"),
          f"offres France Travail (API officielle) ajoutées, avec le lien d'origine : {sorted(ft)}")
    az = {o["poste"]: o for o in c.req("GET", "/api/offers")[1] if o["source"] == "adzuna"}
    check(set(az) == {"Stage Analyste Financier M&A"} and az["Stage Analyste Financier M&A"]["url"].startswith("https://www.adzuna.fr"),
          f"offres Adzuna ajoutées (balises HTML retirées, offre hors profil écartée) : {sorted(az)}")
    sm = {o["poste"]: o for o in c.req("GET", "/api/offers")[1] if o["source"] == "sitemap"}
    check(set(sm) == {"Stage Analyste Crédit", "Stage Contrôle de gestion"} and sm["Stage Analyste Crédit"]["entreprise"] == "Maison Y",
          "offres du site carrières : page interdite par robots.txt jamais lue, offre à Madrid écartée, nom propre de l'entreprise")
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

    # 3b. Lot 5 : brouillons de l'IA pour les questions du recruteur (Claude simulé), jamais enregistrés seuls.
    c.req("POST", "/api/documents", {"type": "cv"}, files={"file": ("cv.pdf", b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")})
    s, r = c.req("POST", "/api/offers", {"url": "https://exemple.fr/offre/q1", "poste": "Stage Analyste financier", "entreprise": "Orsay",
                                          "apply_type": "jobteaser"})
    app_id = next(o["application_id"] for o in c.req("GET", "/api/offers")[1] if o["id"] == r["id"])
    c.req("POST", f"/api/applications/{app_id}/prepare")
    a = until(lambda: (lambda x: x if x["statut"] == "a_valider" else None)(c.req("GET", f"/api/applications/{app_id}")[1]), 30)
    check(a and a["questions"] and a["ai_enabled"], "dossier préparé avec une question du recruteur, assistant IA disponible")
    s, r = c.req("POST", f"/api/applications/{app_id}/suggest")
    check(s == 200 and r["answers"][0]["label"] == a["questions"][0]["label"] and r["answers"][0]["answer"].startswith("(simulation)"),
          "brouillon de réponse proposé par l'IA")
    check(c.req("GET", f"/api/applications/{app_id}")[1]["questions"][0]["answer"] == "", "brouillon jamais enregistré sans le candidat")
    c.req("PUT", f"/api/applications/{app_id}", {"questions": [{"label": a["questions"][0]["label"], "answer": "Ma réponse."}]})
    check(c.req("POST", f"/api/applications/{app_id}/suggest")[0] == 409, "plus de brouillon quand tout est répondu")

    # 4. Sources propres à chaque utilisateur
    other = Client()
    other.req("POST", "/api/auth/register", {"email": f"o{uuid.uuid4().hex[:6]}@example.com", "password": "motdepasse1"})
    check(other.req("GET", "/api/sources")[1]["sources"] == [] and other.req("GET", "/api/offers")[1] == [], "les sources et offres d'un autre ne sont pas visibles")
    sid = next(iter(srcs.values()))["id"]
    check(other.req("DELETE", f"/api/sources/{sid}")[0] == 404, "impossible de retirer la source d'un autre")
    check(other.req("POST", f"/api/applications/{app_id}/suggest")[0] == 404, "impossible de demander des brouillons sur le dossier d'un autre")
    check(c.req("DELETE", f"/api/sources/{sid}")[0] == 200, "retrait de sa propre source")
finally:
    srv.terminate()

print(f"\n{passed} vérifications réussies.")
