"""Test de bout en bout de l'API CandiPilot (serveur lancé avec CANDIPILOT_FAKE_ROBOT=1).

    CANDIPILOT_FAKE_ROBOT=1 CANDIPILOT_DATA=/tmp/cp .venv/bin/python -m uvicorn app.main:app --port 8765
    .venv/bin/python tests/test_api.py http://127.0.0.1:8765
"""
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
passed = 0


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def req(self, method, path, body=None, files=None):
        headers, data = {}, None
        if files:
            boundary = uuid.uuid4().hex
            parts = []
            for k, v in (body or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for k, (name, content) in files.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                             f'Content-Type: application/pdf\r\n\r\n'.encode() + content + b"\r\n")
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(r) as resp:
                raw = resp.read()
                ct = resp.headers.get("content-type", "")
                return resp.status, (json.loads(raw) if "json" in ct else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw


def check(cond, label):
    global passed
    if not cond:
        raise AssertionError(label)
    passed += 1
    print("  ✓", label)


def wait_status(c, app_id, wanted, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s, a = c.req("GET", f"/api/applications/{app_id}")
        if a["statut"] in wanted:
            return a
        time.sleep(0.5)
    raise AssertionError(f"statut {wanted} non atteint (actuel : {a['statut']})")


def main():
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/api/openapi.json")
            break
        except Exception:
            time.sleep(0.5)

    a, b = Client(), Client()
    email = f"test{uuid.uuid4().hex[:6]}@example.com"

    print("Authentification")
    check(a.req("GET", "/api/dashboard")[0] == 401, "accès refusé sans connexion")
    check(a.req("POST", "/api/auth/register", {"email": "pas-un-email", "password": "12345678"})[0] == 422, "e-mail invalide refusé")
    check(a.req("POST", "/api/auth/register", {"email": email, "password": "court"})[0] == 422, "mot de passe trop court refusé")
    s, r = a.req("POST", "/api/auth/register", {"email": email, "password": "motdepasse1", "first_name": "Christian"})
    check(s == 201, "inscription")
    check(a.req("POST", "/api/auth/register", {"email": email, "password": "motdepasse1"})[0] == 409, "doublon d'e-mail refusé")
    check(a.req("GET", "/api/auth/me")[1]["first_name"] == "Christian", "session ouverte après inscription")
    check(a.req("POST", "/api/auth/logout")[0] == 200 and a.req("GET", "/api/auth/me")[0] == 401, "déconnexion")
    check(a.req("POST", "/api/auth/login", {"email": email, "password": "faux-mdp"})[0] == 401, "mauvais mot de passe refusé")
    check(a.req("POST", "/api/auth/login", {"email": email.upper(), "password": "motdepasse1"})[0] == 200, "connexion (e-mail insensible à la casse)")

    print("Profil")
    s, p = a.req("PUT", "/api/profile", {"first_name": "Christian", "last_name": "Sodara", "gender": "Homme",
                                         "availability": "Mars 2027", "duration": "6 mois", "competences": "data python",
                                         "letter_template": "Madame, Monsieur,\n\nCandidature au poste « {role} » au sein {de_company}.\n\nChristian SODARA"})
    check(s == 200 and p["last_name"] == "Sodara", "mise à jour du profil")
    check(a.req("PUT", "/api/profile", {"gender": "Robot"})[0] == 422, "genre invalide refusé")

    print("Documents")
    check(a.req("POST", "/api/documents", {"type": "cv"}, {"file": ("cv.txt", b"pas un pdf")})[0] == 415, "fichier non PDF refusé")
    s, d1 = a.req("POST", "/api/documents", {"type": "cv"}, {"file": ("CV_Finance.pdf", PDF)})
    check(s == 201 and d1["is_default"] == 1, "import du premier CV (par défaut)")
    s, d2 = a.req("POST", "/api/documents", {"type": "cv"}, {"file": ("CV_Finance.pdf", PDF)})
    check(d2["version"] == 2 and d2["is_default"] == 0, "nouvelle version du même CV")
    check(a.req("PATCH", f"/api/documents/{d2['id']}", {"is_default": True, "name": "CV_v2.pdf"})[0] == 200, "renommer + définir par défaut")
    docs = a.req("GET", "/api/documents")[1]
    check([d["name"] for d in docs if d["is_default"]] == ["CV_v2.pdf"], "un seul CV par défaut")
    check(a.req("GET", f"/api/documents/{d1['id']}/file")[1].startswith(b"%PDF"), "téléchargement du CV")
    check(a.req("DELETE", f"/api/documents/{d1['id']}")[0] == 200 and len(a.req("GET", "/api/documents")[1]) == 1, "suppression d'un document")

    print("Critères")
    check(a.req("POST", "/api/search")[0] == 422, "recherche refusée sans métier ciblé")
    check(a.req("PUT", "/api/preferences", {"metiers": ["data"], "start_from": "mars"})[0] == 422, "date invalide refusée")
    s, pr = a.req("PUT", "/api/preferences", {"metiers": ["data", " "], "zones": ["France"], "contrats": ["stage"],
                                               "start_from": "2027-03-01", "min_score": 40})
    check(s == 200 and pr["metiers"] == ["data"], "enregistrement des critères (valeurs vides retirées)")
    check(a.req("POST", "/api/copilot/pause")[1]["paused"] is True, "copilote en pause")
    check(a.req("POST", "/api/copilot/resume")[1]["paused"] is False, "copilote réactivé")

    print("Recherche + matching (robot simulé)")
    check(a.req("POST", "/api/search")[0] == 202, "recherche lancée")
    t0 = time.time()
    while time.time() - t0 < 30 and not a.req("GET", "/api/offers")[1]:
        time.sleep(0.5)
    offers = a.req("GET", "/api/offers")[1]
    check(len(offers) >= 4, f"offres collectées ({len(offers)})")
    check(all(0 <= o["score"] <= 100 for o in offers) and offers == sorted(offers, key=lambda o: -o["score"]), "scores 0-100, classés")
    a.req("POST", "/api/search")
    time.sleep(3)
    check(len(a.req("GET", "/api/offers")[1]) == len(offers), "pas de doublon après une 2e recherche")

    print("Offres (CRUD)")
    s, o = a.req("POST", "/api/offers", {"url": "https://skema.jobteaser.com/fr/job-offers/test-1", "poste": "Data analyst",
                                         "entreprise": "Acme", "localisation": "Paris, France", "contrat": "Stage 6 mois",
                                         "start_date": "2027-04-01"})
    check(s == 201 and o["score"] > 0, "ajout manuel d'une offre (avec score)")
    check(a.req("POST", "/api/offers", {"url": "https://skema.jobteaser.com/fr/job-offers/test-1", "poste": "x", "entreprise": "y"})[0] == 409, "doublon d'offre refusé")
    check(a.req("POST", "/api/offers", {"url": "javascript:alert(1)", "poste": "x", "entreprise": "y"})[0] == 422, "URL non http refusée")
    check(a.req("PUT", f"/api/offers/{o['id']}", {"url": "https://skema.jobteaser.com/fr/job-offers/test-1", "poste": "Data analyst junior",
                                                  "entreprise": "Acme", "apply_type": "jobteaser"})[0] == 200, "modification d'une offre")
    check(len(a.req("GET", "/api/offers?q=Acme")[1]) == 1, "filtre de recherche")

    print("Isolation entre comptes")
    b.req("POST", "/api/auth/register", {"email": "autre" + email, "password": "motdepasse2"})
    check(b.req("GET", "/api/offers")[1] == [], "l'autre compte ne voit pas les offres")
    check(b.req("DELETE", f"/api/offers/{o['id']}")[0] == 404, "l'autre compte ne peut pas supprimer l'offre")
    check(b.req("GET", f"/api/documents/{d2['id']}/file")[0] == 404, "l'autre compte ne peut pas lire le CV")

    print("Cycle de vie d'une candidature")
    apps = a.req("GET", "/api/applications")[1]
    target = next(x for x in apps if x["offer_id"] == o["id"])
    check(target["statut"] == "qualifiee", "offre ajoutée → « À préparer »")
    check(a.req("POST", f"/api/applications/{target['id']}/validate")[0] == 409, "validation refusée avant préparation")
    check(a.req("POST", f"/api/applications/{target['id']}/prepare")[0] == 202, "préparation lancée")
    app = wait_status(a, target["id"], {"a_valider"})
    check(app["letter_path"] and "Acme" in app["letter_text"]
          and a.req("GET", f"/api/applications/{target['id']}/letter")[1][:4] == b"%PDF", "lettre PDF générée et adaptée")
    check(a.req("GET", f"/api/applications/{target['id']}/letter")[1][:4] == b"%PDF", "lettre téléchargeable")
    s, app = a.req("PUT", f"/api/applications/{target['id']}", {"letter_text": app["letter_text"] + "\nPS : test"})
    check("PS : test" in app["letter_text"], "lettre modifiée et PDF regénéré")
    if app["questions"]:
        check(a.req("POST", f"/api/applications/{target['id']}/validate")[0] == 422, "envoi auto refusé avec question ouverte")
        qs = [{"label": q["label"], "answer": "Parce que…"} for q in app["questions"]]
        a.req("PUT", f"/api/applications/{target['id']}", {"questions": qs})
    check(a.req("PUT", f"/api/applications/{target['id']}", {"mode": "nimporte"})[0] == 422, "mode invalide refusé")
    check(a.req("POST", f"/api/applications/{target['id']}/validate")[0] == 202, "candidature validée")
    app = wait_status(a, target["id"], {"confirmee"})
    check(app["preuve"] and app["date_envoi"], "« Envoyée » seulement avec preuve et date")
    check(a.req("POST", f"/api/applications/{target['id']}/validate")[0] == 409, "pas de double envoi")
    check(a.req("DELETE", f"/api/offers/{o['id']}")[0] == 409, "offre envoyée conservée (historique)")

    other = next(x for x in a.req("GET", "/api/applications")[1] if x["statut"] in ("qualifiee", "decouverte"))
    check(a.req("POST", f"/api/applications/{other['id']}/ignore")[1]["statut"] == "ignoree", "ignorer une offre")
    check(a.req("POST", f"/api/applications/{other['id']}/restore")[1]["statut"] == "qualifiee", "restaurer une offre")
    ext = next((x for x in a.req("GET", "/api/applications")[1] if x["apply_type"] == "external"), None)
    if ext:
        check(a.req("PUT", f"/api/applications/{ext['id']}", {"mode": "auto"})[0] == 422, "envoi auto refusé pour une offre externe")
        a.req("PUT", f"/api/applications/{ext['id']}", {"mode": "manual"})
        a.req("POST", f"/api/applications/{ext['id']}/prepare")
        wait_status(a, ext["id"], {"a_valider"})
        check(a.req("POST", f"/api/applications/{ext['id']}/validate")[0] == 409, "mode manuel : pas d'envoi par le robot")
        check(a.req("POST", f"/api/applications/{ext['id']}/mark-sent")[1]["statut"] == "confirmee", "mode manuel : marquer comme envoyée")
    check(a.req("DELETE", f"/api/applications/{other['id']}")[0] == 200, "suppression d'une candidature")

    print("Tableau de bord + notifications")
    s, d = a.req("GET", "/api/dashboard")
    check(s == 200 and d["stats"]["sent"] >= 1 and len(d["chart"]) == 8, "statistiques et graphique 8 semaines")
    check(sum(w["confirmed"] for w in d["chart"]) == d["stats"]["sent"], "graphique cohérent avec les envois")
    check(any("Confirmation reçue" in e["message"] for e in a.req("GET", "/api/events")[1]), "notification de confirmation")
    a.req("POST", "/api/events/read")
    check(a.req("GET", "/api/dashboard")[1]["unread"] == 0, "notifications marquées comme lues")

    print("Compte")
    check(a.req("PUT", "/api/auth/password", {"current": "faux", "new": "nouveaumdp1"})[0] == 401, "changement refusé sans bon mot de passe")
    check(a.req("PUT", "/api/auth/password", {"current": "motdepasse1", "new": "nouveaumdp1"})[0] == 200, "changement de mot de passe")
    a.req("POST", "/api/auth/logout")
    check(a.req("POST", "/api/auth/login", {"email": email, "password": "nouveaumdp1"})[0] == 200, "connexion avec le nouveau mot de passe")
    check(b.req("DELETE", "/api/auth/account", {"password": "faux"})[0] == 401, "suppression refusée sans mot de passe")
    check(b.req("DELETE", "/api/auth/account", {"password": "motdepasse2"})[0] == 200, "suppression du compte")
    check(b.req("POST", "/api/auth/login", {"email": "autre" + email, "password": "motdepasse2"})[0] == 401, "compte supprimé : connexion impossible")

    print(f"\n{passed} vérifications réussies.")


if __name__ == "__main__":
    main()
