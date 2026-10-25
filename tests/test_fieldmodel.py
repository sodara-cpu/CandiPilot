"""Réseau de neurones des champs (apprentissage/), exécuté par l'application : chargé, identique à l'export, utilisé.

    .venv/bin/python tests/test_fieldmodel.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import fieldmodel  # noqa: E402

passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        raise SystemExit(1)
    passed += 1
    print("✓", label)


info = fieldmodel.active()
check(info.get("file") and info.get("same_as_pytorch") == 100.0, f"version active du registre : {info.get('version')} (identique à PyTorch)")
check(info.get("judge_accuracy", 0) >= 90, f"note sur le juge (vrais formulaires vérifiés à la main) : {info.get('judge_accuracy')} %")
f = lambda label, **kw: {"tag": "input", "type": kw.get("type", "text"), "name": kw.get("name", ""), "id": "", "placeholder": "",
                         "aria_label": "", "autocomplete": "", "label": label, "text": "", "context": "", "required": False, "options": []}
form = [f("First name", name="first_name"), f("Last name", name="last_name"), f("Email", type="email", name="email"),
        f("Why do you want to join us?", name="question_1")]
pred = fieldmodel.predict(form)
check(len(pred) == 4 and all(0 <= p <= 1 for _, p in pred), "prédiction : un rôle et une probabilité par champ")
check(pred[0][0] == "first_name" and pred[3][0] == "other", f"rôles reconnus sur un formulaire type : {[r for r, _ in pred]}")
print(f"\n{passed} vérifications réussies.")
