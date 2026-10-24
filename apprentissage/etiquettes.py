"""Étiquettes que le modèle doit apprendre, et étiqueteur de référence par règles.

Les étiquettes correspondent à ce que le robot sait remplir (src/adapters/generic.py) :
c'est le « contrat » qui permettra de brancher le modèle sur le projet plus tard.

L'étiqueteur par règles sert deux fois :
- à pré-étiqueter les pages collectées (données d'entraînement de départ, à corriger à la main) ;
- de point de comparaison : le modèle doit faire mieux que lui.
"""
from __future__ import annotations

import re

from extraction import describe

LABELS = {
    "first_name": "Prénom",
    "last_name": "Nom",
    "full_name": "Nom complet",
    "email": "E-mail",
    "phone": "Téléphone",
    "linkedin": "Profil LinkedIn",
    "city": "Ville / adresse",
    "cv": "Dépôt du CV",
    "cover_file": "Dépôt de la lettre de motivation",
    "cover_text": "Zone de texte de motivation",
    "gender": "Genre / civilité",
    "availability": "Date de disponibilité",
    "school": "École / diplôme",
    "consent": "Consentement (RGPD, conditions)",
    "next": "Bouton Continuer / Suivant",
    "submit": "Bouton Envoyer / Postuler",
    "other": "Autre champ (question du recruteur…)",
}

# Ordre important : du plus spécifique au plus général.
_RULES = [
    ("linkedin", r"linkedin"),
    ("email", r"e-?mail|courriel"),
    ("phone", r"phone|t[ée]l[ée]phone|mobile|portable"),
    ("first_name", r"first.?name|given.?name|pr[ée]nom|fname"),
    ("last_name", r"last.?name|family.?name|surname|nom de famille|lname|\bnom\b"),
    ("full_name", r"full.?name|nom complet|\bname\b"),
    ("cover_file", r"(cover|motivation|lettre).*(file|upload|pdf|fichier|joindre)|(file|upload|fichier).*(cover|motivation|lettre)"),
    ("cv", r"\bcv\b|resume|résumé|curriculum"),
    ("cover_text", r"cover|motivation|lettre|why|pourquoi"),
    ("gender", r"gender|genre|civilit|sexe|salutation"),
    ("availability", r"availab|disponib|start.?date|date de d[ée]but"),
    ("school", r"school|[ée]cole|universit|dipl[oô]me|degree|education"),
    ("city", r"city|ville|address|adresse|postal|zip"),
    ("consent", r"consent|gdpr|rgpd|privacy|confidentialit|terms|conditions|j'accepte|i agree"),
    ("submit", r"submit|envoyer|postuler|apply|soumettre|send"),
    ("next", r"next|suivant|continuer|continue"),
]


def rule_label(field: dict) -> str:
    # D'abord ce qui appartient au champ (libellé, name, placeholder…), puis le texte voisin en dernier recours.
    label = _rule_label(field, describe(dict(field, context="")).lower())
    return label if label != "other" else _rule_label(field, describe(field).lower())


def _rule_label(field: dict, text: str) -> str:
    if field["tag"] == "button" or field["type"] == "submit":
        for label in ("submit", "next"):
            if re.search(dict(_RULES)[label], text):
                return label
        return "other"
    if field["type"] == "file":
        if re.search(dict(_RULES)["cover_file"], text) or re.search(r"cover|motivation|lettre", text):
            return "cover_file"
        return "cv"
    if field["type"] == "checkbox" and re.search(dict(_RULES)["consent"], text):
        return "consent"
    for label, pattern in _RULES:
        if label in ("submit", "next", "cover_file", "consent"):
            continue
        if re.search(pattern, text):
            if label == "cover_text" and field["tag"] != "textarea":
                continue
            return label
    return "other"
