"""Données d'entraînement générées : formulaires de candidature et petits sites carrières, avec la bonne réponse.

Sert à :
- démarrer l'apprentissage sans aucune page étiquetée à la main ;
- tester l'exploration autonome sur des sites locaux (aucun vrai site visité).
"""
from __future__ import annotations

import random
from pathlib import Path

from extraction import extract_fields

# Pour chaque étiquette : libellés possibles (français / anglais), noms techniques et type HTML.
FIELDS = {
    "first_name": (["Prénom", "First name", "Given name", "Votre prénom"], ["firstName", "first_name", "fname", "prenom", "givenName"], "text"),
    "last_name": (["Nom", "Last name", "Family name", "Nom de famille", "Surname"], ["lastName", "last_name", "lname", "nom", "familyName"], "text"),
    "full_name": (["Nom complet", "Full name", "Name", "Nom et prénom"], ["fullName", "name", "full_name", "candidate_name"], "text"),
    "email": (["E-mail", "Email address", "Adresse e-mail", "Courriel", "Your email"], ["email", "mail", "courriel", "emailAddress"], "email"),
    "phone": (["Téléphone", "Phone", "Mobile phone", "Numéro de téléphone", "Portable"], ["phone", "tel", "mobile", "telephone", "phoneNumber"], "tel"),
    "linkedin": (["Profil LinkedIn", "LinkedIn URL", "LinkedIn"], ["linkedin", "linkedin_url", "urls[LinkedIn]"], "url"),
    "city": (["Ville", "City", "Adresse", "Location", "Code postal"], ["city", "ville", "location", "address", "zip"], "text"),
    "cv": (["CV", "Votre CV", "Resume/CV", "Curriculum vitae", "Upload your resume", "Joindre votre CV"], ["resume", "cv", "file_cv", "attachment_resume"], "file"),
    "cover_file": (["Lettre de motivation (fichier)", "Cover letter", "Joindre votre lettre de motivation", "Upload cover letter"], ["cover_letter", "lettre", "file_lm", "coverLetter"], "file"),
    "cover_text": (["Lettre de motivation", "Message au recruteur", "Cover letter (text)", "Why do you want to join us?", "Pourquoi nous rejoindre ?"], ["motivation", "message", "coverLetterText", "why_us"], "textarea"),
    "gender": (["Civilité", "Genre", "Gender", "Salutation"], ["gender", "civilite", "salutation", "genre"], "select"),
    "availability": (["Date de disponibilité", "Available from", "Start date", "Disponible à partir du"], ["availability", "start_date", "dispo", "availableFrom"], "date"),
    "school": (["École", "School", "Université", "Diplôme préparé", "Education"], ["school", "ecole", "university", "degree"], "text"),
    "consent": (["J'accepte la politique de confidentialité", "I agree to the privacy policy", "J'accepte le traitement de mes données (RGPD)"], ["consent", "gdpr", "privacy", "rgpd"], "checkbox"),
    "other": (["Comment avez-vous connu ce poste ?", "Salaire souhaité", "Nationalité", "How did you hear about us?", "Permis de conduire"], ["source", "salary", "nationality", "custom_q1", "question_3"], "text"),
}
BUTTONS = {"submit": ["Postuler", "Envoyer ma candidature", "Submit application", "Apply", "Soumettre"],
           "next": ["Suivant", "Continuer", "Next", "Continue"]}
NAV = {  # Liens : texte des liens utiles et des liens « pièges » (sans formulaire derrière).
    "careers": ["Carrières", "Nous rejoindre", "Careers", "Join us", "Offres d'emploi", "Jobs"],
    "apply": ["Postuler", "Postuler à cette offre", "Apply now", "Apply for this job", "Je postule", "Candidater"],
    "noise": ["Actualités", "Qui sommes-nous", "Mentions légales", "Contact", "Presse", "Nos engagements",
              "Investisseurs", "Partager sur LinkedIn", "Plan du site", "Cookies", "News", "About us", "Blog"],
}


def form_html(rng: random.Random, labels=None) -> tuple:
    """Un formulaire aléatoire ; renvoie (html, étiquettes dans l'ordre des champs)."""
    if labels is None:
        names = rng.choice([["first_name", "last_name"], ["full_name"]])
        extra = rng.sample(["phone", "linkedin", "city", "cover_file", "cover_text", "gender", "availability", "school", "other", "other"], rng.randint(2, 6))
        labels = names + ["email", "cv"] + extra + (["consent"] if rng.random() < 0.6 else [])
        rng.shuffle(labels)
    parts, truth = [f"<h2>{rng.choice(['Votre candidature', 'Apply', 'Formulaire de candidature', 'Postuler'])}</h2><form>"], []
    for i, lab in enumerate(labels):
        texts, names, kind = FIELDS[lab]
        text, name, fid = rng.choice(texts), rng.choice(names), f"f{rng.randint(100, 999)}_{i}"
        style = rng.random()
        if kind == "textarea":
            field = f'<textarea id="{fid}" name="{name}"></textarea>'
        elif kind == "select":
            field = f'<select id="{fid}" name="{name}"><option value="">—</option><option>M.</option><option>Mme</option></select>'
        elif kind == "checkbox":
            parts.append(f'<label><input type="checkbox" name="{name}"> {text}</label>')
            truth.append(lab)
            continue
        else:
            field = f'<input type="{kind if kind != "date" or rng.random() < .5 else "text"}" id="{fid}" name="{name if rng.random() < .8 else fid}">'
        if style < 0.5:
            parts.append(f'<div class="row"><label for="{fid}">{text}{" *" if rng.random() < .5 else ""}</label>{field}</div>')
        elif style < 0.8 and kind not in ("select", "textarea"):
            with_placeholder = field.replace("<input ", '<input placeholder="%s" ' % text)
            parts.append(f'<div class="row"><span>{rng.choice(["", "Requis"])}</span>{with_placeholder}</div>')
        else:
            parts.append(f'<label>{text}{field}</label>')
        truth.append(lab)
    if rng.random() < 0.3:
        parts.append(f'<button type="button">{rng.choice(BUTTONS["next"])}</button>')
        truth.append("next")
    parts.append(f'<button type="submit">{rng.choice(BUTTONS["submit"])}</button></form>')
    truth.append("submit")
    return "".join(parts), truth


def labeled_pages(n: int, seed: int = 0) -> list:
    """n formulaires au format des pages collectées (champs + étiquette vérifiée)."""
    rng, pages = random.Random(seed), []
    for i in range(n):
        html, truth = form_html(rng)
        fields = extract_fields(html)
        assert len(fields) == len(truth), (len(fields), len(truth))
        for f, lab in zip(fields, truth):
            f["etiquette"] = lab
        pages.append({"url": f"synthetique://{seed}/{i}", "site": "synthetique", "verifiee": True, "champs": fields})
    return pages


def build_site(folder: Path, seed: int, n_offers: int = 4) -> Path:
    """Petit site carrières statique : accueil → carrières → offres → formulaire, plus des pages pièges."""
    rng = random.Random(seed)
    folder.mkdir(parents=True, exist_ok=True)
    page = lambda title, body: f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>"
    link = lambda href, text: f'<p><a href="{href}">{text}</a></p>'
    # Pages pièges reliées entre elles : un explorateur au hasard s'y perd.
    noise = [f"{t} {k}" if k else t for k in range(2) for t in rng.sample(NAV["noise"], 6)]
    for i, t in enumerate(noise):
        others = [link(f"info{j}.html", noise[j]) for j in rng.sample(range(len(noise)), 3) if j != i]
        (folder / f"info{i}.html").write_text(page(t, "<p>Page d'information.</p>" + "".join(others) + link("index.html", "Accueil")), encoding="utf-8")
    nav = [link(f"info{i}.html", t) for i, t in enumerate(noise)][:5]
    home = nav[:3] + [link("carrieres.html", rng.choice(NAV["careers"]))] + nav[3:]
    rng.shuffle(home)
    (folder / "index.html").write_text(page("Accueil", "".join(home)), encoding="utf-8")
    offers = []
    for k in range(n_offers):
        apply_text = rng.choice(NAV["apply"])
        body = [link(f"info{j}.html", noise[j]) for j in rng.sample(range(len(noise)), 3)]
        body.insert(rng.randrange(4), link(f"postuler{k}.html", apply_text))
        (folder / f"offre{k}.html").write_text(page(f"Analyste {k}", "<p>Description du poste.</p>" + "".join(body)), encoding="utf-8")
        (folder / f"postuler{k}.html").write_text(page("Candidature", form_html(rng)[0]), encoding="utf-8")
        offers.append(link(f"offre{k}.html", f"Stage analyste financier {k}"))
    (folder / "carrieres.html").write_text(page("Carrières", "".join(nav[:2] + offers + nav[2:4])), encoding="utf-8")
    return folder / "index.html"
