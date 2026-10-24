"""Apprentissage autonome : explorer les sites carrières, comprendre leurs formulaires, s'améliorer seul.

    python autonome.py            explore les sites de config.yaml (lecture seule, robots.txt respecté)
    python autonome.py --demo     même chose sur des sites générés en local (aucun vrai site visité)

À chaque page :
1. le Navigateur (réseau des liens) choisit le prochain lien à suivre — le plus prometteur, parfois
   un lien au hasard pour continuer à découvrir (exploration) ;
2. le réseau des champs lit les champs et décide si la page est un formulaire de candidature ;
3. si oui, les liens qui y ont mené sont récompensés, et les champs dont le réseau est sûr
   deviennent de nouveaux exemples d'entraînement (pseudo-étiquettes).

Après chaque site, le réseau des champs se réentraîne sur ces nouveaux exemples. La nouvelle version
n'est gardée que si elle fait au moins aussi bien sur le jeu de validation : il ne peut pas « désapprendre ».
Rien n'est jamais rempli ni envoyé. Les progrès sont notés dans donnees/journal.jsonl.
"""
from __future__ import annotations

import copy
import json
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import yaml

import collecte
from evaluation import load_pages
from extraction import extract_fields
from modeles.reseau import ModeleReseau, Navigateur
from synthetique import build_site, labeled_pages

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "donnees"
PSEUDO = DATA / "pseudo.jsonl"
JOURNAL = DATA / "journal.jsonl"
CORE = {"email", "cv", "first_name", "last_name", "full_name", "phone"}


def is_application_form(labels: list) -> bool:
    return "submit" in labels and len(CORE & set(labels)) >= 2


def accuracy(model, pages: list) -> float:
    ok = total = 0
    for p in pages:
        for f, g in zip(p["champs"], model.predire(p["champs"])):
            total += 1
            ok += g == f["etiquette"]
    return ok / total if total else 0.0


class Apprenant:
    def __init__(self, seuil: float = 0.95, epsilon: float = 0.15, seed: int = 0, fresh: bool = False,
                 n_synth: int = 300, boot_epochs: int = 20):
        self.seuil, self.epsilon, self.rng = seuil, epsilon, random.Random(seed)
        self.human = load_pages(DATA)
        self.synthetic = labeled_pages(n_synth, seed=seed + 1)
        self.validation = self.human + labeled_pages(80, seed=seed + 99)
        self.pseudo = [] if fresh else [json.loads(l) for l in PSEUDO.read_text(encoding="utf-8").splitlines()] if PSEUDO.exists() else []
        self.fields = ModeleReseau()
        self.links = Navigateur()
        if fresh or not self.fields.charger():
            print("Premier apprentissage (formulaires générés + pages vérifiées)…")
            self._train(self.fields, epochs=boot_epochs)
            self.fields.sauver()
        if not fresh:
            self.links.charger()
        self.best = accuracy(self.fields, self.validation)
        print(f"Précision de départ sur la validation : {self.best:.1%}")

    def _train(self, model, epochs: int) -> None:
        pages = self.synthetic + self.human + self.pseudo
        weights = [0.5] * len(self.synthetic) + [1.0] * len(self.human) + [0.7] * len(self.pseudo)
        model.epochs = epochs
        model.entrainer(pages, weights)

    # ---------- exploration d'un site ----------
    def explore(self, name: str, start: str, fetch, budget: int, delay: float = 0.0, allowed=lambda u: True) -> dict:
        domain = urlparse(start).netloc
        frontier = [(start, "", "", 0, None)]           # (url, href, texte du lien, profondeur, parent)
        parents, seen, visited, forms, first_form, last_form, new_pseudo = {}, set(), 0, [], None, None, 0
        while frontier and visited < budget:
            if self.rng.random() < self.epsilon:
                item = frontier.pop(self.rng.randrange(len(frontier)))
            else:
                scores = self.links.scores([(h, t, d) for _, h, t, d, _ in frontier])
                item = frontier.pop(max(range(len(frontier)), key=scores.__getitem__))
            url, href, text, depth, parent = item
            if url in seen or not allowed(url):
                continue
            seen.add(url)
            page = fetch(url)
            if page is None:
                continue
            html, title, links = page
            visited += 1
            parents[url] = parent
            if parent is not None:
                self.links.observe(url, (href, text), depth)
            champs = extract_fields(html)
            probs = self.fields.probabilites(champs)
            labels = [l for l, _ in probs]
            if is_application_form(labels):
                forms.append(url)
                first_form, last_form = first_form or visited, visited
                chain, u = [], url
                while u is not None:
                    chain.append(u)
                    u = parents.get(u)
                self.links.reward(chain)
                new_pseudo += self._remember(name, url, title, champs, probs)
            else:
                for h, t in links:
                    link = urldefrag(urljoin(url, h))[0]
                    if urlparse(link).netloc == domain and link not in seen:
                        frontier.append((link, h, t, depth + 1, url))
            if visited % 4 == 0:
                self.links.learn()
            time.sleep(delay)
        self.links.learn(60)
        return {"site": name, "pages_visitees": visited, "formulaires": len(forms),
                "pages_avant_premier_formulaire": first_form, "pages_pour_tous_les_formulaires": last_form,
                "pseudo_etiquettes": new_pseudo}

    def _remember(self, site: str, url: str, title: str, champs: list, probs: list) -> int:
        """Garde les champs dont le réseau est sûr ; les autres restent sans étiquette (ignorés à l'entraînement)."""
        sure = 0
        for c, (label, conf) in zip(champs, probs):
            c["etiquette"], c["confiance"] = (label if conf >= self.seuil else ""), round(conf, 3)
            sure += conf >= self.seuil
        if sure:
            self.pseudo.append({"url": url, "site": site, "titre": title, "verifiee": False, "champs": champs})
        return sure

    # ---------- amélioration du réseau des champs ----------
    def improve(self) -> tuple:
        candidate = copy.deepcopy(self.fields)
        self._train(candidate, epochs=4)
        acc = accuracy(candidate, self.validation)
        accepted = acc >= self.best - 0.005
        if accepted:
            self.fields, self.best = candidate, max(self.best, acc)
        return acc, accepted

    def save(self, persist: bool = True) -> None:
        if not persist:
            return
        DATA.mkdir(exist_ok=True)
        self.fields.sauver()
        self.links.sauver()
        PSEUDO.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in self.pseudo[-2000:]), encoding="utf-8")

    def log(self, entry: dict, persist: bool = True) -> None:
        entry["date"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print("   " + ", ".join(f"{k} = {v}" for k, v in entry.items() if k != "date"))
        if persist:
            DATA.mkdir(exist_ok=True)
            with JOURNAL.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------- accès aux pages ----------

class Browser:
    """Lecture des pages avec Playwright (Chromium), sans jamais cliquer sur un bouton ni remplir un champ."""

    def __init__(self, visible: bool = False):
        from playwright.sync_api import sync_playwright
        self._p = sync_playwright().start()
        self._b = self._p.chromium.launch(headless=not visible)
        self._page = self._b.new_page(user_agent=collecte.USER_AGENT)

    def __call__(self, url: str):
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._page.wait_for_timeout(300 if url.startswith("file:") else 2500)
            links = self._page.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, (e.innerText || '').trim()])")
            return self._page.content(), self._page.title(), [tuple(l) for l in links]
        except Exception as e:
            print(f"   échec : {url} ({type(e).__name__})")
            return None

    def close(self):
        self._b.close()
        self._p.stop()


def run(sites: list, budget: int, delay: float, visible: bool, persist: bool = True, fresh: bool = False, seed: int = 0,
        epsilon: float = 0.15, **options) -> list:
    learner, browser, results = Apprenant(fresh=fresh, seed=seed, epsilon=epsilon, **options), Browser(visible), []
    try:
        for site in sites:
            print(f"== {site['nom']}")
            rp = collecte._robots(site["url"]) if site["url"].startswith("http") else None
            allowed = (lambda u: rp.can_fetch(collecte.USER_AGENT, u)) if rp else (lambda u: True)
            r = learner.explore(site["nom"], site["url"], browser, budget, delay, allowed)
            r["precision_validation"], r["modele_garde"] = [round(x, 4) if isinstance(x, float) else x for x in learner.improve()]
            learner.log(r, persist)
            learner.save(persist)
            results.append(r)
    finally:
        browser.close()
    return results


if __name__ == "__main__":
    if "--demo" in sys.argv:
        tmp = Path(tempfile.mkdtemp())
        sites = [{"nom": f"Site généré {i + 1}", "url": build_site(tmp / f"site{i}", seed=100 + i).as_uri()} for i in range(6)]
        print("\n### Exploration au hasard (référence, sans apprentissage des liens)")
        hasard = run(sites, budget=30, delay=0, visible=False, persist=False, fresh=True, epsilon=1.0)
        print("\n### Exploration apprise")
        appris = run(sites, budget=30, delay=0, visible=False, persist=False, fresh=True)
        print("\nPages nécessaires pour trouver tous les formulaires (hasard → appris) :")
        for h, a in zip(hasard, appris):
            print(f"   {a['site']} : {h['pages_pour_tous_les_formulaires']} → {a['pages_pour_tous_les_formulaires']}")
    else:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        if not cfg.get("sites"):
            sys.exit("Aucun site dans config.yaml (ou lance la démonstration : python autonome.py --demo).")
        run(cfg["sites"], cfg.get("pages_max_par_site", 20), cfg.get("delai_secondes", 5), cfg.get("navigateur_visible", False))
