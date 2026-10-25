"""Parcours en 5 étapes, modes de soumission et module de connexion (robot simulé, aucun envoi réel).

    CANDIPILOT_FAKE_ROBOT=1 CANDIPILOT_DATA=/tmp/cp .venv/bin/python -m uvicorn app.main:app --port 8765 > /tmp/cp.log 2>&1
    .venv/bin/python tests/test_workflow.py http://127.0.0.1:8765 /tmp/cp.log
"""
import re
import sys
import time
import uuid

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from test_api import Client, PDF  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
LOG = sys.argv[2] if len(sys.argv) > 2 else ""
passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        sys.exit(1)
    passed += 1
    print("✓", label)


def guide(c):
    return c.req("GET", "/api/guide")[1]


def wait_until(fn, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(1)
    return False


def new_user(tag):
    c = Client()
    # Une adresse IP par compte simulé (sinon la limite anti-abus des inscriptions s'applique).
    c.opener.addheaders.append(("CF-Connecting-IP", f"10.0.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"))
    email = f"{tag}{uuid.uuid4().hex[:6]}@example.com"
    s, _ = c.req("POST", "/api/auth/register", {"email": email, "password": "motdepasse1", "first_name": "Alex"})
    assert s == 201, s
    return c, email


def setup_until_step4(c):
    """Étapes 1 à 3 remplies, avec deux offres sélectionnées et préparées."""
    c.req("PUT", "/api/profile", {"last_name": "Martin", "phone": "+33600000000", "availability": "Mars 2027", "gender": "Femme"})
    c.req("POST", "/api/documents", {"type": "cv"}, files={"file": ("cv.pdf", PDF)})
    c.req("PUT", "/api/workflow", {"letter_source": "template"})
    c.req("PUT", "/api/preferences", {"metiers": ["finance"], "zones": [], "contrats": ["stage"], "min_score": 0})
    c.req("POST", "/api/search")
    wait_until(lambda: len(c.req("GET", "/api/offers")[1]) >= 4)
    offers = [o for o in c.req("GET", "/api/offers")[1] if o["apply_type"] == "jobteaser"][:2]
    for o in offers:
        c.req("POST", f"/api/applications/{o['application_id']}/select", {"selected": True})
    wait_until(lambda: all(c.req("GET", f"/api/applications/{o['application_id']}")[1]["statut"] == "a_valider" for o in offers))
    return [o["application_id"] for o in offers]


def statuses(c, ids):
    return [c.req("GET", f"/api/applications/{i}")[1]["statut"] for i in ids]


def answer_questions(c, ids):
    for i in ids:
        a = c.req("GET", f"/api/applications/{i}")[1]
        if a["questions"]:
            c.req("PUT", f"/api/applications/{i}", {"questions": [{"label": q["label"], "answer": "Réponse."} for q in a["questions"]]})


# ---------- 1. Parcours : étapes calculées sur les données ----------
c, email = new_user("parcours")
g = guide(c)
check([s["label"] for s in g["steps"]] == ["Complète ton profil", "Importe ton CV et ta lettre", "Choisis tes critères",
                                          "Choisis le mode de soumission", "Valide l'envoi"], "cinq étapes dans l'ordre")
check(g["current"] == 0 and "téléphone" in g["steps"][0]["help"] and "genre" in g["steps"][0]["help"], "étape 1 : champs manquants précis")
c.req("GET", "/api/profile")
check(guide(c)["current"] == 0, "ouvrir la page ne valide pas l'étape")
c.req("PUT", "/api/profile", {"last_name": "Martin", "phone": "+33600000000", "availability": "Mars 2027"})
check("genre" in guide(c)["steps"][0]["help"] and not guide(c)["steps"][0]["done"], "profil partiel : étape 1 toujours ouverte")
c.req("PUT", "/api/profile", {"gender": "Femme"})
g = guide(c)
check(g["steps"][0]["done"] and g["current"] == 1, "profil complet : étape 1 faite")
check("CV" in g["steps"][1]["help"] and "lettre" in g["steps"][1]["help"], "étape 2 : CV et choix de lettre manquants")
c.req("POST", "/api/documents", {"type": "cv"}, files={"file": ("cv.pdf", PDF)})
c.req("PUT", "/api/workflow", {"letter_source": "document"})
check("lettre de motivation (PDF)" in guide(c)["steps"][1]["help"], "lettre PDF choisie mais absente : signalée")
s, d = c.req("POST", "/api/documents", {"type": "lettre"}, files={"file": ("lettre.pdf", PDF)})
check(guide(c)["steps"][1]["done"], "CV + lettre importés : étape 2 faite")
check(c.req("PUT", "/api/profile", {"phone": "+33611111111"})[1]["letter_template"], "enregistrer le profil n'efface pas le modèle de lettre")
g = guide(c)
check(g["current"] == 2 and "métier" in g["steps"][2]["help"], "étape 3 : métier manquant")
c.req("PUT", "/api/preferences", {"metiers": ["finance"], "zones": ["Paris"], "contrats": ["stage"], "start_from": "2027-03-01", "min_score": 0})
check("offre sélectionnée" in guide(c)["steps"][2]["help"] or "recherche" in guide(c)["steps"][2]["help"], "étape 3 : sélection d'offre manquante")
c.req("POST", "/api/search")
wait_until(lambda: len(c.req("GET", "/api/offers")[1]) >= 4)
offers = c.req("GET", "/api/offers")[1]
check(len(offers) >= 4 and not any(o["statut"] in ("soumission", "confirmee") for o in offers), "la recherche ne déclenche aucun envoi")
jt = [o for o in offers if o["apply_type"] == "jobteaser"][:2]
for o in jt:
    c.req("POST", f"/api/applications/{o['application_id']}/select", {"selected": True})
ids = [o["application_id"] for o in jt]
wait_until(lambda: statuses(c, ids) == ["a_valider"] * 2)
a = c.req("GET", f"/api/applications/{ids[0]}")[1]
check(a["letter_kind"] == "document" and c.req("GET", f"/api/applications/{ids[0]}/letter")[1] == PDF,
      "préparation : la lettre importée est utilisée telle quelle, pas remplacée")
g = guide(c)
check(g["steps"][2]["done"] and g["current"] == 3, "offres sélectionnées : étape 3 faite")
check(c.req("GET", "/api/preferences")[1]["default_mode"] == "auto" and not guide(c)["steps"][3]["done"], "mode par défaut ≠ mode choisi")

# ---------- 2. Mode : enregistrement, persistance, aucun envoi ----------
c.req("PUT", "/api/workflow", {"submit_mode": "assisted"})
g = guide(c)
check(not g["steps"][3]["done"] and "préparation" in g["steps"][3]["help"] and "relecture" in g["steps"][3]["help"], "assisté : les deux questions sont demandées")
check(c.req("POST", "/api/launch")[0] == 422, "lancement refusé tant que les questions sont sans réponse")
c.req("PUT", "/api/workflow", {"want_prepare": False, "want_review": False})
c2 = Client()
c2.req("POST", "/api/auth/login", {"email": email, "password": "motdepasse1"})
wf = c2.req("GET", "/api/workflow")[1]
check(wf["submit_mode"] == "assisted" and wf["want_prepare"] == 0 and wf["want_review"] == 0, "choix conservés (nouvelle session)")
check(guide(c2)["steps"][3]["done"] and guide(c2)["current"] == 4, "étape 4 faite après réponses")
check(statuses(c, ids) == ["a_valider"] * 2, "changer de mode n'envoie rien")
check(all(c.req("GET", f"/api/applications/{i}")[1]["mode"] == "assisted" for i in ids), "mode appliqué aux dossiers sélectionnés")

# ---------- 3. Valider l'envoi : récapitulatif puis lancement ----------
L = c.req("GET", "/api/launch")[1]
check(len(L["items"]) == 2 and L["mode"] == "assisted" and L["letter"] and L["cv"], "récapitulatif : offres, mode, documents")
check(L["ready_count"] == 2 and statuses(c, ids) == ["a_valider"] * 2, "rien n'est parti avant confirmation")
s, r = c.req("POST", "/api/launch")
check(s == 202 and r["started"] == 2, "Valider et démarrer le robot : 2 envois lancés")
check(c.req("POST", "/api/launch")[0] == 409, "pas de double lancement")
wait_until(lambda: statuses(c, ids) == ["confirmee"] * 2)
R = c.req("GET", "/api/launch")[1]["results"]
check(len(R["sent"]) == 2 and all("Preuve" in x["reason"] for x in R["sent"]), "résultat : envoyées avec preuve")
check(guide(c)["current"] is None, "parcours terminé")
check(c.req("POST", f"/api/applications/{ids[0]}/select", {"selected": False})[0] == 409, "candidature envoyée : non retirable")

# ---------- 4. Les quatre combinaisons en semi-assisté et assisté ----------
for mode in ("assisted", "manual"):
    for prep in (True, False):
        for review in (True, False):
            u, _ = new_user("combo")
            ids = setup_until_step4(u)
            u.req("PUT", "/api/workflow", {"submit_mode": mode, "want_prepare": prep, "want_review": review})
            subs = [x["label"] for x in guide(u)["steps"][4]["subs"]]
            tag = f"{mode} / préparer={'oui' if prep else 'non'} / relire={'oui' if review else 'non'}"
            check(("Vérifie ou personnalise tes dossiers" in subs) == prep and ("Relis chaque candidature" in subs) == review
                  and subs[-1] == "Valide et démarre le robot", f"{tag} : sous-étapes adaptées")
            L = u.req("GET", "/api/launch")[1]
            expect_ready = 0 if (prep or review) else 2
            check(L["ready_count"] == expect_ready, f"{tag} : {expect_ready} dossier(s) prêt(s) avant personnalisation/relecture")
            if prep:
                for i in ids:
                    u.req("PUT", f"/api/applications/{i}", {"customized": True})
            if review:
                for i in ids:
                    check(u.req("PUT", f"/api/applications/{i}", {"reviewed": True})[0] == 200, f"{tag} : relecture → envoi autorisé")
            check(statuses(u, ids) == ["a_valider"] * 2, f"{tag} : aucun envoi avant confirmation")
            s, r = u.req("POST", "/api/launch")
            if mode == "manual":
                check(s == 202 and r["manual"] == 2 and r["started"] == 0, f"{tag} : dossiers prêts à envoyer soi-même, robot n'envoie pas")
                R = u.req("GET", "/api/launch")[1]["results"]
                check(len(R["progress"]) == 2 and any(a.get("action") == "mark-sent" for a in R["progress"][0]["actions"]), f"{tag} : « En cours » avec action « Je l'ai envoyée »")
                u.req("POST", f"/api/applications/{ids[0]}/mark-sent")
                check(len(u.req("GET", "/api/launch")[1]["results"]["sent"]) == 1, f"{tag} : envoyée après déclaration")
            else:
                check(s == 202 and r["started"] == 2, f"{tag} : robot lancé")
                check(wait_until(lambda: statuses(u, ids) == ["confirmee"] * 2), f"{tag} : envoyées après confirmation")

# ---------- 5. Automatique : pas de questions facultatives, dossier incomplet bloqué ----------
u, _ = new_user("auto")
ids = setup_until_step4(u)
# Offre dont le formulaire (simulé) pose une question au candidat.
u.req("POST", "/api/offers", {"url": "https://example.invalid/q", "poste": "Analyste", "entreprise": "Acme SA"})
q_id = next(o["application_id"] for o in u.req("GET", "/api/offers")[1] if o["entreprise"] == "Acme SA")
u.req("POST", f"/api/applications/{q_id}/select", {"selected": True})
wait_until(lambda: statuses(u, [q_id]) == ["a_valider"])
u.req("PUT", "/api/workflow", {"submit_mode": "auto", "auto_consent": True, "want_prepare": True, "want_review": True})
g = guide(u)
check(g["steps"][3]["done"] and [x["label"] for x in g["steps"][4]["subs"]] == ["Valide et démarre le robot"], "automatique : ni personnalisation ni relecture")
L = u.req("GET", "/api/launch")[1]
with_q = [i["id"] for i in L["items"] if any("question" in b["label"] for b in i["blockers"])]
check(with_q == [q_id], "automatique : question sans réponse détectée avant lancement")
s, r = u.req("POST", "/api/launch")
check(s == 202 and r["started"] == 2 and r["skipped"] == 1, "automatique : 2 envoyés, le dossier incomplet est écarté")
if with_q:
    check(with_q[0] not in [x["id"] for x in u.req("GET", "/api/launch")[1]["results"]["progress"] + u.req("GET", "/api/launch")[1]["results"]["sent"]],
          "automatique : dossier avec question sans réponse non envoyé")
    item = next(i for i in u.req("GET", "/api/launch")[1]["items"] if i["id"] == with_q[0])
    check(any(b.get("cta") == "Compléter" for b in item["blockers"]), "blocage expliqué avec action « Compléter »")
answer_questions(u, [q_id])
check(next(i for i in u.req("GET", "/api/launch")[1]["items"] if i["id"] == q_id)["ready"], "réponse donnée : dossier prêt (non envoyé tant qu'on ne relance pas)")
check(statuses(u, [q_id]) == ["a_valider"], "toujours pas envoyé avant nouvelle confirmation")

u, _ = new_user("incomplet")
ids = setup_until_step4(u)
u.req("PUT", "/api/workflow", {"submit_mode": "assisted", "want_prepare": False, "want_review": False})
u.req("PUT", "/api/profile", {"phone": ""})
L = u.req("GET", "/api/launch")[1]
check(L["ready_count"] == 0 and "téléphone" in L["missing_profile"], "« Non » à la relecture n'autorise pas un dossier incomplet")
check(u.req("POST", "/api/launch")[0] == 409 and statuses(u, ids) == ["a_valider"] * 2, "lancement refusé, rien envoyé")

# ---------- 6. Échec : raison, reprise sans double envoi ----------
a_id = ids[0]
u.req("PUT", "/api/profile", {"phone": "+33600000000"})
import sqlite3, os  # noqa: E401
db_path = os.path.join(os.environ.get("CANDIPILOT_DATA", ""), "candipilot.db")
if os.path.exists(db_path):
    con = sqlite3.connect(db_path)
    con.execute("UPDATE applications SET statut='echec', launched=1, detail='Connexion JobTeaser non détectée' WHERE id=?", (a_id,))
    con.commit(); con.close()
    R = u.req("GET", "/api/launch")[1]["results"]
    f = next(x for x in R["failed"] if x["id"] == a_id)
    check("Connexion" in f["reason"] and any(x["cta"] == "Se connecter" for x in f["actions"]) and any(x["cta"] == "Reprendre" for x in f["actions"]),
          "non envoyée : raison + « Se connecter » / « Reprendre »")
    u.req("POST", f"/api/applications/{a_id}/retry")
    a = u.req("GET", f"/api/applications/{a_id}")[1]
    check(a["statut"] == "a_valider" and not a["launched"], "reprendre : retour au récapitulatif, sans envoi automatique")

# ---------- 7. Connexion : mot de passe oublié ----------
u, email = new_user("reset")
check(u.req("POST", "/api/auth/register", {"email": "x" + email, "password": "seulementdeslettres"})[0] == 422, "mot de passe sans chiffre refusé")
check(Client().req("POST", "/api/auth/forgot", {"email": "inconnu@example.com"})[0] == 202, "mot de passe oublié : même réponse si le compte n'existe pas")
check(Client().req("POST", "/api/auth/forgot", {"email": email})[0] == 202, "mot de passe oublié : demande acceptée")
if LOG:
    time.sleep(0.5)
    tokens = re.findall(r"reinitialiser\?token=([\w-]+)", open(LOG, encoding="utf-8").read())
    token = tokens[-1]
    check(Client().req("POST", "/api/auth/reset", {"token": "faux", "password": "nouveau123"})[0] == 400, "lien invalide refusé")
    r = Client()
    check(r.req("POST", "/api/auth/reset", {"token": token, "password": "nouveau123"})[0] == 200, "réinitialisation avec le lien")
    check(r.req("GET", "/api/auth/me")[0] == 200, "connecté après réinitialisation")
    check(u.req("GET", "/api/auth/me")[0] == 401, "anciennes sessions déconnectées")
    check(Client().req("POST", "/api/auth/reset", {"token": token, "password": "autre1234"})[0] == 400, "lien à usage unique")
    check(Client().req("POST", "/api/auth/login", {"email": email, "password": "nouveau123"})[0] == 200, "connexion avec le nouveau mot de passe")
    u = r
check(u.req("PUT", "/api/auth/email", {"email": "n" + email, "password": "faux"})[0] == 401, "changement d'e-mail : mot de passe exigé")
pw = "nouveau123" if LOG else "motdepasse1"
check(u.req("PUT", "/api/auth/email", {"email": "n" + email, "password": pw})[0] == 200, "changement d'e-mail")
check(u.req("POST", "/api/auth/logout-all")[0] == 200 and u.req("GET", "/api/auth/me")[0] == 401, "se déconnecter partout")

print(f"\n{passed} vérifications réussies.")
