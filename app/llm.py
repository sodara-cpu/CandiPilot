"""Matching intelligent (lot 3) : Claude relit chaque offre retenue par les critères stricts et explique, en
français, pourquoi elle correspond (ou non) au profil du candidat.

Hybride et explicable :
1. critères stricts (contrat, zone, mots-clés : matching.score) : filtre rapide, sans IA ;
2. compréhension sémantique (Claude) : note 0-100 + une phrase d'explication par offre.

Sans clé ANTHROPIC_API_KEY, rien ne change : la note des critères stricts reste affichée.
Coût maîtrisé : offres envoyées par lots de BATCH, profil mis en cache (prompt caching), plafond
quotidien d'offres analysées par utilisateur (CANDIPILOT_LLM_DAILY_OFFERS), suivi des jetons (llm_usage).
Tests : CANDIPILOT_FAKE_LLM=1 remplace Claude par une notation fictive déterministe (aucun appel réseau).
"""
from __future__ import annotations

import json
import os

from . import db

MODEL = os.environ.get("CANDIPILOT_LLM_MODEL") or "claude-opus-5-5"
FAKE = os.environ.get("CANDIPILOT_FAKE_LLM") == "1"
DAILY_OFFERS = int(os.environ.get("CANDIPILOT_LLM_DAILY_OFFERS") or 150)
BATCH = 20

INSTRUCTIONS = """Tu es le conseiller carrière de CandiPilot. Pour chaque offre, évalue si elle correspond au candidat :
métier et missions, niveau d'études et d'expérience attendu, type de contrat, lieu et dates, langues, secteur.
Les offres ont déjà passé un filtre par mots-clés : juge le fond (missions réelles, niveau), pas seulement l'intitulé.

Note de 0 à 100 :
- 85-100 : correspond très bien, à postuler en priorité ;
- 60-84 : correspond, avec un ou deux écarts ;
- 30-59 : correspondance partielle (niveau, contrat ou missions différents) ;
- 0-29 : ne correspond pas.

Pour chaque offre, écris une explication d'une phrase (25 mots maximum), en français, adressée au candidat en le
tutoyant, qui cite le point décisif (ex. « Stage de 6 mois en contrôle de gestion à Paris : colle à ton master finance. »).
N'invente aucune information absente de l'offre ou du profil. Réponds pour toutes les offres, avec leur identifiant."""

SCHEMA = {
    "type": "object",
    "properties": {
        "offers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "score": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["id", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["offers"],
    "additionalProperties": False,
}

_client = None


def enabled() -> bool:
    return FAKE or bool(os.environ.get("ANTHROPIC_API_KEY"))


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=3)
    return _client


def profile_text(ctx: dict) -> str:
    """Profil du candidat tel que Claude le lit (identique d'un lot à l'autre : mis en cache)."""
    p, prefs = ctx["profile"], ctx["prefs"]
    lines = [
        ("Formation", " – ".join(x for x in (p.get("school"), p.get("program")) if x)),
        ("Compétences", p.get("competences")),
        ("Langues", p.get("languages")),
        ("Ville", p.get("location")),
        ("Métiers recherchés", ", ".join(prefs.get("metiers") or [])),
        ("Contrats recherchés", ", ".join(prefs.get("contrats") or [])),
        ("Zones", ", ".join(prefs.get("zones") or [])),
        ("Disponible à partir de", prefs.get("start_from")),
    ]
    return "Profil du candidat :\n" + "\n".join(f"- {k} : {v}" for k, v in lines if v)


def _offer_text(o: dict) -> str:
    desc = " ".join((o.get("description") or "").split())[:1500]
    head = " | ".join(x for x in (o.get("poste"), o.get("entreprise"), o.get("localisation"), o.get("contrat"),
                                  "télétravail" if o.get("remote") else "", o.get("start_date")) if x)
    return f"<offre id=\"{o['id']}\">\n{head}\n{desc}\n</offre>"


def _fake(offers: list, ctx: dict) -> tuple:
    """Notation fictive (tests) : mots des métiers recherchés présents dans l'intitulé."""
    words = {w for m in ctx["prefs"].get("metiers") or [] for w in m.lower().split() if len(w) > 3}
    out = []
    for o in offers:
        hit = [w for w in words if w in (o.get("poste") or "").lower()]
        out.append({"id": o["id"], "score": 90 if hit else 25,
                    "reason": f"(simulation) Intitulé proche de ta recherche « {hit[0]} »." if hit else "(simulation) Missions éloignées de ta recherche."})
    return out, {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}


def rate(offers: list, ctx: dict) -> tuple:
    """Note et explique un lot d'offres. Renvoie ([{id, score, reason}], usage). Lève une exception si Claude refuse."""
    if FAKE:
        return _fake(offers, ctx)
    resp = client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",                     # refus d'un classifieur : relancé sur le modèle de secours recommandé
        thinking={"type": "adaptive"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=[{"type": "text", "text": INSTRUCTIONS},
                {"type": "text", "text": profile_text(ctx), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "Offres à évaluer :\n\n" + "\n\n".join(_offer_text(o) for o in offers)}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude a refusé d'évaluer ce lot d'offres.")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Réponse de Claude incomplète (limite de longueur).")
    text = next(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
    return json.loads(text)["offers"], usage


def budget_left(conn, user_id: str) -> int:
    r = conn.execute("SELECT offers FROM llm_usage WHERE user_id=? AND day=?", (user_id, db.now()[:10])).fetchone()
    return max(0, DAILY_OFFERS - (r["offers"] if r else 0))


def record_usage(conn, user_id: str, n: int, usage: dict) -> None:
    day = db.now()[:10]
    conn.execute("INSERT OR IGNORE INTO llm_usage(user_id, day) VALUES (?,?)", (user_id, day))
    conn.execute("""UPDATE llm_usage SET calls=calls+1, offers=offers+?, input_tokens=input_tokens+?, output_tokens=output_tokens+?,
                    cache_read_tokens=cache_read_tokens+? WHERE user_id=? AND day=?""",
                 (n, usage["input_tokens"], usage["output_tokens"], usage["cache_read_input_tokens"], user_id, day))


# ---------- Questions du recruteur : brouillons de réponse (lot 5) ----------
# Claude propose, le candidat décide : rien n'est enregistré ni envoyé sans sa relecture.

ANSWER_INSTRUCTIONS = """Tu aides un candidat à répondre aux questions d'un formulaire de candidature.
Pour chaque question, propose un brouillon de réponse, écrit à la première personne, dans la langue de la question.
Règles :
- N'utilise QUE les informations du profil et de l'offre fournis. N'invente jamais d'expérience, de diplôme, de
  chiffre, de date, de nationalité ou d'autorisation de travail.
- Question factuelle (disponibilité, LinkedIn, ville, langues…) : réponse courte tirée du profil.
- Question de motivation : 2 à 4 phrases sobres qui relient le profil aux missions de l'offre.
- Si l'information nécessaire manque (ex. autorisation de travail, prétentions salariales, handicap, situation
  personnelle) : laisse "answer" vide et indique dans "missing" ce que le candidat doit préciser lui-même.
Réponds pour toutes les questions, en recopiant exactement leur intitulé dans "label"."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "answer": {"type": "string"}, "missing": {"type": "string"}},
                "required": ["label", "answer", "missing"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}
ANSWER_COST = 5          # une demande de brouillons compte comme 5 offres dans le plafond quotidien


def _fake_answers(labels: list, ctx: dict) -> tuple:
    p = ctx["profile"]
    out = []
    for l in labels:
        if "linkedin" in l.lower():
            out.append({"label": l, "answer": p.get("linkedin", ""), "missing": "" if p.get("linkedin") else "ton profil LinkedIn"})
        elif any(w in l.lower() for w in ("autoris", "authoriz", "visa", "salaire", "salary")):
            out.append({"label": l, "answer": "", "missing": "information personnelle : à préciser par toi"})
        else:
            out.append({"label": l, "answer": f"(simulation) Brouillon pour « {l} » à partir de ton profil.", "missing": ""})
    return out, {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}


def suggest_answers(labels: list, ctx: dict, offer: dict) -> tuple:
    """Brouillons de réponse aux questions du recruteur. Renvoie ([{label, answer, missing}], usage)."""
    if FAKE:
        return _fake_answers(labels, ctx)
    p = ctx["profile"]
    extra = "\n".join(f"- {k} : {v}" for k, v in (("Prénom", p.get("first_name")), ("LinkedIn", p.get("linkedin")),
                                                   ("Disponibilité", p.get("availability"))) if v)
    resp = client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        system=[{"type": "text", "text": ANSWER_INSTRUCTIONS},
                {"type": "text", "text": profile_text(ctx) + ("\n" + extra if extra else ""), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _offer_text(offer) + "\n\nQuestions du formulaire :\n" + "\n".join(f"- {l}" for l in labels)}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude a refusé de proposer des réponses.")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Réponse de Claude incomplète (limite de longueur).")
    text = next(b.text for b in resp.content if b.type == "text")
    u = resp.usage
    usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
    wanted = set(labels)
    return [a for a in json.loads(text)["answers"] if a.get("label") in wanted], usage
