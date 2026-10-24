"""Worker du robot : exécute les tâches en arrière-plan, une à la fois.

Une seule tâche à la fois, car toutes partagent le navigateur du robot
(profil Chromium ./.user-profile, connecté à JobTeaser).

Tâches :
- search  : recherche + classement des offres (F04, F05)
- prepare : lettre PDF + lecture du formulaire (lettre demandée ? questions ?) (F06)
- submit  : envoi automatique ou assisté, puis preuve d'envoi (F07, règle « preuve vérifiable »)
- verify  : réconciliation d'une candidature « Incertaine » (§3.4)

CANDIPILOT_FAKE_ROBOT=1 simule le robot (aucun navigateur, aucun envoi réel) :
utile pour les tests et les démonstrations.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import db, matching, remote, robot

FAKE = os.environ.get("CANDIPILOT_FAKE_ROBOT") == "1"
POLL_S = 2
_stop = threading.Event()
_threads: list = []


# ---------- file de tâches ----------

def enqueue(conn, user_id: str, kind: str, application_id: str = "") -> str:
    """Ajoute une tâche, sauf si la même est déjà en file (pas de doublon d'envoi)."""
    dup = conn.execute("""SELECT id FROM jobs WHERE user_id=? AND type=? AND application_id=?
                          AND statut IN ('en_attente','en_cours')""", (user_id, kind, application_id)).fetchone()
    if dup:
        return dup["id"]
    jid = db.new_id()
    conn.execute("""INSERT INTO jobs(id,user_id,type,application_id,statut,created_at,updated_at)
                    VALUES (?,?,?,?, 'en_attente', ?, ?)""", (jid, user_id, kind, application_id, db.now(), db.now()))
    return jid


def _progress(job_id: str, text: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE jobs SET progress=?, updated_at=? WHERE id=?", (text[:300], db.now(), job_id))


def _set_app(c, app_id: str, **fields) -> None:
    fields["updated_at"] = db.now()
    cols = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE applications SET {cols} WHERE id=?", (*fields.values(), app_id))


def _load_app(c, app_id: str):
    r = c.execute("""SELECT a.*, o.url, o.poste, o.entreprise, o.localisation, o.contrat, o.domaine,
                     o.start_date, o.apply_type, o.score FROM applications a JOIN offers o ON o.id=a.offer_id
                     WHERE a.id=?""", (app_id,)).fetchone()
    return db.row(r)


# ---------- compte JobTeaser de l'utilisateur ----------

def _jobteaser_ready(uid: str, app_id: str = "") -> bool:
    """Le robot réel n'agit qu'avec le compte JobTeaser de l'utilisateur, jamais avec celui d'un autre."""
    with db.tx() as c:
        st = c.execute("SELECT jobteaser_status FROM preferences WHERE user_id=?", (uid,)).fetchone()["jobteaser_status"]
        if st == "connected":
            return True
        msg = ("Ta session JobTeaser a expiré" if st == "expired" else "Ton compte JobTeaser n'est pas connecté") + \
              " : connecte-le à l'étape 3 (Critères et offres)."
        db.add_event(c, uid, "error", msg, app_id)
        if app_id:
            _set_app(c, app_id, statut="echec", detail=msg)
    return False


def _session(uid: str):
    from src.browser import browser_session
    with db.tx() as c:
        return browser_session(robot.browser_settings(c, uid))


# ---------- tâches ----------

def run_search(job: dict) -> None:
    uid = job["user_id"]
    with db.tx() as c:
        ctx = robot.load_context(c, uid)
        known = {r["url"] for r in c.execute("SELECT url FROM offers WHERE user_id=?", (uid,))}
    cfg = robot.search_cfg(ctx["prefs"])
    if not cfg["keywords"]:
        with db.tx() as c:
            db.add_event(c, uid, "warning", "Recherche impossible : ajoute au moins un métier ciblé dans tes critères.")
        return

    if FAKE:
        found, stats = _fake_search(cfg, known), {"external": 0, "date": 0, "login": True}
    else:
        from src.search import collect
        if not _jobteaser_ready(uid):
            return
        with db.tx() as c:
            settings = robot.browser_settings(c, uid)
        found, stats = collect(settings, cfg, known, robot.default_domain_lines(),
                               log=lambda m: _progress(job["id"], m))

    with db.tx() as c:
        if not stats["login"]:
            c.execute("UPDATE preferences SET jobteaser_status='expired' WHERE user_id=?", (uid,))
            db.add_event(c, uid, "error", "Ta session JobTeaser a expiré : reconnecte ton compte (étape 3) puis relance la recherche.")
            return
        min_score = int(ctx["prefs"].get("min_score") or 50)
        kept = 0
        for o in found:
            offer = {"poste": o["role"], "entreprise": o["company"], "localisation": o.get("location", ""),
                     "contrat": o.get("contract", ""), "domaine": o.get("domain", ""),
                     "start_date": o.get("start_date", ""), "apply_type": o.get("apply_type", "jobteaser")}
            s = matching.score(offer, ctx["prefs"], ctx["profile"])
            oid = db.new_id()
            cur = c.execute("""INSERT OR IGNORE INTO offers(id,user_id,url,poste,entreprise,localisation,contrat,
                               domaine,start_date,apply_type,source,score,created_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?, 'jobteaser', ?, ?)""",
                            (oid, uid, o["url"], offer["poste"], offer["entreprise"], offer["localisation"],
                             offer["contrat"], offer["domaine"], offer["start_date"], offer["apply_type"], s, db.now()))
            if cur.rowcount:
                statut = "qualifiee" if s >= min_score else "decouverte"
                kept += statut == "qualifiee"
                c.execute("""INSERT INTO applications(id,user_id,offer_id,statut,mode,created_at,updated_at)
                             VALUES (?,?,?,?,?,?,?)""", (db.new_id(), uid, oid, statut,
                                                        ctx["prefs"].get("default_mode") or "auto", db.now(), db.now()))
        c.execute("UPDATE preferences SET last_search_at=? WHERE user_id=?", (db.now(), uid))
        db.add_event(c, uid, "info", f"{len(found)} nouvelles offres analysées, {kept} retenues.")


def run_prepare(job: dict) -> None:
    uid, app_id = job["user_id"], job["application_id"]
    with db.tx() as c:
        a = _load_app(c, app_id)
        if not a or a["statut"] in ("confirmee", "soumission"):
            return
        ctx = robot.load_context(c, uid)
        cv = robot.default_cv(c, uid)
        _set_app(c, app_id, statut="preparation", cv_document_id=a["cv_document_id"] or (cv["id"] if cv else ""))

    # Lettre : celle importée par le candidat si c'est son choix (jamais remplacée sans lui),
    # sinon une lettre adaptée à l'offre à partir de son modèle.
    with db.tx() as c:
        source = c.execute("SELECT letter_source FROM preferences WHERE user_id=?", (uid,)).fetchone()["letter_source"]
        doc = c.execute("""SELECT chemin, name FROM documents WHERE user_id=? AND type='lettre'
                           ORDER BY is_default DESC, created_at DESC LIMIT 1""", (uid,)).fetchone()
    kind = a["letter_kind"] or ("document" if source == "document" else "generated")
    if kind == "document":
        text, pdf = "", robot.copy_letter(doc, app_id) if doc else ""
        _progress(job["id"], "Lettre importée sélectionnée" if doc else "Lettre importée introuvable")
    else:
        text = a["letter_text"] or robot.draft_letter(ctx, a)
        _progress(job["id"], "Lettre adaptée générée")
        pdf = robot.render_letter(ctx, a, text, app_id)

    if not FAKE and a["apply_type"] == "jobteaser" and not _jobteaser_ready(uid, app_id):
        return
    info = _inspect(a, ctx, job["id"])
    with db.tx() as c:
        questions = db.jload(a["questions"], [])
        known = {q["label"]: q.get("answer", "") for q in questions}
        questions = [{"label": l, "answer": known.get(l, "")} for l in info["questions"]]
        fields = dict(letter_text=text, letter_path=pdf, letter_kind=kind, questions=json.dumps(questions, ensure_ascii=False),
                      letter_needed=int(info["letter"]) if info["kind"] == "easy" else -1, detail=info["detail"])
        if info["kind"] == "already":
            fields.update(statut="confirmee", preuve="JobTeaser : « Vous avez postulé à cette offre »",
                          date_envoi=db.now())
            db.add_event(c, uid, "success", f"Déjà postulé : {a['entreprise']} (preuve JobTeaser).", app_id)
        else:
            if info["kind"] == "external":
                fields["mode"] = "manual"
            fields["statut"] = "a_valider"
            msg = f"Dossier prêt pour {a['entreprise']}"
            msg += f" : {len(questions)} question(s) du recruteur." if questions else "."
            db.add_event(c, uid, "info", msg, app_id)
        _set_app(c, app_id, **fields)


def _inspect(a: dict, ctx: dict, job_id: str) -> dict:
    if FAKE:
        fake_q = ["Pourquoi souhaitez-vous nous rejoindre ?"] if len(a["entreprise"]) % 2 else []
        return {"kind": "easy" if a["apply_type"] == "jobteaser" else "external",
                "letter": True, "questions": fake_q, "detail": "Formulaire lu (robot simulé)."}
    if a["apply_type"] != "jobteaser":
        return {"kind": "external", "letter": False, "questions": [],
                "detail": "Candidature sur le site de l'entreprise : envoi manuel."}
    from src.adapters.jobteaser import JobTeaserAdapter
    _progress(job_id, "Lecture du formulaire JobTeaser")
    with _session(a["user_id"]) as bctx:
        page = bctx.pages[0] if bctx.pages else bctx.new_page()
        page.goto(a["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        return JobTeaserAdapter().inspect(page)


def run_submit(job: dict) -> None:
    uid, app_id = job["user_id"], job["application_id"]
    with db.tx() as c:
        a = _load_app(c, app_id)
        if not a or a["statut"] != "soumission":
            return
        ctx = robot.load_context(c, uid)
        dup = c.execute("""SELECT 1 FROM applications a JOIN offers o ON o.id=a.offer_id
                           WHERE a.user_id=? AND o.url=? AND a.statut='confirmee' AND a.id<>?""",
                        (uid, a["url"], app_id)).fetchone()
        cv = c.execute("SELECT chemin FROM documents WHERE id=? AND user_id=?",
                       (a["cv_document_id"], uid)).fetchone() or robot.default_cv(c, uid)
    if dup:
        with db.tx() as c:
            _set_app(c, app_id, statut="ignoree", detail="Doublon : une candidature confirmée existe déjà.")
        return

    screening = {q["label"]: q.get("answer", "") for q in db.jload(a["questions"], [])}
    mode = a["mode"] if a["mode"] in ("auto", "assisted") else "auto"
    _progress(job["id"], "Envoi automatique" if mode == "auto" else "Envoi assisté : à toi de cliquer « Postuler »")

    if not FAKE and a["apply_type"] == "jobteaser" and not _jobteaser_ready(uid, app_id):
        return
    if FAKE:
        status, detail = "submitted", f"envoi simulé ({mode})"
    else:
        status, detail = _submit_real(a, ctx, cv["chemin"] if cv else "", screening, mode)

    with db.tx() as c:
        if status in ("submitted", "skipped"):
            _set_app(c, app_id, statut="confirmee", detail=detail, date_envoi=db.now(),
                     preuve="JobTeaser : « Vous avez postulé à cette offre »" if not FAKE else "Preuve simulée")
            db.add_event(c, uid, "success", f"Confirmation reçue : {a['entreprise']}.", app_id)
        elif status == "uncertain":
            _set_app(c, app_id, statut="incertaine", detail=detail)
            db.add_event(c, uid, "warning", f"Envoi non confirmé pour {a['entreprise']} : vérification à faire.", app_id)
        else:
            _set_app(c, app_id, statut="echec", detail=detail)
            db.add_event(c, uid, "error", f"Échec de l'envoi pour {a['entreprise']} : {detail}", app_id)


def _submit_real(a: dict, ctx: dict, cv_path: str, screening: dict, mode: str) -> tuple:
    from src.adapters import pick_adapter
    from src.adapters.jobteaser import JobTeaserAdapter

    profile = robot.to_profile(ctx, cv_path=cv_path, screening=screening)
    offer = robot.to_offer(a)
    message = {"body": a["letter_text"], "pdf_path": a["letter_path"]}
    adapter = pick_adapter(offer)
    with _session(a["user_id"]) as bctx:
        page = bctx.pages[0] if bctx.pages else bctx.new_page()
        page.goto(a["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if isinstance(adapter, JobTeaserAdapter):
            res = adapter.apply(page, offer, profile, message, dry_run=False,
                                confirm_before_submit=False, mode=mode)
        else:
            res = adapter.apply(page, offer, profile, message, dry_run=(mode != "auto"),
                                confirm_before_submit=False)
        if mode == "assisted" and res.status == "filled_dry":
            ok = JobTeaserAdapter().wait_for_user_submit(page) if isinstance(adapter, JobTeaserAdapter) else False
            return ("submitted", res.detail + " ; envoyé par toi") if ok else \
                   ("uncertain", res.detail + " ; envoi non détecté (fenêtre fermée ou délai dépassé)")
    if res.status in ("submitted", "skipped"):
        return res.status, res.detail
    if res.status == "needs_human" and "non confirmé" in res.detail:
        return "uncertain", res.detail
    return "failed", res.detail


def run_verify(job: dict) -> None:
    uid, app_id = job["user_id"], job["application_id"]
    with db.tx() as c:
        a = _load_app(c, app_id)
    if not a:
        return
    ok = True
    if not FAKE and not _jobteaser_ready(uid):
        return
    if not FAKE:
        from src.adapters.jobteaser import ALREADY_APPLIED
        with _session(uid) as bctx:
            page = bctx.pages[0] if bctx.pages else bctx.new_page()
            page.goto(a["url"], wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            ok = page.get_by_text(ALREADY_APPLIED).count() > 0
    with db.tx() as c:
        if ok:
            _set_app(c, app_id, statut="confirmee", date_envoi=a["date_envoi"] or db.now(),
                     preuve="JobTeaser : « Vous avez postulé à cette offre »")
            db.add_event(c, uid, "success", f"Confirmation reçue : {a['entreprise']}.", app_id)
        else:
            db.add_event(c, uid, "warning", f"Aucune preuve d'envoi pour {a['entreprise']} : elle reste « Incertaine ».", app_id)


def _fake_search(cfg: dict, known: set) -> list:
    samples = [("Nordline Analytics", "Data Analyst junior", "Paris, France", "Stage 6 mois"),
               ("Banque Orsay", "Analyste financier", "Paris La Défense, France", "Stage 6 mois"),
               ("Studio Kairn", "Consultant data", "Nantes, France", "Stage 6 mois"),
               ("Helios Énergie", "Contrôleur de gestion junior", "Lyon, France", "Stage 6 mois"),
               ("Maison Lumen", "Chargé de marketing digital", "Lyon, France", "Alternance")]
    out = []
    for i, (company, role, loc, contract) in enumerate(samples):
        url = "https://example.invalid/offre/" + hashlib.md5(f"{company}|{role}|{cfg['keywords'][0]}".encode()).hexdigest()[:10]
        if url in known:
            continue
        out.append({"url": url, "company": company, "role": role,
                    "domain": cfg["keywords"][0].title(), "location": loc, "contract": contract,
                    "start_date": cfg.get("start_from") or "2027-03-01",
                    "apply_type": "jobteaser" if i != 4 else "external"})
    return out


HANDLERS = {"search": run_search, "prepare": run_prepare, "submit": run_submit, "verify": run_verify}


# ---------- boucles ----------

def _next_job():
    with db.tx() as c:
        # Les tâches d'un utilisateur en train de connecter son JobTeaser attendent (même navigateur).
        busy = sorted(remote.active_users())
        r = c.execute(f"""SELECT * FROM jobs WHERE statut='en_attente'
                          AND user_id NOT IN ({','.join('?' * len(busy))}) ORDER BY created_at LIMIT 1""", busy).fetchone()
        if r is None:
            return None
        c.execute("UPDATE jobs SET statut='en_cours', updated_at=? WHERE id=?", (db.now(), r["id"]))
        return db.row(r)


def _worker_loop() -> None:
    while not _stop.is_set():
        job = _next_job()
        if job is None:
            _stop.wait(POLL_S)
            continue
        try:
            HANDLERS[job["type"]](job)
            statut, progress = "termine", ""
        except Exception as e:
            traceback.print_exc()
            statut, progress = "erreur", f"{type(e).__name__}: {e}"
            with db.tx() as c:
                db.add_event(c, job["user_id"], "error", f"Le robot a rencontré une erreur ({job['type']}) : {e}",
                             job["application_id"])
                if job["application_id"] and job["type"] in ("prepare", "submit"):
                    _set_app(c, job["application_id"], statut="echec", detail=progress[:300])
        with db.tx() as c:
            c.execute("UPDATE jobs SET statut=?, progress=?, updated_at=? WHERE id=?",
                      (statut, progress[:300], db.now(), job["id"]))


def _scheduler_loop() -> None:
    """Copilote : relance la recherche de chaque compte actif selon sa fréquence."""
    while not _stop.wait(60):
        with db.tx() as c:
            for p in c.execute("SELECT * FROM preferences WHERE paused=0").fetchall():
                every = timedelta(hours=max(1, int(p["search_every_hours"] or 24)))
                last = p["last_search_at"]
                due = not last or datetime.fromisoformat(last) + every <= datetime.now(timezone.utc)
                ready = FAKE or p["jobteaser_status"] == "connected"     # jamais avec le compte d'un autre
                if due and ready and db.jload(p["metiers"], []):
                    enqueue(c, p["user_id"], "search")
                    c.execute("UPDATE preferences SET last_search_at=? WHERE user_id=?", (db.now(), p["user_id"]))


def start() -> None:
    if _threads:
        return
    _stop.clear()
    for target in (_worker_loop, _scheduler_loop):
        t = threading.Thread(target=target, daemon=True, name=target.__name__)
        t.start()
        _threads.append(t)


def stop() -> None:
    _stop.set()
