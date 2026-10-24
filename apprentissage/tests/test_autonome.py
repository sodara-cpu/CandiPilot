"""Tests du deep learning et de l'exploration autonome, sur des sites générés en local (aucun vrai site).

    .venv/bin/python apprentissage/tests/test_autonome.py
"""
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import autonome  # noqa: E402
from autonome import Apprenant, accuracy, is_application_form, run  # noqa: E402
from etiquettes import rule_label  # noqa: E402
from modeles.reseau import ModeleReseau, Navigateur, tokens  # noqa: E402
from synthetique import build_site, labeled_pages  # noqa: E402

passed = 0


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        sys.exit(1)
    passed += 1
    print("✓", label)


# Les fichiers du vrai apprentissage (donnees/) ne sont pas touchés par les tests.
tmp = Path(tempfile.mkdtemp())
autonome.DATA, autonome.PSEUDO, autonome.JOURNAL = tmp / "donnees", tmp / "donnees/pseudo.jsonl", tmp / "donnees/journal.jsonl"

check(tokens("Prénom") == tokens("prénom") and len(tokens("CV")) == 3, "texte haché (mot + trigrammes)")
check(is_application_form(["email", "cv", "submit"]) and not is_application_form(["email", "other"]), "détection d'un formulaire de candidature")

# 1. Le réseau des champs apprend mieux que les règles sur des formulaires jamais vus.
train, test = labeled_pages(150, seed=1), labeled_pages(100, seed=2)
m = ModeleReseau(epochs=10)
m.entrainer(train)
rules = sum(rule_label(f) == f["etiquette"] for p in test for f in p["champs"]) / sum(len(p["champs"]) for p in test)
net = accuracy(m, test)
check(net > rules and net > 0.95, f"réseau des champs {net:.1%} > règles {rules:.1%} (formulaires jamais vus)")
conf = m.probabilites(test[0]["champs"])
check(all(0 <= c <= 1 for _, c in conf), "chaque réponse a un niveau de confiance")
m.sauver(tmp / "champs.pt")
m2 = ModeleReseau()
check(m2.charger(tmp / "champs.pt") and m2.predire(test[0]["champs"]) == m.predire(test[0]["champs"]), "modèle sauvegardé et rechargé à l'identique")

# 2. Le navigateur apprend qu'« Postuler » mène à un formulaire et « Mentions légales » non.
nav = Navigateur()
for i in range(30):
    nav.observe(f"a{i}", (f"/offre/{i}/postuler", "Postuler"), 2)
    nav.observe(f"n{i}", (f"/info/legal{i}", "Mentions légales"), 2)
    nav.reward([f"a{i}"])
nav.learn(200)
s_apply, s_noise = nav.scores([("/offre/99/postuler", "Postuler", 2), ("/info/legal99", "Mentions légales", 2)])
check(s_apply > 0.8 and s_noise < 0.2, f"liens : « Postuler » {s_apply:.2f}, « Mentions légales » {s_noise:.2f}")
nav.observe("x", ("/a", "a"), 1)
nav.observe("y", ("/b", "b"), 2)
nav.reward(["y", "x"])
check(nav.memory["y"][2] == 1.0 and abs(nav.memory["x"][2] - 0.6) < 1e-9, "récompense dégressive en remontant le chemin")

# 3. Pas de « désapprentissage » : un réentraînement sur de fausses étiquettes est refusé.
learner = Apprenant(fresh=True, n_synth=120, boot_epochs=8)
before = learner.best
learner.pseudo = [dict(p, champs=[dict(f, etiquette="other") for f in p["champs"]]) for p in labeled_pages(200, seed=7)]
acc, kept = learner.improve()
check(not kept and learner.best == before, f"modèle dégradé ({acc:.1%} < {before:.1%}) refusé")

# 4. Exploration autonome de sites générés : apprise vs au hasard.
sites = [{"nom": f"Site {i}", "url": build_site(tmp / f"site{i}", seed=200 + i).as_uri()} for i in range(3)]
opts = dict(budget=30, delay=0, visible=False, persist=False, fresh=True, n_synth=120, boot_epochs=8)
hasard = run(sites, epsilon=1.0, **opts)
appris = run(sites, **opts)
check(all(r["formulaires"] == 4 for r in appris), "les 4 formulaires de chaque site sont trouvés")
check(sum(r["pseudo_etiquettes"] for r in appris) > 50, "nouveaux exemples appris seuls (pseudo-étiquettes)")
h = sum(r["pages_pour_tous_les_formulaires"] for r in hasard)
a = sum(r["pages_pour_tous_les_formulaires"] for r in appris)
check(a < h, f"exploration apprise plus efficace : {a} pages contre {h} au hasard")
check(appris[-1]["pages_pour_tous_les_formulaires"] <= appris[0]["pages_pour_tous_les_formulaires"], "elle s'améliore d'un site à l'autre")
check(not autonome.JOURNAL.exists(), "mode test : rien n'est écrit dans donnees/")

print(f"\n{passed} vérifications réussies.")
