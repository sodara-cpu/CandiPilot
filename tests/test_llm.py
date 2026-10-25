"""Matching intelligent (lot 3) : requête envoyée à Claude, refus, plafond quotidien, sans aucun appel réseau.

    .venv/bin/python tests/test_llm.py

Claude est remplacé par un faux client qui enregistre la requête et renvoie une réponse préparée.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("DATABASE_URL", None)
os.environ.pop("CANDIPILOT_FAKE_LLM", None)
os.environ["CANDIPILOT_DATA"] = tempfile.mkdtemp()
os.environ["CANDIPILOT_LLM_DAILY_OFFERS"] = "30"

from app import db, llm  # noqa: E402

passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        raise SystemExit(1)
    passed += 1
    print("✓", label)


class FakeClient:
    """Imite client.beta.messages.create : garde la requête, renvoie la réponse prévue."""
    def __init__(self, stop_reason="end_turn", payload=None):
        self.calls, self.stop_reason, self.payload = [], stop_reason, payload
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        content = [] if self.stop_reason == "refusal" else [SimpleNamespace(type="text", text=json.dumps(self.payload))]
        usage = SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=800)
        return SimpleNamespace(stop_reason=self.stop_reason, content=content, usage=usage)


ctx = {"profile": {"school": "SKEMA", "program": "Master finance", "competences": "Excel, modélisation", "languages": "anglais"},
       "prefs": {"metiers": ["contrôle de gestion"], "contrats": ["stage"], "zones": ["Paris"], "start_from": "2027-01"}}
offers = [{"id": "o1", "poste": "Stage Contrôle de gestion", "entreprise": "Acme", "localisation": "Paris", "contrat": "stage",
           "description": "<b>Missions</b> : reporting mensuel " * 200, "remote": 0},
          {"id": "o2", "poste": "Backend Engineer", "entreprise": "Acme", "localisation": "Lyon", "contrat": "cdi", "description": ""}]

# 1. Sans clé : aucune IA, la note des critères stricts reste
os.environ.pop("ANTHROPIC_API_KEY", None)
check(not llm.enabled(), "sans clé ANTHROPIC_API_KEY : matching IA désactivé (note des critères stricts)")

# 2. Requête envoyée à Claude
fake = FakeClient(payload={"offers": [{"id": "o1", "score": 92, "reason": "Stage en contrôle de gestion à Paris."},
                                      {"id": "o2", "score": 10, "reason": "CDI d'ingénieur, loin de ta recherche."}]})
llm._client = fake
res, usage = llm.rate(offers, ctx)
kw = fake.calls[0]
check(kw["model"] == "claude-opus-5-5", "modèle Claude Opus 5.5 par défaut")
check(kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"], "modèle de secours en cas de refus (fallbacks)")
check(kw["output_config"]["format"]["type"] == "json_schema" and kw["output_config"]["effort"] == "low", "réponse JSON validée par un schéma, effort réduit")
check(kw["system"][-1].get("cache_control") == {"type": "ephemeral"} and "SKEMA" in kw["system"][-1]["text"], "profil du candidat mis en cache")
check("SKEMA" not in kw["system"][0]["text"] and "Master finance" in kw["system"][1]["text"], "consignes et profil séparés (cache réutilisable)")
msg = kw["messages"][0]["content"]
check('<offre id="o1">' in msg and '<offre id="o2">' in msg, "chaque offre envoyée avec son identifiant")
check(len(msg) < 4000, "descriptions tronquées (coût maîtrisé)")
check([r["score"] for r in res] == [92, 10] and usage["cache_read_input_tokens"] == 800, "notes, explications et jetons du cache récupérés")

# 3. Refus et réponse incomplète : erreur explicite (le worker garde alors les notes strictes)
for stop in ("refusal", "max_tokens"):
    llm._client = FakeClient(stop_reason=stop)
    try:
        llm.rate(offers, ctx)
        check(False, f"{stop} détecté")
    except RuntimeError:
        check(True, f"« {stop} » détecté : aucune note inventée")

# 4. Plafond quotidien par utilisateur
db.init()
with db.tx() as c:
    c.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES ('u1','u1@example.com','x',?)", (db.now(),))
    check(llm.budget_left(c, "u1") == 30, "plafond quotidien : 30 offres (réglable)")
    llm.record_usage(c, "u1", 20, usage)
    llm.record_usage(c, "u1", 20, usage)
    check(llm.budget_left(c, "u1") == 0, "plafond atteint : plus d'appel à Claude aujourd'hui")
    r = db.row(c.execute("SELECT * FROM llm_usage WHERE user_id='u1'").fetchone())
    check(r["calls"] == 2 and r["input_tokens"] == 2400 and r["cache_read_tokens"] == 1600, "consommation suivie (appels, jetons)")

print(f"\n{passed} vérifications réussies.")
