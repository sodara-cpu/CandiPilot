"""Pont entre la base CandiPilot et le robot (src/) : profils, offres, lettres.

Les profils de la base sont convertis en `src.config.Profile` pour que le robot
fonctionne à l'identique en ligne de commande et depuis l'application.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import yaml

from src.config import Offer, Profile
from src.letter_pdf import make_pdf
from src.message import build_message

from . import db

YAML_PATH = db.ROOT / "config" / "profile.yaml"
LETTERS_DIR = db.DATA_DIR / "letters"

_yaml_cache: dict = {}


def yaml_profile() -> dict:
    if not _yaml_cache and YAML_PATH.exists():
        _yaml_cache.update(yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {})
    return _yaml_cache


def default_domain_lines() -> dict:
    return dict(yaml_profile().get("domain_lines") or {"default": "l'analyse financière et à l'investissement"})


def default_letter_template() -> str:
    body = (yaml_profile().get("cover_letter") or {}).get("body", "")
    return body.strip() or (
        "Madame, Monsieur,\n\nJe me permets de vous adresser ma candidature au poste « {role} » "
        "au sein {de_company}.\n\nJe serais ravi d'échanger avec vous.\n\nBien à vous,\n")


def browser_settings() -> dict:
    """Réglages navigateur du robot : Chromium dédié, fenêtre visible (connexion SKEMA possible)."""
    s = dict(yaml_profile().get("settings") or {})
    s["browser"] = {"mode": "persistent"}
    s["headless"] = False
    return s


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
            "include_external": bool(prefs.get("include_external"))}


def draft_letter(ctx: dict, offer: dict) -> str:
    """Texte de lettre adapté à l'offre, à partir du modèle du profil."""
    return build_message(to_profile(ctx), to_offer(offer))["body"]


def render_letter(ctx: dict, offer: dict, text: str, app_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{offer['entreprise']}_{offer['poste']}").strip("_")[:60]
    out = LETTERS_DIR / app_id / f"Lettre_{slug}.pdf"
    make_pdf(text, out, candidate_dict(ctx))
    return str(out)


def copy_letter(doc, app_id: str) -> str:
    """Copie la lettre importée dans le dossier de la candidature, sous son nom d'origine (vu par le recruteur)."""
    name = re.sub(r"[^\w.\- ]+", "_", Path(doc["name"]).name) or "Lettre.pdf"
    out = LETTERS_DIR / app_id / (name if name.lower().endswith(".pdf") else name + ".pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(doc["chemin"], out)
    return str(out)


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
                  default_letter_template(), user_id))
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
        exists = conn.execute("SELECT 1 FROM documents WHERE user_id=? AND chemin=?", (user_id, cv)).fetchone()
        if not exists:
            conn.execute("UPDATE documents SET is_default=0 WHERE user_id=? AND type='cv'", (user_id,))
            conn.execute("""INSERT INTO documents(id,user_id,type,name,chemin,version,is_default,created_at)
                            VALUES (?,?,?,?,?,1,1,?)""",
                         (db.new_id(), user_id, "cv", Path(cv).name, str(Path(cv).expanduser()), db.now()))
        done.append("CV")
    return done
