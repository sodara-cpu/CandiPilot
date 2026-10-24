"""Mesure la qualité d'un modèle sur les pages vérifiées à la main.

    python evaluation.py               (modèle « regles »)
    python evaluation.py <nom>         (un modèle déclaré dans modeles/__init__.py)

Seules les pages dont "verifiee" vaut true servent de référence. Une partie sert à
l'entraînement, le reste au test, pour mesurer ce que le modèle sait sur des pages jamais vues.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

from modeles import charger

ROOT = Path(__file__).resolve().parent


def load_pages(folder: Path = ROOT / "donnees") -> list:
    pages = []
    for f in sorted(folder.rglob("*.json")):
        page = json.loads(f.read_text(encoding="utf-8"))
        if page.get("verifiee"):
            pages.append(page)
    return pages


def evaluate(model, pages: list, test_share: float = 0.3, seed: int = 0) -> dict:
    pages = pages[:]
    random.Random(seed).shuffle(pages)
    cut = int(len(pages) * (1 - test_share))
    train, test = (pages[:cut], pages[cut:]) if len(pages) > 1 else (pages, pages)
    model.entrainer(train)
    ok, total, errors = 0, 0, Counter()
    for page in test:
        for field, guess in zip(page["champs"], model.predire(page["champs"])):
            total += 1
            if guess == field["etiquette"]:
                ok += 1
            else:
                errors[(field["etiquette"], guess)] += 1
    return {"pages_entrainement": len(train), "pages_test": len(test), "champs": total,
            "precision": ok / total if total else 0.0, "erreurs": errors.most_common(10)}


if __name__ == "__main__":
    pages = load_pages()
    if not pages:
        sys.exit("Aucune page vérifiée dans donnees/ : passe \"verifiee\": true sur les pages corrigées.")
    r = evaluate(charger(sys.argv[1] if len(sys.argv) > 1 else "regles"), pages)
    print(f"{r['pages_entrainement']} pages d'entraînement, {r['pages_test']} de test, {r['champs']} champs")
    print(f"Précision : {r['precision']:.1%}")
    for (truth, guess), n in r["erreurs"]:
        print(f"  {n} × « {truth} » pris pour « {guess} »")
