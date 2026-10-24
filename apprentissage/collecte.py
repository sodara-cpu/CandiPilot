"""Collecte de pages de sites carrières pour constituer le jeu d'apprentissage.

    python collecte.py                 (lit config.yaml)

Pour chaque site : respecte robots.txt, attend entre deux pages, suit seulement les liens
qui ressemblent à des offres ou à des formulaires de candidature, et s'arrête à la limite fixée.
Ne remplit et n'envoie jamais rien : il lit seulement les pages.

Chaque page est enregistrée dans donnees/brutes/<site>/ :
- <page>.html : le code de la page ;
- <page>.json : ses champs de formulaire, pré-étiquetés par les règles (à vérifier à la main).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import yaml

from etiquettes import rule_label
from extraction import extract_fields

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "donnees" / "brutes"
USER_AGENT = "CandiPilot-apprentissage/0.1 (projet étudiant)"


def _robots(url: str):
    rp = robotparser.RobotFileParser()
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    try:
        req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            rp.parse(r.read().decode("utf-8", "ignore").splitlines())
    except Exception:
        rp.parse([])            # pas de robots.txt lisible : tout est permis
    return rp


def _slug(url: str) -> str:
    path = re.sub(r"[^A-Za-z0-9]+", "_", urlparse(url).path).strip("_")[:50] or "accueil"
    return f"{path}_{hashlib.md5(url.encode()).hexdigest()[:8]}"


def save_page(site: str, url: str, html: str, title: str) -> dict:
    fields = extract_fields(html)
    for f in fields:
        f["etiquette"] = rule_label(f)
    record = {"url": url, "site": site, "titre": title, "collecte_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "verifiee": False, "champs": fields}
    out = RAW / re.sub(r"[^\w.-]+", "_", site)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{_slug(url)}.html").write_text(html, encoding="utf-8")
    (out / f"{_slug(url)}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def crawl(cfg: dict) -> None:
    from playwright.sync_api import sync_playwright

    keywords = [k.lower() for k in cfg.get("mots_cles_liens", [])]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not cfg.get("navigateur_visible", False))
        page = browser.new_page(user_agent=USER_AGENT)
        for site in cfg.get("sites") or []:
            start, name = site["url"], site.get("nom") or urlparse(site["url"]).netloc
            domain, rp = urlparse(start).netloc, _robots(start)
            todo, seen, saved = [(start, 0)], set(), 0
            print(f"== {name}")
            while todo and saved < cfg.get("pages_max_par_site", 20):
                url, depth = todo.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                if not rp.can_fetch(USER_AGENT, url):
                    print(f"   interdit par robots.txt : {url}")
                    continue
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2500)
                except Exception as e:
                    print(f"   échec : {url} ({type(e).__name__})")
                    continue
                rec = save_page(name, url, page.content(), page.title())
                saved += 1
                print(f"   {len(rec['champs']):3d} champs  {url}")
                if depth < cfg.get("profondeur_max", 2):
                    for href in page.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, e.innerText])"):
                        link, text = urldefrag(urljoin(url, href[0]))[0], (href[1] or "").lower()
                        if urlparse(link).netloc == domain and link not in seen and \
                                any(k in link.lower() or k in text for k in keywords):
                            todo.append((link, depth + 1))
                time.sleep(cfg.get("delai_secondes", 5))
        browser.close()


if __name__ == "__main__":
    crawl(yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")))
