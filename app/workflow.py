"""Parcours du candidat : cinq étapes, blocages de chaque dossier, récapitulatif et résultats d'un lancement.

Tout est calculé à partir des données enregistrées : la progression reste juste après actualisation.
Rien ici n'envoie de candidature ; seul POST /api/launch (main.py) déclenche des envois.
"""
from __future__ import annotations

import os

from . import db, files

# Robot simulé (tests, recette) : pas de vrai compte JobTeaser nécessaire.
FAKE = os.environ.get("CANDIPILOT_FAKE_ROBOT") == "1"


ORDER = {"manual": 0, "assisted": 1, "auto": 2}


def capped_mode(c, a: dict) -> str:
    """Mode effectif : celui choisi, plafonné par le registre des sites (sites inconnus : remplissage seulement)."""
    from .migrations import policy_for
    if a.get("apply_type") != "jobteaser":
        return "manual"
    pol = policy_for(c, a["url"])
    test_portal = os.environ.get("CANDIPILOT_TEST_PORTAL") == "1" and "127.0.0.1" in a["url"]   # faux portail des tests
    cap = pol["mode_max"] if pol else ("auto" if FAKE or test_portal else "manual")
    return a["mode"] if ORDER.get(a["mode"], 0) <= ORDER[cap] else cap


def jobteaser_missing(pr: dict) -> str:
    if FAKE or pr["jobteaser_status"] == "connected":
        return ""
    if pr["jobteaser_status"] == "expired":
        return "ta session JobTeaser (expirée : reconnecte-toi à JobTeaser dans ton navigateur)"
    return "l'extension CandiPilot et ta session JobTeaser ouverte dans ton navigateur"

MODES = {
    "auto": ("Automatique", "Le robot remplit le formulaire, l'envoie et attend la preuve de réception."),
    "assisted": ("Assisté", "Le robot ouvre et pré-remplit le formulaire ; tu cliques toi-même « Postuler maintenant »."),
    "manual": ("Semi-assisté", "CandiPilot prépare CV, lettre et réponses ; tu postules toi-même puis tu le déclares."),
}
REQUIRED_PROFILE = (("first_name", "prénom"), ("last_name", "nom"), ("phone", "téléphone"),
                    ("availability", "date de disponibilité"), ("gender", "genre"))
PRE_SEND = ("decouverte", "qualifiee", "preparation", "a_valider", "echec")


def prefs(c, uid: str) -> dict:
    p = db.row(c.execute("SELECT * FROM preferences WHERE user_id=?", (uid,)).fetchone())
    p["metiers"] = db.jload(p["metiers"], [])
    return p


def default_doc(c, uid: str, kind: str):
    return c.execute("""SELECT * FROM documents WHERE user_id=? AND type=? ORDER BY is_default DESC, created_at DESC
                        LIMIT 1""", (uid, kind)).fetchone()


def missing_profile(c, uid: str) -> list:
    p = c.execute("SELECT * FROM profiles WHERE user_id=?", (uid,)).fetchone()
    return [label for key, label in REQUIRED_PROFILE if not (p[key] or "").strip()]


def missing_documents(c, uid: str, pr: dict) -> list:
    out = []
    if not default_doc(c, uid, "cv"):
        out.append("ton CV (PDF)")
    src = pr["letter_source"]
    if not src:
        out.append("le choix de ta lettre de motivation")
    elif src == "document" and not default_doc(c, uid, "lettre"):
        out.append("ta lettre de motivation (PDF)")
    elif src == "template":
        tpl = c.execute("SELECT letter_template FROM profiles WHERE user_id=?", (uid,)).fetchone()["letter_template"]
        if not (tpl or "").strip():
            out.append("ton modèle de lettre")
    return out


def flow(pr: dict, mode: str) -> dict:
    """Options facultatives applicables au mode (jamais en envoi automatique)."""
    optional = mode in ("assisted", "manual")
    return {"prepare": optional and pr["want_prepare"] == 1, "review": optional and pr["want_review"] == 1}


def blockers(c, a: dict, uid: str, pr: dict, missing_prof=None) -> tuple:
    """(blocages, avertissements) d'un dossier sélectionné avant son lancement."""
    b, w = [], []
    link = f"#/candidature/{a['id']}"
    s = a["statut"]
    if a.get("expired_at"):
        b.append({"label": "Offre expirée : elle n'est plus publiée", "action": "ignore", "cta": "Retirer"})
    if s == "preparation":
        return [{"label": "Préparation technique en cours (lettre, lecture du formulaire)", "wait": True}], w
    if s in ("decouverte", "qualifiee"):
        b.append({"label": "Préparation technique pas encore faite", "action": "prepare", "cta": "Préparer"})
    if s == "echec":
        b.append({"label": "Le dernier essai a échoué : " + (a["detail"] or "raison inconnue"), "action": "retry", "cta": "Reprendre"})
    missing_prof = missing_profile(c, uid) if missing_prof is None else missing_prof
    if missing_prof:
        b.append({"label": "Profil incomplet : " + ", ".join(missing_prof), "href": "#/profil", "cta": "Compléter"})
    has_cv = (a["cv_document_id"] and c.execute("SELECT 1 FROM documents WHERE id=? AND user_id=?",
                                                (a["cv_document_id"], uid)).fetchone()) or default_doc(c, uid, "cv")
    if not has_cv:
        b.append({"label": "Aucun CV", "href": "#/documents", "cta": "Importer"})
    if s == "a_valider" and a["letter_needed"] != 0 and not files.exists(c, a["letter_path"]):
        b.append({"label": "Lettre de motivation manquante", "href": link, "cta": "Corriger"})
    open_q = [q for q in db.jload(a["questions"], []) if not q.get("answer", "").strip()]
    if open_q:
        msg = f"{len(open_q)} question{'s' if len(open_q) > 1 else ''} du recruteur sans réponse"
        if a["mode"] == "auto":
            b.append({"label": msg, "href": link, "cta": "Compléter"})
        else:
            w.append(msg + " : réponds-y dans CandiPilot ou directement dans le formulaire.")
    dup = c.execute("""SELECT 1 FROM applications x JOIN offers o ON o.id=x.offer_id WHERE x.user_id=? AND o.url=?
                       AND x.statut IN ('soumission','confirmee') AND x.id<>?""", (uid, a["url"], a["id"])).fetchone()
    if dup:
        b.append({"label": "Déjà envoyée pour cette offre", "href": "#/candidatures", "cta": "Voir"})
    f = flow(pr, a["mode"])
    if f["prepare"] and not a["customized"]:
        b.append({"label": "À vérifier ou personnaliser (tu l'as demandé)", "href": link, "cta": "Personnaliser", "optional": "prepare"})
    if f["review"] and not a["reviewed"]:
        b.append({"label": "À relire avant l'envoi (tu l'as demandé)", "href": link, "cta": "Relire", "optional": "review"})
    if a["apply_type"] != "jobteaser":
        w.append("Offre sur le site de l'entreprise : tu postuleras toi-même (semi-assisté).")
    return b, w


APP_SQL = """SELECT a.*, o.url, o.poste, o.entreprise, o.localisation, o.contrat, o.start_date, o.apply_type, o.score,
             o.expired_at, o.match_reason FROM applications a JOIN offers o ON o.id=a.offer_id"""


def pending(c, uid: str) -> list:
    """Offres sélectionnées pas encore lancées."""
    return db.rows(c.execute(APP_SQL + """ WHERE a.user_id=? AND a.selected=1 AND a.launched=0
                                          AND a.statut IN ('decouverte','qualifiee','preparation','a_valider','echec')
                                          ORDER BY o.score DESC""", (uid,)))


def outcome(a: dict) -> tuple:
    """Catégorie d'une candidature lancée, raison et actions proposées."""
    s, link = a["statut"], f"#/candidature/{a['id']}"
    if s == "confirmee":
        return "sent", a["preuve"] or "Confirmée", []
    if s == "incertaine":
        return "verify", a["detail"] or "Envoi non confirmé", [{"action": "verify", "cta": "Vérifier"}, {"action": "mark-sent", "cta": "Je l'ai envoyée"}]
    if s == "soumission":
        why = "Le robot a ouvert le formulaire : clique « Postuler maintenant » dans sa fenêtre." if a["mode"] == "assisted" \
            else "Le robot envoie la candidature et attend la confirmation."
        return "progress", why, []
    if s == "a_valider" and a["mode"] == "manual":
        return "progress", "À toi de postuler sur le site, puis de le déclarer.", [
            {"href": a["url"], "cta": "Ouvrir l'offre", "external": True}, {"href": link, "cta": "Mon dossier"},
            {"action": "mark-sent", "cta": "Je l'ai envoyée"}]
    detail = a["detail"] or "Raison inconnue"
    low = detail.lower()
    acts = []
    if "connexion" in low or "connect" in low or "login" in low:
        acts.append({"href": a["url"], "cta": "Se connecter", "external": True})
    if "question" in low or "champ" in low:
        acts.append({"href": link, "cta": "Compléter"})
    acts += [{"action": "retry", "cta": "Reprendre"}, {"href": link, "cta": "Corriger"},
             {"href": a["url"], "cta": "Ouvrir l'offre", "external": True}]
    return "failed", detail, acts


def summary(a: dict) -> dict:
    return {k: a[k] for k in ("id", "poste", "entreprise", "localisation", "score", "statut", "mode", "url",
                              "apply_type", "letter_kind", "customized", "reviewed", "launched", "selected")}


def launch_state(c, uid: str) -> dict:
    """Récapitulatif avant lancement (étape 5) et résultats des lancements."""
    pr = prefs(c, uid)
    mprof = missing_profile(c, uid)
    items = []
    for a in pending(c, uid):
        b, w = blockers(c, a, uid, pr, mprof)
        items.append({**summary(a), "blockers": b, "warnings": w, "ready": not b,
                      "has_letter": bool(a["letter_path"]), "letter_needed": a["letter_needed"]})
    results = {"sent": [], "failed": [], "progress": [], "verify": []}
    for a in db.rows(c.execute(APP_SQL + " WHERE a.user_id=? AND a.launched=1 ORDER BY a.updated_at DESC", (uid,))):
        if a["statut"] == "ignoree":
            continue
        cat, why, acts = outcome(a)
        results[cat].append({**summary(a), "reason": why, "actions": acts})
    cv, letter = default_doc(c, uid, "cv"), default_doc(c, uid, "lettre")
    busy = c.execute("""SELECT COUNT(*) n FROM jobs WHERE user_id=? AND type IN ('prepare','submit')
                        AND statut IN ('en_attente','en_cours')""", (uid,)).fetchone()["n"]
    return {
        "mode": pr["default_mode"], "mode_label": MODES[pr["default_mode"]][0], "mode_chosen": bool(pr["mode_chosen"]),
        "flow": flow(pr, pr["default_mode"]), "want_prepare": pr["want_prepare"], "want_review": pr["want_review"],
        "letter_source": pr["letter_source"],
        "cv": {"id": cv["id"], "name": cv["name"], "version": cv["version"]} if cv else None,
        "letter": {"id": letter["id"], "name": letter["name"], "version": letter["version"]} if letter else None,
        "missing_profile": mprof, "missing_documents": missing_documents(c, uid, pr),
        "missing_jobteaser": jobteaser_missing(pr),
        "items": items, "ready_count": sum(1 for i in items if i["ready"]), "results": results, "busy": busy,
    }


def guide(c, uid: str) -> dict:
    """Les cinq étapes, avec les éléments manquants exacts et les sous-étapes facultatives."""
    pr = prefs(c, uid)
    mprof = missing_profile(c, uid)
    mdocs = missing_documents(c, uid, pr)
    n_selected = c.execute("""SELECT COUNT(*) n FROM applications WHERE user_id=? AND selected=1
                              AND statut<>'ignoree'""", (uid,)).fetchone()["n"]
    n_offers = c.execute("SELECT COUNT(*) n FROM offers WHERE user_id=?", (uid,)).fetchone()["n"]
    # Ce qui tourne en ce moment, en clair (plutôt qu'un bouton qui tourne sans rien dire).
    running = db.rows(c.execute("""SELECT type, statut, progress, created_at FROM jobs WHERE user_id=?
                                   AND type IN ('search','discover','rank') AND statut IN ('en_attente','en_cours')
                                   ORDER BY created_at""", (uid,)))
    searching = any(r["type"] in ("search", "discover") for r in running)
    disc = next((r for r in running if r["type"] == "discover"), None)
    arrived = c.execute("""SELECT COUNT(*) n FROM offers WHERE user_id=? AND source<>'jobteaser' AND created_at>=?""",
                        (uid, disc["created_at"])).fetchone()["n"] if disc else 0
    search_status = [{"type": r["type"], "statut": r["statut"], "progress": r["progress"] or "", "since": r["created_at"],
                      "arrived": arrived if r["type"] == "discover" else None} for r in running]
    mode = pr["default_mode"]
    optional = mode in ("assisted", "manual")
    st = launch_state(c, uid)
    items, res = st["items"], st["results"]
    launched_any = any(res[k] for k in res)

    crit_missing = []
    if jobteaser_missing(pr):
        crit_missing.append(jobteaser_missing(pr))
    if not pr["metiers"]:
        crit_missing.append("au moins un métier")
    if not n_selected:
        crit_missing.append("au moins une offre sélectionnée" if n_offers else "une recherche d'offres puis une sélection")
    mode_missing = []
    if not pr["mode_chosen"]:
        mode_missing.append("ton mode de soumission")
    elif optional:
        if pr["want_prepare"] == -1:
            mode_missing.append("ta réponse sur la préparation")
        if pr["want_review"] == -1:
            mode_missing.append("ta réponse sur la relecture")

    def missing_text(xs):
        return "Il manque : " + ", ".join(xs) + "." if xs else ""

    subs = []
    f = flow(pr, mode) if pr["mode_chosen"] else {"prepare": False, "review": False}
    def pending_for(kind):   # Un dossier encore en préparation technique n'est ni vérifié ni relu.
        return [i for i in items if any(b.get("optional") == kind or b.get("wait") for b in i["blockers"])]
    if f["prepare"] and items:
        need = pending_for("prepare")
        subs.append({"label": "Vérifie ou personnalise tes dossiers", "done": bool(items) and not need,
                     "detail": f"{len(items) - len(need)} / {len(items)} vérifié(s)"})
    if f["review"] and items:
        need = pending_for("review")
        subs.append({"label": "Relis chaque candidature", "done": bool(items) and not need,
                     "detail": f"{len(items) - len(need)} / {len(items)} relue(s)"})
    subs.append({"label": "Valide et démarre le robot", "done": launched_any and not items,
                 "detail": f"{st['ready_count']} dossier(s) prêt(s)" if items else ""})

    waiting_prep = any(any(b.get("wait") for b in i["blockers"]) for i in items)
    steps = [
        {"key": "profile", "label": "Complète ton profil", "href": "#/profil", "done": not mprof,
         "missing": mprof, "help": missing_text(mprof) or "Profil complet."},
        {"key": "documents", "label": "Importe ton CV et ta lettre", "href": "#/documents", "done": not mdocs,
         "missing": mdocs, "help": missing_text(mdocs) or "Documents prêts."},
        {"key": "criteria", "label": "Choisis tes critères", "href": "#/criteres", "done": not crit_missing,
         "missing": crit_missing,      # une recherche en cours ne bloque rien : les offres arrivent au fil de l'eau
         "help": "Recherche en cours : coche déjà les offres arrivées, les autres s'ajoutent au fil de l'eau." if searching
         else (missing_text(crit_missing) + " Renseigne tes critères, lance « Rechercher des offres » puis coche celles qui t'intéressent.").strip()},
        {"key": "mode", "label": "Choisis le mode de soumission", "href": "#/mode", "done": not mode_missing,
         "missing": mode_missing, "help": missing_text(mode_missing) or f"Mode : {MODES[mode][0]}."},
        {"key": "send", "label": "Valide l'envoi", "href": "#/envoi", "done": launched_any and not items,
         "waiting": waiting_prep or bool(st["busy"]), "subs": subs,
         "help": "Vérifie le récapitulatif puis clique « Valider et démarrer le robot »." if items
         else ("Lancement fait : consulte le résultat." if launched_any else "Sélectionne d'abord des offres (étape 3).")},
    ]
    current = next((i for i, s in enumerate(steps) if not s["done"]), None)
    nxt = None
    if current is None:
        if res["failed"] or res["verify"]:
            nxt = {"label": "Des candidatures demandent ton attention", "href": "#/envoi",
                   "help": "Certaines n'ont pas pu être envoyées ou ne sont pas confirmées : ouvre le résultat."}
        elif res["progress"]:
            nxt = {"label": "Envoi en cours", "href": "#/envoi", "help": "Suis la progression du robot.", "waiting": True}
        else:
            nxt = {"label": "Sélectionne de nouvelles offres", "href": "#/criteres",
                   "help": "Ton copilote continue de chercher : coche les offres qui t'intéressent pour un nouveau lancement."}
    return {"steps": steps, "current": current, "next": nxt, "searching": searching, "search_status": search_status}
