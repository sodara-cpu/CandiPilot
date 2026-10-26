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
from urllib.parse import urlparse
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


# Version minimale de l'extension pour chaque type de tâche (une extension plus ancienne ne sait pas la faire).
MIN_VERSION = {"apply": "0.6.0"}


def _older(a: str, b: str) -> bool:
    parse = lambda v: [int(x) if x.isdigit() else 0 for x in (v or "0").split(".")[:3]] + [0] * 3
    return parse(a)[:3] < parse(b)[:3]


def agent_user(authorization: str = Header(default=""), x_candipilot_version: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Extension non reliée.")
    with db.tx() as c:
        r = c.execute("""SELECT u.id, u.email FROM agent_tokens t JOIN users u ON u.id=t.user_id
                         WHERE t.token_hash=? AND u.statut='actif'""", (_hash(token),)).fetchone()
        if not r:
            raise HTTPException(401, "Jeton d'extension invalide : ouvre CandiPilot pour relier l'extension.")
        # Version annoncée à chaque échange (elle change quand le candidat met l'extension à jour, sans nouvelle liaison).
        if x_candipilot_version:
            c.execute("UPDATE agent_tokens SET last_seen=?, version=? WHERE token_hash=?", (db.now(), x_candipilot_version[:20], _hash(token)))
        else:
            c.execute("UPDATE agent_tokens SET last_seen=? WHERE token_hash=?", (db.now(), _hash(token)))
    return dict(r, version=x_candipilot_version)


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
            # Dernière version de l'extension (téléchargeable) : l'appli propose la mise à jour si l'installée est plus ancienne.
            "latest": json.loads((EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))["version"],
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
    if kind in ("submit", "apply"):
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
def next_job(response: Response, urgent: int = 0, user=Agent):
    """Prochaine tâche de CET utilisateur (204 s'il n'y en a pas). urgent=1 : seulement ses actions (préparer,
    envoyer, vérifier), demandé par l'extension entre deux offres pendant une longue recherche."""
    with db.tx() as c:
        worker.release_stale(c)
        while True:
            # Les actions demandées par l'utilisateur passent avant les recherches (longues) et les vérifications de sites.
            types = ("submit", "apply", "prepare", "verify") if urgent else worker.AGENT_TYPES
            j = c.execute(f"""SELECT * FROM jobs WHERE user_id=? AND statut='en_attente'
                              AND type IN ({','.join('?' * len(types))})
                              AND NOT (type='prepare' AND EXISTS (SELECT 1 FROM applications a JOIN offers o ON o.id=a.offer_id WHERE a.id=jobs.application_id AND o.apply_type<>'jobteaser'))
                              ORDER BY CASE WHEN type IN ('submit','apply','prepare','verify') THEN 0 ELSE 1 END, created_at LIMIT 1""",
                          (user["id"], *types)).fetchone()
            if not j:
                response.status_code = 204
                return None
            job = db.row(j)
            need = MIN_VERSION.get(job["type"])
            if need and _older(user.get("version") or "0", need):
                # Extension trop ancienne : on ne lui confie pas la tâche, on le dit clairement au candidat.
                msg = f"Ton extension CandiPilot (v{user.get('version') or '?'}) est trop ancienne : mets-la à jour (v{need} ou plus) puis clique « Reprendre l'envoi »."
                c.execute("UPDATE jobs SET statut='erreur', progress=?, updated_at=? WHERE id=?", (msg[:300], db.now(), job["id"]))
                if job["application_id"]:
                    worker._set_app(c, job["application_id"], statut="attente_utilisateur", detail=msg)
                worker.log_event("job_refused_old_extension", user=user["id"], job=job["id"], type=job["type"], version=user.get("version"))
                continue
            if job["type"] in ("submit", "apply") and not worker.submit_guard(job):
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
    phase: str = ""

# Étapes détaillées d'une candidature sur le site d'une entreprise (persistées dans applications.phase + journal d'audit).
PHASES = ("OPENING", "AUTHENTICATION_REQUIRED", "FILLING", "VALIDATING", "READY_TO_SUBMIT", "SUBMITTING", "SUBMITTED",
          "HUMAN_ACTION_REQUIRED", "UNSUPPORTED", "FAILED")


class PartialIn(BaseModel):
    found: list = []


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
    job = _own_job(job_id, user["id"])
    worker.progress(job_id, body.text)
    if body.phase in PHASES and job["application_id"]:
        with db.tx() as c:
            old = c.execute("SELECT phase FROM applications WHERE id=?", (job["application_id"],)).fetchone()
            if old and old["phase"] != body.phase:
                c.execute("UPDATE applications SET phase=?, updated_at=? WHERE id=?", (body.phase, db.now(), job["application_id"]))
                c.execute("""INSERT INTO audit_log(user_id, application_id, actor, action, from_status, to_status, detail, created_at)
                             VALUES (?,?,?,?,?,?,?,?)""", (user["id"], job["application_id"], "extension", "phase", old["phase"] or "",
                                                           body.phase, body.text[:200], db.now()))
    # Arrêt demandé par le candidat (tâche plus « en cours ») : l'extension s'arrête immédiatement.
    return {"ok": True, "stop": job["statut"] != "en_cours"}


class FieldsIn(BaseModel):
    fields: list = []
    job_id: str = ""
    url: str = ""


# Rôles reconnus par le réseau de neurones des champs → valeur du profil (les autres rôles ne sont jamais remplis ici).
MODEL_SURE = 0.85
# Rôles où le réseau fait au moins aussi bien que les règles sur le juge (vrais formulaires vérifiés à la main) :
# il n'est utilisé que là. E-mail, téléphone, consentement : les règles restent meilleures pour l'instant.
MODEL_ROLES = ("first_name", "last_name", "full_name", "city", "linkedin", "school")


def _model_answers(fields: list, ctx: dict, email: str) -> tuple:
    """Niveau 2 : le réseau de neurones (apprentissage/) identifie le rôle des champs non reconnus par les règles.
    Renvoie (réponses, champs restants pour Claude, champs à ne jamais remplir)."""
    from . import fieldmodel
    p = ctx["profile"]
    full = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    values = {"first_name": p.get("first_name"), "last_name": p.get("last_name"), "full_name": full, "email": email,
              "phone": p.get("phone"), "linkedin": p.get("linkedin"), "city": p.get("location"), "availability": p.get("availability"),
              "school": " – ".join(x for x in (p.get("school"), p.get("program")) if x)}
    champs = [{"tag": f.get("tag", "input"), "type": f.get("input_type", ""), "name": f.get("name", ""), "id": f.get("html_id", ""),
               "placeholder": f.get("placeholder", ""), "aria_label": f.get("aria_label", ""), "autocomplete": f.get("autocomplete", ""),
               "label": f.get("field_label") or f.get("label", ""), "text": "", "context": f.get("context", ""),
               "required": f.get("required", False), "options": f.get("options") or []} for f in fields]
    preds = fieldmodel.predict(champs)
    answers, rest, never = [], [], 0
    for f, (role, prob) in zip(fields, preds or [("other", 0.0)] * len(fields)):
        if prob >= MODEL_SURE and role in ("consent", "submit", "next"):
            never += 1                                        # consentement, bouton : jamais rempli automatiquement
        elif prob >= MODEL_SURE and role in MODEL_ROLES and values.get(role) and not f.get("options"):
            answers.append({"id": f["id"], "value": str(values[role]), "ok": True, "source": f"réseau ({role}, {prob:.0%})"})
        else:
            rest.append(f)
    return answers, rest, never


@router.post("/api/agent/fields")
def agent_fields(body: FieldsIn, user=Agent):
    """Agent de remplissage, pour les champs que les règles de l'extension n'ont pas compris (intitulés et choix
    seulement, jamais le contenu de la page) : 1) le réseau de neurones des champs reconnaît les champs du profil ;
    2) Claude répond aux questions propres à l'entreprise. Rien n'est envoyé : le candidat vérifie."""
    from . import llm
    fields = [f for f in body.fields if isinstance(f, dict) and isinstance(f.get("id"), int)][:40]
    if not fields:
        return {"answers": [], "ai": llm.enabled()}
    with db.tx() as c:
        ctx = robot.load_context(c, user["id"])
    model_out, fields, never = _model_answers(fields, ctx, ctx["email"])
    worker.log_event("fields_model", user=user["id"], recognized=len(model_out), excluded=never, left=len(fields))
    if not fields or not llm.enabled():
        return {"answers": model_out, "ai": llm.enabled()}
    with db.tx() as c:
        if llm.budget_left(c, user["id"]) < llm.FIELDS_COST:
            return {"answers": model_out, "ai": True, "detail": "Limite quotidienne de l'IA atteinte."}
        offer, answers = None, {}
        job = c.execute("SELECT application_id FROM jobs WHERE id=? AND user_id=?", (body.job_id, user["id"])).fetchone() if body.job_id else None
        if job and job["application_id"]:
            a = worker.load_app(c, job["application_id"])
            if a:
                offer = db.row(c.execute("SELECT * FROM offers WHERE id=? AND user_id=?", (a["offer_id"], user["id"])).fetchone())
                answers = {q["label"]: q.get("answer", "") for q in db.jload(a["questions"], [])}
        elif body.url:
            offer = db.row(c.execute("SELECT * FROM offers WHERE user_id=? AND url=?", (user["id"], body.url)).fetchone())
    try:
        out, usage = llm.map_fields(fields, ctx, offer, answers)
    except Exception as e:
        worker.log_event("fields_error", user=user["id"], error=f"{type(e).__name__}: {e}"[:300])
        return {"answers": model_out, "ai": True, "detail": "L'IA n'a pas pu répondre."}
    with db.tx() as c:
        llm.record_usage(c, user["id"], llm.FIELDS_COST, usage)
    worker.log_event("fields_mapped", user=user["id"], fields=len(fields), answered=len(out), **usage)
    return {"answers": model_out + [dict(a, source="claude") for a in out], "ai": True}


class StepIn(BaseModel):
    job_id: str = ""
    step: int = 0
    observation: dict = {}
    history: list = []


@router.post("/api/agent/step")
def agent_step(body: StepIn, user=Agent):
    """Agent navigateur : Claude décide des prochaines actions dans l'onglet de candidature (page observée par
    l'extension). Les actions sont exécutées par l'extension, dans le Chrome du candidat, qui ne clique jamais
    sur l'envoi final."""
    from . import llm
    if not llm.enabled():
        return {"actions": [{"name": "finish", "input": {"status": "need_user", "message": "Agent IA non activé : termine toi-même."}}]}
    if body.step >= llm.AGENT_MAX_STEPS:
        return {"actions": [{"name": "finish", "input": {"status": "need_user", "message": "Trop d'étapes : termine toi-même la candidature."}}]}
    with db.tx() as c:
        if body.job_id:
            st = c.execute("SELECT statut FROM jobs WHERE id=? AND user_id=?", (body.job_id, user["id"])).fetchone()
            if st and st["statut"] != "en_cours":            # arrêt immédiat demandé par le candidat
                return {"actions": [{"name": "finish", "input": {"status": "need_user", "message": "Candidature arrêtée par toi."}}], "stop": True}
        if llm.budget_left(c, user["id"]) < llm.AGENT_COST:
            return {"actions": [{"name": "finish", "input": {"status": "need_user", "message": "Limite quotidienne de l'IA atteinte."}}]}
        ctx = robot.load_context(c, user["id"])
        offer, answers = None, {}
        job = c.execute("SELECT application_id FROM jobs WHERE id=? AND user_id=?", (body.job_id, user["id"])).fetchone() if body.job_id else None
        if job and job["application_id"]:
            a = worker.load_app(c, job["application_id"])
            if a:
                offer = db.row(c.execute("SELECT * FROM offers WHERE id=? AND user_id=?", (a["offer_id"], user["id"])).fetchone())
                answers = {q["label"]: q.get("answer", "") for q in db.jload(a["questions"], [])}
        allow = _allow_submit(c, user["id"], (body.observation or {}).get("url", ""))
    try:
        actions, usage = llm.agent_step(body.observation or {}, [str(h)[:200] for h in body.history][-20:], ctx, offer, answers, allow)
    except Exception as e:
        worker.log_event("agent_step_error", user=user["id"], error=f"{type(e).__name__}: {e}"[:300])
        return {"actions": [{"name": "finish", "input": {"status": "need_user", "message": "L'IA n'a pas pu répondre : termine toi-même."}}]}
    with db.tx() as c:
        llm.record_usage(c, user["id"], llm.AGENT_COST, usage)
    if body.job_id:
        worker.progress(body.job_id, f"Agent IA, étape {body.step + 1} : " + ", ".join(a["name"] for a in actions))
    # Traces sans donnée personnelle : actions décidées, cibles (n° d'élément), résultat de la précédente.
    last = str(body.history[-1]).split("→")[-1].strip()[:80] if body.history else ""
    worker.log_event("agent_step", user=user["id"], step=body.step, actions=[f"{a['name']}#{a['input'].get('id', '')}" for a in actions],
                     last_result=last, page=urlparse((body.observation or {}).get("url", "")).path[:80],
                     errors=len((body.observation or {}).get("errors") or []), allow_submit=allow, cost_usd=llm.cost(usage), **usage)
    return {"actions": actions, "allow_submit": allow}


def _allow_submit(c, uid: str, url: str) -> bool:
    """Envoi final par l'agent : seulement si le candidat l'a choisi (mode automatique + consentement enregistré),
    et jamais sur un site dont les conditions interdisent l'automatisation (registre des sites : LinkedIn…)."""
    p = c.execute("SELECT default_mode, auto_submit_consent_at FROM preferences WHERE user_id=?", (uid,)).fetchone()
    if not p or p["default_mode"] != "auto" or not p["auto_submit_consent_at"]:
        return False
    host = urlparse(url).netloc.lower()
    return not any(d in host for d in ("linkedin.com", "welcometothejungle.com"))


@router.post("/api/agent/jobs/{job_id}/partial")
def job_partial(job_id: str, body: PartialIn, user=Agent):
    """Paquet d'offres trouvé en cours de recherche : visible tout de suite, noté par l'IA sans attendre la fin."""
    job = _own_job(job_id, user["id"])
    if job["type"] != "search" or job["statut"] not in ("en_cours", "en_attente"):
        raise HTTPException(409, "Tâche non concernée.")
    worker.finish_search(job, body.found[:100], {"login": True}, partial=True)
    worker.progress(job_id, f"{len(body.found)} offre(s) ajoutée(s), recherche en cours")
    return {"ok": True}


@router.post("/api/agent/jobs/{job_id}/result")
def job_result(job_id: str, body: ResultIn, user=Agent):
    job = _own_job(job_id, user["id"])
    # Remise en file par erreur pendant que l'extension travaillait (ancien redémarrage du serveur) : résultat accepté.
    if job["statut"] == "en_attente" and job.get("claimed_at"):
        with db.tx() as c:
            c.execute("UPDATE jobs SET statut='en_cours' WHERE id=? AND statut='en_attente'", (job_id,))
        job["statut"] = "en_cours"
    if job["statut"] != "en_cours":
        raise HTTPException(409, "Tâche déjà terminée.")
    d = body.data or {}
    if not body.ok:
        worker.fail_job(job, body.error or "erreur inconnue")
    elif job["type"] == "search":
        worker.finish_search(job, d.get("found") or [], d.get("stats") or {})
    elif job["type"] == "prepare":
        worker.finish_prepare(job, d.get("info") or {})
    elif job["type"] in ("submit", "apply"):
        with db.tx() as c:
            current = (worker.load_app(c, job["application_id"]) or {}).get("idempotency_key", "")
        if d.get("key") and current and d["key"] != current:
            # Résultat d'un ancien essai (navigateur resté ouvert, double clic…) : ignoré, pas de double comptage.
            worker.log_event("submit_result_ignored", job=job_id, reason="clé d'idempotence différente")
        else:
            worker.finish_submit(job, str(d.get("status") or "failed"), str(d.get("detail") or "")[:500],
                                 str(d.get("proof") or "")[:300])
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
