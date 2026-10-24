"""Base SQLite de CandiPilot (modèle du dossier de conception, §3.2).

Une connexion par appel : SQLite gère les accès concurrents du serveur web et
du worker du robot (mode WAL).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# CANDIPILOT_DATA permet d'isoler une base de test ou de démonstration.
DATA_DIR = Path(os.environ.get("CANDIPILOT_DATA") or ROOT / "data")
DB_PATH = DATA_DIR / "candipilot.db"

# Cycle de vie d'une candidature (§3.4).
STATUSES = ["decouverte", "qualifiee", "a_valider", "preparation", "soumission",
            "confirmee", "echec", "incertaine", "ignoree"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  statut TEXT NOT NULL DEFAULT 'actif',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  first_name TEXT DEFAULT '', last_name TEXT DEFAULT '', phone TEXT DEFAULT '',
  linkedin TEXT DEFAULT '', location TEXT DEFAULT '', school TEXT DEFAULT '',
  program TEXT DEFAULT '', competences TEXT DEFAULT '',
  gender TEXT DEFAULT '', availability TEXT DEFAULT '', duration TEXT DEFAULT '',
  languages TEXT DEFAULT '', internship_agreement TEXT DEFAULT '',
  letter_template TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,              -- cv | lettre
  name TEXT NOT NULL,
  chemin TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  metiers TEXT DEFAULT '[]',       -- mots-clés de recherche
  zones TEXT DEFAULT '[]',
  contrats TEXT DEFAULT '["stage"]',
  start_from TEXT DEFAULT '',
  max_pages INTEGER DEFAULT 3,
  include_external INTEGER DEFAULT 0,
  min_score INTEGER DEFAULT 50,
  default_mode TEXT DEFAULT 'auto',
  paused INTEGER DEFAULT 0,
  search_every_hours INTEGER DEFAULT 24,
  last_search_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  poste TEXT NOT NULL,
  entreprise TEXT NOT NULL,
  localisation TEXT DEFAULT '',
  contrat TEXT DEFAULT '',
  domaine TEXT DEFAULT '',
  start_date TEXT DEFAULT '',
  apply_type TEXT DEFAULT 'jobteaser',
  source TEXT DEFAULT 'jobteaser',
  score INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, url)
);
CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  offer_id TEXT NOT NULL UNIQUE REFERENCES offers(id) ON DELETE CASCADE,
  statut TEXT NOT NULL DEFAULT 'decouverte',
  mode TEXT DEFAULT 'auto',        -- auto | assisted | manual
  cv_document_id TEXT DEFAULT '',
  letter_needed INTEGER DEFAULT -1, -- -1 inconnu, 0 non, 1 oui
  letter_text TEXT DEFAULT '',
  letter_path TEXT DEFAULT '',
  questions TEXT DEFAULT '[]',     -- [{"label":..., "answer":...}]
  detail TEXT DEFAULT '',
  preuve TEXT DEFAULT '',
  date_envoi TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,              -- info | success | warning | error
  message TEXT NOT NULL,
  application_id TEXT DEFAULT '',
  read INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,              -- search | prepare | submit
  application_id TEXT DEFAULT '',
  statut TEXT NOT NULL DEFAULT 'en_attente',  -- en_attente | en_cours | termine | erreur
  progress TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS password_resets (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_app_user ON applications(user_id, statut);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_statut ON jobs(statut, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Colonnes ajoutées après la première version : ajoutées aux bases existantes au démarrage.
MIGRATIONS = {
    "preferences": {
        "mode_chosen": "INTEGER DEFAULT 0",       # le candidat a choisi son mode (étape 4)
        "want_prepare": "INTEGER DEFAULT -1",     # personnaliser les dossiers : -1 non répondu, 0 non, 1 oui
        "want_review": "INTEGER DEFAULT -1",      # relire chaque dossier avant l'envoi
        "letter_source": "TEXT DEFAULT ''",       # document (lettre PDF importée) | template (modèle adapté)
    },
    "applications": {
        "selected": "INTEGER DEFAULT 0",          # offre choisie par le candidat (étape 3)
        "letter_kind": "TEXT DEFAULT ''",         # document | generated
        "customized": "INTEGER DEFAULT 0",        # dossier vérifié/personnalisé par le candidat
        "reviewed": "INTEGER DEFAULT 0",          # relu et autorisé à l'envoi
        "launched": "INTEGER DEFAULT 0",          # inclus dans un lancement « Valider et démarrer »
    },
}


def init() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        # Un worker arrêté en pleine tâche : on la remet en file au démarrage.
        c.execute("UPDATE jobs SET statut='en_attente' WHERE statut='en_cours'")


def row(r) -> dict:
    return dict(r) if r is not None else None


def rows(rs) -> list:
    return [dict(r) for r in rs]


def jload(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def add_event(conn, user_id: str, kind: str, message: str, application_id: str = "") -> None:
    conn.execute("INSERT INTO events(user_id, kind, message, application_id, created_at) VALUES (?,?,?,?,?)",
                 (user_id, kind, message, application_id, now()))
