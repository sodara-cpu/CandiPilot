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
import re
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
JOURNAL = DATA / "journal.jsonl"      # un bilan par site
PAGES = DATA / "pages.jsonl"          # chaque page visitée, avec ce qui a été compris
CORE = {"email", "cv", "first_name", "last_name", "full_name", "phone"}

# Plateformes de recrutement vers lesquelles le bouton « Postuler » d'un site carrières renvoie souvent.
ATS = ("taleo.net", "myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co", "smartrecruiters.com",
       "successfactors.com", "successfactors.eu", "icims.com", "csod.com", "teamtailor.com", "recruitee.com",
       "welcomekit.co", "jobteaser.com", "workable.com", "bamboohr.com", "jobvite.com", "oraclecloud.com")
APPLY_WORDS = re.compile(r"postul|apply|candidat|jobapply|application", re.I)
BLOCKED = re.compile(r"access denied|forbidden|just a moment|attention required|captcha|are you a robot|"
                     r"accès refusé|request blocked|not authorized", re.I)
SKIP = re.compile(r"^(mailto|tel|javascript):|facebook\.com|twitter\.com|x\.com/|linkedin\.com/(share|sharing)|"
                  r"instagram\.com|youtube\.com|\.(pdf|jpg|png|zip)(\?|$)", re.I)


def is_application_form(labels: list) -> bool:
    return "submit" in labels and len(CORE & set(labels)) >= 2


def classify(title: str, html: str, champs: list, labels: list) -> str:
    """formulaire | connexion | bloquee | page"""
    if BLOCKED.search(title or "") or (len(html) < 2000 and BLOCKED.search(html)):
        return "bloquee"
    if is_application_form(labels):
        return "formulaire"
    if any(c["type"] == "password" for c in champs):
        return "connexion"
    return "page"


def is_ats(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in ATS)


def follow(link: str, text: str, allowed: set) -> bool:
    """Lien à suivre : même site, ou bouton « Postuler » / plateforme de recrutement (ajoutée aux sites permis)."""
    if SKIP.search(link) or not link.startswith(("http", "file:")):
        return False
    domain = urlparse(link).netloc
    if domain in allowed:
        return True
    if is_ats(domain) or APPLY_WORDS.search(text or "") or APPLY_WORDS.search(urlparse(link).path):
        allowed.add(domain)
        return True
    return False


def accuracy(model, pages: list) -> float:
    ok = total = 0
    for p in pages:
        for f, g in zip(p["champs"], model.predire(p["champs"])):
            total += 1
            ok += g == f["etiquette"]
    return ok / total if total else 0.0


def diagnose(r: dict, kinds: dict, platforms: set, budget: int, more_links: bool) -> str:
    """Phrase simple qui explique le résultat d'un site."""
    if r["formulaires"]:
        return f"{r['formulaires']} formulaire(s) de candidature lu(s) et appris."
    if kinds["bloquee"] and r["pages_visitees"] <= 1:
        return "Accès refusé : ce site bloque les robots. Retire-le de config.yaml."
    if platforms:
        return ("Le bouton « Postuler » mène à " + ", ".join(sorted(platforms)) +
                ", qui demande de se connecter avant le formulaire : chemin appris, formulaire non lisible sans compte.")
    if kinds["connexion"]:
        return "Une page de connexion bloque l'accès aux formulaires."
    if r["echecs"] and not r["pages_visitees"]:
        return "Aucune page n'a pu être ouverte (adresse incorrecte ou site injoignable)."
    if more_links and r["pages_visitees"] >= budget:
        return f"Limite de {budget} pages atteinte avant de trouver un formulaire : augmente pages_max_par_site."
    return "Aucun lien vers un formulaire de candidature trouvé depuis cette page de départ."


class Apprenant:
    def __init__(self, seuil: float = 0.95, epsilon: float = 0.15, seed: int = 0, fresh: bool = False,
                 n_synth: int = 300, boot_epochs: int = 20, persist: bool = True):
        self.seuil, self.epsilon, self.rng, self.persist = seuil, epsilon, random.Random(seed), persist
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
        allowed_domains = {urlparse(start).netloc}
        frontier = [(start, "", "", 0, None)]           # (url, href, texte du lien, profondeur, parent)
        parents, seen, visited, forms, first_form, last_form, new_pseudo = {}, set(), 0, [], None, None, 0
        kinds, failures, platforms = {"formulaire": 0, "connexion": 0, "bloquee": 0, "page": 0}, 0, set()
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
                failures += 1
                self._page_log({"site": name, "url": url, "type": "echec", "profondeur": depth, "lien": text[:80]})
                continue
            html, title, links = page
            visited += 1
            parents[url] = parent
            if parent is not None:
                self.links.observe(url, (href, text), depth)
            champs = extract_fields(html)
            probs = self.fields.probabilites(champs)
            labels = [l for l, _ in probs]
            kind = classify(title, html, champs, labels)
            kinds[kind] += 1
            self._page_log({"site": name, "url": url, "titre": (title or "")[:100], "type": kind, "profondeur": depth,
                            "lien": text[:80], "champs": len(champs), "liens": len(links),
                            "roles": sorted({l for l in labels if l != "other"})})
            if kind == "bloquee":
                if visited == 1:
                    break                                   # le site refuse les robots : on n'insiste pas
                continue
            if kind == "connexion" and is_ats(urlparse(url).netloc):
                # Porte de la candidature atteinte (connexion obligatoire) : bon chemin, mais rien à lire derrière.
                platforms.add(urlparse(url).netloc)
                chain, u = [], url
                while u is not None:
                    chain.append(u)
                    u = parents.get(u)
                self.links.reward(chain)
            elif kind == "formulaire":
                forms.append(url)
                first_form, last_form = first_form or visited, visited
                chain, u = [], url
                while u is not None:
                    chain.append(u)
                    u = parents.get(u)
                self.links.reward(chain)
                new_pseudo += self._remember(name, url, title, champs, probs)
            elif kind != "connexion":
                for h, t in links:
                    link = urldefrag(urljoin(url, h))[0]
                    if link not in seen and follow(link, t, allowed_domains):
                        frontier.append((link, h, t, depth + 1, url))
            if visited % 4 == 0:
                self.links.learn()
            time.sleep(delay)
        self.links.learn(60)
        result = {"site": name, "pages_visitees": visited, "formulaires": len(forms),
                  "pages_avant_premier_formulaire": first_form, "pages_pour_tous_les_formulaires": last_form,
                  "pseudo_etiquettes": new_pseudo, "connexions_requises": kinds["connexion"],
                  "pages_bloquees": kinds["bloquee"], "echecs": failures}
        result["diagnostic"] = diagnose(result, kinds, platforms, budget, bool(frontier))
        return result

    def _page_log(self, entry: dict) -> None:
        print(f"   [{entry['type']}] {entry['url'][:110]}")
        if self.persist:
            DATA.mkdir(exist_ok=True)
            entry["date"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with PAGES.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
        print("   Bilan : " + ", ".join(f"{k} = {v}" for k, v in entry.items() if k not in ("date", "diagnostic")))
        print("   → " + entry.get("diagnostic", ""))
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
    learner = Apprenant(fresh=fresh, seed=seed, epsilon=epsilon, persist=persist, **options)
    browser, results = Browser(visible), []
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
