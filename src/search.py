"""Recherche automatique d'offres sur JobTeaser et ajout à config/offers.csv.

Ne garde que les offres « Candidature simplifiée » (badge jobad-card-internal),
les seules que le robot sait envoyer de bout en bout, puis ouvre chacune pour lire
sa « Date de début » et ne garder que celles qui commencent à partir de
search.start_from (ex. "2027-04-01").
Les mots-clés et filtres sont dans config/profile.yaml (section `search`).

Exemples :
    python -m src.search
    python -m src.search --keywords "private debt" "credit" --max-pages 2
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from urllib.parse import quote_plus

from .browser import browser_session
from .config import ROOT, load_profile
from . import tracker as T

BASE = "https://skema.jobteaser.com"
START_SEL = '[data-testid="jobad-DetailView__CandidacyDetails__start_date"]'
MONTHS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
          "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
          "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12}
OFFERS_PATH = ROOT / "config" / "offers.csv"
OFFER_FIELDS = ["url", "company", "role", "domain", "start_date", "apply_type", "status"]

# Extrait chaque carte d'offre de la page de résultats.
CARDS_JS = """
els => els.map(c => {
  const q = s => c.querySelector(s);
  const a = q('h3 a[href*="/job-offers/"]');
  return {
    href: a ? a.getAttribute('href') : '',
    title: a ? a.innerText.trim() : '',
    company: (q('[data-testid="jobad-card-company-name"]') || {}).innerText || '',
    contract: (q('[data-testid="jobad-card-contract"]') || {}).innerText || '',
    location: (q('[data-testid="jobad-card-location"]') || {}).innerText || '',
    easy: !!q('[data-testid="jobad-card-internal"]'),
  };
})
"""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Cherche des offres JobTeaser et les ajoute à offers.csv.")
    p.add_argument("--keywords", nargs="+", default=None, help="Mots-clés (remplace ceux du profil).")
    p.add_argument("--max-pages", type=int, default=None, help="Pages de résultats max par mot-clé.")
    p.add_argument("--include-external", action="store_true",
                   help="Ajoute aussi les offres à candidature externe (à finir à la main).")
    return p.parse_args(argv)


def _known_urls() -> set[str]:
    urls: set[str] = set()
    for path in (OFFERS_PATH, T.LOG_PATH):
        if path.exists():
            with open(path, encoding="utf-8") as f:
                urls |= {(r.get("url") or "").strip() for r in csv.DictReader(f)}
    return urls


def _clean_title(title: str) -> str:
    """Retire les mentions parasites (« - Mars 2027 », « (H/F) »…) et les MAJUSCULES."""
    t = re.sub(r"\s*[-–(]*\s*(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|"
               r"novembre|décembre|january|february|march|april|june|july|august|september|"
               r"october|november|december|s1|s2)\s*20\d\d\s*\)?", "", title, flags=re.I)
    t = re.sub(r"\s*\((h/f|f/h|m/f|f/m|h/f/x)\)|\s+(h/f|f/h|m/f|f/m)$", "", t, flags=re.I).strip(" -–")
    if t.isupper():
        t = t.capitalize()
    return t


def _guess_domain(title: str, keyword: str, domain_lines: dict) -> str:
    """Domaine = première clé de domain_lines trouvée dans le titre, sinon le mot-clé."""
    t = title.lower()
    for key in domain_lines:
        if key != "default" and key in t:
            return key.title() if key != "m&a" else "M&A"
    return keyword.title() if keyword.lower() != "m&a" else "M&A"


def _read_start(page, url: str) -> tuple[str, str]:
    """Ouvre l'offre et renvoie (texte brut, date ISO) de la « Date de début »."""
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector(START_SEL, timeout=10000)
        raw = page.inner_text(START_SEL).replace("Date de début", "").strip()
    except Exception:
        return "", ""
    words = raw.lower().split()
    month = next((MONTHS[w] for w in words if w in MONTHS), None)
    year = next((w for w in words if w.isdigit() and len(w) == 4), None)
    iso = f"{year}-{month:02d}-01" if month and year else ""
    return raw, iso


def _wait_for_login(page, url: str, timeout_s: int = 300) -> bool:
    """Si JobTeaser renvoie vers la connexion SKEMA, attend que l'utilisateur se connecte."""
    page.goto(url, wait_until="domcontentloaded")
    t0 = time.time()
    warned = False
    while time.time() - t0 < timeout_s:
        if "jobteaser.com" in page.url and "job-offers" in page.url and "moment" not in page.title().lower():
            return True
        if not warned:
            print(">>> Connecte-toi à JobTeaser (compte SKEMA) dans la fenêtre du navigateur. "
                  "J'attends 5 minutes max.")
            warned = True
        if "jobteaser.com" in page.url and "job-offers" not in page.url:
            page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
    return False


def _matches(card: dict, cfg: dict) -> bool:
    contract = card["contract"].lower()
    location = card["location"].lower()
    wanted_contracts = [c.lower() for c in cfg.get("contract_contains", ["stage"])]
    wanted_locations = [l.lower() for l in cfg.get("location_contains", [])]
    if wanted_contracts and not any(c in contract for c in wanted_contracts):
        return False
    if wanted_locations and not any(l in location for l in wanted_locations):
        return False
    return True


def collect(settings: dict, cfg: dict, known: set, domain_lines: dict, log=print) -> tuple:
    """Cherche les offres et renvoie (offres retenues, statistiques).

    Utilisé par la commande ci-dessous et par l'application web (app/worker.py).
    `known` : URL déjà connues (jamais renvoyées). `log` : fonction de suivi.
    """
    base = (cfg.get("base_url") or BASE).rstrip("/")   # portail JobTeaser de l'utilisateur (école)
    keywords = cfg.get("keywords") or []
    max_pages = int(cfg.get("max_pages", 3))
    include_external = bool(cfg.get("include_external", False))
    start_from = str(cfg.get("start_from") or "")
    known = set(known) | {u.strip() for u in cfg.get("exclude_urls", []) or []}
    candidates: list = []
    found: list = []
    stats = {"external": 0, "date": 0, "login": True}

    with browser_session(settings) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        first_url = f"{base}/fr/job-offers?q={quote_plus(keywords[0])}"
        if not _wait_for_login(page, first_url):
            stats["login"] = False
            return found, stats

        for kw in keywords:
            log(f"Recherche « {kw} »")
            for n in range(1, max_pages + 1):
                page.goto(f"{base}/fr/job-offers?q={quote_plus(kw)}&page={n}",
                          wait_until="domcontentloaded")
                try:
                    page.wait_for_selector('[data-testid="jobad-card"]', timeout=15000)
                except Exception:
                    break  # plus de résultats
                cards = page.eval_on_selector_all('[data-testid="jobad-card"]', CARDS_JS)
                if not cards:
                    break
                for c in cards:
                    url = base + c["href"] if c["href"].startswith("/") else c["href"]
                    if not c["href"] or url in known or not _matches(c, cfg):
                        continue
                    if not c["easy"] and not include_external:
                        stats["external"] += 1
                        continue
                    known.add(url)
                    candidates.append({
                        "url": url,
                        "company": c["company"].strip(),
                        "role": _clean_title(c["title"]),
                        "domain": _guess_domain(c["title"], kw, domain_lines),
                        "location": c["location"].strip(),
                        "contract": c["contract"].strip(),
                        "keyword": kw,
                        "start_date": "",
                        "apply_type": "jobteaser" if c["easy"] else "external",
                        "status": "",
                    })
                page.wait_for_timeout(1500)  # rythme poli
            log(f"{len(candidates)} offres candidates au total")

        log(f"Lecture des dates de début ({len(candidates)} offres)")
        for o in candidates:
            raw, iso = _read_start(page, o["url"])
            # Date inconnue ou trop tôt -> écartée (ISO : comparaison de texte suffisante).
            if start_from and (not iso or iso < start_from):
                stats["date"] += 1
                continue
            o["start_date"] = iso
            found.append(o)
            tag = "simplifiée" if o["apply_type"] == "jobteaser" else "externe"
            log(f"+ {tag} {raw or '?'} {o['company']} · {o['role']}")
            page.wait_for_timeout(1000)  # rythme poli
    return found, stats


def main(argv=None) -> int:
    args = parse_args(argv)
    profile = load_profile()
    cfg = dict(profile.data.get("search", {}) or {})
    if args.keywords:
        cfg["keywords"] = args.keywords
    if args.max_pages:
        cfg["max_pages"] = args.max_pages
    if args.include_external:
        cfg["include_external"] = True

    if not cfg.get("keywords"):
        print("Aucun mot-clé : renseigne search.keywords dans config/profile.yaml.")
        return 2

    found, stats = collect(profile.settings, cfg, _known_urls(),
                           profile.data.get("domain_lines", {}))
    if not stats["login"]:
        print("Connexion JobTeaser non détectée. Arrêt.")
        return 1

    if found:
        new_file = not OFFERS_PATH.exists()
        with open(OFFERS_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=OFFER_FIELDS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerows(found)

    print("\n===== RECHERCHE =====")
    print(f"  offres ajoutées à config/offers.csv : {len(found)}")
    if not cfg.get("include_external"):
        print(f"  offres externes ignorées            : {stats['external']}")
    if cfg.get("start_from"):
        print(f"  début avant {cfg['start_from']} ou inconnu : {stats['date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
