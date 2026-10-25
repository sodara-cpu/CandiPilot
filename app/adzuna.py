"""Offres de l'agrégateur Adzuna (API officielle, developer.adzuna.com), en France.

Recherche par mots-clés (métiers ciblés du candidat), côté serveur. Adzuna regroupe des offres de nombreux
sites (entreprises, cabinets, sites d'emploi) : stages, alternances, CDI. Chaque offre garde son lien Adzuna,
qui mène à l'annonce d'origine. Les conditions d'Adzuna demandent de citer la source (« via Adzuna »).

Identifiants : ADZUNA_APP_ID / ADZUNA_APP_KEY. Tests : CANDIPILOT_ADZUNA_BASE remplace l'adresse de l'API.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

_TEST = os.environ.get("CANDIPILOT_ADZUNA_BASE", "").rstrip("/")
SEARCH_URL = (_TEST or "https://api.adzuna.com/v1/api/jobs/fr/search") + "/{page}"
MAX_KEYWORDS = 6           # une requête par métier ciblé : reste loin des limites du compte gratuit
PER_QUERY = 50             # maximum de l'API par page
CONTRACTS = {"permanent": "cdi", "contract": "cdd"}


def enabled() -> bool:
    return bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))


def _search(what: str, where: str = "") -> list:
    params = {"app_id": os.environ["ADZUNA_APP_ID"].strip(), "app_key": os.environ["ADZUNA_APP_KEY"].strip(),
              "what": what, "results_per_page": PER_QUERY, "sort_by": "date", "content-type": "application/json"}
    if where:
        params["where"] = where
    req = urllib.request.Request(SEARCH_URL.format(page=1) + "?" + urllib.parse.urlencode(params),
                                 headers={"Accept": "application/json", "User-Agent": "CandiPilot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}").get("results", [])


def _job(o: dict) -> dict:
    title = re.sub(r"<[^>]+>", "", o.get("title") or "")
    return {"url": o.get("redirect_url") or "", "title": title, "company": (o.get("company") or {}).get("display_name") or "Entreprise (via Adzuna)",
            "location": (o.get("location") or {}).get("display_name") or "", "contract": CONTRACTS.get(o.get("contract_type") or "", ""),
            "remote": False, "description": re.sub(r"<[^>]+>", "", o.get("description") or "")[:6000],
            "published_at": o.get("created") or "", "ref": str(o.get("id") or "")}


def search(prefs: dict) -> list:
    """Offres correspondant aux métiers ciblés (une requête par métier ; contrat ajouté aux mots-clés s'il est unique)."""
    contrats = [c.lower() for c in prefs.get("contrats") or []]
    extra = {"stage": "stage", "alternance": "alternance"}.get(contrats[0], "") if len(contrats) == 1 else ""
    zones = [z for z in prefs.get("zones") or [] if z.lower() not in ("france", "télétravail", "teletravail", "remote")]
    where = zones[0] if len(zones) == 1 else ""
    words = [w for w in (re.sub(r"[^\w\s&-]", " ", m).strip() for m in prefs.get("metiers") or []) if len(w) >= 2][:MAX_KEYWORDS]
    seen, out = set(), []
    for w in words:
        for o in _search(f"{w} {extra}".strip(), where):
            if o.get("id") in seen:
                continue
            seen.add(o.get("id"))
            out.append(_job(o))
        time.sleep(0.3)
    return out
