"""Base de données de CandiPilot (modèle du dossier de conception, §3.2).

- SQLite par défaut (poste local, tests) : data/candipilot.db, mode WAL.
- PostgreSQL si DATABASE_URL est défini (hébergement Render).

Le code écrit ses requêtes une seule fois, en syntaxe SQLite ; pour PostgreSQL elles sont
traduites à la volée (paramètres ?, INSERT OR IGNORE, LIKE insensible à la casse…).
Le contenu des fichiers (CV, lettres) est stocké dans la table files : le disque d'un
hébergeur peut être effacé à chaque déploiement.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# CANDIPILOT_DATA permet d'isoler une base de test ou de démonstration.
DATA_DIR = Path(os.environ.get("CANDIPILOT_DATA") or ROOT / "data")
DB_PATH = DATA_DIR / "candipilot.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# Cycle de vie d'une candidature (§3.4).
STATUSES = ["decouverte", "qualifiee", "a_valider", "preparation", "soumission",
            "confirmee", "echec", "incertaine", "ignoree",
            "attente_utilisateur"]    # le robot s'est arrêté : connexion expirée, CAPTCHA… (jamais contourné)

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
  id INTEGER PRIMARY KEY AUTOINCREMENT,  -- SERIAL en PostgreSQL
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
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  contenu BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_tokens (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  last_seen TEXT DEFAULT '',
  version TEXT DEFAULT ''
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


# ---------- PostgreSQL : traduction des requêtes écrites pour SQLite ----------

def _pg_sql(sql: str) -> str:
    sql = sql.replace("%", "%%").replace("?", "%s")
    if re.match(r"\s*INSERT OR IGNORE", sql, re.I):
        sql = re.sub(r"INSERT OR IGNORE", "INSERT", sql, count=1, flags=re.I).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    sql = re.sub(r"\bLIKE\b", "ILIKE", sql)
    return re.sub(r",\s*rowid\b", ", id", sql)


def _pg_schema(sql: str) -> str:
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace("BLOB", "BYTEA")
    return re.sub(r"--[^\n]*", "", sql)


_pool = None


def _pg_pool():
    """Réserve de connexions ouvertes : ouvrir une connexion sécurisée vers la base coûte près d'une seconde,
    la réutiliser quelques millisecondes."""
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        # prepare_threshold=None : compatible avec les « poolers » (Neon, PgBouncer).
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=8, max_idle=240, open=True, timeout=30,
                               check=ConnectionPool.check_connection,   # écarte une connexion fermée par la base
                               kwargs={"row_factory": dict_row, "prepare_threshold": None, "connect_timeout": 20})
    return _pool


class _PgConn:
    """Connexion PostgreSQL (prise dans la réserve) qui accepte les requêtes de l'application (syntaxe SQLite)."""

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql: str, params=()):
        return self._c.execute(_pg_sql(sql), tuple(params))

    def executescript(self, script: str) -> None:
        for stmt in _pg_schema(script).split(";"):
            if stmt.strip():
                self._c.execute(stmt)

def connect():
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx():
    if PG:
        with _pg_pool().connection() as conn:      # valide (commit) en sortie, annule en cas d'erreur
            # Le pooler partage les connexions : un réglage laissé par un autre client (ex. pg_dump vide le
            # search_path) s'y retrouve. On fixe le schéma pour cette transaction seulement.
            conn.execute("SET LOCAL search_path TO public")
            yield _PgConn(conn)
        return
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
        "jobteaser_url": "TEXT DEFAULT ''",       # portail JobTeaser de l'utilisateur (ex. https://skema.jobteaser.com)
        "jobteaser_status": "TEXT DEFAULT ''",    # '' jamais connecté | connected | expired
        "jobteaser_at": "TEXT DEFAULT ''",
        "sites_status": "TEXT DEFAULT '{}'",      # sites connectés, vus par l'extension
    },
    "applications": {
        "selected": "INTEGER DEFAULT 0",          # offre choisie par le candidat (étape 3)
        "letter_kind": "TEXT DEFAULT ''",         # document | generated
        "customized": "INTEGER DEFAULT 0",        # dossier vérifié/personnalisé par le candidat
        "reviewed": "INTEGER DEFAULT 0",          # relu et autorisé à l'envoi
        "launched": "INTEGER DEFAULT 0",          # inclus dans un lancement « Valider et démarrer »
    },
    "jobs": {
        "claimed_at": "TEXT DEFAULT ''",          # prise en charge par l'extension du navigateur
    },
}


def _columns(c, table: str) -> set:
    if PG:
        return {r["column_name"] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?", (table,))}
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def init() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = _columns(c, table)
            for col, decl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        from . import migrations
        migrations.apply(c)                       # migrations versionnées (app/migrations.py)
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
