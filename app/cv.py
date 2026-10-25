"""Lecture d'un CV (PDF) pour proposer de remplir le profil.

Rien n'est enregistré ici : l'utilisateur valide chaque proposition (onboarding, étape 2).
Seul ce qui est écrit dans le CV est proposé ; rien n'est deviné ni inventé.
"""
from __future__ import annotations

import io
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}|\+\d{2,3}(?:[\s.-]?\d{1,4}){3,5}")
LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", re.I)
SCHOOL = re.compile(r"\b(business school|école|ecole|université|universite|university|institut|institute|"
                    r"skema|essec|hec|edhec|escp|em lyon|emlyon|neoma|kedge|audencia|grenoble em|tbs|iéseg|ieseg|"
                    r"dauphine|sciences po|polytechnique|centrale|mines|insa)\b", re.I)
LANGS = {"français": "Français", "francais": "Français", "french": "Français", "anglais": "Anglais", "english": "Anglais",
         "espagnol": "Espagnol", "spanish": "Espagnol", "allemand": "Allemand", "german": "Allemand", "italien": "Italien",
         "italian": "Italien", "chinois": "Chinois", "mandarin": "Chinois", "arabe": "Arabe", "arabic": "Arabe",
         "portugais": "Portugais", "portuguese": "Portugais", "russe": "Russe", "japonais": "Japonais"}
CITIES = ["Paris", "Lyon", "Marseille", "Lille", "Bordeaux", "Toulouse", "Nantes", "Nice", "Strasbourg", "Montpellier",
          "Rennes", "Grenoble", "Sophia Antipolis", "La Défense", "London", "Londres", "Bruxelles", "Genève", "Luxembourg"]


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages[:3])
    except Exception:
        return ""


def _name(lines: list, email: str) -> tuple:
    """Prénom et nom : une des premières lignes faite de 2 à 4 mots commençant par une majuscule."""
    for line in lines[:8]:
        words = line.replace(",", " ").split()
        if 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words) and not any(ch.isdigit() for ch in line) \
                and "@" not in line and not SCHOOL.search(line) and len(line) < 45:
            # NOM en majuscules → nom de famille ; sinon le dernier mot.
            upper = [w for w in words if w.isupper() and len(w) > 1]
            if upper and len(upper) < len(words):
                last = " ".join(upper)
                first = " ".join(w for w in words if w not in upper)
            else:
                first, last = " ".join(words[:-1]), words[-1]
            return first.title(), last.title() if last.isupper() else last
    if email:   # repli : prenom.nom@…
        local = email.split("@")[0]
        parts = re.split(r"[._-]", re.sub(r"\d+", "", local))
        if len(parts) == 2 and all(len(p) > 1 for p in parts):
            return parts[0].title(), parts[1].title()
    return "", ""


def extract(text: str) -> dict:
    """Propositions trouvées dans le texte du CV (seulement ce qui y figure)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out: dict = {}
    m = EMAIL.search(text)
    email = m.group(0) if m else ""
    if email:
        out["email"] = email
    m = PHONE.search(text)
    if m:
        out["phone"] = re.sub(r"\s+", " ", m.group(0)).strip()
    m = LINKEDIN.search(text)
    if m:
        url = m.group(0)
        out["linkedin"] = url if url.startswith("http") else "https://" + url
    first, last = _name(lines, email)
    if first:
        out["first_name"], out["last_name"] = first, last
    school = next((l for l in lines if SCHOOL.search(l) and len(l) < 90), "")
    if school:
        out["school"] = school
    low = text.lower()
    city = next((c for c in CITIES if re.search(r"\b" + re.escape(c.lower()) + r"\b", low)), "")
    if city:
        out["location"] = city
    langs = []
    for k, v in LANGS.items():
        if re.search(r"\b" + k + r"\b", low) and v not in langs:
            langs.append(v)
    if langs:
        out["languages"] = ", ".join(langs)
    return out
