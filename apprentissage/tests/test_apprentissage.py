"""Tests hors ligne du mini-projet (aucun site visité).

    .venv/bin/python apprentissage/tests/test_apprentissage.py
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import collecte  # noqa: E402
from etiquettes import LABELS, rule_label  # noqa: E402
from evaluation import evaluate, load_pages  # noqa: E402
from extraction import describe, extract_fields  # noqa: E402
from modeles import charger  # noqa: E402

FORM_FR = """<html><body><script>var x = "<input name='piege'>";</script>
<h1>Postuler : Analyste M&amp;A</h1>
<form>
  <label for="prenom">Prénom *</label><input id="prenom" name="prenom" required>
  <label>Nom<input name="nom"></label>
  <input type="email" name="courriel" placeholder="Adresse e-mail">
  <input type="tel" aria-label="Téléphone portable">
  <input type="hidden" name="csrf" value="x">
  <label for="cv">Votre CV (PDF)</label><input type="file" id="cv" accept=".pdf">
  <label for="lm">Lettre de motivation (fichier)</label><input type="file" id="lm">
  <label>Civilité<select name="civ"><option value="M">M.</option><option value="Mme">Mme</option></select></label>
  <label for="q1">Pourquoi nous rejoindre ?</label><textarea id="q1"></textarea>
  <label><input type="checkbox" name="rgpd"> J'accepte la politique de confidentialité</label>
  <button type="submit">Envoyer ma candidature</button>
</form></body></html>"""

FORM_EN = """<form><input name="first_name" placeholder="First name"><input name="last_name" placeholder="Last name">
<input name="email"><input name="linkedin_url" placeholder="LinkedIn profile"><input type="file" name="resume">
<label for="city">City</label><input id="city"><button type="button">Next</button><button>Submit application</button></form>"""

passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        sys.exit(1)
    passed += 1
    print("✓", label)


fr = extract_fields(FORM_FR)
check(len(fr) == 10, "10 champs visibles extraits (champ caché et faux champ du script ignorés)")
check(fr[0]["label"] == "Prénom *" and fr[0]["required"], "<label for> associé et champ obligatoire détecté")
check(fr[1]["label"] == "Nom", "<label> englobant associé")
check(fr[6]["options"] == ["M", "Mme"] and fr[6]["label"] == "Civilité", "liste déroulante : options et libellé séparés")
check(fr[-1]["text"] == "Envoyer ma candidature", "texte d'un bouton")
check("Postuler" in fr[0]["context"], "texte proche conservé comme contexte")
check("courriel" in describe(fr[2]) and "e-mail" in describe(fr[2]), "description texte d'un champ")

expected_fr = ["first_name", "last_name", "email", "phone", "cv", "cover_file", "gender", "cover_text", "consent", "submit"]
got_fr = [rule_label(f) for f in fr if f["type"] != "hidden"][:10]
check(got_fr == expected_fr, f"règles sur le formulaire français : {got_fr}")
got_en = [rule_label(f) for f in extract_fields(FORM_EN)]
check(got_en == ["first_name", "last_name", "email", "linkedin", "cv", "city", "next", "submit"], f"règles sur le formulaire anglais : {got_en}")
check(all(rule_label(f) in LABELS for f in fr), "toutes les étiquettes appartiennent au contrat LABELS")

with tempfile.TemporaryDirectory() as tmp:
    collecte.RAW = Path(tmp) / "brutes"
    rec = collecte.save_page("Banque Test", "https://example.invalid/jobs/42?x=1", FORM_FR, "Analyste")
    files = sorted(p.suffix for p in (Path(tmp) / "brutes" / "Banque_Test").iterdir())
    check(files == [".html", ".json"], "page enregistrée (HTML + champs pré-étiquetés)")
    check(rec["verifiee"] is False and rec["champs"][0]["etiquette"] == "first_name", "pré-étiquetage, à vérifier à la main")
    check(load_pages(Path(tmp)) == [], "une page non vérifiée ne sert pas de référence")
    j = next((Path(tmp) / "brutes" / "Banque_Test").glob("*.json"))
    data = json.loads(j.read_text(encoding="utf-8"))
    data["verifiee"] = True
    data["champs"][3]["etiquette"] = "phone"
    j.write_text(json.dumps(data), encoding="utf-8")
    pages = load_pages(Path(tmp))
    r = evaluate(charger("regles"), pages)
    check(len(pages) == 1 and r["champs"] == 10 and r["precision"] > 0.8, f"évaluation du modèle de référence : {r['precision']:.0%}")

for f in ROOT.rglob("*.py"):
    src = f.read_text(encoding="utf-8")
    check(not re.search(r"^\s*(from|import)\s+(app|src)\b", src, re.M), f"{f.relative_to(ROOT)} : indépendant du projet principal")

print(f"\n{passed} vérifications réussies.")
