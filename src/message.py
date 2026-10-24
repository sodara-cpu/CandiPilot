"""Génère le message de motivation adapté à une offre."""
from __future__ import annotations

from .config import Offer, Profile


def build_message(profile: Profile, offer: Offer) -> dict[str, str]:
    """Renvoie {'subject': ..., 'body': ...} personnalisé pour l'offre."""
    cl = profile.data.get("cover_letter", {})
    subject_tpl = cl.get("subject", "Candidature - Stage {role}")
    body_tpl = cl.get("body", "")

    role = offer.role or "en finance"
    domain = _domain_phrase(offer.domain)
    domain_line = profile.domain_line(offer.domain)

    fields = {
        "role": role,
        "company": offer.company or "votre société",
        "de_company": _de(offer.company or "votre société"),
        "domain": domain,
        "domain_line": domain_line,
    }
    subject = _safe_format(subject_tpl, fields)
    body = _safe_format(body_tpl, fields).strip()
    # Contractions françaises après insertion de {domain_line} : "à les" -> "aux".
    body = body.replace(" à les ", " aux ").replace(" à le ", " au ")
    return {"subject": subject, "body": body}


def _domain_phrase(domain: str) -> str:
    """Formulation naturelle du domaine pour la 1re phrase ('en <domaine>')."""
    d = (domain or "").strip()
    if not d:
        return "finance"
    return d


def _de(name: str) -> str:
    """Élision française : 'd'Eurazeo', mais 'de SCOR'."""
    return ("d'" if name[:1].lower() in "aeiouyhàâéèêëîïôöùûü" else "de ") + name


def _safe_format(tpl: str, fields: dict[str, str]) -> str:
    out = tpl
    for k, v in fields.items():
        out = out.replace("{" + k + "}", v)
    return out
