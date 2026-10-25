"""Extension CandiPilot de bout en bout : vrai Chromium + extension, faux portail JobTeaser (aucun vrai site).

    .venv/bin/python tests/test_extension.py <port>

Démarre son propre serveur (robot « extension », faux portail), puis vérifie : liaison automatique,
détection de la session JobTeaser, recherche, préparation, envoi des candidatures dans le navigateur
de l'utilisateur, et remplissage d'un formulaire quelconque sans l'envoyer.
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

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8795
BASE = f"http://127.0.0.1:{PORT}"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        raise SystemExit(1)
    passed += 1
    print("✓", label)


def until(fn, timeout=60, every=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


tmp = tempfile.mkdtemp()
env = {**{k: v for k, v in os.environ.items() if k not in ("CANDIPILOT_FAKE_ROBOT", "DATABASE_URL")},
       "CANDIPILOT_ROBOT": "extension", "CANDIPILOT_TEST_PORTAL": "1", "CANDIPILOT_DATA": tmp}
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], env=env, cwd=str(ROOT),
                          stdout=open(Path(tmp) / "server.log", "w"), stderr=subprocess.STDOUT)


def _up():
    try:
        return urllib.request.urlopen(BASE + "/api/openapi.json").status == 200
    except Exception:
        return False


errors = []
try:
    until(_up, 30)
    with sync_playwright() as p:
        ext = str(ROOT / "extension")
        ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), channel="chromium", headless=True,
                                                   args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
        poll = lambda: sw.evaluate("poll()")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        api = page.request

        # 1. Inscription : l'extension se relie seule.
        page.goto(BASE + "/#/register")
        page.fill("input[name=first_name]", "Alex"); page.fill("input[name=last_name]", "Martin")
        page.fill("input[name=email]", f"ext{uuid.uuid4().hex[:6]}@example.com")
        page.fill("input[name=password]", "motdepasse1"); page.fill("input[name=confirm]", "motdepasse1")
        page.get_by_role("button", name="Créer mon compte").click()
        check(until(lambda: api.get(BASE + "/api/agent/status").json().get("linked"), 30), "extension reliée toute seule au compte")
        check(page.evaluate("document.documentElement.dataset.candipilotExtension") == json.load(open(ROOT / "extension/manifest.json"))["version"], "l'appli voit que l'extension est installée")

        api.put(BASE + "/api/profile", data={"phone": "+33600000000", "availability": "Mars 2027", "gender": "Homme",
                                              "linkedin": "https://www.linkedin.com/in/alex-martin"})
        api.post(BASE + "/api/documents", multipart={"type": "cv", "file": {"name": "Mon_CV.pdf", "mimeType": "application/pdf", "buffer": PDF}})
        api.put(BASE + "/api/workflow", data={"letter_source": "template"})
        api.put(BASE + "/api/preferences", data={"metiers": ["analyste"], "zones": [], "contrats": ["stage"], "start_from": "2027-03-01", "min_score": 0})
        api.put(BASE + "/api/jobteaser", data={"portal_url": BASE + "/fake-jobteaser"})

        # 2. Sessions : JobTeaser pas connecté, puis connecté après connexion dans le navigateur.
        jt = lambda: next(s for s in api.get(BASE + "/api/agent/status").json()["sites"] if s["key"] == "jobteaser")
        api.post(BASE + "/api/agent/check"); poll()
        check(until(lambda: jt()["connected"] is False, 40), "JobTeaser détecté comme non connecté")
        r = api.post(BASE + "/api/search")
        check(r.status == 422 and "JobTeaser" in r.json()["detail"], "recherche refusée tant que JobTeaser n'est pas connecté")
        login = ctx.new_page()
        login.goto(BASE + "/fake-jobteaser/login")
        login.fill("input[name=email]", "alex@ecole.fr"); login.fill("input[name=password]", "bonmotdepasse")
        login.get_by_role("button", name="Se connecter").click()
        login.wait_for_url("**/job-offers")
        login.close()
        api.post(BASE + "/api/agent/check"); poll()
        check(until(lambda: jt()["connected"], 40), "JobTeaser connecté : session du navigateur détectée (aucun mot de passe donné)")

        # 3. Recherche dans le navigateur de l'utilisateur.
        check(api.post(BASE + "/api/search").status == 202, "recherche lancée")
        poll()
        offers = until(lambda: (lambda o: o if len(o) >= 2 else None)(api.get(BASE + "/api/offers").json()), 60)
        names = sorted(o["entreprise"] for o in offers)
        check(names == ["Acme SA", "Banque Orsay"], f"offres trouvées et filtrées (externe et trop tôt écartées) : {names}")
        check(any(o["poste"] == "Analyste M&A" for o in offers), "titre nettoyé (« - Mars 2027 » retiré)")
        check(all(o["start_date"] >= "2027-03-01" for o in offers), "dates de début lues sur les pages d'offres")

        # 4. Préparation (lecture des formulaires) puis lancement.
        for o in offers:
            api.post(BASE + f"/api/applications/{o['application_id']}/select", data={"selected": True})
        poll()
        apps = {o["entreprise"]: o["application_id"] for o in offers}
        get = lambda name: api.get(BASE + f"/api/applications/{apps[name]}").json()
        check(until(lambda: get("Banque Orsay")["statut"] == "a_valider" and get("Acme SA")["statut"] == "a_valider", 60),
              "dossiers préparés par l'extension")
        check(get("Banque Orsay")["letter_needed"] == 1 and get("Acme SA")["questions"] == [{"label": "Pourquoi notre fonds ?", "answer": ""}],
              "formulaires lus : lettre demandée, question du recruteur")
        api.put(BASE + f"/api/applications/{apps['Acme SA']}", data={"questions": [{"label": "Pourquoi notre fonds ?", "answer": "Pour sa stratégie de dette privée."}]})
        api.put(BASE + "/api/workflow", data={"submit_mode": "auto", "auto_consent": True})
        subs = lambda: json.loads(urllib.request.urlopen(BASE + "/fake-jobteaser/_submissions").read())
        check(subs() == [], "rien n'est envoyé avant « Valider et démarrer »")
        r = api.post(BASE + "/api/launch")
        check(r.status == 202 and r.json()["started"] == 2, "lancement validé")
        poll()
        check(until(lambda: get("Banque Orsay")["statut"] == "confirmee" and get("Acme SA")["statut"] == "confirmee", 90),
              "candidatures envoyées depuis le navigateur, avec preuve")
        s = {x["offer"]: x for x in subs()}
        check(len(subs()) == 2 and s["101"]["gender"] == "GENDER_MALE" and s["101"]["resume"] == "Mon_CV.pdf" and s["101"]["resume_size"] == len(PDF),
              "formulaire JobTeaser : genre et CV du candidat déposés")
        check(s["101"]["cover"].startswith("Lettre_") and s["101"]["cover"].endswith(".pdf"), "lettre de motivation PDF déposée")
        check(s["102"]["answers"] == ["Pour sa stratégie de dette privée."], "réponse du candidat saisie")
        check(api.post(BASE + "/api/launch").status == 409, "pas de double envoi")

        # 5. Remplissage d'un formulaire quelconque (n'importe quel site) : rien n'est envoyé.
        form = ctx.new_page()
        form.goto(BASE + "/fake-form")
        tab_id = sw.evaluate("chrome.tabs.query({url: '*://127.0.0.1/fake-form*'}).then(t => t[0].id)")
        res = sw.evaluate(f"fillTab({tab_id})")
        v = lambda sel: form.eval_on_selector(sel, "e => e.value")
        check(v("#fn") == "Alex" and v("#ln") == "Martin" and v("input[type=tel]") == "+33600000000" and "@example.com" in v("input[type=email]"),
              "formulaire quelconque : prénom, nom, téléphone, e-mail remplis")
        check(form.eval_on_selector("#cvf", "e => e.files.length && e.files[0].name") == "Mon_CV.pdf", "formulaire quelconque : CV déposé")
        check(any("Comment avez-vous connu" in t for t in res["todo"]) and any("cocher" in t for t in res["todo"]),
              "questions inconnues et cases à cocher laissées au candidat (surlignées)")
        check(not form.evaluate("window.submitted === true"), "le formulaire n'est jamais envoyé par l'extension")

        def fill_page(path):
            pg = ctx.new_page()
            pg.goto(BASE + path)
            tid = sw.evaluate(f"chrome.tabs.query({{url: '*://127.0.0.1{path}*'}}).then(t => t[0].id)")
            return pg, sw.evaluate(f"fillTab({tid})")

        # 5b. Lot 4 : formulaires Greenhouse et Lever reconnus, composants, étapes, cadres.
        gh, res = fill_page("/fake-ats/apply/greenhouse")
        v = lambda sel: gh.eval_on_selector(sel, "e => e.value")
        check(res["platform"] == "greenhouse" and v("#first_name") == "Alex" and v("#last_name") == "Martin" and v("#phone") == "+33600000000",
              "Greenhouse reconnu : identité et téléphone remplis")
        check(gh.eval_on_selector("#resume", "e => e.files.length && e.files[0].name") == "Mon_CV.pdf", "Greenhouse : CV déposé dans le champ caché")
        check(v("#question_901") == "https://www.linkedin.com/in/alex-martin", "Greenhouse : question LinkedIn reconnue")
        check(any("legally authorized" in t for t in res["todo"]) and v("#question_902") == "" and not gh.eval_on_selector("#gdpr", "e => e.checked"),
              "Greenhouse : question d'autorisation de travail et consentement laissés au candidat")
        check(not gh.evaluate("window.submitted === true"), "Greenhouse : rien n'est envoyé")

        lv, res = fill_page("/fake-ats/apply/lever")
        v = lambda sel: lv.eval_on_selector(sel, "e => e.value")
        check(res["platform"] == "lever" and v("input[name=name]") == "Alex Martin" and v("input[name='urls[LinkedIn]']").endswith("alex-martin"),
              "Lever reconnu : nom complet et lien LinkedIn remplis")
        check(lv.eval_on_selector("input[name=resume]", "e => e.files.length") == 1 and v("input[name=org]") == "", "Lever : CV déposé, entreprise actuelle laissée vide")

        st, res = fill_page("/fake-form/etapes")
        check(st.evaluate("document.getElementById('comp').shadowRoot.getElementById('mail').value").endswith("@example.com"),
              "champ dans un composant (shadow DOM) rempli")
        check(res["steps"] and res["watching"], "formulaire en plusieurs étapes détecté")
        st.click("#next")                    # le candidat passe lui-même à l'étape suivante
        check(until(lambda: st.eval_on_selector("#tel", "e => e.value") == "+33600000000", 10), "étape 2 remplie automatiquement après « Suivant »")
        check(st.eval_on_selector("#cv2", "e => e.files.length") == 1 and not st.evaluate("window.submitted === true"),
              "étape 2 : CV déposé, et rien n'est envoyé")

        fr, res = fill_page("/fake-form/cadre")
        check(res["platform"] == "greenhouse" and fr.frame_locator("#gh").locator("#first_name").input_value() == "Alex",
              "formulaire intégré dans un cadre (iframe) rempli")

        # 6. Sécurité du jeton.
        bad = urllib.request.Request(BASE + "/api/agent/next", headers={"Authorization": "Bearer faux"})
        try:
            urllib.request.urlopen(bad)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check(code == 401, "jeton d'extension invalide refusé")
        ctx.close()
finally:
    server.terminate()

check(not errors, f"aucune erreur dans les pages : {errors or 'aucune'}")
print(f"\n{passed} vérifications réussies.")
