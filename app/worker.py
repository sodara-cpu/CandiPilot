"""Tâches du robot : file d'attente, exécution, enregistrement des résultats.

Tâches :
- search  : recherche + classement des offres (F04, F05)
- prepare : lecture du formulaire (lettre demandée ? questions ?) + lettre PDF (F06)
- submit  : envoi automatique ou assisté, puis preuve d'envoi (F07, règle « preuve vérifiable »)
- verify  : réconciliation d'une candidature « Incertaine » (§3.4)
- check_sites : vérifier les sites où l'utilisateur est connecté (extension)
- discover : offres des plateformes de recrutement (serveur, lot 2)
- rank     : note et explication de chaque nouvelle offre par Claude (serveur, lot 3)

Trois façons d'exécuter (CANDIPILOT_ROBOT) :
- extension (défaut) : l'extension CandiPilot du navigateur de CHAQUE utilisateur prend ses tâches
                       (app/agent.py) et les exécute avec ses propres sessions ; le serveur n'ouvre
                       aucun navigateur.
- local              : le robot Playwright de src/ tourne sur la machine du serveur (ancien mode).
- simule             : aucun navigateur, résultats fictifs (tests ; aussi CANDIPILOT_FAKE_ROBOT=1).

Quel que soit le mode, les résultats passent par les fonctions finish_* : mêmes règles partout
(pas de doublon, « Envoyée » seulement avec une preuve).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import traceback
from datetime import datetime, timedelta, timezone

from . import db, files, llm, matching, migrations, robot

FAKE = os.environ.get("CANDIPILOT_FAKE_ROBOT") == "1"
ROBOT = "simule" if FAKE else (os.environ.get("CANDIPILOT_ROBOT") or "extension")
AGENT_TYPES = ("search", "prepare", "submit", "verify", "check_sites")
STALE_MIN = 20          # tâche prise par une extension sans réponse depuis 20 min
POLL_S = 2
_stop = threading.Event()
_threads: list = []


def log_event(event: str, **fields) -> None:
    """Journal structuré (une ligne JSON par événement : lisible dans les journaux de Render)."""
    print(json.dumps({"ts": db.now(), "event": event, **fields}, ensure_ascii=False, default=str), flush=True)


# ---------- file de tâches ----------

def enqueue(conn, user_id: str, kind: str, application_id: str = "") -> str:
    """Ajoute une tâche, sauf si la même est déjà en file (pas de doublon d'envoi)."""
    # Découverte : une nouvelle passe est permise pendant qu'une autre tourne (sources ajoutées entre-temps).
    states = "('en_attente')" if kind in SERVER_TYPES else "('en_attente','en_cours')"
    dup = conn.execute(f"""SELECT id FROM jobs WHERE user_id=? AND type=? AND application_id=?
                          AND statut IN {states}""", (user_id, kind, application_id)).fetchone()
    if dup:
        return dup["id"]
    jid = db.new_id()
    conn.execute("""INSERT INTO jobs(id,user_id,type,application_id,statut,created_at,updated_at)
                    VALUES (?,?,?,?, 'en_attente', ?, ?)""", (jid, user_id, kind, application_id, db.now(), db.now()))
    return jid


def progress(job_id: str, text: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE jobs SET progress=?, updated_at=? WHERE id=?", (text[:300], db.now(), job_id))


def _set_app(c, app_id: str, **fields) -> None:
    fields["updated_at"] = db.now()
    cols = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE applications SET {cols} WHERE id=?", (*fields.values(), app_id))


def load_app(c, app_id: str):
    r = c.execute("""SELECT a.*, o.url, o.poste, o.entreprise, o.localisation, o.contrat, o.domaine,
                     o.start_date, o.apply_type, o.score FROM applications a JOIN offers o ON o.id=a.offer_id
                     WHERE a.id=?""", (app_id,)).fetchone()
    return db.row(r)


def jobteaser_ready(uid: str, app_id: str = "") -> bool:
    """Le robot réel n'agit qu'avec le compte JobTeaser de l'utilisateur, jamais avec celui d'un autre."""
    with db.tx() as c:
        st = c.execute("SELECT jobteaser_status FROM preferences WHERE user_id=?", (uid,)).fetchone()["jobteaser_status"]
        if st == "connected":
            return True
        msg = ("Ta session JobTeaser a expiré" if st == "expired" else "Ton compte JobTeaser n'est pas connecté") + \
              " : connecte-toi à JobTeaser dans ton navigateur (étape 3)."
        db.add_event(c, uid, "error", msg, app_id)
        if app_id:
            _set_app(c, app_id, statut="echec", detail=msg)
    return False


# ---------- enregistrement des résultats (commun aux trois modes) ----------

def finish_search(job: dict, found: list, stats: dict) -> None:
    from src.search import _clean_title, _guess_domain
    uid = job["user_id"]
    with db.tx() as c:
        ctx = robot.load_context(c, uid)
        if not stats.get("login", True):
            c.execute("UPDATE preferences SET jobteaser_status='expired' WHERE user_id=?", (uid,))
            db.add_event(c, uid, "error", "Ta session JobTeaser a expiré : reconnecte-toi à JobTeaser dans ton navigateur, puis relance la recherche.")
            return
        min_score = int(ctx["prefs"].get("min_score") or 50)
        lines = robot.default_domain_lines()
        kept = 0
        for o in found:
            role = _clean_title(o.get("role") or o.get("title") or "")
            offer = {"poste": role, "entreprise": (o.get("company") or "").strip(), "localisation": (o.get("location") or "").strip(),
                     "contrat": (o.get("contract") or "").strip(),
                     "domaine": o.get("domain") or _guess_domain(role, o.get("keyword") or "", lines),
                     "start_date": o.get("start_date", ""), "apply_type": o.get("apply_type", "jobteaser")}
            if not o.get("url") or not offer["poste"]:
                continue
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
        if found and llm.enabled():
            enqueue(c, uid, "rank")


def _letter(c, a: dict, ctx: dict) -> tuple:
    """Lettre de la candidature : la lettre importée si c'est le choix du candidat (jamais remplacée sans lui),
    sinon une lettre adaptée à l'offre à partir de son modèle. Renvoie (texte, référence, type)."""
    uid = a["user_id"]
    source = c.execute("SELECT letter_source FROM preferences WHERE user_id=?", (uid,)).fetchone()["letter_source"]
    kind = a["letter_kind"] or ("document" if source == "document" else "generated")
    if kind == "document":
        doc = c.execute("""SELECT chemin, name FROM documents WHERE user_id=? AND type='lettre'
                           ORDER BY is_default DESC, created_at DESC LIMIT 1""", (uid,)).fetchone()
        return "", robot.copy_letter(c, doc, uid) if doc else "", kind
    text = a["letter_text"] or robot.draft_letter(ctx, a)
    return text, robot.render_letter(c, ctx, a, text, a["id"]), kind


def finish_prepare(job: dict, info: dict) -> None:
    uid, app_id = job["user_id"], job["application_id"]
    with db.tx() as c:
        a = load_app(c, app_id)
        if not a or a["statut"] in ("confirmee", "soumission"):
            return
        ctx = robot.load_context(c, uid)
        cv = robot.default_cv(c, uid)
        old = a["letter_path"]
        text, pdf, kind = _letter(c, a, ctx)
        if old and old != pdf:
            files.delete(c, old)
        questions = db.jload(a["questions"], [])
        known = {q["label"]: q.get("answer", "") for q in questions}
        questions = [{"label": l, "answer": known.get(l, "")} for l in info.get("questions") or []]
        fields = dict(letter_text=text, letter_path=pdf, letter_kind=kind, questions=json.dumps(questions, ensure_ascii=False),
                      letter_needed=int(bool(info.get("letter"))) if info.get("kind") == "easy" else -1,
                      detail=info.get("detail", ""), cv_document_id=a["cv_document_id"] or (cv["id"] if cv else ""))
        if info.get("kind") == "already":
            fields.update(statut="confirmee", preuve="JobTeaser : « Vous avez postulé à cette offre »", date_envoi=db.now())
            db.add_event(c, uid, "success", f"Déjà postulé : {a['entreprise']} (preuve JobTeaser).", app_id)
        elif info.get("kind") == "login":
            c.execute("UPDATE preferences SET jobteaser_status='expired' WHERE user_id=?", (uid,))
            fields.update(statut="echec", detail="Connexion JobTeaser expirée : reconnecte-toi puis reprends ce dossier.")
        else:
            if info.get("kind") == "external":
                fields["mode"] = "manual"
            fields["statut"] = "a_valider"
            msg = f"Dossier prêt pour {a['entreprise']}"
            msg += f" : {len(questions)} question(s) du recruteur." if questions else "."
            db.add_event(c, uid, "info", msg, app_id)
        _set_app(c, app_id, **fields)


def finish_submit(job: dict, status: str, detail: str, proof: str = "") -> None:
    uid, app_id = job["user_id"], job["application_id"]
    with db.tx() as c:
        a = load_app(c, app_id)
        if not a:
            return
        if status in ("submitted", "skipped"):
            _set_app(c, app_id, statut="confirmee", detail=detail, date_envoi=db.now(),
                     preuve=proof or "JobTeaser : « Vous avez postulé à cette offre »")
            db.add_event(c, uid, "success", f"Confirmation reçue : {a['entreprise']}.", app_id)
        elif status == "uncertain":
            _set_app(c, app_id, statut="incertaine", detail=detail)
            db.add_event(c, uid, "warning", f"Envoi non confirmé pour {a['entreprise']} : vérification à faire.", app_id)
        elif status in ("login", "captcha"):
            # Le robot s'arrête et passe la main : il ne contourne jamais une vérification anti-robot.
            if status == "login":
                c.execute("UPDATE preferences SET jobteaser_status='expired' WHERE user_id=?", (uid,))
            _set_app(c, app_id, statut="attente_utilisateur", detail=detail)
            db.add_event(c, uid, "warning", f"À toi de jouer pour {a['entreprise']} : {detail}", app_id)
        else:
            _set_app(c, app_id, statut="echec", detail=detail)
            db.add_event(c, uid, "error", f"Échec de l'envoi pour {a['entreprise']} : {detail}", app_id)


def finish_verify(job: dict, ok: bool) -> None:
    uid, app_id = job["user_id"], job["application_id"]
    with db.tx() as c:
        a = load_app(c, app_id)
        if not a:
            return
        if ok:
            _set_app(c, app_id, statut="confirmee", date_envoi=a["date_envoi"] or db.now(),
                     preuve="JobTeaser : « Vous avez postulé à cette offre »")
            db.add_event(c, uid, "success", f"Confirmation reçue : {a['entreprise']}.", app_id)
        else:
            db.add_event(c, uid, "warning", f"Aucune preuve d'envoi pour {a['entreprise']} : elle reste « Incertaine ».", app_id)


def submit_guard(job: dict):
    """Avant tout envoi : la candidature est-elle toujours à envoyer, sans doublon ? Renvoie l'application ou None."""
    with db.tx() as c:
        a = load_app(c, job["application_id"])
        if not a or a["statut"] != "soumission":
            return None
        dup = c.execute("""SELECT 1 FROM applications a JOIN offers o ON o.id=a.offer_id
                           WHERE a.user_id=? AND o.url=? AND a.statut='confirmee' AND a.id<>?""",
                        (a["user_id"], a["url"], a["id"])).fetchone()
        if dup:
            _set_app(c, a["id"], statut="ignoree", detail="Doublon : une candidature confirmée existe déjà.")
            return None
        return a


# ---------- exécution sur le serveur : robot simulé ou robot local (Playwright) ----------

def _session(uid: str):
    from src.browser import browser_session
    with db.tx() as c:
        return browser_session(robot.browser_settings(c, uid))


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
        if not jobteaser_ready(uid):
            return
        with db.tx() as c:
            settings = robot.browser_settings(c, uid)
        found, stats = collect(settings, cfg, known, robot.default_domain_lines(), log=lambda m: progress(job["id"], m))
    finish_search(job, found, stats)


def run_prepare(job: dict) -> None:
    with db.tx() as c:
        a = load_app(c, job["application_id"])
        if not a or a["statut"] in ("confirmee", "soumission"):
            return
        _set_app(c, a["id"], statut="preparation")
    if FAKE:
        fake_q = ["Pourquoi souhaitez-vous nous rejoindre ?"] if len(a["entreprise"]) % 2 else []
        info = {"kind": "easy" if a["apply_type"] == "jobteaser" else "external",
                "letter": True, "questions": fake_q, "detail": "Formulaire lu (robot simulé)."}
    elif a["apply_type"] != "jobteaser":
        info = {"kind": "external", "letter": False, "questions": [], "detail": "Candidature sur le site de l'entreprise : envoi manuel."}
    else:
        if not jobteaser_ready(job["user_id"], a["id"]):
            return
        from src.adapters.jobteaser import JobTeaserAdapter
        progress(job["id"], "Lecture du formulaire JobTeaser")
        with _session(job["user_id"]) as bctx:
            page = bctx.pages[0] if bctx.pages else bctx.new_page()
            page.goto(a["url"], wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            info = JobTeaserAdapter().inspect(page)
    finish_prepare(job, info)


def run_submit(job: dict) -> None:
    a = submit_guard(job)
    if not a:
        return
    mode = a["mode"] if a["mode"] in ("auto", "assisted") else "auto"
    progress(job["id"], "Envoi automatique" if mode == "auto" else "Envoi assisté : à toi de cliquer « Postuler »")
    if FAKE:
        return finish_submit(job, "submitted", f"envoi simulé ({mode})", "Preuve simulée")
    if a["apply_type"] == "jobteaser" and not jobteaser_ready(job["user_id"], a["id"]):
        return
    with db.tx() as c:
        ctx = robot.load_context(c, a["user_id"])
        cv = c.execute("SELECT chemin FROM documents WHERE id=? AND user_id=?", (a["cv_document_id"], a["user_id"])).fetchone() \
            or robot.default_cv(c, a["user_id"])
        cv_path = files.materialize(c, cv["chemin"]) if cv else ""
        letter_path = files.materialize(c, a["letter_path"])
    screening = {q["label"]: q.get("answer", "") for q in db.jload(a["questions"], [])}
    finish_submit(job, *_submit_local(a, ctx, cv_path, letter_path, screening, mode))


def _submit_local(a: dict, ctx: dict, cv_path: str, letter_path: str, screening: dict, mode: str) -> tuple:
    from src.adapters import pick_adapter
    from src.adapters.jobteaser import JobTeaserAdapter

    profile = robot.to_profile(ctx, cv_path=cv_path, screening=screening)
    offer = robot.to_offer(a)
    message = {"body": a["letter_text"], "pdf_path": letter_path}
    adapter = pick_adapter(offer)
    with _session(a["user_id"]) as bctx:
        page = bctx.pages[0] if bctx.pages else bctx.new_page()
        page.goto(a["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if isinstance(adapter, JobTeaserAdapter):
            res = adapter.apply(page, offer, profile, message, dry_run=False, confirm_before_submit=False, mode=mode)
        else:
            res = adapter.apply(page, offer, profile, message, dry_run=(mode != "auto"), confirm_before_submit=False)
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
    with db.tx() as c:
        a = load_app(c, job["application_id"])
    if not a:
        return
    if FAKE:
        return finish_verify(job, True)
    if not jobteaser_ready(job["user_id"]):
        return
    from src.adapters.jobteaser import ALREADY_APPLIED
    with _session(job["user_id"]) as bctx:
        page = bctx.pages[0] if bctx.pages else bctx.new_page()
        page.goto(a["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        finish_verify(job, page.get_by_text(ALREADY_APPLIED).count() > 0)


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


def run_discover(job: dict) -> None:
    """Découverte multi-sources (lot 2) : API publiques des plateformes de recrutement, côté serveur."""
    from . import discovery
    uid, started = job["user_id"], db.now()
    with db.tx() as c:
        ctx = robot.load_context(c, uid)
        sources = db.rows(c.execute("SELECT * FROM job_sources WHERE user_id=? AND enabled=1", (uid,)))
        for src in sources:      # nom lisible pour les offres (les anciennes sources gardaient l'identifiant)
            if src["company"] == src["token"]:
                src["company"] = discovery.company_name(src["platform"], src["token"])
    prefs, prof = ctx["prefs"], ctx["profile"]
    min_score = int(prefs.get("min_score") or 50)
    wanted = [x.lower() for x in prefs.get("contrats") or []]
    zones = [z.lower() for z in prefs.get("zones") or []]
    total_new = 0
    from . import adzuna, francetravail
    AGGREGATORS = {"francetravail": francetravail, "adzuna": adzuna}     # API officielles, cherchées par métier
    for name, mod in AGGREGATORS.items():
        if mod.enabled():
            sources.append({"id": None, "platform": name, "token": "", "company": ""})

    def read(src):
        try:
            if src["platform"] in AGGREGATORS:
                return AGGREGATORS[src["platform"]].search(prefs), None
            return discovery.fetch(src["platform"], src["token"], src["company"]), None
        except Exception as e:
            return None, e
    from concurrent.futures import ThreadPoolExecutor, as_completed
    ex = ThreadPoolExecutor(8)                 # des dizaines d'entreprises : lues en parallèle…
    pending = {ex.submit(read, src): src for src in sources}
    for fut in as_completed(pending):         # …et chaque entreprise enregistrée dès qu'elle est lue (offres visibles au fil de l'eau)
        src = pending[fut]
        jobs, e = fut.result()
        if e is not None:
            with db.tx() as c:
                if src["id"]:
                    c.execute("UPDATE job_sources SET last_run_at=?, last_status=? WHERE id=?", (db.now(), f"erreur : {e}"[:200], src["id"]))
            log_event("discover_error", user=uid, source=f"{src['platform']}:{src['token']}", error=str(e)[:200])
            continue
        new = kept = 0
        with db.tx() as c:
            for j in jobs[:500]:
                contract = discovery.contract_of(j)
                if wanted and not any(w in contract or w in j["contract"].lower() for w in wanted):
                    continue
                if zones and not discovery.in_zone(j["location"], zones, j["remote"]):
                    continue
                offer = {"poste": j["title"], "entreprise": j["company"], "localisation": j["location"], "contrat": contract,
                         "domaine": "", "start_date": "", "apply_type": "external"}
                score = matching.score(offer, prefs, prof)
                if score < min_score:
                    continue
                kept += 1
                fp = discovery.fingerprint(j["company"], j["title"], j["location"])
                ref = f"{src['platform']}:{src['token']}:{j['ref']}"
                dup = c.execute("SELECT id FROM offers WHERE user_id=? AND (url=? OR fingerprint=?)", (uid, j["url"], fp)).fetchone()
                if dup:      # déjà connue (même adresse, ou même offre publiée ailleurs) : toujours en ligne
                    c.execute("UPDATE offers SET last_seen_at=?, expired_at='' WHERE id=?", (db.now(), dup["id"]))
                    continue
                oid = db.new_id()
                c.execute("""INSERT INTO offers(id,user_id,url,poste,entreprise,localisation,contrat,domaine,start_date,apply_type,
                             source,score,created_at,source_ref,fingerprint,description,remote,last_seen_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (oid, uid, j["url"], j["title"], j["company"], j["location"], contract, "", "", "external",
                           src["platform"], score, db.now(), ref, fp, j["description"], int(j["remote"]), db.now()))
                c.execute("""INSERT INTO applications(id,user_id,offer_id,statut,mode,created_at,updated_at) VALUES (?,?,?,?,?,?,?)""",
                          (db.new_id(), uid, oid, "qualifiee" if score >= min_score else "decouverte", "manual", db.now(), db.now()))
                new += 1
            if src["id"]:      # (agrégateurs : résultats d'une recherche, pas la liste complète d'une entreprise)
                # Offres de cette source qui ne sont plus publiées : expirées.
                c.execute("""UPDATE offers SET expired_at=? WHERE user_id=? AND source_ref LIKE ? AND expired_at=''
                             AND last_seen_at<>'' AND last_seen_at<?""", (db.now(), uid, f"{src['platform']}:{src['token']}:%", started))
                c.execute("UPDATE job_sources SET last_run_at=?, last_status=?, last_count=? WHERE id=?",
                          (db.now(), f"ok : {len(jobs)} offres publiées, {kept} pour toi", kept, src["id"]))
        total_new += new
        log_event("discover_source", user=uid, source=f"{src['platform']}:{src['token']}", published=len(jobs), kept=kept, new=new)
    ex.shutdown()
    with db.tx() as c:
        db.add_event(c, uid, "info", f"Découverte : {total_new} nouvelle(s) offre(s) sur {len(sources)} source(s).")
        if total_new and llm.enabled():
            enqueue(c, uid, "rank")


def run_rank(job: dict) -> None:
    """Matching intelligent (lot 3) : Claude note et explique les offres pas encore relues, par lots.
    En cas d'erreur, les notes des critères stricts restent : rien n'est bloqué."""
    uid = job["user_id"]
    with db.tx() as c:
        ctx = robot.load_context(c, uid)
        limit = min(llm.budget_left(c, uid), llm.BATCH * 3)
        offers = db.rows(c.execute("""SELECT o.* FROM offers o LEFT JOIN applications a ON a.offer_id=o.id
                                       WHERE o.user_id=? AND o.matched_at='' AND (o.expired_at='' OR o.expired_at IS NULL)
                                       AND COALESCE(a.launched, 0)=0 ORDER BY o.score DESC, o.created_at DESC LIMIT ?""",
                                    (uid, limit))) if limit else []
    min_score = int(ctx["prefs"].get("min_score") or 50)
    rated = 0
    for i in range(0, len(offers), llm.BATCH):
        batch = offers[i:i + llm.BATCH]
        try:
            results, usage = llm.rate(batch, ctx)
        except Exception as e:
            log_event("rank_error", user=uid, model=llm.MODEL, error=f"{type(e).__name__}: {e}"[:300])
            break
        known = {o["id"]: o for o in batch}
        with db.tx() as c:
            llm.record_usage(c, uid, len(batch), usage)
            for r in results:
                o = known.get(r.get("id"))
                if not o:
                    continue          # identifiant inventé ou modifié : ignoré
                s = max(0, min(100, int(r["score"])))
                c.execute("""UPDATE offers SET rule_score=?, score=?, match_reason=?, matched_at=? WHERE id=? AND user_id=?""",
                          (o["score"], s, str(r.get("reason") or "")[:300], db.now(), o["id"], uid))
                # Une offre que Claude juge (in)adaptée change de colonne, tant que le candidat n'y a pas touché.
                col = "qualifiee" if s >= min_score else "decouverte"
                c.execute("""UPDATE applications SET statut=?, updated_at=? WHERE offer_id=? AND user_id=?
                             AND statut IN ('qualifiee','decouverte') AND statut<>? AND selected=0 AND launched=0""",
                          (col, db.now(), o["id"], uid, col))
                rated += 1
        log_event("rank_batch", user=uid, model=llm.MODEL, offers=len(batch), **usage)
    if rated:
        with db.tx() as c:
            db.add_event(c, uid, "info", f"{rated} offre(s) relue(s) par l'IA : note et explication mises à jour.")
            if rated == len(offers) == limit and llm.budget_left(c, uid) > 0:
                enqueue(c, uid, "rank")        # il en reste : on continue (dans la limite du plafond quotidien)


HANDLERS = {"search": run_search, "prepare": run_prepare, "submit": run_submit, "verify": run_verify, "discover": run_discover,
            "rank": run_rank}
SERVER_TYPES = ("discover", "rank")   # toujours exécutées par le serveur, même en mode extension


# ---------- boucles ----------

def _next_job():
    """Prochaine tâche exécutée sur le serveur (modes simulé et local ; en mode extension, c'est agent.py)."""
    with db.tx() as c:
        if ROBOT == "extension":       # le navigateur de l'utilisateur fait le reste (agent.py)
            r = c.execute(f"""SELECT * FROM jobs WHERE statut='en_attente' AND type IN ({",".join("?" * len(SERVER_TYPES))})
                             ORDER BY created_at LIMIT 1""", SERVER_TYPES).fetchone()
        else:
            r = c.execute("""SELECT * FROM jobs WHERE statut='en_attente' AND type<>'check_sites'
                             ORDER BY created_at LIMIT 1""").fetchone()
        if r is None:
            return None
        c.execute("UPDATE jobs SET statut='en_cours', updated_at=? WHERE id=?", (db.now(), r["id"]))
        return db.row(r)


def fail_job(job: dict, error: str) -> None:
    with db.tx() as c:
        db.add_event(c, job["user_id"], "error", f"Le robot a rencontré une erreur ({job['type']}) : {error}", job["application_id"])
        if job["application_id"] and job["type"] in ("prepare", "submit"):
            _set_app(c, job["application_id"], statut="echec", detail=error[:300])


def _worker_loop() -> None:
    while not _stop.is_set():
        job = _next_job()
        if job is None:
            _stop.wait(POLL_S)
            continue
        try:
            HANDLERS[job["type"]](job)
            statut, msg = "termine", ""
        except Exception as e:
            traceback.print_exc()
            statut, msg = "erreur", f"{type(e).__name__}: {e}"
            fail_job(job, msg)
        with db.tx() as c:
            c.execute("UPDATE jobs SET statut=?, progress=?, updated_at=? WHERE id=?", (statut, msg[:300], db.now(), job["id"]))


def release_stale(c) -> None:
    """Extension fermée en pleine tâche : on la remet en file, sauf un envoi (il a peut-être eu lieu)."""
    limit = (datetime.now(timezone.utc) - timedelta(minutes=STALE_MIN)).isoformat(timespec="seconds")
    # Sans nouvelle depuis 20 min (ni prise en charge, ni progression) : l'extension a été fermée.
    for j in c.execute("SELECT * FROM jobs WHERE statut='en_cours' AND claimed_at<>'' AND updated_at<?", (limit,)).fetchall():
        if j["type"] == "submit":
            c.execute("UPDATE jobs SET statut='erreur', progress='Navigateur fermé pendant l''envoi', updated_at=? WHERE id=?",
                      (db.now(), j["id"]))
            _set_app(c, j["application_id"], statut="incertaine",
                     detail="Le navigateur s'est fermé pendant l'envoi : vérifie sur JobTeaser.")
        else:
            c.execute("UPDATE jobs SET statut='en_attente', claimed_at='', updated_at=? WHERE id=?", (db.now(), j["id"]))


def _scheduler_loop() -> None:
    """Copilote : relance la recherche de chaque compte actif selon sa fréquence."""
    while not _stop.wait(60):
        with db.tx() as c:
            release_stale(c)
            for p in c.execute("SELECT * FROM preferences WHERE paused=0").fetchall():
                every = timedelta(hours=max(1, int(p["search_every_hours"] or 24)))
                last = p["last_search_at"]
                due = not last or datetime.fromisoformat(last) + every <= datetime.now(timezone.utc)
                ready = FAKE or p["jobteaser_status"] == "connected"     # jamais avec le compte d'un autre
                if not (due and db.jload(p["metiers"], [])):
                    continue
                migrations.ensure_default_sources(c, p["user_id"])
                from . import adzuna, francetravail
                has_sources = francetravail.enabled() or adzuna.enabled() or \
                    c.execute("SELECT 1 FROM job_sources WHERE user_id=? AND enabled=1", (p["user_id"],)).fetchone()
                if has_sources:
                    enqueue(c, p["user_id"], "discover")         # plateformes de recrutement (serveur)
                if ready:
                    enqueue(c, p["user_id"], "search")           # JobTeaser (navigateur de l'utilisateur)
                if has_sources or ready:
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
