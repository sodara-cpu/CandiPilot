"""Pont entre la base CandiPilot et le robot (src/) : profils, offres, lettres.

Les profils de la base sont convertis en `src.config.Profile` pour que le robot
fonctionne à l'identique en ligne de commande et depuis l'application.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import yaml

from src.config import Offer, Profile
from src.letter_pdf import make_pdf
from src.message import build_message

from . import db, files

YAML_PATH = db.ROOT / "config" / "profile.yaml"

_yaml_cache: dict = {}


def yaml_profile() -> dict:
    if not _yaml_cache and YAML_PATH.exists():
        _yaml_cache.update(yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {})
    return _yaml_cache


def default_domain_lines() -> dict:
    return dict(yaml_profile().get("domain_lines") or {"default": "l'analyse financière et à l'investissement"})


def default_letter_template() -> str:
    """Modèle neutre pour un nouveau compte (la lettre personnelle de config/profile.yaml n'est jamais partagée)."""
    return ("Madame, Monsieur,\n\nJe me permets de vous adresser ma candidature au poste « {role} » "
            "au sein {de_company}. Mon parcours m'a préparé à {domain_line}, et je serais heureux de mettre "
            "ma rigueur et ma motivation au service de vos équipes.\n\n"
            "Je reste à votre disposition pour un entretien.\n\nBien à vous,\n")


# ---------- navigateur et compte JobTeaser de chaque utilisateur ----------

BROWSERS_DIR = db.DATA_DIR / "navigateurs"
OWNER_PROFILE = db.ROOT / ".user-profile"      # profil historique du robot en ligne de commande
DEFAULT_PORTAL = "https://www.jobteaser.com"
LOGIN_WORDS = re.compile(r"se connecter|connexion|log ?in|sign ?in|s'identifier", re.I)


def is_owner(conn, user_id: str) -> bool:
    first = conn.execute("SELECT id FROM users ORDER BY created_at, rowid LIMIT 1").fetchone()
    return bool(first) and first["id"] == user_id


def profile_dir(conn, user_id: str) -> Path:
    """Dossier du navigateur robot de l'utilisateur : ses cookies JobTeaser, à lui seul."""
    own = BROWSERS_DIR / user_id
    fake = os.environ.get("CANDIPILOT_FAKE_ROBOT") == "1"     # tests / recette : jamais le vrai profil
    if not fake and not own.exists() and OWNER_PROFILE.exists() and is_owner(conn, user_id):
        return OWNER_PROFILE
    return own


def portal(conn, user_id: str) -> str:
    r = conn.execute("SELECT jobteaser_url FROM preferences WHERE user_id=?", (user_id,)).fetchone()
    return ((r and r["jobteaser_url"]) or DEFAULT_PORTAL).rstrip("/")


def browser_settings(conn=None, user_id: str = "") -> dict:
    """Réglages du robot : Chromium dédié à l'utilisateur, fenêtre visible (Cloudflare bloque l'invisible)."""
    s = dict(yaml_profile().get("settings") or {}) if (conn is None or is_owner(conn, user_id)) else {}
    s["browser"] = {"mode": "persistent"}
    if conn is not None:
        s["browser"]["user_data_dir"] = str(profile_dir(conn, user_id))
    s["headless"] = False
    return s


def logged_in(page) -> bool:
    """Page des offres affichée, sans formulaire ni lien de connexion : la session JobTeaser est ouverte."""
    try:
        return ("/job-offers" in page.url
                and page.locator('[data-testid="jobad-card"]').count() > 0
                and page.locator("input[type=password]").count() == 0
                and page.get_by_role("link", name=LOGIN_WORDS).count() == 0
                and page.get_by_role("button", name=LOGIN_WORDS).count() == 0)
    except Exception:
        return False


def load_context(conn, user_id: str) -> dict:
    prof = db.row(conn.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()) or {}
    prefs = db.row(conn.execute("SELECT * FROM preferences WHERE user_id=?", (user_id,)).fetchone()) or {}
    for k, d in (("metiers", []), ("zones", []), ("contrats", [])):
        prefs[k] = db.jload(prefs.get(k), d)
    user = db.row(conn.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()) or {}
    return {"profile": prof, "prefs": prefs, "email": user.get("email", "")}


def candidate_dict(ctx: dict) -> dict:
    p = ctx["profile"]
    full = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    return {"first_name": p.get("first_name", ""), "last_name": p.get("last_name", ""),
            "full_name": full, "email": ctx["email"], "phone": p.get("phone", ""),
            "linkedin": p.get("linkedin", ""), "location": p.get("location", "") or "Paris",
            "school": p.get("school", ""), "program": p.get("program", "")}


def to_profile(ctx: dict, cv_path: str = "", screening: dict = None, letter_body: str = "") -> Profile:
    p = ctx["profile"]
    return Profile({
        "candidate": candidate_dict(ctx),
        "documents": {"cv": cv_path},
        "answers": {"gender": p.get("gender", ""), "screening": screening or {}},
        "cover_letter": {"body": letter_body or p.get("letter_template") or default_letter_template()},
        "domain_lines": default_domain_lines(),
        "settings": browser_settings(),
    })


def to_offer(o: dict) -> Offer:
    return Offer(url=o["url"], company=o["entreprise"], role=o["poste"], domain=o.get("domaine", ""),
                 start_date=o.get("start_date", ""), apply_type=o.get("apply_type", ""))


def search_cfg(prefs: dict) -> dict:
    return {"keywords": prefs.get("metiers") or [], "contract_contains": prefs.get("contrats") or [],
            "location_contains": prefs.get("zones") or [], "start_from": prefs.get("start_from") or "",
            "max_pages": int(prefs.get("max_pages") or 3),
            "include_external": bool(prefs.get("include_external")),
            "base_url": (prefs.get("jobteaser_url") or DEFAULT_PORTAL).rstrip("/")}


def draft_letter(ctx: dict, offer: dict) -> str:
    """Texte de lettre adapté à l'offre, à partir du modèle du profil."""
    return build_message(to_profile(ctx), to_offer(offer))["body"]


def render_letter(conn, ctx: dict, offer: dict, text: str, app_id: str) -> str:
    """Lettre PDF de la candidature, enregistrée dans la base ; renvoie sa référence (file:<id>)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{offer['entreprise']}_{offer['poste']}").strip("_")[:60]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"Lettre_{slug}.pdf"
        make_pdf(text, out, candidate_dict(ctx))
        return files.save(conn, offer["user_id"], out.name, out.read_bytes())


def copy_letter(conn, doc, user_id: str) -> str:
    """Copie de la lettre importée pour la candidature, sous son nom d'origine (vu par le recruteur)."""
    got = files.read(conn, doc["chemin"])
    if not got:
        return ""
    name = re.sub(r"[^\w.\- ]+", "_", Path(doc["name"]).name) or "Lettre.pdf"
    return files.save(conn, user_id, name if name.lower().endswith(".pdf") else name + ".pdf", got[1])


def default_cv(conn, user_id: str):
    return db.row(conn.execute("""SELECT * FROM documents WHERE user_id=? AND type='cv'
                                  ORDER BY is_default DESC, created_at DESC LIMIT 1""", (user_id,)).fetchone())


def import_yaml(conn, user_id: str) -> list:
    """Importe config/profile.yaml (profil, préférences, réponses, CV) dans le compte."""
    y = yaml_profile()
    if not y:
        return []
    done = []
    cand = y.get("candidate") or {}
    ans = y.get("answers") or {}
    clean = lambda v: "" if str(v or "").startswith("<") else str(v or "")
    conn.execute("""UPDATE profiles SET first_name=?, last_name=?, phone=?, linkedin=?, location=?, school=?,
                    program=?, gender=?, availability=?, letter_template=? WHERE user_id=?""",
                 (clean(cand.get("first_name")), clean(cand.get("last_name")), clean(cand.get("phone")),
                  clean(cand.get("linkedin")), clean(cand.get("location")), clean(cand.get("school")),
                  clean(cand.get("program")), clean(ans.get("gender")), clean(ans.get("availability")),
                  ((y.get("cover_letter") or {}).get("body") or "").strip() or default_letter_template(), user_id))
    done.append("profil")
    s = y.get("search") or {}
    if s:
        conn.execute("""UPDATE preferences SET metiers=?, zones=?, contrats=?, start_from=?, max_pages=?,
                        include_external=? WHERE user_id=?""",
                     (json.dumps(s.get("keywords") or []), json.dumps(s.get("location_contains") or []),
                      json.dumps(s.get("contract_contains") or ["stage"]), str(s.get("start_from") or ""),
                      int(s.get("max_pages") or 3), int(bool(s.get("include_external"))), user_id))
        done.append("critères de recherche")
    cv = clean((y.get("documents") or {}).get("cv"))
    if cv and Path(cv).expanduser().exists():
        path = Path(cv).expanduser()
        exists = conn.execute("SELECT 1 FROM documents WHERE user_id=? AND type='cv' AND name=?", (user_id, path.name)).fetchone()
        if not exists:
            conn.execute("UPDATE documents SET is_default=0 WHERE user_id=? AND type='cv'", (user_id,))
            conn.execute("""INSERT INTO documents(id,user_id,type,name,chemin,version,is_default,created_at)
                            VALUES (?,?,?,?,?,1,1,?)""",
                         (db.new_id(), user_id, "cv", path.name, files.save(conn, user_id, path.name, path.read_bytes()), db.now()))
        done.append("CV")
    return done
