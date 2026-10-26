"""Essai de bout en bout avec le VRAI agent Claude sur le site carrières de test (aucun employeur réel).

    set -a; . ./.env; set +a; .venv/bin/python tests/e2e_agent_reel.py

Nécessite ANTHROPIC_API_KEY (coût : quelques centimes). Hors de la suite automatique (appel payant).
Vérifie : tâche lancée → extension → agent Claude (outils) → formulaire en 3 étapes avec champ conditionnel et
validation → envoi autorisé → confirmation du site → base de données (statut, étape, preuve) → réception par le site.
"""
import json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8891
BASE = f"http://127.0.0.1:{PORT}"
sys.path.insert(0, str(ROOT / "tests"))
from test_cv import simple_pdf  # noqa: E402

if not os.environ.get("ANTHROPIC_API_KEY"):
    raise SystemExit("ANTHROPIC_API_KEY manquante : charge .env.")
tmp = tempfile.mkdtemp()
env = {**{k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "CANDIPILOT_FAKE_ROBOT", "CANDIPILOT_FAKE_LLM")},
       "CANDIPILOT_ROBOT": "extension", "CANDIPILOT_TEST_PORTAL": "1", "CANDIPILOT_DATA": tmp, "CANDIPILOT_DEFAULT_SOURCES": "0"}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], env=env, cwd=str(ROOT),
                       stdout=open(Path(tmp) / "server.log", "w"), stderr=subprocess.STDOUT)
ok = []


def until(fn, t=60, every=1):
    end = time.time() + t
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(every)
    return None


def check(cond, label):
    ok.append(bool(cond))
    print(("✓ " if cond else "✗ ") + label, flush=True)


try:
    until(lambda: urllib.request.urlopen(BASE + "/healthz").status == 200, 30)
    with sync_playwright() as p:
        ext = str(ROOT / "extension")
        ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="chromium", headless=True,
                                                   args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        api = page.request
        page.goto(BASE + "/#/register")
        page.fill("input[name=first_name]", "Camille"); page.fill("input[name=last_name]", "Durand")
        page.fill("input[name=email]", "camille.durand@example.com")
        page.fill("input[name=password]", "motdepasse1"); page.fill("input[name=confirm]", "motdepasse1")
        page.get_by_role("button", name="Créer mon compte").click()
        check(until(lambda: api.get(BASE + "/api/agent/status").json().get("linked"), 30), "extension reliée au compte")
        api.put(BASE + "/api/profile", data={"phone": "+33612345678", "location": "Paris", "school": "SKEMA Business School",
                                              "program": "Master in Management (Bac+5), spécialisation Finance", "languages": "Français, Anglais (C1)",
                                              "availability": "Mars 2027", "competences": "Excel, modélisation financière, reporting"})
        cv = simple_pdf(["Camille Durand", "SKEMA Business School - Master in Management", "Finance, Excel"])
        api.post(BASE + "/api/documents", multipart={"type": "cv", "file": {"name": "CV_Camille_Durand.pdf", "mimeType": "application/pdf", "buffer": cv}})
        api.put(BASE + "/api/preferences", data={"metiers": ["analyste financier"], "zones": ["Paris"], "contrats": ["stage"], "min_score": 0})
        api.put(BASE + "/api/workflow", data={"submit_mode": "auto", "auto_consent": True})        # envoi autorisé (site de test)
        s = api.post(BASE + "/api/offers", data={"url": BASE + "/fake-site/offre/AF7", "poste": "Analyste Financier Junior",
                                                 "entreprise": "Groupe Exemple", "apply_type": "external"})
        app_id = next(o["application_id"] for o in api.get(BASE + "/api/offers").json() if o["id"] == s.json()["id"])
        api.post(BASE + f"/api/applications/{app_id}/prepare")
        check(until(lambda: api.get(BASE + f"/api/applications/{app_id}").json()["statut"] == "a_valider", 60), "dossier préparé (CV, lettre)")
        t0 = time.time()
        check(api.post(BASE + f"/api/applications/{app_id}/apply").status == 202, "tâche de candidature lancée depuis l'application")
        sw.evaluate("() => { poll(); return 1; }")
        a = until(lambda: (lambda x: x if x["statut"] in ("confirmee", "echec", "attente_utilisateur", "incertaine") else None)(
            api.get(BASE + f"/api/applications/{app_id}").json()), 300, 3)
        print(f"  durée : {time.time() - t0:.0f} s")
        check(a and a["statut"] == "confirmee" and a["phase"] == "SUBMITTED", f"base de données : statut et étape ({a and (a['statut'], a['phase'])})")
        check(a and "GE-AF7-" in (a.get("preuve") or ""), f"preuve de confirmation du site : {a and a.get('preuve', '')[:120]}")
        recu = json.loads(urllib.request.urlopen(BASE + "/fake-site/_candidatures").read())
        r = recu[0] if recu else {}
        check(len(recu) == 1, "candidature reçue par le site, une seule fois (pas de doublon)")
        check(r.get("prenom") == "Camille" and r.get("nom") == "Durand" and r.get("courriel") == "camille.durand@example.com", "identité exacte")
        check(r.get("cv_name") == "CV_Camille_Durand.pdf" and r.get("cv_size") == len(cv), "CV reçu en entier")
        check(r.get("dispo") == "date" and "2027" in (r.get("date_debut") or ""), f"champ conditionnel : disponibilité « date » + date ({r.get('dispo')}, {r.get('date_debut')})")
        check((r.get("niveau") or "").startswith("Master"), f"niveau d'études tiré du profil ({r.get('niveau')})")
        check(len(r.get("motivation") or "") > 30, f"motivation rédigée ({(r.get('motivation') or '')[:90]}…)")
        check(not r.get("salaire") and not r.get("site_web"), "gratification laissée vide (pas inventée), champ piège vide")
        check(r.get("rgpd") == "on", "consentement coché (envoi automatique autorisé par le candidat)")
        hist = api.get(BASE + f"/api/applications/{app_id}/history").json() if api.get(BASE + f"/api/applications/{app_id}/history").ok else []
        ctx.close()
finally:
    srv.terminate()
    steps = [json.loads(l) for l in open(Path(tmp) / "server.log") if l.startswith("{") and '"agent_step"' in l]
    print("\nTrace de l'agent (sans donnée personnelle) :")
    for s in steps:
        print(f"  étape {s['step']:>2} · {s['page']:<28} · précédent : {s['last_result'][:40]:<40} · {', '.join(s['actions'])}  ({s.get('cost_usd', 0)} $)")
    print(f"Coût total de l'agent : {sum(s.get('cost_usd', 0) for s in steps):.3f} $")
print(f"\n{sum(ok)}/{len(ok)} vérifications réussies.")
sys.exit(0 if ok and all(ok) else 1)
