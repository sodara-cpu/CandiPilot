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
       "CANDIPILOT_ROBOT": "extension", "CANDIPILOT_FAKE_LLM": "1", "CANDIPILOT_DEFAULT_SOURCES": "0", "CANDIPILOT_TEST_PORTAL": "1", "CANDIPILOT_DATA": tmp}
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
        check(sw.evaluate("injectLink()") >= 1 and page.evaluate("document.querySelectorAll('meta[name=candipilot]').length") == 1,
              "à l'installation, l'extension s'active dans l'onglet CandiPilot déjà ouvert (sans recharger)")

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
        captcha_on = lambda oid, on: urllib.request.urlopen(urllib.request.Request(BASE + f"/fake-jobteaser/_captcha/{oid}?on={on}", method="POST"))
        orsay_oid = next(o["url"] for o in offers if o["entreprise"] == "Banque Orsay").rstrip("/").split("/")[-1]
        captcha_on(orsay_oid, 1)               # le site affiche une vérification anti-robot sur cette offre
        r = api.post(BASE + "/api/launch")
        check(r.status == 202 and r.json()["started"] == 2, "lancement validé")
        poll()
        check(until(lambda: get("Banque Orsay")["statut"] == "attente_utilisateur" and get("Acme SA")["statut"] == "confirmee", 90),
              "CAPTCHA : le robot s'arrête et passe la main (« À toi de jouer »), l'autre candidature part")
        check("CAPTCHA" in get("Banque Orsay")["detail"] and len(subs()) == 1, "CAPTCHA jamais contourné : rien n'est envoyé pour cette offre")
        check(api.post(BASE + f"/api/applications/{apps['Acme SA']}/resume").status == 409, "reprise refusée pour une candidature qui n'attend rien")
        captcha_on(orsay_oid, 0)               # le candidat a fait la vérification lui-même
        check(api.post(BASE + f"/api/applications/{apps['Banque Orsay']}/resume").status == 202, "« Reprendre l'envoi » après la vérification")
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

        # Autres plateformes courantes : chacune reconnue par son balisage propre.
        for name, first, phone, cv in (("workday", "[data-automation-id=legalNameSection_firstName]", "[data-automation-id=phone-number]", "[data-automation-id=file-upload-input-ref]"),
                                       ("workable", "input[name=firstname]", "input[name=phone]", "[data-ui=resume] input"),
                                       ("teamtailor", "input[name='candidate[first_name]']", "input[name='candidate[phone]']", "input[name='candidate[resume]']")):
            pg, res = fill_page(f"/fake-ats/apply/{name}")
            check(res["platform"] == name and pg.eval_on_selector(first, "e => e.value") == "Alex" and pg.eval_on_selector(phone, "e => e.value") == "+33600000000"
                  and pg.eval_on_selector(cv, "e => e.files.length") == 1 and not pg.evaluate("window.submitted === true"),
                  f"{name} reconnu : identité, téléphone, CV ; rien n'est envoyé")
        pg, res = fill_page("/fake-ats/apply/ashby")
        check(res["platform"] == "ashby" and pg.eval_on_selector("[name=_systemfield_name]", "e => e.value") == "Alex Martin",
              "Ashby reconnu : nom complet")
        pg, res = fill_page("/fake-ats/apply/smartrecruiters")
        check(pg.evaluate("document.getElementById('sr').shadowRoot.getElementById('phone-number-input').value") == "+33600000000",
              "SmartRecruiters : champs dans ses composants (shadow DOM) remplis")

        # Formulaire intégré depuis un site non autorisé : repéré pour demander l'accès (une fois par site).
        other = ctx.new_page()
        other.goto(BASE + "/fake-form/autre-site")
        tid = sw.evaluate("chrome.tabs.query({url: '*://127.0.0.1/fake-form/autre-site*'}).then(t => t[0].id)")
        check(sw.evaluate(f"framesNeedingAccess({tid})") == ["https://apply.example.org"], "site non autorisé repéré : l'accès sera demandé au candidat")
        check(sw.evaluate(f"framesNeedingAccess({tab_id})") == [], "aucune demande d'accès inutile")

        # 5c. Offre sur le site d'une entreprise (SmartRecruiters…) : dossier préparé par le serveur, sans l'extension.
        s = api.post(BASE + "/api/offers", data={"url": "https://jobs.smartrecruiters.com/acme/123", "poste": "Stage Audit",
                                                 "entreprise": "Acme Audit", "apply_type": "external"})
        ext_app = next(o["application_id"] for o in api.get(BASE + "/api/offers").json() if o["id"] == s.json()["id"])
        api.post(BASE + f"/api/applications/{ext_app}/prepare")
        a = until(lambda: (lambda x: x if x["statut"] != "preparation" else None)(api.get(BASE + f"/api/applications/{ext_app}").json()), 30)
        check(a and a["statut"] == "a_valider" and a["mode"] == "manual" and "Remplir ce formulaire" in a["detail"],
              "offre sur le site d'une entreprise : dossier préparé par le serveur (pas d'accès refusé dans l'extension)")

        # 5d. Postuler sur le site de l'entreprise : l'extension ouvre l'offre, clique « Apply », remplit ; le candidat
        #     envoie ; la page de confirmation sert de preuve.
        api.put(BASE + "/api/workflow", data={"submit_mode": "assisted"})      # le candidat envoie lui-même
        s = api.post(BASE + "/api/offers", data={"url": BASE + "/fake-ats/offer/aventure", "poste": "Stage Audit",
                                                 "entreprise": "Acme Corp", "apply_type": "external"})
        site_app = next(o["application_id"] for o in api.get(BASE + "/api/offers").json() if o["id"] == s.json()["id"])
        api.post(BASE + f"/api/applications/{site_app}/prepare")
        until(lambda: api.get(BASE + f"/api/applications/{site_app}").json()["statut"] == "a_valider", 30)
        check(api.post(BASE + f"/api/applications/{site_app}/apply").status == 202, "« Postuler avec l'extension » sur une offre d'entreprise")
        sw.evaluate("() => { poll(); return 1; }")          # l'extension prend la tâche (sans attendre la fin)
        # (onglet ouvert par l'extension : piloté depuis l'extension, l'outil de test ne le voit pas)
        tab_js = "chrome.tabs.query({}).then(t => (t.find(x => x.url.includes('/fake-ats/apply/greenhouse?real=1')) || {}).id || 0)"
        site_tab = until(lambda: sw.evaluate(tab_js), 40)
        check(bool(site_tab), "agent IA : bouton inconnu des règles (« Rejoindre l'aventure ») trouvé et cliqué, formulaire atteint")
        in_tab = lambda js: sw.evaluate(f"chrome.scripting.executeScript({{target: {{tabId: {site_tab}}}, func: () => {js}}}).then(r => r[0].result)")
        check(until(lambda: in_tab("document.querySelector('#first_name').value") == "Alex", 20)
              and in_tab("document.querySelector('#resume').files.length") == 1, "formulaire de l'entreprise rempli (identité, CV)")
        check(until(lambda: in_tab("document.querySelector('#q_city').value") == "Paris", 15)
              and in_tab("document.querySelector('#q_degree').value") == "b5",
              "agent IA : questions maison comprises (ville, niveau d'études choisi dans la liste)")
        check(in_tab("document.querySelector('#q_salary').value") == "" and not in_tab("document.querySelector('#gdpr').checked"),
              "agent IA : salaire et consentement laissés au candidat")
        check(in_tab("document.querySelector('#q_hp').value") == "", "champ piège pour robots jamais rempli (ni par les règles, ni par l'IA)")
        check(api.get(BASE + f"/api/applications/{site_app}").json()["statut"] == "soumission", "rien n'est envoyé sans le candidat")
        in_tab("(document.querySelector('#question_902').value = 'Oui', document.querySelector('button[type=submit]').click(), 1)")   # le candidat envoie
        a = until(lambda: (lambda x: x if x["statut"] == "confirmee" else None)(api.get(BASE + f"/api/applications/{site_app}").json()), 30)
        check(a and "Thank you for applying" in a["preuve"], f"page de confirmation repérée : « Envoyée » avec preuve ({a and a['preuve'][:60]})")

        # 5e. Envoi automatique choisi par le candidat (mode auto + consentement) : l'agent coche le consentement et envoie.
        api.put(BASE + "/api/workflow", data={"submit_mode": "auto", "auto_consent": True})
        s = api.post(BASE + "/api/offers", data={"url": BASE + "/fake-ats/offer/greenhouse?auto=1", "poste": "Stage Contrôle",
                                                 "entreprise": "Auto Corp", "apply_type": "external"})
        auto_app = next(o["application_id"] for o in api.get(BASE + "/api/offers").json() if o["id"] == s.json()["id"])
        api.post(BASE + f"/api/applications/{auto_app}/prepare")
        until(lambda: api.get(BASE + f"/api/applications/{auto_app}").json()["statut"] == "a_valider", 30)
        api.post(BASE + f"/api/applications/{auto_app}/apply")
        sw.evaluate("() => { poll(); return 1; }")
        a = until(lambda: (lambda x: x if x["statut"] == "confirmee" else None)(api.get(BASE + f"/api/applications/{auto_app}").json()), 60)
        check(a and "Thank you for applying" in a["preuve"], "envoi automatique autorisé : l'agent remplit, accepte, envoie ; « Envoyée » avec preuve")

        s = api.post(BASE + "/api/offers", data={"url": "https://careers.exemple-inconnu.fr/job/42", "poste": "Stage Finance",
                                                 "entreprise": "Inconnue SA", "apply_type": "external"})
        unk = next(o["application_id"] for o in api.get(BASE + "/api/offers").json() if o["id"] == s.json()["id"])
        api.post(BASE + f"/api/applications/{unk}/prepare")
        until(lambda: api.get(BASE + f"/api/applications/{unk}").json()["statut"] == "a_valider", 30)
        api.post(BASE + f"/api/applications/{unk}/apply")
        sw.evaluate("() => { poll(); return 1; }")
        a = until(lambda: (lambda x: x if x["statut"] == "attente_utilisateur" else None)(api.get(BASE + f"/api/applications/{unk}").json()), 30)
        check(a and "Autoriser les sites carrières" in a["detail"], "site carrières pas encore autorisé : « À toi de jouer » avec la marche à suivre")

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
