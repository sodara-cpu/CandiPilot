"""Matching et classement des offres (F05) : score de 0 à 100, explicable.

Pondération :
- 45 pts  intitulé du poste vs métiers ciblés (mots-clés des préférences)
- 15 pts  compétences du profil retrouvées dans l'intitulé
- 15 pts  type de contrat recherché
- 10 pts  zone géographique
- 10 pts  date de début compatible
-  5 pts  candidature simplifiée (envoi possible par le robot)
"""
from __future__ import annotations

import re
import unicodedata

STOP = {"de", "du", "des", "la", "le", "les", "et", "en", "a", "au", "aux", "d", "l", "un", "une",
        "stage", "stagiaire", "intern", "internship", "h", "f", "x", "analyste", "analyst", "junior"}


def _tokens(text: str) -> set:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return {w for w in re.split(r"[^a-z0-9&]+", t) if len(w) > 1 and w not in STOP}


def score(offer: dict, prefs: dict, profile: dict) -> int:
    title = f"{offer.get('poste', '')} {offer.get('domaine', '')}"
    title_tokens = _tokens(title)

    pts = 0.0
    metiers = prefs.get("metiers") or []
    best = 0.0
    for kw in metiers:
        kt = _tokens(kw)
        if kt:
            best = max(best, len(kt & title_tokens) / len(kt))
    pts += 45 * best if metiers else 22

    comp = _tokens(profile.get("competences", ""))
    if comp:
        pts += 15 * min(1.0, len(comp & title_tokens) / 2)

    contrats = [c.lower() for c in prefs.get("contrats") or []]
    contrat = (offer.get("contrat") or "").lower()
    pts += 15 if not contrats or not contrat or any(c in contrat for c in contrats) else 0

    zones = [z.lower() for z in prefs.get("zones") or []]
    loc = (offer.get("localisation") or "").lower()
    pts += 10 if not zones or not loc or any(z in loc for z in zones) else 0

    start_from = prefs.get("start_from") or ""
    start = offer.get("start_date") or ""
    pts += 10 if not start_from or (start and start >= start_from) else (5 if not start else 0)

    pts += 5 if offer.get("apply_type") == "jobteaser" else 0
    return int(round(min(100, pts)))
