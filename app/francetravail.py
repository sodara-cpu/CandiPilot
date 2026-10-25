"""Offres de France Travail (API officielle « Offres d'emploi v2 », francetravail.io).

Recherche par mots-clés (métiers ciblés du candidat), côté serveur. Couvre surtout emplois et alternances ;
peu de stages (un stage n'est pas un contrat de travail). Chaque offre garde son lien d'origine.

Identifiants de l'application (francetravail.io → Mon espace) : FRANCE_TRAVAIL_CLIENT_ID / FRANCE_TRAVAIL_CLIENT_SECRET.
Tests : CANDIPILOT_FT_BASE remplace les adresses de l'API par un faux serveur.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

_TEST = os.environ.get("CANDIPILOT_FT_BASE", "").rstrip("/")
TOKEN_URL = (_TEST + "/token") if _TEST else "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
SEARCH_URL = (_TEST + "/search") if _TEST else "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"
MAX_KEYWORDS = 6           # requêtes par recherche (une par métier ciblé)
PER_QUERY = 150            # offres par requête (maximum de l'API)
CONTRACTS = {"CDI": "cdi", "CDD": "cdd", "MIS": "interim", "SAI": "saisonnier"}

_token = {"value": "", "until": 0.0}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(os.environ.get("FRANCE_TRAVAIL_CLIENT_ID") and os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET"))


def _access_token() -> str:
    with _lock:
        if _token["value"] and time.time() < _token["until"] - 60:
            return _token["value"]
        data = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SCOPE,
                                       "client_id": os.environ["FRANCE_TRAVAIL_CLIENT_ID"].strip(),
                                       "client_secret": os.environ["FRANCE_TRAVAIL_CLIENT_SECRET"].strip()}).encode()
        req = urllib.request.Request(TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read())
        _token.update(value=tok["access_token"], until=time.time() + int(tok.get("expires_in") or 1400))
        return _token["value"]


def _search(params: dict) -> list:
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _access_token(), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status == 204:          # aucune offre
            return []
        return json.loads(r.read() or b"{}").get("resultats", [])


def _job(o: dict) -> dict:
    lieu = o.get("lieuTravail") or {}
    origin = (o.get("origineOffre") or {}).get("urlOrigine") or f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id')}"
    contract = "alternance" if o.get("alternance") else CONTRACTS.get(o.get("typeContrat") or "", (o.get("typeContratLibelle") or "").lower())
    return {"url": origin, "title": o.get("intitule") or "", "company": (o.get("entreprise") or {}).get("nom") or "Entreprise (via France Travail)",
            "location": lieu.get("libelle") or "", "contract": contract, "remote": False,
            "description": (o.get("description") or "")[:6000], "published_at": o.get("dateCreation") or "", "ref": str(o.get("id") or "")}


def search(prefs: dict) -> list:
    """Offres correspondant aux métiers ciblés du candidat (une requête par métier, sans doublon)."""
    # L'API n'accepte que lettres, chiffres et espaces dans les mots-clés (« M&A » → « M A » : trop court, ignoré).
    words = [w for w in (re.sub(r"[^\w\s-]", " ", m).strip() for m in prefs.get("metiers") or []) if len(w) >= 3][:MAX_KEYWORDS]
    seen, out = set(), []
    for w in words:
        for o in _search({"motsCles": w, "range": f"0-{PER_QUERY - 1}", "sort": 1}):
            if o.get("id") in seen:
                continue
            seen.add(o.get("id"))
            out.append(_job(o))
        time.sleep(0.4)                                  # limite de l'API : quelques appels par seconde
    return out
