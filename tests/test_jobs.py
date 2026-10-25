"""File de tâches : un redémarrage du serveur n'interrompt pas le travail de l'extension.

    .venv/bin/python tests/test_jobs.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("DATABASE_URL", None)
os.environ["CANDIPILOT_DATA"] = tempfile.mkdtemp()

from app import db, worker  # noqa: E402

passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        raise SystemExit(1)
    passed += 1
    print("✓", label)


ago = lambda m: (datetime.now(timezone.utc) - timedelta(minutes=m)).isoformat(timespec="seconds")
db.init()
with db.tx() as c:
    c.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES ('u1','u1@example.com','x',?)", (db.now(),))
    for jid, kind, claimed, updated in (("ext", "search", ago(30), ago(1)), ("srv", "discover", "", ago(1)), ("dead", "search", ago(40), ago(25))):
        c.execute("""INSERT INTO jobs(id,user_id,type,application_id,statut,claimed_at,created_at,updated_at)
                     VALUES (?,?,?,'','en_cours',?,?,?)""", (jid, "u1", kind, claimed, ago(60), updated))
db.init()          # redémarrage du serveur (mise en ligne)
with db.tx() as c:
    s = {r["id"]: r["statut"] for r in c.execute("SELECT id, statut FROM jobs")}
check(s["ext"] == "en_cours", "redémarrage : la tâche que l'extension exécute continue (son résultat sera accepté)")
check(s["srv"] == "en_attente", "redémarrage : la tâche du serveur interrompue est remise en file")
with db.tx() as c:
    worker.release_stale(c)
    s = {r["id"]: r["statut"] for r in c.execute("SELECT id, statut FROM jobs")}
check(s["ext"] == "en_cours", "tâche longue qui progresse encore (prise il y a 30 min) : pas abandonnée")
check(s["dead"] == "en_attente", "extension sans nouvelle depuis 25 min : tâche remise en file")

print(f"\n{passed} vérifications réussies.")
