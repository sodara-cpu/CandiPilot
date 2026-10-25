"""Migrations versionnées de la base (lot 1 « fondations »).

Chaque migration a un numéro, est appliquée une seule fois, dans l'ordre, et notée dans
schema_migrations. Le SQL est écrit pour SQLite et pour PostgreSQL quand ils diffèrent.
Pour ajouter une migration : l'ajouter à la fin de MIGRATIONS (ne jamais modifier une migration déjà livrée).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import db

# (version, description, SQL SQLite, SQL PostgreSQL — None = identique)
MIGRATIONS = [
    (1, "Journal d'audit des candidatures", """
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  application_id TEXT DEFAULT '',
  actor TEXT DEFAULT 'system',
  action TEXT NOT NULL,
  from_status TEXT DEFAULT '',
  to_status TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_app ON audit_log(application_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, id);
CREATE TRIGGER IF NOT EXISTS trg_app_status AFTER UPDATE OF statut ON applications
WHEN OLD.statut <> NEW.statut BEGIN
  INSERT INTO audit_log(user_id, application_id, action, from_status, to_status, detail, created_at)
  VALUES (NEW.user_id, NEW.id, 'statut', OLD.statut, NEW.statut, COALESCE(NEW.detail, ''), NEW.updated_at);
END""", """
CREATE TABLE IF NOT EXISTS audit_log (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  application_id TEXT DEFAULT '',
  actor TEXT DEFAULT 'system',
  action TEXT NOT NULL,
  from_status TEXT DEFAULT '',
  to_status TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_app ON audit_log(application_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, id);
CREATE OR REPLACE FUNCTION cp_audit_status() RETURNS trigger AS $$
BEGIN
  IF OLD.statut IS DISTINCT FROM NEW.statut THEN
    INSERT INTO audit_log(user_id, application_id, action, from_status, to_status, detail, created_at)
    VALUES (NEW.user_id, NEW.id, 'statut', OLD.statut, NEW.statut, COALESCE(NEW.detail, ''), NEW.updated_at);
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_app_status ON applications;
CREATE TRIGGER trg_app_status AFTER UPDATE OF statut ON applications FOR EACH ROW EXECUTE FUNCTION cp_audit_status()"""),

    (2, "Registre des sites (automatisation permise par plateforme)", """
CREATE TABLE IF NOT EXISTS site_policies (
  domain TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  mode_max TEXT NOT NULL DEFAULT 'manual',
  official_api TEXT DEFAULT '',
  terms_checked_at TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  updated_at TEXT NOT NULL
)""", None),

    (3, "Consentement explicite à l'envoi automatique, limite quotidienne, clé d'idempotence", """
ALTER TABLE preferences ADD COLUMN auto_submit_consent_at TEXT DEFAULT '';
ALTER TABLE preferences ADD COLUMN daily_limit INTEGER DEFAULT 20;
ALTER TABLE applications ADD COLUMN idempotency_key TEXT DEFAULT ''""", None),

    (4, "Sources d'offres multi-plateformes, doublons, expiration", """
CREATE TABLE IF NOT EXISTS job_sources (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  token TEXT NOT NULL,
  company TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  last_run_at TEXT DEFAULT '',
  last_status TEXT DEFAULT '',
  last_count INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, platform, token)
);
ALTER TABLE offers ADD COLUMN source_ref TEXT DEFAULT '';
ALTER TABLE offers ADD COLUMN fingerprint TEXT DEFAULT '';
ALTER TABLE offers ADD COLUMN description TEXT DEFAULT '';
ALTER TABLE offers ADD COLUMN remote INTEGER DEFAULT 0;
ALTER TABLE offers ADD COLUMN last_seen_at TEXT DEFAULT '';
ALTER TABLE offers ADD COLUMN expired_at TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_offers_fp ON offers(user_id, fingerprint)""", None),
    (5, "Matching expliqué par Claude, consommation de l'API", """
ALTER TABLE offers ADD COLUMN rule_score INTEGER DEFAULT -1;
ALTER TABLE offers ADD COLUMN match_reason TEXT DEFAULT '';
ALTER TABLE offers ADD COLUMN matched_at TEXT DEFAULT '';
CREATE TABLE IF NOT EXISTS llm_usage (
  user_id TEXT NOT NULL,
  day TEXT NOT NULL,
  calls INTEGER DEFAULT 0,
  offers INTEGER DEFAULT 0,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0,
  PRIMARY KEY (user_id, day)
)""", None),
    (6, "Entreprises suivies par défaut (ajoutées une seule fois par compte)", """
ALTER TABLE preferences ADD COLUMN default_sources_at TEXT DEFAULT ''""", None),
]

# Entreprises suivies par défaut pour chaque compte : catalogue construit par scripts/find_sources.py (API publiques
# vérifiées, offres en France). Les entreprises qu'on ne peut pas lire sont proposées en candidature assistée.
CATALOG = json.loads((Path(__file__).parent / "sources_catalog.json").read_text(encoding="utf-8"))
DEFAULT_SOURCES = [(x["platform"], x["token"], x["company"]) for x in CATALOG["sources"]]
SUGGESTED_SOURCES = DEFAULT_SOURCES      # proposées de nouveau si l'utilisateur en a retiré
ASSISTED_COMPANIES = CATALOG["assisted"]

# Niveau d'automatisation par défaut (modifiable par le propriétaire : /api/admin/sites).
# auto = le robot peut envoyer ; assisted = il pré-remplit, l'utilisateur envoie ; manual = remplissage seulement.
DEFAULT_POLICIES = [
    ("jobteaser.com", "JobTeaser", "auto", "",
     "Conditions d'utilisation non encore vérifiées pour l'automatisation : à confirmer (risque R1 de l'audit)."),
    ("welcometothejungle.com", "Welcome to the Jungle", "manual", "", "Conditions à vérifier ; remplissage assisté seulement."),
    ("linkedin.com", "LinkedIn", "manual", "", "Les conditions de LinkedIn interdisent l'automatisation : jamais d'envoi automatique."),
    ("greenhouse.io", "Greenhouse", "assisted", "Job Board API (lecture des offres)", "API publique pour lister les offres."),
    ("lever.co", "Lever", "assisted", "Postings API (lecture des offres)", "API publique pour lister les offres."),
    ("ashbyhq.com", "Ashby", "assisted", "Job Postings API (lecture des offres)", "API publique pour lister les offres."),
    ("smartrecruiters.com", "SmartRecruiters", "assisted", "Posting API (lecture des offres)", "API publique pour lister les offres."),
    ("workable.com", "Workable", "assisted", "Widget API (lecture des offres)", "API publique pour lister les offres."),
    ("myworkdayjobs.com", "Workday", "manual", "", "Compte requis par entreprise ; remplissage assisté."),
    ("taleo.net", "Taleo", "manual", "", "Compte requis ; remplissage assisté."),
]


def apply(conn) -> list:
    """Applique les migrations manquantes ; renvoie les versions appliquées."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT NOT NULL)")
    done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, desc, sql_lite, sql_pg in MIGRATIONS:
        if version in done:
            continue
        script = (sql_pg or sql_lite) if db.PG else sql_lite
        if db.PG:
            conn._c.execute(script)          # plusieurs instructions d'un coup (fonctions $$ … $$), sans traduction
        else:
            conn.executescript(script)
        conn.execute("INSERT INTO schema_migrations(version, description, applied_at) VALUES (?,?,?)", (version, desc, db.now()))
        applied.append(version)
    for domain, platform, mode, api, notes in DEFAULT_POLICIES:
        conn.execute("""INSERT OR IGNORE INTO site_policies(domain, platform, mode_max, official_api, notes, updated_at)
                        VALUES (?,?,?,?,?,?)""", (domain, platform, mode, api, notes, db.now()))
    return applied


def ensure_default_sources(conn, user_id: str) -> int:
    """Ajoute les entreprises suivies par défaut à un compte, à chaque nouvelle version du catalogue. Une entreprise
    retirée par l'utilisateur reste désactivée (sa ligne est gardée) : jamais réactivée toute seule.
    Désactivable (tests sans Internet) : CANDIPILOT_DEFAULT_SOURCES=0."""
    if os.environ.get("CANDIPILOT_DEFAULT_SOURCES", "1") == "0":
        return 0
    p = conn.execute("SELECT default_sources_at FROM preferences WHERE user_id=?", (user_id,)).fetchone()
    version = CATALOG["verified_at"]
    if not p or p["default_sources_at"] == version:
        return 0
    n = 0
    for platform, token, company in DEFAULT_SOURCES:
        n += conn.execute("""INSERT OR IGNORE INTO job_sources(id,user_id,platform,token,company,last_status,last_count,created_at)
                             VALUES (?,?,?,?,?,?,?,?)""", (db.new_id(), user_id, platform, token, company, "suivie par défaut", 0, db.now())).rowcount
    conn.execute("UPDATE preferences SET default_sources_at=? WHERE user_id=?", (version, user_id))
    return n


def policy_for(conn, url: str):
    """Règle du site d'une adresse (le domaine le plus précis l'emporte)."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    best = None
    for r in conn.execute("SELECT * FROM site_policies").fetchall():
        d = r["domain"]
        if (host == d or host.endswith("." + d)) and (best is None or len(d) > len(best["domain"])):
            best = r
    return db.row(best)
