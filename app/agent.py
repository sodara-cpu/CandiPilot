"""API de l'extension CandiPilot (le robot qui tourne dans le navigateur de chaque utilisateur).

1. Liaison : sur une page CandiPilot où l'utilisateur est connecté, l'extension demande un jeton
   (POST /api/agent/link, avec le cookie de session). Rien à saisir.
2. Travail : avec ce jeton, elle prend ses tâches (GET /api/agent/next), les exécute dans le navigateur
   de l'utilisateur, avec SES sessions (JobTeaser, LinkedIn…), puis renvoie le résultat.
3. Sites : elle signale sur quels sites l'utilisateur est connecté (PUT /api/agent/sites).

Le jeton ne donne accès qu'aux données de son propriétaire ; seule son empreinte est stockée.
"""
from __future__ import annotations

import hashlib
import io
import json
import secrets
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel

from . import auth, db, files, robot, worker

router = APIRouter()
EXTENSION_DIR = db.ROOT / "extension"
ONLINE_S = 120
SITES = {"jobteaser": "JobTeaser", "linkedin": "LinkedIn", "wttj": "Welcome to the Jungle"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def agent_user(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Extension non reliée.")
    with db.tx() as c:
        r = c.execute("""SELECT u.id, u.email FROM agent_tokens t JOIN users u ON u.id=t.user_id
                         WHERE t.token_hash=? AND u.statut='actif'""", (_hash(token),)).fetchone()
        if not r:
            raise HTTPException(401, "Jeton d'extension invalide : ouvre CandiPilot pour relier l'extension.")
        c.execute("UPDATE agent_tokens SET last_seen=? WHERE token_hash=?", (db.now(), _hash(token)))
    return dict(r)


Agent = Depends(agent_user)


# ---------- liaison (depuis l'application, avec la session de l'utilisateur) ----------

class LinkIn(BaseModel):
    version: str = ""


@router.post("/api/agent/link")
def link(body: LinkIn, user=Depends(auth.current_user)):
    token = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute("INSERT INTO agent_tokens(token_hash, user_id, created_at, last_seen, version) VALUES (?,?,?,?,?)",
                  (_hash(token), user["id"], db.now(), db.now(), body.version[:20]))
        # Garde les 3 navigateurs les plus récents de l'utilisateur.
        old = c.execute("SELECT token_hash FROM agent_tokens WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()[3:]
        for o in old:
            c.execute("DELETE FROM agent_tokens WHERE token_hash=?", (o["token_hash"],))
        db.add_event(c, user["id"], "success", "Extension CandiPilot reliée à ton compte.")
    return {"token": token, "user_id": user["id"]}


def status(c, uid: str) -> dict:
    t = c.execute("SELECT last_seen, version FROM agent_tokens WHERE user_id=? ORDER BY last_seen DESC LIMIT 1", (uid,)).fetchone()
    p = c.execute("SELECT sites_status, jobteaser_url FROM preferences WHERE user_id=?", (uid,)).fetchone()
    online = bool(t and t["last_seen"] and datetime.fromisoformat(t["last_seen"]) > datetime.now(timezone.utc) - timedelta(seconds=ONLINE_S))
    sites = db.jload(p["sites_status"], {})
    import os
    return {"robot": worker.ROBOT, "linked": bool(t), "online": online, "last_seen": t["last_seen"] if t else "",
            # Page de l'extension sur le Chrome Web Store, une fois publiée : installation en un clic.
            "store_url": os.environ.get("CANDIPILOT_EXTENSION_URL", ""),
            "version": t["version"] if t else "", "portal": p["jobteaser_url"] or robot.DEFAULT_PORTAL,
            "sites": [{"key": k, "name": n, **sites.get(k, {"connected": None})} for k, n in SITES.items()]}


@router.get("/api/agent/status")
def agent_status(user=Depends(auth.current_user)):
    with db.tx() as c:
        return status(c, user["id"])


@router.post("/api/agent/check", status_code=202)
def ask_check(user=Depends(auth.current_user)):
    """Demande à l'extension de revérifier les sites (après une connexion par exemple)."""
    with db.tx() as c:
        return {"job_id": worker.enqueue(c, user["id"], "check_sites")}


@router.get("/extension.zip", include_in_schema=False)
def extension_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(EXTENSION_DIR.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                z.write(f, "candipilot-extension/" + str(f.relative_to(EXTENSION_DIR)))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="candipilot-extension.zip"'})


# ---------- travail de l'extension (jeton) ----------

def _payload(c, job: dict) -> Optional[dict]:
    uid, kind = job["user_id"], job["type"]
    out = {"id": job["id"], "type": kind}
    if kind in ("search", "check_sites"):
        ctx = robot.load_context(c, uid)
        cfg = robot.search_cfg(ctx["prefs"])
        out.update(portal=cfg["base_url"], keywords=cfg["keywords"], contracts=cfg["contract_contains"],
                   locations=cfg["location_contains"], start_from=cfg["start_from"], max_pages=cfg["max_pages"],
                   include_external=cfg["include_external"],
                   known=[r["url"] for r in c.execute("SELECT url FROM offers WHERE user_id=?", (uid,))])
        return out
    a = worker.load_app(c, job["application_id"])
    if not a:
        return None
    out["url"] = a["url"]
    if kind == "submit":
        prof = c.execute("SELECT gender FROM profiles WHERE user_id=?", (uid,)).fetchone()
        cv = c.execute("SELECT id, name, chemin FROM documents WHERE id=? AND user_id=?", (a["cv_document_id"], uid)).fetchone() \
            or robot.default_cv(c, uid)
        letter = files.read(c, a["letter_path"]) if a["letter_path"] else None
        out.update(mode=a["mode"] if a["mode"] in ("auto", "assisted") else "auto", gender=prof["gender"],
                   answers={q["label"]: q.get("answer", "") for q in db.jload(a["questions"], []) if q.get("answer", "").strip()},
                   cv={"ref": cv["chemin"], "name": cv["name"]} if cv else None,
                   letter={"ref": a["letter_path"], "name": letter[0]} if letter else None,
                   company=a["entreprise"], role=a["poste"], key=a["idempotency_key"])
    return out


@router.get("/api/agent/next")
def next_job(response: Response, user=Agent):
    """Prochaine tâche de CET utilisateur (204 s'il n'y en a pas)."""
    with db.tx() as c:
        worker.release_stale(c)
        while True:
            j = c.execute(f"""SELECT * FROM jobs WHERE user_id=? AND statut='en_attente'
                              AND type IN ({','.join('?' * len(worker.AGENT_TYPES))}) ORDER BY created_at LIMIT 1""",
                          (user["id"], *worker.AGENT_TYPES)).fetchone()
            if not j:
                response.status_code = 204
                return None
            job = db.row(j)
            if job["type"] == "submit" and not worker.submit_guard(job):
                c.execute("UPDATE jobs SET statut='termine', progress='Plus rien à envoyer', updated_at=? WHERE id=?", (db.now(), job["id"]))
                continue
            payload = _payload(c, job)
            if payload is None:
                c.execute("UPDATE jobs SET statut='termine', updated_at=? WHERE id=?", (db.now(), job["id"]))
                continue
            c.execute("UPDATE jobs SET statut='en_cours', claimed_at=?, updated_at=? WHERE id=?", (db.now(), db.now(), job["id"]))
            worker.log_event("agent_claim", user=user["id"], job=job["id"], type=job["type"])
            if job["type"] == "prepare":
                c.execute("UPDATE applications SET statut='preparation', updated_at=? WHERE id=? AND statut IN ('decouverte','qualifiee','a_valider','echec')",
                          (db.now(), job["application_id"]))
            return payload


class ProgressIn(BaseModel):
    text: str


class ResultIn(BaseModel):
    ok: bool = True
    data: dict = {}
    error: str = ""


def _own_job(job_id: str, uid: str) -> dict:
    with db.tx() as c:
        j = c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, uid)).fetchone()
    if not j:
        raise HTTPException(404, "Tâche introuvable.")
    return db.row(j)


@router.post("/api/agent/jobs/{job_id}/progress")
def job_progress(job_id: str, body: ProgressIn, user=Agent):
    _own_job(job_id, user["id"])
    worker.progress(job_id, body.text)
    return {"ok": True}


@router.post("/api/agent/jobs/{job_id}/result")
def job_result(job_id: str, body: ResultIn, user=Agent):
    job = _own_job(job_id, user["id"])
    if job["statut"] != "en_cours":
        raise HTTPException(409, "Tâche déjà terminée.")
    d = body.data or {}
    if not body.ok:
        worker.fail_job(job, body.error or "erreur inconnue")
    elif job["type"] == "search":
        worker.finish_search(job, d.get("found") or [], d.get("stats") or {})
    elif job["type"] == "prepare":
        worker.finish_prepare(job, d.get("info") or {})
    elif job["type"] == "submit":
        with db.tx() as c:
            current = (worker.load_app(c, job["application_id"]) or {}).get("idempotency_key", "")
        if d.get("key") and current and d["key"] != current:
            # Résultat d'un ancien essai (navigateur resté ouvert, double clic…) : ignoré, pas de double comptage.
            worker.log_event("submit_result_ignored", job=job_id, reason="clé d'idempotence différente")
        else:
            worker.finish_submit(job, str(d.get("status") or "failed"), str(d.get("detail") or "")[:500])
    elif job["type"] == "verify":
        worker.finish_verify(job, bool(d.get("ok")))
    elif job["type"] == "check_sites":
        save_sites(user["id"], d.get("sites") or {})
    with db.tx() as c:
        c.execute("UPDATE jobs SET statut=?, progress=?, updated_at=? WHERE id=?",
                  ("termine" if body.ok else "erreur", (body.error or "")[:300], db.now(), job_id))
    worker.log_event("agent_result", user=user["id"], job=job_id, type=job["type"], ok=body.ok, status=d.get("status"))
    return {"ok": True}


class SitesIn(BaseModel):
    sites: dict


def save_sites(uid: str, sites: dict) -> None:
    """État des sites vu par l'extension : {clé: {connected, portal?, detail?}}."""
    with db.tx() as c:
        p = c.execute("SELECT sites_status, jobteaser_status FROM preferences WHERE user_id=?", (uid,)).fetchone()
        merged = db.jload(p["sites_status"], {})
        for key, v in sites.items():
            if key in SITES and isinstance(v, dict):
                merged[key] = {"connected": bool(v.get("connected")), "detail": str(v.get("detail") or "")[:200],
                               "checked_at": db.now()}
        jt = sites.get("jobteaser")
        if isinstance(jt, dict):
            was = p["jobteaser_status"]
            st = "connected" if jt.get("connected") else ("expired" if was == "connected" else "")
            c.execute("UPDATE preferences SET jobteaser_status=?, jobteaser_at=? WHERE user_id=?", (st, db.now(), uid))
            portal = str(jt.get("portal") or "")
            if jt.get("connected") and portal.startswith("https://") and portal.split("/")[2].endswith("jobteaser.com"):
                c.execute("UPDATE preferences SET jobteaser_url=? WHERE user_id=?", ("/".join(portal.split("/")[:3]), uid))
            if st == "connected" and was != "connected":
                db.add_event(c, uid, "success", "JobTeaser connecté : le robot utilisera ta session.")
        c.execute("UPDATE preferences SET sites_status=? WHERE user_id=?", (json.dumps(merged), uid))


@router.put("/api/agent/sites")
def put_sites(body: SitesIn, user=Agent):
    save_sites(user["id"], body.sites)
    with db.tx() as c:
        return status(c, user["id"])


@router.get("/api/agent/config")
def agent_config(user=Agent):
    """Portail JobTeaser de l'utilisateur et sites à vérifier (seulement JobTeaser avec le faux portail de test)."""
    import os
    with db.tx() as c:
        portal = robot.portal(c, user["id"])
    test = worker.FAKE or os.environ.get("CANDIPILOT_TEST_PORTAL") == "1"
    return {"portal": portal, "sites": ["jobteaser"] if test else list(SITES)}


@router.get("/api/agent/file")
def agent_file(ref: str, user=Agent):
    """CV ou lettre du candidat, à déposer dans un formulaire."""
    with db.tx() as c:
        if not ref.startswith(files.PREFIX) or not c.execute("SELECT 1 FROM files WHERE id=? AND user_id=?",
                                                             (ref[len(files.PREFIX):], user["id"])).fetchone():
            raise HTTPException(404, "Fichier introuvable.")
        name, data = files.read(c, ref)
    return Response(data, media_type="application/pdf", headers={"X-Filename": name.encode("ascii", "ignore").decode()})


@router.get("/api/agent/profile")
def agent_profile(user=Agent):
    """Ce que l'extension écrit dans un formulaire de candidature (remplissage de n'importe quel site)."""
    with db.tx() as c:
        p = c.execute("SELECT * FROM profiles WHERE user_id=?", (user["id"],)).fetchone()
        cv = robot.default_cv(c, user["id"])
        letter = c.execute("""SELECT name, chemin FROM documents WHERE user_id=? AND type='lettre'
                              ORDER BY is_default DESC, created_at DESC LIMIT 1""", (user["id"],)).fetchone()
    full = f"{p['first_name']} {p['last_name']}".strip()
    return {"first_name": p["first_name"], "last_name": p["last_name"], "full_name": full, "email": user["email"],
            "phone": p["phone"], "linkedin": p["linkedin"], "city": p["location"], "school": p["school"],
            "program": p["program"], "availability": p["availability"], "gender": p["gender"], "languages": p["languages"],
            "cv": {"ref": cv["chemin"], "name": cv["name"]} if cv else None,
            "letter": {"ref": letter["chemin"], "name": letter["name"]} if letter else None}
