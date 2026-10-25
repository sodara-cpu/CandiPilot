"""Construit le catalogue des entreprises lisibles automatiquement (app/sources_catalog.json).

    .venv/bin/python scripts/find_sources.py

Pour chaque entreprise de CANDIDATES, essaie plusieurs identifiants sur les API publiques des plateformes de
recrutement (Greenhouse, Lever, Ashby, SmartRecruiters, Workable) et garde celles qui publient au moins une offre
en France. Les autres sont notées « assisté » : offres à ajouter par adresse, formulaire rempli par l'extension.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import os  # noqa: E402
os.environ.setdefault("CANDIPILOT_SITEMAP_PAGES", "40")      # validation : les pages stages / en français sont lues en premier
os.environ.setdefault("CANDIPILOT_CRAWL_DELAY", "0.5")
from app import discovery  # noqa: E402

FR = ("france", "paris", "lyon", "lille", "bordeaux", "toulouse", "nantes", "marseille", "nice", "rennes", "montpellier",
      "strasbourg", "grenoble", "sophia", "la défense", "la defense", "boulogne", "levallois", "issy", "neuilly", "saint-denis",
      "courbevoie", "puteaux", "nanterre", "rueil", "massy", "versailles", "aix", "remote - fr", ", fr")

# Entreprises qui recrutent en France (start-up, scale-up, grands groupes, conseil, banque, industrie…).
CANDIDATES = """
Doctolib, Mirakl, Datadog, Dataiku, Algolia, Adyen, Alan, Qonto, Pennylane, Swile, Hugging Face, Ubisoft, Accor, Devoteam,
BlaBlaCar, Back Market, Contentsquare, Criteo, Ledger, PayFit, Spendesk, Sorare, Deezer, Lydia, Sumeria, Mistral AI, Malt,
ManoMano, Vinted, Veepee, Vestiaire Collective, Shift Technology, Ivalua, Sendinblue, Brevo, Aircall, Agicap, Ankorstore,
Backmarket, Believe, Botify, Cajoo, Captain Train, Trainline, Chauffeur Privé, Heetch, Getaround, Lunchr, Libeo, Livestorm,
Lucca, Luko, Meero, Moka Care, Nickel, October, OpenClassrooms, Ornikar, Owkin, Pigment, PayPlug, Platform.sh, Pretto,
Qover, Quonto, Sezane, Sezzle, Shine, Side, Skello, Sigfox, Spacefill, Station F, Swan, Swile, Talend, Tiller, Ulule,
Upway, Voodoo, Welcome to the Jungle, Wakam, Yousign, Younited, Zenchef, Alma, Ledgy, Nabla, Photoroom, Poolside, H Company,
Dust, Hivebrite, Jellysmack, Kard, Lemlist, Luko, Matera, Memo Bank, Mooncard, Mangopay, Lemonway, Fintecture, Defacto,
Silvr, Karos, Cityscoot, Zity, Dott, Tier, Bolt, Uber, Deliveroo, Frichti, Getir, Gorillas, Flink, Zapier, Stripe, Revolut,
N26, Wise, Monzo, Klarna, Checkout.com, GoCardless, Airwallex, Pleo, Personio, Celonis, Contentful, Miro, Notion, Figma,
Canva, Atlassian, Slack, Dropbox, Airbnb, Booking, Spotify, Netflix, Twilio, Snowflake, Databricks, MongoDB, Elastic,
Confluent, HashiCorp, GitLab, Cloudflare, Okta, Zendesk, HubSpot, Salesforce, ServiceNow, Workday, SAP, Oracle, Amazon,
Google, Meta, Microsoft, Apple, OpenAI, Anthropic, Scale AI, Cohere, Palantir, Pinterest, Reddit, Discord, Coinbase,
Kraken, Binance, Bitpanda, Ledger, Sorare, Circle, Chainalysis, Paddle, Mollie, Adevinta, Leboncoin, Seloger, Meilleurs Agents,
Bergamotte, Le Slip Français, Jimmy Fairly, Typology, Merci Handy, Asphalte, Faguo, Aigle, Lacoste, Sandro, Maje, SMCP,
Hermès, Chanel, Dior, LVMH, Kering, L'Oréal, Sephora, Yves Rocher, Clarins, Pernod Ricard, Danone, Lactalis, Bonduelle,
Carrefour, Auchan, Decathlon, Leroy Merlin, Fnac Darty, Cdiscount, Boulanger, Picard, Michelin, Renault, Stellantis, Valeo,
Safran, Thales, Airbus, Dassault Systèmes, Capgemini, Atos, Sopra Steria, CGI, Accenture, Deloitte, EY, KPMG, PwC, Mazars,
Forvis Mazars, Grant Thornton, BCG, McKinsey, Bain, Roland Berger, Oliver Wyman, Kearney, Wavestone, Sia Partners, Onepoint,
Octo Technology, Theodo, Publicis Sapient, BNP Paribas, Société Générale, Crédit Agricole, Natixis, AXA, Allianz, Generali,
BPCE, La Banque Postale, Crédit Mutuel, Amundi, Rothschild, Lazard, Goldman Sachs, Morgan Stanley, JP Morgan, Citi, Barclays,
HSBC, UBS, Deutsche Bank, BlackRock, Ardian, Eurazeo, Tikehau, Edmond de Rothschild, Oddo BHF, Exane, Bpifrance, Euronext,
TotalEnergies, Engie, EDF, Veolia, Suez, Schneider Electric, Legrand, Saint-Gobain, Air Liquide, Vinci, Bouygues, Eiffage,
Orange, SFR, Free, Iliad, Bouygues Telecom, TF1, Canal+, Vivendi, Havas, Publicis, JCDecaux, Ipsos, Sodexo, Elior, Edenred,
Pluxee, Worldline, Ingenico, Lydia, Sanofi, Servier, Ipsen, BioMérieux, Doctolib, Withings, Alan, Qare, Livi, Cegedim,
Air France, SNCF, RATP, Transdev, Keolis, Getlink, ADP, Club Med, Pierre et Vacances, Ubisoft, Gameloft, Ankama, Focus,
Nacon, Dontnod, Amplitude, Riot Games, Electronic Arts, Activision, Blizzard, King, Supercell, Unity, Epic Games, Niantic
"""


# Identifiants qui existent mais ne sont pas l'entreprise visée (vérifiés à la main).
EXCLUDE = {("smartrecruiters", "free2"), ("ashby", "vinci"), ("greenhouse", "sezzle"), ("smartrecruiters", "dataiku")}
ALIASES = {"Backmarket": "Back Market"}
# Entreprises disparues, rachetées ou doublons : retirées de la liste « assistée ».
GONE = {"Quonto", "Station F", "Captain Train", "Chauffeur Privé", "Lunchr", "Cajoo", "Luko", "Gorillas", "Getir", "Frichti",
        "Sendinblue", "Sezzle", "Kard", "Side", "Focus", "King", "Tier", "Bolt", "Dott", "Backmarket"}


# Sites carrières « maison » des grands groupes (lus comme un moteur de recherche : robots.txt, plan du site, fiches
# JobPosting). Gardés seulement si la lecture est autorisée et donne des offres en France.
CAREER_SITES = {
    "L'Oréal": "https://careers.loreal.com", "BNP Paribas": "https://group.bnpparibas", "Société Générale": "https://careers.societegenerale.com",
    "TotalEnergies": "https://jobs.totalenergies.com", "Danone": "https://careers.danone.com", "Capgemini": "https://www.capgemini.com",
    "Schneider Electric": "https://careers.se.com", "AXA": "https://careers.axa.com", "Crédit Agricole": "https://www.groupecreditagricole.jobs",
    "BPCE": "https://recrutement.bpce.fr", "Natixis": "https://recrutement.bpce.fr", "Thales": "https://careers.thalesgroup.com",
    "Deloitte": "https://www2.deloitte.com", "PwC": "https://www.pwc.fr", "KPMG": "https://careers.kpmg.fr", "EY": "https://careers.ey.com",
    "LVMH": "https://www.lvmh.com", "Hermès": "https://careers.hermes.com", "Chanel": "https://careers.chanel.com",
    "Orange": "https://orange.jobs", "EDF": "https://www.edf.fr", "Engie": "https://jobs.engie.com", "Publicis": "https://careers.publicisgroupe.com",
    "SNCF": "https://www.emploi.sncf.com", "Amundi": "https://jobs.amundi.com", "Renault": "https://www.renaultgroup.com",
    "Stellantis": "https://careers.stellantis.com", "Safran": "https://www.safran-group.com", "Airbus": "https://www.airbus.com",
    "Decathlon": "https://joinus.decathlon.fr", "Carrefour": "https://www.recrutement.carrefour.fr", "Michelin": "https://recrutement.michelin.fr",
    "Air France": "https://recrutement.airfrance.com", "Bouygues": "https://www.bouygues.com", "Vinci": "https://jobs.vinci.com",
    "Veolia": "https://www.veolia.com", "Saint-Gobain": "https://joinus.saint-gobain.com", "Pernod Ricard": "https://www.pernod-ricard.com",
    "Kering": "https://www.kering.com", "Sanofi": "https://www.sanofi.com", "Accenture": "https://www.accenture.com",
    "McKinsey": "https://www.mckinsey.com", "BCG": "https://careers.bcg.com", "Bain": "https://www.bain.com", "Rothschild": "https://www.rothschildandco.com",
    "Lazard": "https://www.lazard.com", "Goldman Sachs": "https://higher.gs.com", "JP Morgan": "https://careers.jpmorgan.com",
    "Morgan Stanley": "https://www.morganstanley.com", "Citi": "https://jobs.citi.com", "HSBC": "https://www.hsbc.com", "Barclays": "https://search.jobs.barclays",
    "Allianz": "https://careers.allianz.com", "Generali": "https://www.generali.fr", "La Banque Postale": "https://www.labanquepostale.com",
    "Crédit Mutuel": "https://recrutement.creditmutuel.fr", "Bpifrance": "https://www.bpifrance.fr", "Euronext": "https://www.euronext.com",
    "Ardian": "https://www.ardian.com", "Eurazeo": "https://www.eurazeo.com", "Tikehau": "https://www.tikehaucapital.com",
    "Edenred": "https://careers.edenred.com", "Sodexo": "https://jobs.sodexo.com", "Legrand": "https://www.legrandgroup.com",
    "Air Liquide": "https://www.airliquide.com", "Dassault Systèmes": "https://www.3ds.com", "Atos": "https://atos.net",
    "Sopra Steria": "https://www.soprasteria.fr", "Wavestone": "https://www.wavestone.com", "Sia Partners": "https://www.sia-partners.com",
    "Ipsos": "https://www.ipsos.com", "Havas": "https://www.havas.com", "TF1": "https://www.groupe-tf1.fr", "Club Med": "https://www.clubmedjobs.com",
    "Fnac Darty": "https://www.fnacdarty.com", "Leroy Merlin": "https://recrutement.leroymerlin.fr", "Auchan": "https://www.auchan-recrute.fr",
    "Lacoste": "https://corporate.lacoste.com", "Sephora": "https://www.sephora.fr", "Clarins": "https://www.clarins.com",
}
WD = ("wd3", "wd1", "wd5", "wd103", "wd12", "wd2", "wd10")


def probe_workday(name: str):
    """Entreprise sur Workday : son robots.txt indique l'adresse du plan du site (donc du site carrières)."""
    for slug in slugs(name)[:3]:
        for wd in WD:
            host = f"https://{slug}.{wd}.myworkdayjobs.com"
            try:
                text = discovery._get(host + "/robots.txt", timeout=10, accept="*/*")
            except Exception:
                continue
            maps = [l.split(":", 1)[1].strip() for l in text.splitlines() if l.lower().startswith("sitemap:")]
            for m in maps:
                site = m.rsplit("/", 1)[0]
                r = probe((name, "sitemap", site))
                if r:
                    return r
    return None


def slugs(name: str) -> list:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace("'", "").replace(".", "")
    a = re.sub(r"[^a-z0-9]+", "", n)
    b = re.sub(r"[^a-z0-9]+", "-", n).strip("-")
    c = re.sub(r"[^A-Za-z0-9]+", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode())
    return list(dict.fromkeys(x for x in (a, b, c, a + "2", c + "2") if x))


def probe(item):
    name, platform, token = item
    try:
        jobs = discovery.fetch(platform, token, name)
    except Exception:
        return None
    fr = [j for j in jobs if any(k in j["location"].lower() for k in FR)]
    if not fr:
        return None
    early = [j for j in fr if discovery.contract_of(j) in ("stage", "alternance", "vie", "graduate")]
    return {"platform": platform, "token": token, "company": name, "jobs": len(jobs), "france": len(fr), "early": len(early)}


def main():
    names = list(dict.fromkeys(x.strip() for x in CANDIDATES.replace("\n", " ").split(",") if x.strip()))
    tries = [(n, p, s) for n in names for p in ("greenhouse", "lever", "ashby", "smartrecruiters", "workable") for s in slugs(n)]
    print(f"{len(names)} entreprises, {len(tries)} essais…", flush=True)
    with ThreadPoolExecutor(24) as ex:
        found = [r for r in ex.map(probe, tries) if r]
    readable_ats = {r["company"] for r in found}
    rest = [n for n in names if n not in readable_ats]
    print(f"Sites carrières : Workday pour {len(rest)} entreprises, {len(CAREER_SITES)} sites maison…", flush=True)
    with ThreadPoolExecutor(16) as ex:
        found += [r for r in ex.map(probe_workday, rest) if r]
        found += [r for r in ex.map(probe, [(n, "sitemap", u) for n, u in CAREER_SITES.items()
                                            if n not in {r["company"] for r in found}]) if r]
    best = {}
    found = [dict(r, company=ALIASES.get(r["company"], r["company"])) for r in found
             if (r["platform"], r["token"].lower()) not in EXCLUDE]
    for r in found:      # une source par entreprise et par plateforme (l'identifiant qui publie le plus)
        k = (r["platform"], r["token"].lower())
        if k not in best or r["france"] > best[k]["france"]:
            best[k] = r
    sources = sorted(best.values(), key=lambda r: (-r["early"], -r["france"]))
    readable = {r["company"] for r in sources}
    out = {"verified_at": date.today().isoformat(), "sources": sources,
           "assisted": sorted((n for n in names if n not in readable and n not in GONE), key=str.lower)}
    (ROOT / "app" / "sources_catalog.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(sources)} sources lisibles ({sum(r['france'] for r in sources)} offres en France, "
          f"{sum(r['early'] for r in sources)} stages/alternances/VIE) ; {len(out['assisted'])} entreprises en mode assisté")


if __name__ == "__main__":
    main()
