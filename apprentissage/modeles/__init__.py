"""Point d'entrée des modèles.

Un modèle est une classe avec :
    nom: str
    def entrainer(self, pages: list[dict]) -> None      # pages = fichiers JSON de donnees/ (champs + etiquette)
    def predire(self, champs: list[dict]) -> list[str]  # une étiquette de etiquettes.LABELS par champ

C'est ce contrat qui servira à brancher le modèle sur le robot plus tard.
Le modèle de deep learning est dans modeles/reseau.py (PyTorch).
"""
from __future__ import annotations

from etiquettes import rule_label


class ModeleRegles:
    """Référence à battre : les règles par mots-clés."""
    nom = "regles"

    def entrainer(self, pages):
        pass

    def predire(self, champs):
        return [rule_label(c) for c in champs]


def _reseau():
    from .reseau import ModeleReseau   # importé à la demande : PyTorch n'est nécessaire que pour ce modèle
    m = ModeleReseau()
    m.charger()                        # reprend le dernier modèle appris par autonome.py s'il existe
    return m


MODELES = {"regles": ModeleRegles, "reseau": _reseau}


def charger(nom: str):
    if nom not in MODELES:
        raise SystemExit(f"Modèle inconnu : {nom}. Disponibles : {', '.join(MODELES)}")
    return MODELES[nom]()
