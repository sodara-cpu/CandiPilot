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
           "assisted": sorted(n for n in names if n not in readable)}
    (ROOT / "app" / "sources_catalog.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(sources)} sources lisibles ({sum(r['france'] for r in sources)} offres en France, "
          f"{sum(r['early'] for r in sources)} stages/alternances/VIE) ; {len(out['assisted'])} entreprises en mode assisté")


if __name__ == "__main__":
    main()
