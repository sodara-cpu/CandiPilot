"""Entreprises suivies par défaut : la recherche couvre plusieurs sites dès la première fois, sans rien configurer.

    .venv/bin/python tests/test_defaults.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("DATABASE_URL", None)
os.environ["CANDIPILOT_DATA"] = tempfile.mkdtemp()
os.environ["CANDIPILOT_DEFAULT_SOURCES"] = "1"

from app import db, migrations  # noqa: E402

passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        raise SystemExit(1)
    passed += 1
    print("✓", label)


db.init()
with db.tx() as c:
    c.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES ('u1','u1@example.com','x',?)", (db.now(),))
    c.execute("INSERT INTO preferences(user_id) VALUES ('u1')")
    n = migrations.ensure_default_sources(c, "u1")
    names = {r["company"] for r in c.execute("SELECT company FROM job_sources WHERE user_id='u1'")}
    check(n == len(migrations.DEFAULT_SOURCES) and {"Doctolib", "Ubisoft", "Qonto"} <= names,
          f"{n} entreprises suivies dès la première recherche, sans rien configurer")
    check(migrations.ensure_default_sources(c, "u1") == 0, "ajoutées une seule fois")
    c.execute("DELETE FROM job_sources WHERE user_id='u1' AND company='Ubisoft'")
    migrations.ensure_default_sources(c, "u1")
    check(not c.execute("SELECT 1 FROM job_sources WHERE user_id='u1' AND company='Ubisoft'").fetchone(),
          "une entreprise retirée par l'utilisateur ne revient pas toute seule")
    platforms = {p for p, _, _ in migrations.DEFAULT_SOURCES}
    check(platforms <= {"greenhouse", "lever", "ashby", "smartrecruiters", "workable"}, "uniquement des API publiques prévues pour être lues")
    os.environ["CANDIPILOT_DEFAULT_SOURCES"] = "0"
    c.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES ('u2','u2@example.com','x',?)", (db.now(),))
    c.execute("INSERT INTO preferences(user_id) VALUES ('u2')")
    check(migrations.ensure_default_sources(c, "u2") == 0, "désactivable (tests sans Internet)")

print(f"\n{passed} vérifications réussies.")
