"""Découverte d'offres multi-sources (lot 2), côté serveur, par des canaux prévus pour être lus.

Sources :
- API publiques des plateformes de recrutement (ATS) : Greenhouse, Lever, Ashby, SmartRecruiters, Workable.
  Elles sont documentées pour être consultées par des services tiers ; aucune connexion, aucun robot.
- Données structurées schema.org « JobPosting » d'une page carrières (le format prévu pour les moteurs
  de recherche), en respectant robots.txt.

Une « source » = une entreprise sur une plateforme (ex. greenhouse:adyen). L'utilisateur l'ajoute en collant
l'adresse de la page carrières : la plateforme est reconnue automatiquement (detect_source).
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from urllib import robotparser
from urllib.parse import urlparse

UA = "CandiPilot/1.0 (+https://candipilot.onrender.com/confidentialite)"
# Adresses des API (remplaçables dans les tests par un faux serveur : CANDIPILOT_ATS_BASE).
_TEST = os.environ.get("CANDIPILOT_ATS_BASE", "").rstrip("/")
API = {
    "greenhouse": (_TEST + "/greenhouse/{t}") if _TEST else "https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true",
    "lever": (_TEST + "/lever/{t}") if _TEST else "https://api.lever.co/v0/postings/{t}?mode=json",
    "ashby": (_TEST + "/ashby/{t}") if _TEST else "https://api.ashbyhq.com/posting-api/job-board/{t}",
    "smartrecruiters": (_TEST + "/smartrecruiters/{t}") if _TEST else "https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=100",
    "workable": (_TEST + "/workable/{t}") if _TEST else "https://apply.workable.com/api/v1/widget/accounts/{t}",
}
PLATFORMS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "smartrecruiters": "SmartRecruiters",
             "workable": "Workable", "jsonld": "Page carrières", "sitemap": "Site carrières"}
# Reconnaissance de la plateforme à partir d'une adresse.
PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
]


def _get(url: str, timeout: int = 25, accept: str = "application/json, text/html"):
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*~")       # adresses avec accents (plans de site mal encodés)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _text(h: str) -> str:
    """HTML → texte (descriptions d'offres)."""
    t = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", html.unescape(h or ""), flags=re.I)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", t)).strip()[:6000]


def detect_source(url: str):
    """(plateforme, identifiant) d'une adresse de page carrières ou d'offre, sinon (jsonld, adresse) si c'est une page web."""
    for platform, pat in PATTERNS:
        m = re.search(pat, url or "", re.I)
        if m:
            return platform, m.group(1).strip("/").lower() if platform != "ashby" else m.group(1).strip("/")
    if (url or "").startswith(("http://", "https://")):
        return "jsonld", url
    return None, None


def _job(**kw) -> dict:
    base = {"url": "", "title": "", "company": "", "location": "", "contract": "", "remote": False,
            "description": "", "published_at": "", "ref": ""}
    base.update({k: (v if v is not None else base.get(k, "")) for k, v in kw.items()})
    return base


_CACHE: dict = {}
CACHE_S = 3 * 3600


def fetch(platform: str, token: str, company: str = "") -> list:
    """Offres actuellement publiées par une source (lues une fois toutes les 3 h pour tous les utilisateurs)."""
    key = (platform, token, company)
    hit = _CACHE.get(key)
    ttl = CACHE_S * 4 if platform == "sitemap" else CACHE_S       # site carrières : relu deux fois par jour au plus
    if hit and time.time() - hit[0] < ttl and not _TEST:
        return [dict(j) for j in hit[1]]
    jobs = _fetch(platform, token, company)
    _CACHE[key] = (time.time(), jobs)
    return [dict(j) for j in jobs]


def _fetch(platform: str, token: str, company: str = "") -> list:
    if platform == "jsonld":
        return fetch_jsonld(token, company)
    if platform == "sitemap":
        return fetch_sitemap(token, company)
    data = json.loads(_get(API[platform].format(t=token)))
    out = []
    if platform == "greenhouse":
        for j in data.get("jobs", []):
            out.append(_job(url=j.get("absolute_url"), title=j.get("title"), company=company or token,
                            location=(j.get("location") or {}).get("name", ""), description=_text(j.get("content", "")),
                            published_at=j.get("updated_at", ""), ref=str(j.get("id"))))
    elif platform == "lever":
        for j in data if isinstance(data, list) else []:
            cat = j.get("categories") or {}
            out.append(_job(url=j.get("hostedUrl"), title=j.get("text"), company=company or token,
                            location=cat.get("location", ""), contract=cat.get("commitment", ""),
                            remote=(j.get("workplaceType") == "remote"), description=(j.get("descriptionPlain") or "")[:6000],
                            published_at=str(j.get("createdAt", "")), ref=j.get("id")))
    elif platform == "ashby":
        for j in data.get("jobs", []):
            out.append(_job(url=j.get("jobUrl"), title=j.get("title"), company=company or token,
                            location=j.get("location", ""), contract=j.get("employmentType", ""), remote=bool(j.get("isRemote")),
                            description=(j.get("descriptionPlain") or "")[:6000], published_at=j.get("publishedAt", ""), ref=j.get("id")))
    elif platform == "smartrecruiters":
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append(_job(url=f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}", title=j.get("name"),
                            company=(j.get("company") or {}).get("name") or company or token,
                            location=", ".join(x for x in (loc.get("city"), loc.get("country", "").upper()) if x),
                            contract=(j.get("typeOfEmployment") or {}).get("label", ""), remote=bool(loc.get("remote")),
                            published_at=j.get("releasedDate", ""), ref=j.get("id")))
    elif platform == "workable":
        for j in data.get("jobs", []):
            out.append(_job(url=j.get("url") or j.get("application_url"), title=j.get("title"), company=data.get("name") or company or token,
                            location=", ".join(x for x in (j.get("city"), j.get("country")) if x), contract=j.get("employment_type", ""),
                            remote=bool(j.get("telecommuting")), published_at=j.get("published_on", ""), ref=j.get("shortcode")))
    return [j for j in out if j["url"] and j["title"]]


def _allowed(url: str) -> bool:
    p = urlparse(url)
    rp = robotparser.RobotFileParser()
    try:
        rp.parse(_get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=10, accept="*/*").splitlines())
    except Exception:
        return True
    return rp.can_fetch(UA, url)


def _name(v):
    """Valeur schema.org qui peut être un texte ou un objet {"name": …}."""
    return v.get("name", "") if isinstance(v, dict) else v


def fetch_jsonld(url: str, company: str = "", check_robots: bool = True) -> list:
    """Offres décrites en JSON-LD schema.org JobPosting sur une page carrières (si robots.txt l'autorise)."""
    if check_robots and not _allowed(url):
        raise PermissionError("robots.txt n'autorise pas la lecture de cette page.")
    page = _get(url, accept="text/html,*/*")
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if not isinstance(it, dict) or "JobPosting" not in str(it.get("@type")):
                continue
            loc = it.get("jobLocation") or {}
            loc = loc[0] if isinstance(loc, list) and loc else loc
            addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
            org = it.get("hiringOrganization") or {}
            out.append(_job(url=it.get("url") or url, title=html.unescape(str(it.get("title") or "")),
                            company=(org.get("name") if isinstance(org, dict) else "") or company,
                            location=", ".join(x for x in (addr.get("addressLocality"), _name(addr.get("addressCountry"))) if isinstance(x, str) and x),
                            contract=str(it.get("employmentType") or ""), remote=it.get("jobLocationType") == "TELECOMMUTE",
                            description=_text(it.get("description", "")), published_at=str(it.get("datePosted", "")),
                            ref=str(it.get("identifier", {}).get("value", "") if isinstance(it.get("identifier"), dict) else it.get("url", ""))))
    return [j for j in out if j["title"]]


# ---------- Sites carrières (Workday, Taleo, SuccessFactors, sites maison) : comme un moteur de recherche ----------
# 1. robots.txt doit autoriser la lecture ; on y trouve le plan du site (Sitemap) prévu pour les moteurs de recherche.
# 2. Le plan du site liste les pages d'offres ; on garde celles qui semblent en France (lieu dans l'adresse, si présent).
# 3. Chaque page d'offre est lue pour sa fiche schema.org « JobPosting » (celle que les entreprises publient pour
#    Google Emplois). Lecture lente (CRAWL_DELAY entre deux pages), limitée (MAX_PAGES), identifiée (UA), mise en cache.
MAX_PAGES = int(os.environ.get("CANDIPILOT_SITEMAP_PAGES") or 80)    # stages et pages en français lus en premier
CRAWL_DELAY = float(os.environ.get("CANDIPILOT_CRAWL_DELAY") or 1.0)
JOB_URL = re.compile(r"/(job|jobs|offre|offres|emploi|emplois|career|careers|vacancy|position|requisition|stage)s?[/-]", re.I)
WORKDAY_JOB = re.compile(r"/job/([^/]+)/[^/]+$")


def _robots(base: str):
    p = urlparse(base)
    rp = robotparser.RobotFileParser()
    try:
        text = _get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=15, accept="*/*")
    except Exception:
        text = ""
    rp.parse(text.splitlines())
    maps = [l.split(":", 1)[1].strip() for l in text.splitlines() if l.lower().startswith("sitemap:")]
    return rp, maps


def _sitemap_urls(url: str, depth: int = 0, limit: int = 5000) -> list:
    """Adresses listées par un plan du site (et ses sous-plans)."""
    try:
        xml = _get(url, timeout=30, accept="*/*")
    except Exception:
        return []
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    if "<sitemapindex" in xml and depth < 2:
        out = []
        for sub in locs[:30]:
            if len(out) >= limit:
                break
            out += _sitemap_urls(html.unescape(sub), depth + 1, limit - len(out))
        return out
    return [html.unescape(u) for u in locs[:limit]]


def _looks_french(url: str) -> bool:
    """Lieu dans l'adresse : /job/<Lieu>/<Intitulé>[/…] (Workday, L'Oréal…). En France ? Sans lieu repérable : à lire."""
    m = re.search(r"/jobs?/([^/]+)/[^/]+", urlparse(url).path, re.I)
    if not m:
        return True
    loc = _norm(urllib.parse.unquote(m.group(1)).replace("-", " "))
    if any(_norm(c) in loc for c in FRANCE + IDF) or loc in ("", "multiple locations", "remote", "france"):
        return True
    return bool(re.search(r"\d", loc))        # segment avec chiffres : identifiant, pas un lieu


def fetch_sitemap(base: str, company: str = "") -> list:
    """Offres d'un site carrières lues comme le fait un moteur de recherche (voir plus haut)."""
    rp, maps = _robots(base)
    if not rp.can_fetch(UA, base.rstrip("/") + "/"):
        raise PermissionError("robots.txt n'autorise pas la lecture de ce site carrières.")
    root = base.rstrip("/")
    maps = [m for m in maps if m.startswith(root) or urlparse(m).netloc == urlparse(base).netloc] or \
        [root + "/siteMap.xml", root + "/sitemap.xml"]
    urls = []
    for m in maps:
        urls += [u for u in _sitemap_urls(m) if u.startswith(root) and JOB_URL.search(urlparse(u).path)]
        if urls:
            break
    urls = [u for u in dict.fromkeys(urls) if _looks_french(u)]
    # Pages en français d'abord (presque toujours des offres en France) : utile quand le lieu n'est pas dans l'adresse.
    # Puis stages, alternances, VIE, jeunes diplômés d'abord : le site est lu en partie seulement (MAX_PAGES).
    urls.sort(key=lambda u: (0 if re.search(r"stage|stagiaire|intern|alternan|apprenti|v-?i-?e|graduate|trainee|campus", u, re.I) else 1,
                             0 if re.search(r"(/fr[-_/]|/fr$|[-_]fr$|lang=fr)", u, re.I) else 1))
    urls = [u for u in urls if rp.can_fetch(UA, u)][:MAX_PAGES]
    out = []
    for i, u in enumerate(urls):
        if i:
            time.sleep(CRAWL_DELAY)
        try:
            jobs = fetch_jsonld(u, company, check_robots=False)
        except Exception:
            continue
        for j in jobs:
            j["url"] = u
            j["ref"] = j["ref"] or u
            j["company"] = company or j["company"]       # nom propre plutôt que la raison sociale de la filiale
        out += jobs[:1]
    return out


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"\((h/f|f/h|m/f|f/m|w/m/d|m/w/d)\)|\b(h/f|f/h|m/f)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def fingerprint(company: str, title: str, location: str) -> str:
    """Même entreprise + même intitulé + même ville = même offre, quelle que soit la source (doublons)."""
    city = _norm(location).split(" ")[0] if location else ""
    return hashlib.sha1(f"{_norm(company)}|{_norm(title)}|{city}".encode()).hexdigest()[:20]


FRANCE = ("france", "paris", "lyon", "lille", "bordeaux", "toulouse", "nantes", "marseille", "nice", "rennes", "montpellier",
          "strasbourg", "grenoble", "sophia", "antipolis", "la defense", "boulogne", "levallois", "issy", "neuilly", "saint-denis",
          "courbevoie", "puteaux", "nanterre", "rueil", "massy", "versailles", "aix-en-provence", "ile-de-france")
IDF = ("paris", "ile-de-france", "la defense", "boulogne", "levallois", "issy", "neuilly", "saint-denis", "courbevoie", "puteaux",
       "nanterre", "rueil", "massy", "versailles", "montrouge", "clichy", "saint-ouen", "vincennes", "ivry", "saint-cloud")
ZONE_ALIASES = {"london": ("london", "londres", "londre"), "londres": ("london",), "londre": ("london",),
                "remote": ("remote", "teletravail", "télétravail"), "teletravail": ("remote",), "télétravail": ("remote",)}


def in_zone(location: str, zones: list, remote: bool = False) -> bool:
    """La ville ou le pays de l'offre correspond-il à une des zones voulues ? (« Rennes, FR » est en France,
    « Levallois-Perret » est à Paris au sens Île-de-France, « London » = Londres.)"""
    loc = _norm(location)
    raw = (location or "").lower()
    for z in (_norm(x) for x in zones or []):
        if not z:
            continue
        if z in loc or any(a in loc for a in ZONE_ALIASES.get(z, ())):
            return True
        if z == "france" and (re.search(r"(^|[ ,(])fr($|[ ,)])", raw) or any(c in loc for c in (_norm(x) for x in FRANCE))):
            return True
        if z == "paris" and any(c in loc for c in (_norm(x) for x in IDF)):
            return True
        if z in ("teletravail", "remote") and remote:
            return True
    return False


CONTRACT_WORDS = {"stage": ("intern", "stage", "stagiaire", "internship", "praktikum"),
                  "alternance": ("alternance", "apprenti", "apprentice", "work-study"),
                  "cdi": ("cdi", "full-time", "full time", "permanent", "fulltime"),
                  "cdd": ("cdd", "fixed-term", "temporary", "contract"),
                  "vie": ("vie", "v.i.e"), "graduate": ("graduate", "new grad", "early career", "junior")}


def contract_of(job: dict) -> str:
    """Type de contrat déduit de l'intitulé et du champ « contrat » de la source (mots entiers : « Internal » ≠ « intern »)."""
    t = f"{job['title']} {job['contract']}".lower()
    for k, words in CONTRACT_WORDS.items():
        if any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t) for w in words):
            return k
    return (job["contract"] or "").lower()


def company_name(platform: str, token: str) -> str:
    """Nom officiel de l'entreprise quand la plateforme le publie (sinon l'identifiant, en majuscule)."""
    try:
        if platform == "greenhouse" and not _TEST:
            return json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{token}")).get("name") or token.title()
    except Exception:
        pass
    return token.replace("-", " ").title() if platform != "jsonld" else ""
