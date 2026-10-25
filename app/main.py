"""API CandiPilot (FastAPI) + interface web de la maquette.

Lancement :
    .venv/bin/python -m uvicorn app.main:app --port 8000
puis ouvrir http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, auth, db, files, matching, migrations, robot, worker, workflow

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 5 * 1024 * 1024

app = FastAPI(title="CandiPilot", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(agent.router)

CurrentUser = Depends(auth.current_user)


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """Anti-CSRF : une modification (POST, PUT, PATCH, DELETE) doit venir de CandiPilot lui-même.
    L'extension s'authentifie par jeton (pas de cookie) : ses routes /api/agent/* ne sont pas concernées."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") \
            and not request.url.path.startswith("/api/agent/") or request.url.path == "/api/agent/link":
        origin = request.headers.get("origin") or ""
        if origin and request.method != "GET":
            host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
            if origin.split("://", 1)[-1].rstrip("/") != host:
                return JSONResponse({"detail": "Requête refusée : elle ne vient pas de CandiPilot."}, status_code=403)
    return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
    db.init()
    with db.tx() as c:
        files.migrate_legacy(c)
    _adopt_owner_jobteaser()
    worker.start()


def _adopt_owner_jobteaser() -> None:
    """Le propriétaire garde la session SKEMA du robot en ligne de commande (.user-profile)."""
    if worker.ROBOT != "local" or not robot.OWNER_PROFILE.exists():
        return
    with db.tx() as c:
        first = c.execute("SELECT id FROM users ORDER BY created_at, rowid LIMIT 1").fetchone()
        if first and not (robot.BROWSERS_DIR / first["id"]).exists():
            c.execute("""UPDATE preferences SET jobteaser_url=COALESCE(NULLIF(jobteaser_url,''), 'https://skema.jobteaser.com'),
                         jobteaser_status=COALESCE(NULLIF(jobteaser_status,''), 'connected') WHERE user_id=?""", (first["id"],))


@app.on_event("shutdown")
def _shutdown() -> None:
    worker.stop()


@app.get("/healthz", include_in_schema=False)
def healthz(db_check: int = 0):
    """Le site répond. Sans ?db_check=1, ne touche pas la base (elle peut rester en veille)."""
    if db_check:
        with db.tx() as c:
            c.execute("SELECT 1").fetchone()
    return {"ok": True}


@app.get("/confidentialite", include_in_schema=False)
def privacy():
    """Politique de confidentialité (publique, exigée par le Chrome Web Store)."""
    return FileResponse(STATIC / "confidentialite.html")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


# ===================== Authentification (F01) =====================

class RegisterIn(BaseModel):
    email: str
    password: str
    first_name: str = ""
    last_name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordIn(BaseModel):
    current: str
    new: str


class DeleteAccountIn(BaseModel):
    password: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str


class EmailIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response):
    email = body.email.strip().lower()
    auth.check_credentials_format(email, body.password)
    auth.rate_limit(f"register:{auth.client_ip(request)}")
    uid = db.new_id()
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Un compte existe déjà avec cette adresse.")
        # Heure à la microseconde : le premier compte (propriétaire) est désigné sans égalité possible.
        c.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES (?,?,?,?)",
                  (uid, email, auth.hash_password(body.password), datetime.now(timezone.utc).isoformat()))
        c.execute("INSERT INTO profiles(user_id, first_name, last_name, letter_template) VALUES (?,?,?,?)",
                  (uid, body.first_name.strip(), body.last_name.strip(), robot.default_letter_template()))
        # Première recherche automatique après le délai choisi, pas dès l'inscription.
        c.execute("INSERT INTO preferences(user_id, last_search_at) VALUES (?,?)", (uid, db.now()))
        db.add_event(c, uid, "info", "Bienvenue sur CandiPilot ! Complète ton profil et tes critères.")
        token = auth.create_session(c, uid)
    auth.set_cookie(response, token)
    return {"id": uid, "email": email}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    email = body.email.strip().lower()
    auth.rate_limit(f"login:{auth.client_ip(request)}:{email}")
    with db.tx() as c:
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u or not auth.verify_password(body.password, u["password_hash"]):
            raise HTTPException(401, "E-mail ou mot de passe incorrect.")
        if u["statut"] != "actif":
            raise HTTPException(403, "Compte désactivé.")
        c.execute("DELETE FROM sessions WHERE expires_at <= ?", (db.now(),))
        token = auth.create_session(c, u["id"])
    auth.set_cookie(response, token)
    return {"id": u["id"], "email": u["email"]}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))
    auth.clear_session_cookie(response)
    return {"ok": True}


@app.post("/api/auth/logout-all")
def logout_all(response: Response, user=CurrentUser):
    """Déconnecte tous les appareils, y compris celui-ci."""
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    auth.clear_session_cookie(response)
    return {"ok": True}


@app.post("/api/auth/forgot", status_code=202)
def forgot_password(body: ForgotIn, request: Request):
    """Envoie un lien de réinitialisation. Réponse identique que le compte existe ou non."""
    email = body.email.strip().lower()
    auth.rate_limit(f"forgot:{auth.client_ip(request)}")
    with db.tx() as c:
        u = c.execute("SELECT id FROM users WHERE email=? AND statut='actif'", (email,)).fetchone()
        token = auth.create_reset(c, u["id"]) if u else None
    if token:
        base = auth.PUBLIC_URL or str(request.base_url).rstrip("/")
        link = f"{base}/#/reinitialiser?token={token}"
        body = (f"Bonjour,\n\nPour choisir un nouveau mot de passe, ouvre ce lien (valable {auth.RESET_MINUTES} minutes) :\n{link}\n\n"
                "Si tu n'es pas à l'origine de cette demande, ignore ce message.\n\nCandiPilot")
        # Envoi en arrière-plan : la page répond tout de suite, même si le serveur d'e-mail est lent.
        threading.Thread(target=auth.send_mail_logged, args=(email, "CandiPilot : réinitialise ton mot de passe", body),
                         daemon=True).start()
    else:
        print(f"[e-mail] mot de passe oublié : aucun compte pour {email}, rien envoyé", flush=True)
    return {"ok": True}


@app.post("/api/auth/reset")
def reset_password(body: ResetIn, request: Request, response: Response):
    auth.rate_limit(f"reset:{auth.client_ip(request)}")
    auth.check_password_strength(body.password)
    with db.tx() as c:
        uid = auth.consume_reset(c, body.token)
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(body.password), uid))
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        db.add_event(c, uid, "info", "Mot de passe réinitialisé. Les autres appareils ont été déconnectés.")
        token = auth.create_session(c, uid)
    auth.set_cookie(response, token)
    return {"ok": True}


@app.put("/api/auth/email")
def change_email(body: EmailIn, user=CurrentUser):
    email = body.email.strip().lower()
    if not auth.EMAIL_RE.match(email):
        raise HTTPException(422, "Adresse e-mail invalide.")
    with db.tx() as c:
        h = c.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()["password_hash"]
        if not auth.verify_password(body.password, h):
            raise HTTPException(401, "Mot de passe incorrect.")
        if c.execute("SELECT 1 FROM users WHERE email=? AND id<>?", (email, user["id"])).fetchone():
            raise HTTPException(409, "Un compte existe déjà avec cette adresse.")
        c.execute("UPDATE users SET email=? WHERE id=?", (email, user["id"]))
    return {"email": email}


def _is_owner(c, uid: str) -> bool:
    """Le premier compte créé est celui du propriétaire du robot (config/profile.yaml)."""
    first = c.execute("SELECT id FROM users ORDER BY created_at, rowid LIMIT 1").fetchone()
    return bool(first) and first["id"] == uid


@app.get("/api/auth/me")
def me(user=CurrentUser):
    with db.tx() as c:
        p = db.row(c.execute("SELECT first_name, last_name FROM profiles WHERE user_id=?", (user["id"],)).fetchone())
        owner = _is_owner(c, user["id"])
        n_sessions = c.execute("SELECT COUNT(*) n FROM sessions WHERE user_id=? AND expires_at>?",
                               (user["id"], db.now())).fetchone()["n"]
    return {**user, **(p or {}), "owner": owner, "sessions": n_sessions}


@app.put("/api/auth/password")
def change_password(body: PasswordIn, request: Request, user=CurrentUser):
    auth.check_password_strength(body.new)
    token = request.cookies.get(auth.COOKIE)
    with db.tx() as c:
        h = c.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()["password_hash"]
        if not auth.verify_password(body.current, h):
            raise HTTPException(401, "Mot de passe actuel incorrect.")
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(body.new), user["id"]))
        # Déconnecte les autres appareils.
        c.execute("DELETE FROM sessions WHERE user_id=? AND token<>?", (user["id"], token))
    return {"ok": True}


@app.delete("/api/auth/account")
def delete_account(body: DeleteAccountIn, response: Response, user=CurrentUser):
    with db.tx() as c:
        h = c.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()["password_hash"]
        if not auth.verify_password(body.password, h):
            raise HTTPException(401, "Mot de passe incorrect.")
        c.execute("DELETE FROM users WHERE id=?", (user["id"],))
    shutil.rmtree(robot.BROWSERS_DIR / user["id"], ignore_errors=True)   # navigateur robot (cookies JobTeaser)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


# ===================== Profil (F01, F02) =====================

PROFILE_FIELDS = ["first_name", "last_name", "phone", "linkedin", "location", "school", "program",
                  "competences", "gender", "availability", "duration", "languages",
                  "internship_agreement", "letter_template"]


class ProfileIn(BaseModel):
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    linkedin: str = ""
    location: str = ""
    school: str = ""
    program: str = ""
    competences: str = ""
    gender: str = ""
    availability: str = ""
    duration: str = ""
    languages: str = ""
    internship_agreement: str = ""
    letter_template: str = ""


@app.get("/api/profile")
def get_profile(user=CurrentUser):
    with db.tx() as c:
        p = db.row(c.execute("SELECT * FROM profiles WHERE user_id=?", (user["id"],)).fetchone())
    return {**p, "email": user["email"]}


@app.put("/api/profile")
def put_profile(body: ProfileIn, user=CurrentUser):
    if body.gender and body.gender.lower() not in ("homme", "femme", "autre"):
        raise HTTPException(422, "Genre : Homme, Femme ou Autre.")
    # Seuls les champs envoyés sont modifiés (le modèle de lettre se règle sur la page Documents).
    data = {k: v for k, v in body.dict(exclude_unset=True).items() if k in PROFILE_FIELDS}
    if data:
        with db.tx() as c:
            c.execute(f"UPDATE profiles SET {', '.join(f'{k}=?' for k in data)} WHERE user_id=?",
                      (*[v.strip() for v in data.values()], user["id"]))
    return get_profile(user)


@app.post("/api/profile/import-yaml")
def import_yaml(user=CurrentUser):
    with db.tx() as c:
        if not _is_owner(c, user["id"]):
            raise HTTPException(403, "Réservé au propriétaire du robot.")
        done = robot.import_yaml(c, user["id"])
        if done:
            db.add_event(c, user["id"], "info", "Importé depuis config/profile.yaml : " + ", ".join(done) + ".")
    if not done:
        raise HTTPException(404, "config/profile.yaml introuvable ou vide.")
    return {"imported": done}


# ===================== Documents (F02) =====================

@app.get("/api/documents")
def list_documents(user=CurrentUser):
    with db.tx() as c:
        docs = db.rows(c.execute("SELECT * FROM documents WHERE user_id=? ORDER BY type, created_at DESC",
                                 (user["id"],)))
    for d in docs:
        d.pop("chemin", None)
    return docs


@app.post("/api/documents", status_code=201)
async def upload_document(file: UploadFile = File(...), type: str = Form("cv"), user=CurrentUser):
    if type not in ("cv", "lettre"):
        raise HTTPException(422, "Type : cv ou lettre.")
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "Fichier trop lourd (5 Mo maximum).")
    if not content.startswith(b"%PDF"):
        raise HTTPException(415, "Seuls les fichiers PDF sont acceptés.")
    did = db.new_id()
    name = Path(file.filename or "document.pdf").name
    with db.tx() as c:
        ref = files.save(c, user["id"], name, content)
        prev = c.execute("SELECT MAX(version) v FROM documents WHERE user_id=? AND type=? AND name=?",
                         (user["id"], type, name)).fetchone()["v"] or 0
        first = not c.execute("SELECT 1 FROM documents WHERE user_id=? AND type=?", (user["id"], type)).fetchone()
        c.execute("""INSERT INTO documents(id,user_id,type,name,chemin,version,is_default,created_at)
                     VALUES (?,?,?,?,?,?,?,?)""", (did, user["id"], type, name, ref, prev + 1, int(first), db.now()))
    return {"id": did, "name": name, "type": type, "version": prev + 1, "is_default": int(first)}


class DocumentPatch(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None


@app.patch("/api/documents/{doc_id}")
def patch_document(doc_id: str, body: DocumentPatch, user=CurrentUser):
    with db.tx() as c:
        d = c.execute("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user["id"])).fetchone()
        if not d:
            raise HTTPException(404, "Document introuvable.")
        if body.name:
            c.execute("UPDATE documents SET name=? WHERE id=?", (Path(body.name).name, doc_id))
        if body.is_default:
            c.execute("UPDATE documents SET is_default=0 WHERE user_id=? AND type=?", (user["id"], d["type"]))
            c.execute("UPDATE documents SET is_default=1 WHERE id=?", (doc_id,))
    return {"ok": True}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str, user=CurrentUser):
    with db.tx() as c:
        d = c.execute("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user["id"])).fetchone()
        if not d:
            raise HTTPException(404, "Document introuvable.")
        c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        c.execute("UPDATE applications SET cv_document_id='' WHERE cv_document_id=? AND user_id=?", (doc_id, user["id"]))
        if d["is_default"]:
            nxt = c.execute("SELECT id FROM documents WHERE user_id=? AND type=? ORDER BY created_at DESC LIMIT 1",
                            (user["id"], d["type"])).fetchone()
            if nxt:
                c.execute("UPDATE documents SET is_default=1 WHERE id=?", (nxt["id"],))
        files.delete(c, d["chemin"])
    return {"ok": True}


# (L'e-mail du CV n'est pas proposé : c'est l'identifiant de connexion, modifiable dans « Mon compte ».)
CV_FIELDS = {"first_name": "Prénom", "last_name": "Nom", "phone": "Téléphone", "linkedin": "LinkedIn",
             "location": "Ville", "school": "École", "languages": "Langues"}


@app.post("/api/documents/{doc_id}/extract")
def extract_cv(doc_id: str, user=CurrentUser):
    """Lit le CV et propose de compléter le profil ; rien n'est enregistré sans validation de l'utilisateur."""
    from .cv import extract, pdf_text
    with db.tx() as c:
        d = c.execute("SELECT * FROM documents WHERE id=? AND user_id=? AND type='cv'", (doc_id, user["id"])).fetchone()
        got = files.read(c, d["chemin"]) if d else None
        p = db.row(c.execute("SELECT * FROM profiles WHERE user_id=?", (user["id"],)).fetchone())
    if not got:
        raise HTTPException(404, "CV introuvable.")
    found = extract(pdf_text(got[1]))
    current = {**p, "email": user["email"]}
    return {"suggestions": [{"field": k, "label": CV_FIELDS[k], "value": v, "current": current.get(k) or ""}
                            for k, v in found.items() if k in CV_FIELDS and v and v != (current.get(k) or "")]}


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: str, user=CurrentUser):
    with db.tx() as c:
        d = c.execute("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user["id"])).fetchone()
        got = files.read(c, d["chemin"]) if d else None
    if not got:
        raise HTTPException(404, "Fichier introuvable.")
    return _pdf(d["name"], got[1])


# ===================== Préférences (F03) + copilote =====================

class PreferencesIn(BaseModel):
    metiers: List[str] = []
    zones: List[str] = []
    contrats: List[str] = ["stage"]
    start_from: str = ""
    max_pages: int = Field(3, ge=1, le=10)
    include_external: bool = False
    min_score: int = Field(50, ge=0, le=100)
    default_mode: Optional[str] = None     # se choisit à l'étape 4 (PUT /api/workflow)
    search_every_hours: int = Field(24, ge=1, le=168)


def _prefs(c, uid: str) -> dict:
    p = db.row(c.execute("SELECT * FROM preferences WHERE user_id=?", (uid,)).fetchone())
    for k in ("metiers", "zones", "contrats"):
        p[k] = db.jload(p[k], [])
    p["include_external"] = bool(p["include_external"])
    p["paused"] = bool(p["paused"])
    return p


@app.get("/api/preferences")
def get_preferences(user=CurrentUser):
    with db.tx() as c:
        return _prefs(c, user["id"])


@app.put("/api/preferences")
def put_preferences(body: PreferencesIn, user=CurrentUser):
    if body.default_mode is not None and body.default_mode not in ("auto", "assisted", "manual"):
        raise HTTPException(422, "Mode : auto, assisted ou manual.")
    if body.start_from:
        try:
            datetime.strptime(body.start_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "Date de début : format AAAA-MM-JJ.")
    clean = lambda xs: [x.strip() for x in xs if x.strip()]
    with db.tx() as c:
        c.execute("""UPDATE preferences SET metiers=?, zones=?, contrats=?, start_from=?, max_pages=?,
                     include_external=?, min_score=?, default_mode=COALESCE(?, default_mode), search_every_hours=?
                     WHERE user_id=?""",
                  (json.dumps(clean(body.metiers), ensure_ascii=False), json.dumps(clean(body.zones), ensure_ascii=False),
                   json.dumps(clean(body.contrats), ensure_ascii=False), body.start_from, body.max_pages,
                   int(body.include_external), body.min_score, body.default_mode, body.search_every_hours, user["id"]))
        _rescore(c, user["id"])
        return _prefs(c, user["id"])


@app.post("/api/copilot/{action}")
def copilot(action: str, user=CurrentUser):
    if action not in ("pause", "resume"):
        raise HTTPException(404, "Action inconnue.")
    with db.tx() as c:
        c.execute("UPDATE preferences SET paused=? WHERE user_id=?", (int(action == "pause"), user["id"]))
        db.add_event(c, user["id"], "info", "Copilote mis en pause." if action == "pause" else "Copilote réactivé.")
    return {"paused": action == "pause"}


@app.post("/api/search", status_code=202)
def launch_search(user=CurrentUser):
    with db.tx() as c:
        p = c.execute("SELECT metiers, jobteaser_status FROM preferences WHERE user_id=?", (user["id"],)).fetchone()
        if not db.jload(p["metiers"], []):
            raise HTTPException(422, "Ajoute au moins un métier ciblé dans tes critères.")
        if not worker.FAKE and p["jobteaser_status"] != "connected":
            raise HTTPException(422, "Connecte d'abord ton compte JobTeaser (bouton « Connecter mon JobTeaser »).")
        jid = worker.enqueue(c, user["id"], "search")
    return {"job_id": jid}


@app.get("/api/jobs")
def list_jobs(user=CurrentUser):
    with db.tx() as c:
        return db.rows(c.execute("""SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 20""",
                                 (user["id"],)))


# ===================== Compte JobTeaser de l'utilisateur (connexion à distance) =====================

class PortalIn(BaseModel):
    portal_url: str


def _jobteaser_state(c, uid: str) -> dict:
    p = c.execute("SELECT jobteaser_url, jobteaser_status, jobteaser_at FROM preferences WHERE user_id=?", (uid,)).fetchone()
    return {"portal_url": p["jobteaser_url"] or robot.DEFAULT_PORTAL, "status": p["jobteaser_status"] or "",
            "connected_at": p["jobteaser_at"], "required": not worker.FAKE}


@app.get("/api/jobteaser")
def jobteaser_state(user=CurrentUser):
    with db.tx() as c:
        return _jobteaser_state(c, user["id"])


@app.put("/api/jobteaser")
def jobteaser_portal(body: PortalIn, user=CurrentUser):
    """Portail JobTeaser de l'école (ex. https://skema.jobteaser.com) ou https://www.jobteaser.com."""
    url = body.portal_url.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    host = url.split("/")[2] if url.count("/") >= 2 else ""
    fake_portal = TEST_PORTAL and host.startswith("127.0.0.1")     # faux portail des tests
    if not (host == "jobteaser.com" or host.endswith(".jobteaser.com")) and not fake_portal:
        raise HTTPException(422, "Adresse JobTeaser attendue, par exemple https://monecole.jobteaser.com")
    if not fake_portal:
        url = "/".join(url.split("/")[:3])                         # le portail = l'adresse du site de l'école
    with db.tx() as c:
        old = c.execute("SELECT jobteaser_url FROM preferences WHERE user_id=?", (user["id"],)).fetchone()["jobteaser_url"]
        c.execute("UPDATE preferences SET jobteaser_url=? WHERE user_id=?", (url, user["id"]))
        if old and old != url:      # autre portail : il faudra se reconnecter
            c.execute("UPDATE preferences SET jobteaser_status='' WHERE user_id=?", (user["id"],))
        return _jobteaser_state(c, user["id"])


@app.delete("/api/jobteaser")
def jobteaser_disconnect(user=CurrentUser):
    """Oublie la session (robot local : le profil de navigateur de l'utilisateur est effacé)."""
    own = robot.BROWSERS_DIR / user["id"]
    shutil.rmtree(own, ignore_errors=True)
    own.mkdir(parents=True, exist_ok=True)       # (le propriétaire quitte aussi l'ancien profil partagé)
    with db.tx() as c:
        c.execute("UPDATE preferences SET jobteaser_status='', jobteaser_at='' WHERE user_id=?", (user["id"],))
        return _jobteaser_state(c, user["id"])


TEST_PORTAL = worker.FAKE or os.environ.get("CANDIPILOT_TEST_PORTAL") == "1"
if TEST_PORTAL:
    # Faux portail JobTeaser et faux formulaires, pour tester le robot et l'extension sans vrai site.
    from .testportal import router as _test_portal
    app.include_router(_test_portal)


# ===================== Offres (F04, F05) =====================

class OfferIn(BaseModel):
    url: str
    poste: str
    entreprise: str
    localisation: str = ""
    contrat: str = ""
    domaine: str = ""
    start_date: str = ""
    apply_type: str = "jobteaser"


def _rescore(c, uid: str) -> None:
    prefs = _prefs(c, uid)
    prof = db.row(c.execute("SELECT * FROM profiles WHERE user_id=?", (uid,)).fetchone())
    for o in c.execute("SELECT * FROM offers WHERE user_id=?", (uid,)).fetchall():
        c.execute("UPDATE offers SET score=? WHERE id=?", (matching.score(dict(o), prefs, prof), o["id"]))
    c.execute("""UPDATE applications SET statut = CASE WHEN (SELECT score FROM offers WHERE offers.id=applications.offer_id) >= ?
                 THEN 'qualifiee' ELSE 'decouverte' END, updated_at=?
                 WHERE user_id=? AND statut IN ('decouverte','qualifiee')""", (prefs["min_score"], db.now(), uid))


@app.get("/api/offers")
def list_offers(q: str = "", min_score: int = 0, user=CurrentUser):
    sql = """SELECT o.*, a.id AS application_id, a.statut, a.selected, a.launched FROM offers o
             LEFT JOIN applications a ON a.offer_id=o.id WHERE o.user_id=? AND o.score>=?"""
    args = [user["id"], min_score]
    if q:
        sql += " AND (o.poste LIKE ? OR o.entreprise LIKE ? OR o.localisation LIKE ?)"
        args += [f"%{q}%"] * 3
    with db.tx() as c:
        return db.rows(c.execute(sql + " ORDER BY o.score DESC, o.created_at DESC", args))


@app.post("/api/offers", status_code=201)
def create_offer(body: OfferIn, user=CurrentUser):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(422, "L'adresse de l'offre doit commencer par http(s)://")
    if body.apply_type not in ("jobteaser", "external"):
        raise HTTPException(422, "Type : jobteaser ou external.")
    with db.tx() as c:
        if c.execute("SELECT 1 FROM offers WHERE user_id=? AND url=?", (user["id"], body.url.strip())).fetchone():
            raise HTTPException(409, "Cette offre est déjà dans ta liste.")
        prefs = _prefs(c, user["id"])
        prof = db.row(c.execute("SELECT * FROM profiles WHERE user_id=?", (user["id"],)).fetchone())
        data = body.dict()
        s = matching.score(data, prefs, prof)
        oid = db.new_id()
        c.execute("""INSERT INTO offers(id,user_id,url,poste,entreprise,localisation,contrat,domaine,start_date,
                     apply_type,source,score,created_at) VALUES (?,?,?,?,?,?,?,?,?,?, 'manuel', ?, ?)""",
                  (oid, user["id"], body.url.strip(), body.poste.strip(), body.entreprise.strip(), body.localisation,
                   body.contrat, body.domaine, body.start_date, body.apply_type, s, db.now()))
        c.execute("""INSERT INTO applications(id,user_id,offer_id,statut,mode,created_at,updated_at)
                     VALUES (?,?,?,'qualifiee',?,?,?)""",
                  (db.new_id(), user["id"], oid, "manual" if body.apply_type == "external" else prefs["default_mode"],
                   db.now(), db.now()))
    return {"id": oid, "score": s}


@app.put("/api/offers/{offer_id}")
def update_offer(offer_id: str, body: OfferIn, user=CurrentUser):
    with db.tx() as c:
        o = c.execute("SELECT * FROM offers WHERE id=? AND user_id=?", (offer_id, user["id"])).fetchone()
        if not o:
            raise HTTPException(404, "Offre introuvable.")
        s = matching.score(body.dict(), _prefs(c, user["id"]),
                           db.row(c.execute("SELECT * FROM profiles WHERE user_id=?", (user["id"],)).fetchone()))
        c.execute("""UPDATE offers SET url=?, poste=?, entreprise=?, localisation=?, contrat=?, domaine=?,
                     start_date=?, apply_type=?, score=? WHERE id=?""",
                  (body.url.strip(), body.poste.strip(), body.entreprise.strip(), body.localisation, body.contrat,
                   body.domaine, body.start_date, body.apply_type, s, offer_id))
    return {"id": offer_id, "score": s}


@app.delete("/api/offers/{offer_id}")
def delete_offer(offer_id: str, user=CurrentUser):
    with db.tx() as c:
        a = c.execute("SELECT statut FROM applications WHERE offer_id=? AND user_id=?", (offer_id, user["id"])).fetchone()
        if a and a["statut"] in ("soumission", "confirmee"):
            raise HTTPException(409, "Candidature envoyée ou en cours d'envoi : elle est conservée pour l'historique.")
        cur = c.execute("DELETE FROM offers WHERE id=? AND user_id=?", (offer_id, user["id"]))
        if not cur.rowcount:
            raise HTTPException(404, "Offre introuvable.")
    return {"ok": True}


# ===================== Candidatures (F06, F07, F08) =====================

class QuestionIn(BaseModel):
    label: str
    answer: str = ""


class ApplicationIn(BaseModel):
    mode: Optional[str] = None
    letter_text: Optional[str] = None
    letter_kind: Optional[str] = None      # document : lettre importée | generated : lettre adaptée à l'offre
    cv_document_id: Optional[str] = None
    questions: Optional[List[QuestionIn]] = None
    customized: Optional[bool] = None      # « J'ai vérifié ce dossier »
    reviewed: Optional[bool] = None        # « Autoriser l'envoi » après relecture


APP_SQL = """SELECT a.*, o.url, o.poste, o.entreprise, o.localisation, o.contrat, o.domaine, o.start_date,
             o.apply_type, o.score, o.source FROM applications a JOIN offers o ON o.id=a.offer_id"""


def _app_or_404(c, app_id: str, uid: str) -> dict:
    a = db.row(c.execute(APP_SQL + " WHERE a.id=? AND a.user_id=?", (app_id, uid)).fetchone())
    if not a:
        raise HTTPException(404, "Candidature introuvable.")
    a["questions"] = db.jload(a["questions"], [])
    return a


def _readiness(c, a: dict, uid: str) -> list:
    """Points du dossier (panneau « Votre décision » de la maquette)."""
    prof = db.row(c.execute("SELECT * FROM profiles WHERE user_id=?", (uid,)).fetchone())
    has_cv = bool(a["cv_document_id"]) or bool(robot.default_cv(c, uid))
    open_q = [q for q in a["questions"] if not q.get("answer", "").strip()]
    return [
        {"label": "CV sélectionné", "ok": has_cv},
        {"label": "Lettre adaptée", "ok": bool(a["letter_path"]) or a["letter_needed"] == 0},
        {"label": "Profil complet (genre, disponibilité)", "ok": bool(prof["gender"] and prof["availability"])},
        {"label": "Formulaire lu par le robot", "ok": a["statut"] in ("a_valider", "soumission", "confirmee", "echec", "incertaine")},
        {"label": "Questions du recruteur", "ok": not open_q},
    ]


@app.get("/api/applications")
def list_applications(statut: str = "", user=CurrentUser):
    sql, args = APP_SQL + " WHERE a.user_id=?", [user["id"]]
    if statut:
        wanted = [s for s in statut.split(",") if s in db.STATUSES]
        sql += f" AND a.statut IN ({','.join('?' * len(wanted))})"
        args += wanted
    with db.tx() as c:
        out = db.rows(c.execute(sql + " ORDER BY a.updated_at DESC", args))
    for a in out:
        qs = db.jload(a.pop("questions"), [])
        a["open_questions"] = sum(1 for q in qs if not q.get("answer", "").strip())
        a.pop("letter_text", None)
    return out


@app.get("/api/applications/{app_id}")
def get_application(app_id: str, user=CurrentUser):
    with db.tx() as c:
        a = _app_or_404(c, app_id, user["id"])
        prof = db.row(c.execute("SELECT availability, duration, languages, internship_agreement, gender FROM profiles WHERE user_id=?",
                                (user["id"],)).fetchone())
        cv = (c.execute("SELECT id, name, version FROM documents WHERE id=? AND user_id=?",
                        (a["cv_document_id"], user["id"])).fetchone() if a["cv_document_id"] else None) \
            or robot.default_cv(c, user["id"])
        a["cv"] = {"id": cv["id"], "name": cv["name"], "version": cv["version"]} if cv else None
        a["prepared_answers"] = prof
        a["readiness"] = _readiness(c, a, user["id"])
        a["job"] = db.row(c.execute("""SELECT type, statut, progress FROM jobs WHERE application_id=?
                                       ORDER BY created_at DESC LIMIT 1""", (app_id,)).fetchone())
        pr = workflow.prefs(c, user["id"])
        a["flow"] = workflow.flow(pr, a["mode"])
        raw = dict(a, questions=json.dumps(a["questions"]))
        a["blockers"], a["warnings"] = workflow.blockers(c, raw, user["id"], pr)
        lettre = workflow.default_doc(c, user["id"], "lettre")
        a["imported_letter"] = {"id": lettre["id"], "name": lettre["name"]} if lettre else None
    return a


@app.put("/api/applications/{app_id}")
def update_application(app_id: str, body: ApplicationIn, user=CurrentUser):
    with db.tx() as c:
        a = _app_or_404(c, app_id, user["id"])
        if a["statut"] in ("soumission", "confirmee"):
            raise HTTPException(409, "Candidature déjà envoyée ou en cours d'envoi.")
        fields = {}
        if body.mode is not None:
            if body.mode not in ("auto", "assisted", "manual"):
                raise HTTPException(422, "Mode : auto, assisted ou manual.")
            if body.mode != "manual" and a["apply_type"] != "jobteaser":
                raise HTTPException(422, "Cette offre se postule sur le site de l'entreprise : envoi manuel uniquement.")
            fields["mode"] = body.mode
        if body.cv_document_id is not None:
            if not c.execute("SELECT 1 FROM documents WHERE id=? AND user_id=? AND type='cv'",
                             (body.cv_document_id, user["id"])).fetchone():
                raise HTTPException(404, "CV introuvable.")
            fields["cv_document_id"] = body.cv_document_id
        if body.questions is not None:
            known = {q["label"] for q in a["questions"]}
            qs = [q.dict() for q in body.questions if q.label in known]
            fields["questions"] = json.dumps(qs, ensure_ascii=False)
        if body.letter_text is not None and body.letter_text.strip() != (a["letter_text"] or "").strip():
            ctx = robot.load_context(c, user["id"])
            fields["letter_text"] = body.letter_text.strip()
            fields["letter_path"] = robot.render_letter(c, ctx, a, body.letter_text.strip(), app_id)
            fields["letter_kind"] = "generated"
        elif body.letter_kind is not None and body.letter_kind != a["letter_kind"]:
            if body.letter_kind == "document":
                doc = workflow.default_doc(c, user["id"], "lettre")
                if not doc:
                    raise HTTPException(422, "Aucune lettre importée : ajoute-la dans « Documents ».")
                fields.update(letter_kind="document", letter_path=robot.copy_letter(c, doc, user["id"]), letter_text="")
            elif body.letter_kind == "generated":
                ctx = robot.load_context(c, user["id"])
                text = robot.draft_letter(ctx, a)
                fields.update(letter_kind="generated", letter_text=text,
                              letter_path=robot.render_letter(c, ctx, a, text, app_id))
            else:
                raise HTTPException(422, "Lettre : document ou generated.")
        if fields:
            fields["reviewed"] = 0      # Un dossier modifié doit être relu à nouveau.
        if body.customized is not None:
            fields["customized"] = int(body.customized)
        if body.reviewed is not None:
            if body.reviewed:
                pr = workflow.prefs(c, user["id"])
                b, _ = workflow.blockers(c, dict(a, questions=json.dumps(a["questions"]), customized=fields.get("customized", a["customized"])),
                                         user["id"], pr)
                b = [x for x in b if x.get("optional") != "review"]
                if b:
                    raise HTTPException(422, "Dossier incomplet : " + " ; ".join(x["label"] for x in b))
            fields["reviewed"] = int(body.reviewed)
        if fields:
            fields["updated_at"] = db.now()
            c.execute(f"UPDATE applications SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                      (*fields.values(), app_id))
    return get_application(app_id, user)


def _transition(app_id: str, uid: str, allowed: tuple, **fields) -> dict:
    with db.tx() as c:
        a = _app_or_404(c, app_id, uid)
        if a["statut"] not in allowed:
            raise HTTPException(409, f"Action impossible depuis le statut « {a['statut']} ».")
        fields["updated_at"] = db.now()
        c.execute(f"UPDATE applications SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), app_id))
    return a


@app.post("/api/applications/{app_id}/prepare", status_code=202)
def prepare(app_id: str, user=CurrentUser):
    with db.tx() as c:
        a = _app_or_404(c, app_id, user["id"])
        if a["statut"] not in ("decouverte", "qualifiee", "a_valider", "echec"):
            raise HTTPException(409, "Cette candidature ne peut plus être préparée.")
        if not robot.default_cv(c, user["id"]):
            raise HTTPException(422, "Ajoute d'abord un CV (PDF) dans ton profil.")
        c.execute("UPDATE applications SET statut='preparation', selected=1, updated_at=? WHERE id=?", (db.now(), app_id))
        jid = worker.enqueue(c, user["id"], "prepare", app_id)
    return {"job_id": jid}


@app.post("/api/applications/{app_id}/validate", status_code=202)
def validate(app_id: str, user=CurrentUser):
    with db.tx() as c:
        a = _app_or_404(c, app_id, user["id"])
        if a["statut"] != "a_valider":
            raise HTTPException(409, "Le dossier doit être préparé avant validation.")
        if a["mode"] == "manual":
            raise HTTPException(409, "Envoi manuel : télécharge ton dossier, postule, puis clique « Marquer comme envoyée ».")
        open_q = [q["label"] for q in a["questions"] if not q.get("answer", "").strip()]
        if open_q and a["mode"] == "auto":
            raise HTTPException(422, "Réponds aux questions du recruteur pour pouvoir valider : " + " / ".join(open_q))
        dup = c.execute("""SELECT 1 FROM applications a JOIN offers o ON o.id=a.offer_id WHERE a.user_id=? AND o.url=?
                           AND a.statut IN ('soumission','confirmee') AND a.id<>?""", (user["id"], a["url"], app_id)).fetchone()
        if dup:
            raise HTTPException(409, "Candidature déjà envoyée pour cette offre.")
        c.execute("UPDATE applications SET statut='soumission', selected=1, launched=1, updated_at=? WHERE id=?",
                  (db.now(), app_id))
        db.add_event(c, user["id"], "info", f"Candidature validée : {a['entreprise']} (envoi {'automatique' if a['mode'] == 'auto' else 'assisté'}).", app_id)
        jid = worker.enqueue(c, user["id"], "submit", app_id)
    return {"job_id": jid}


@app.post("/api/applications/{app_id}/ignore")
def ignore(app_id: str, user=CurrentUser):
    _transition(app_id, user["id"], ("decouverte", "qualifiee", "a_valider", "echec", "incertaine"), statut="ignoree", selected=0)
    return get_application(app_id, user)


@app.post("/api/applications/{app_id}/restore")
def restore(app_id: str, user=CurrentUser):
    _transition(app_id, user["id"], ("ignoree",), statut="qualifiee")
    return get_application(app_id, user)


@app.post("/api/applications/{app_id}/retry")
def retry(app_id: str, user=CurrentUser):
    with db.tx() as c:
        a = _app_or_404(c, app_id, user["id"])
    # Le dossier revient dans le récapitulatif : il ne repart qu'après « Valider et démarrer le robot ».
    _transition(app_id, user["id"], ("echec",), statut="a_valider" if a["letter_path"] else "qualifiee", detail="",
                selected=1, launched=0)
    return get_application(app_id, user)


@app.post("/api/applications/{app_id}/verify", status_code=202)
def verify(app_id: str, user=CurrentUser):
    with db.tx() as c:
        a = _app_or_404(c, app_id, user["id"])
        if a["statut"] not in ("incertaine", "echec"):
            raise HTTPException(409, "Seules les candidatures incertaines ou en échec peuvent être vérifiées.")
        if a["apply_type"] != "jobteaser":
            raise HTTPException(422, "Vérification automatique possible seulement sur JobTeaser.")
        jid = worker.enqueue(c, user["id"], "verify", app_id)
    return {"job_id": jid}


@app.post("/api/applications/{app_id}/mark-sent")
def mark_sent(app_id: str, user=CurrentUser):
    a = _transition(app_id, user["id"], ("a_valider", "incertaine", "echec"), statut="confirmee",
                    date_envoi=db.now(), preuve="Déclarée par toi (envoi manuel)")
    with db.tx() as c:
        db.add_event(c, user["id"], "success", f"Candidature marquée comme envoyée : {a['entreprise']}.", app_id)
    return get_application(app_id, user)


@app.delete("/api/applications/{app_id}")
def delete_application(app_id: str, user=CurrentUser):
    with db.tx() as c:
        a = _app_or_404(c, app_id, user["id"])
        if a["statut"] in ("soumission", "confirmee"):
            raise HTTPException(409, "Candidature envoyée ou en cours d'envoi : elle est conservée pour l'historique.")
        c.execute("DELETE FROM offers WHERE id=?", (a["offer_id"],))
        files.delete(c, a["letter_path"])
    return {"ok": True}


@app.get("/api/applications/{app_id}/letter")
def application_letter(app_id: str, user=CurrentUser):
    with db.tx() as c:
        a = _app_or_404(c, app_id, user["id"])
        got = files.read(c, a["letter_path"])
    if not got:
        raise HTTPException(404, "Lettre pas encore générée.")
    return _pdf(*got)


def _pdf(name: str, data: bytes) -> Response:
    safe = name.encode("ascii", "ignore").decode() or "document.pdf"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "private, no-store"})


# ===================== Tableau de bord, activité (F08, F09) =====================

def _iso_week(d: datetime) -> str:
    return f"S{d.isocalendar()[1]:02d}"


@app.get("/api/guide")
def guide(user=CurrentUser):
    """Parcours du candidat : cinq étapes, éléments manquants, sous-étapes facultatives."""
    with db.tx() as c:
        return workflow.guide(c, user["id"])


# ===================== Parcours : mode de soumission, sélection, lancement =====================

class WorkflowIn(BaseModel):
    submit_mode: Optional[str] = None       # auto | assisted | manual
    want_prepare: Optional[bool] = None
    want_review: Optional[bool] = None
    letter_source: Optional[str] = None     # document | template
    auto_consent: Optional[bool] = None     # consentement explicite à l'envoi automatique en son nom
    daily_limit: Optional[int] = Field(None, ge=1, le=100)


def audit(c, uid: str, action: str, detail: str = "", application_id: str = "", actor: str = "utilisateur") -> None:
    c.execute("""INSERT INTO audit_log(user_id, application_id, actor, action, detail, created_at)
                 VALUES (?,?,?,?,?,?)""", (uid, application_id, actor, action, detail[:500], db.now()))


@app.get("/api/workflow")
def get_workflow(user=CurrentUser):
    with db.tx() as c:
        pr = workflow.prefs(c, user["id"])
    return {"submit_mode": pr["default_mode"] if pr["mode_chosen"] else "", "want_prepare": pr["want_prepare"],
            "want_review": pr["want_review"], "letter_source": pr["letter_source"],
            "auto_consent": bool(pr.get("auto_submit_consent_at")), "daily_limit": pr.get("daily_limit") or 20,
            "modes": [{"key": k, "label": l, "help": h} for k, (l, h) in workflow.MODES.items()]}


@app.put("/api/workflow")
def put_workflow(body: WorkflowIn, user=CurrentUser):
    """Enregistre les choix du parcours. Ne déclenche jamais d'envoi."""
    uid = user["id"]
    with db.tx() as c:
        if body.submit_mode is not None:
            if body.submit_mode not in workflow.MODES:
                raise HTTPException(422, "Mode : auto, assisted ou manual.")
            pr = workflow.prefs(c, uid)
            if body.submit_mode == "auto" and not (body.auto_consent or pr.get("auto_submit_consent_at")):
                raise HTTPException(422, "L'envoi automatique demande ton autorisation explicite (case à cocher).")
            if body.auto_consent:
                c.execute("UPDATE preferences SET auto_submit_consent_at=? WHERE user_id=?", (db.now(), uid))
                audit(c, uid, "consentement", "Autorise l'envoi automatique de candidatures en son nom.")
            audit(c, uid, "mode", f"Mode de soumission : {body.submit_mode}")
            c.execute("UPDATE preferences SET default_mode=?, mode_chosen=1 WHERE user_id=?", (body.submit_mode, uid))
            # Dossiers pas encore lancés : nouveau mode (les offres externes restent en semi-assisté).
            c.execute("""UPDATE applications SET mode=?, reviewed=0, updated_at=? WHERE user_id=? AND launched=0
                         AND statut IN ('decouverte','qualifiee','preparation','a_valider','echec')
                         AND offer_id IN (SELECT id FROM offers WHERE apply_type='jobteaser')""",
                      (body.submit_mode, db.now(), uid))
        if body.want_prepare is not None:
            c.execute("UPDATE preferences SET want_prepare=? WHERE user_id=?", (int(body.want_prepare), uid))
        if body.want_review is not None:
            c.execute("UPDATE preferences SET want_review=? WHERE user_id=?", (int(body.want_review), uid))
        if body.daily_limit is not None:
            c.execute("UPDATE preferences SET daily_limit=? WHERE user_id=?", (body.daily_limit, uid))
        if body.auto_consent is False:
            c.execute("UPDATE preferences SET auto_submit_consent_at='' WHERE user_id=?", (uid,))
            audit(c, uid, "consentement", "Retire l'autorisation d'envoi automatique.")
        if body.letter_source is not None:
            if body.letter_source not in ("document", "template"):
                raise HTTPException(422, "Lettre : document ou template.")
            c.execute("UPDATE preferences SET letter_source=? WHERE user_id=?", (body.letter_source, uid))
    return get_workflow(user)


class SelectIn(BaseModel):
    selected: bool


@app.post("/api/applications/{app_id}/select")
def select_application(app_id: str, body: SelectIn, user=CurrentUser):
    """Coche une offre pour le prochain lancement ; lance sa préparation technique (aucun envoi)."""
    uid = user["id"]
    with db.tx() as c:
        a = _app_or_404(c, app_id, uid)
        if a["statut"] in ("soumission", "confirmee") or (a["launched"] and not body.selected):
            raise HTTPException(409, "Candidature déjà lancée : elle ne peut plus être retirée.")
        if not body.selected:
            c.execute("UPDATE applications SET selected=0, updated_at=? WHERE id=?", (db.now(), app_id))
        else:
            statut = "qualifiee" if a["statut"] == "ignoree" else a["statut"]
            c.execute("UPDATE applications SET selected=1, statut=?, updated_at=? WHERE id=?", (statut, db.now(), app_id))
            if statut in ("decouverte", "qualifiee") and robot.default_cv(c, uid):
                c.execute("UPDATE applications SET statut='preparation' WHERE id=?", (app_id,))
                worker.enqueue(c, uid, "prepare", app_id)
    return get_application(app_id, user)


@app.get("/api/launch")
def get_launch(user=CurrentUser):
    with db.tx() as c:
        return workflow.launch_state(c, user["id"])


@app.post("/api/launch", status_code=202)
def launch(user=CurrentUser):
    """« Valider et démarrer le robot » : seul point d'envoi du parcours. N'envoie que les dossiers sans blocage."""
    uid = user["id"]
    with db.tx() as c:
        pr = workflow.prefs(c, uid)
        if not pr["mode_chosen"]:
            raise HTTPException(422, "Choisis d'abord ton mode de soumission (étape 4).")
        if pr["default_mode"] in ("assisted", "manual") and -1 in (pr["want_prepare"], pr["want_review"]):
            raise HTTPException(422, "Réponds aux deux questions de l'étape 4 (préparation et relecture).")
        if workflow.jobteaser_missing(pr):
            raise HTTPException(422, "Il manque " + workflow.jobteaser_missing(pr) + " (étape 3).")
        if workflow.missing_documents(c, uid, pr):
            raise HTTPException(422, "Il manque : " + ", ".join(workflow.missing_documents(c, uid, pr)) + ".")
        mprof = workflow.missing_profile(c, uid)
        today = datetime.now(timezone.utc).date().isoformat()
        sent_today = c.execute("""SELECT COUNT(*) n FROM audit_log WHERE user_id=? AND action='statut' AND to_status='soumission'
                                  AND created_at>=?""", (uid, today)).fetchone()["n"]
        budget = max(0, int(pr.get("daily_limit") or 20) - sent_today)
        started, manual, skipped, capped, over = [], [], 0, 0, 0
        for a in workflow.pending(c, uid):
            b, _ = workflow.blockers(c, a, uid, pr, mprof)
            if b:
                skipped += 1
                continue
            # Registre des sites : jamais plus d'automatisation que ce que la plateforme permet.
            mode = workflow.capped_mode(c, a)
            if mode != a["mode"]:
                capped += 1
                c.execute("UPDATE applications SET mode=?, updated_at=? WHERE id=?", (mode, db.now(), a["id"]))
                audit(c, uid, "mode_plafonné", f"{a['mode']} → {mode} (règle du site)", a["id"], "système")
                a["mode"] = mode
            if a["mode"] == "auto" and not pr.get("auto_submit_consent_at"):
                raise HTTPException(422, "L'envoi automatique demande ton autorisation explicite (étape 4).")
            if a["mode"] != "manual" and len(started) >= budget:
                over += 1
                continue
            if a["mode"] == "manual":
                c.execute("UPDATE applications SET launched=1, updated_at=? WHERE id=?", (db.now(), a["id"]))
                manual.append(a["id"])
                continue
            c.execute("UPDATE applications SET statut='soumission', launched=1, idempotency_key=?, updated_at=? WHERE id=?",
                      (db.new_id(), db.now(), a["id"]))
            worker.enqueue(c, uid, "submit", a["id"])
            started.append(a["id"])
        if not started and not manual:
            raise HTTPException(409, "Aucun dossier prêt : résous les blocages indiqués dans le récapitulatif."
                                     + (" Limite quotidienne d'envois atteinte." if over else ""))
        audit(c, uid, "lancement", f"{len(started)} envoi(s), {len(manual)} à envoyer soi-même, {skipped} bloqué(s), {over} reporté(s)")
        db.add_event(c, uid, "info", f"Lancement validé : {len(started)} envoi(s) par le robot, "
                                     f"{len(manual)} à envoyer toi-même, {skipped} dossier(s) bloqué(s) non envoyé(s)"
                                     + (f", {over} reporté(s) à demain (limite quotidienne)" if over else "") + ".")
        worker.log_event("launch", user=uid, started=len(started), manual=len(manual), skipped=skipped, capped=capped, over=over)
    return {"started": len(started), "manual": len(manual), "skipped": skipped, "capped": capped, "postponed": over}


@app.get("/api/applications/{app_id}/history")
def application_history(app_id: str, user=CurrentUser):
    """Journal d'audit d'une candidature : chaque changement de statut et chaque décision."""
    with db.tx() as c:
        _app_or_404(c, app_id, user["id"])
        return db.rows(c.execute("""SELECT actor, action, from_status, to_status, detail, created_at FROM audit_log
                                    WHERE application_id=? AND user_id=? ORDER BY id""", (app_id, user["id"])))


@app.get("/api/admin/sites")
def admin_sites(user=CurrentUser):
    """Registre des sites (consultable par tous, modifiable par le propriétaire)."""
    with db.tx() as c:
        return db.rows(c.execute("SELECT * FROM site_policies ORDER BY platform"))


class SitePolicyIn(BaseModel):
    mode_max: str
    notes: str = ""
    terms_checked: bool = False


@app.put("/api/admin/sites/{domain}")
def admin_site_update(domain: str, body: SitePolicyIn, user=CurrentUser):
    if body.mode_max not in ("auto", "assisted", "manual"):
        raise HTTPException(422, "Niveau : auto, assisted ou manual.")
    with db.tx() as c:
        if not _is_owner(c, user["id"]):
            raise HTTPException(403, "Réservé au propriétaire.")
        if not c.execute("SELECT 1 FROM site_policies WHERE domain=?", (domain,)).fetchone():
            raise HTTPException(404, "Site inconnu.")
        c.execute("UPDATE site_policies SET mode_max=?, notes=?, terms_checked_at=CASE WHEN ?=1 THEN ? ELSE terms_checked_at END, updated_at=? WHERE domain=?",
                  (body.mode_max, body.notes, int(body.terms_checked), db.now(), db.now(), domain))
        audit(c, user["id"], "registre_sites", f"{domain} → {body.mode_max}")
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(user=CurrentUser):
    uid = user["id"]
    with db.tx() as c:
        last_search = c.execute("""SELECT updated_at FROM jobs WHERE user_id=? AND type='search' AND statut='termine'
                                   ORDER BY updated_at DESC LIMIT 1""", (uid,)).fetchone()
        prefs = _prefs(c, uid)
        n_offers = c.execute("SELECT COUNT(*) n FROM offers WHERE user_id=?", (uid,)).fetchone()["n"]
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        n_week = c.execute("SELECT COUNT(*) n FROM offers WHERE user_id=? AND created_at>=?", (uid, week_ago)).fetchone()["n"]
        n_match = c.execute("SELECT COUNT(*) n FROM offers WHERE user_id=? AND score>=?", (uid, prefs["min_score"])).fetchone()["n"]
        n_high = c.execute("SELECT COUNT(*) n FROM offers WHERE user_id=? AND score>=85", (uid,)).fetchone()["n"]
        counts = {r["statut"]: r["n"] for r in c.execute(
            "SELECT statut, COUNT(*) n FROM applications WHERE user_id=? GROUP BY statut", (uid,))}
        to_validate = db.rows(c.execute(APP_SQL + " WHERE a.user_id=? AND a.statut='a_valider' ORDER BY o.score DESC LIMIT 5", (uid,)))
        for a in to_validate:
            qs = db.jload(a.pop("questions"), [])
            a["open_questions"] = sum(1 for q in qs if not q.get("answer", "").strip())
            a.pop("letter_text", None)
        recommended = db.rows(c.execute(APP_SQL + """ WHERE a.user_id=? AND a.statut IN ('qualifiee','decouverte')
                                         ORDER BY o.score DESC LIMIT 3""", (uid,)))
        for a in recommended:
            a.pop("letter_text", None)
            a.pop("questions", None)
        events = db.rows(c.execute("SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT 6", (uid,)))
        unread = c.execute("SELECT COUNT(*) n FROM events WHERE user_id=? AND read=0", (uid,)).fetchone()["n"]
        jobs = db.rows(c.execute("""SELECT type, statut, progress, updated_at FROM jobs WHERE user_id=?
                                    ORDER BY created_at DESC LIMIT 4""", (uid,)))
        sent = c.execute("""SELECT a.statut, a.date_envoi, a.updated_at FROM applications a
                            WHERE a.user_id=? AND a.statut IN ('confirmee','soumission','incertaine')""", (uid,)).fetchall()

    # Graphique : 8 dernières semaines (envoi confirmé / en attente).
    today = datetime.now(timezone.utc)
    weeks = [_iso_week(today - timedelta(weeks=i)) for i in range(7, -1, -1)]
    chart = {w: {"week": w, "confirmed": 0, "pending": 0} for w in weeks}
    for r in sent:
        when = datetime.fromisoformat(r["date_envoi"] or r["updated_at"])
        w = _iso_week(when)
        if w in chart:
            chart[w]["confirmed" if r["statut"] == "confirmee" else "pending"] += 1

    return {
        "stats": {"offers": n_offers, "offers_week": n_week, "matching": n_match, "matching_high": n_high,
                  "sent": counts.get("confirmee", 0),
                  "pending": counts.get("soumission", 0) + counts.get("incertaine", 0),
                  "to_validate": counts.get("a_valider", 0), "counts": counts},
        # Dernière VRAIE recherche (last_search_at sert aussi à programmer la première, à l'inscription).
        "copilot": {"paused": prefs["paused"], "last_search_at": last_search["updated_at"] if last_search else "",
                    "every_hours": prefs["search_every_hours"], "jobs": jobs, "fake": worker.FAKE},
        "chart": list(chart.values()),
        "to_validate": to_validate,
        "recommended": recommended,
        "events": events,
        "unread": unread,
    }


@app.get("/api/events")
def list_events(user=CurrentUser):
    with db.tx() as c:
        return db.rows(c.execute("SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT 50", (user["id"],)))


@app.post("/api/events/read")
def read_events(user=CurrentUser):
    with db.tx() as c:
        c.execute("UPDATE events SET read=1 WHERE user_id=?", (user["id"],))
    return {"ok": True}
