"""Données d'entraînement générées : formulaires de candidature et petits sites carrières, avec la bonne réponse.

Sert à :
- démarrer l'apprentissage sans aucune page étiquetée à la main ;
- tester l'exploration autonome sur des sites locaux (aucun vrai site visité).
"""
from __future__ import annotations

import random
from pathlib import Path

from extraction import application_fields, extract_fields

# Pour chaque étiquette : libellés possibles (français / anglais), noms techniques et type HTML.
FIELDS = {
    "first_name": (["Prénom", "First name", "Given name", "Votre prénom"], ["firstName", "first_name", "fname", "prenom", "givenName"], "text"),
    "last_name": (["Nom", "Last name", "Family name", "Nom de famille", "Surname"], ["lastName", "last_name", "lname", "nom", "familyName"], "text"),
    "full_name": (["Nom complet", "Full name", "Name", "Nom et prénom"], ["fullName", "name", "full_name", "candidate_name"], "text"),
    "email": (["E-mail", "Email address", "Adresse e-mail", "Courriel", "Your email"], ["email", "mail", "courriel", "emailAddress"], "email"),
    "phone": (["Téléphone", "Phone", "Mobile phone", "Numéro de téléphone", "Portable"], ["phone", "tel", "mobile", "telephone", "phoneNumber"], "tel"),
    "linkedin": (["Profil LinkedIn", "LinkedIn URL", "LinkedIn", "Lien vers ton LinkedIn", "LinkedIn profile URL"], ["linkedin", "linkedin_url", "urls[LinkedIn]", "question_li"], "url"),
    "city": (["Ville", "City", "Adresse", "Location", "Code postal", "Ville actuelle", "Location (city)", "Postal code",
              "Street address", "Département", "Lieu de résidence"], ["city", "ville", "location", "address", "zip", "postal"], "text"),
    "cv": (["CV", "Votre CV", "Resume/CV", "Curriculum vitae", "Upload your resume", "Joindre votre CV"], ["resume", "cv", "file_cv", "attachment_resume"], "file"),
    "cover_file": (["Lettre de motivation (fichier)", "Cover letter", "Joindre votre lettre de motivation", "Upload cover letter"], ["cover_letter", "lettre", "file_lm", "coverLetter"], "file"),
    "cover_text": (["Lettre de motivation", "Message au recruteur", "Cover letter (text)", "Why do you want to join us?", "Pourquoi nous rejoindre ?"], ["motivation", "message", "coverLetterText", "why_us"], "textarea"),
    "gender": (["Civilité", "Genre", "Gender", "Salutation"], ["gender", "civilite", "salutation", "genre"], "select"),
    "availability": (["Date de disponibilité", "Available from", "Start date", "Disponible à partir du"], ["availability", "start_date", "dispo", "availableFrom"], "date"),
    "school": (["École", "School", "Université", "Diplôme préparé", "Education"], ["school", "ecole", "university", "degree"], "text"),
    "consent": (["J'accepte la politique de confidentialité", "I agree to the privacy policy", "J'accepte le traitement de mes données (RGPD)"], ["consent", "gdpr", "privacy", "rgpd"], "checkbox"),
    "other": (["Comment avez-vous connu ce poste ?", "Salaire souhaité", "Nationalité", "How did you hear about us?", "Permis de conduire"], ["source", "salary", "nationality", "custom_q1", "question_3"], "text"),
}
# Ce que les vrais sites mettent AUTOUR et DANS le formulaire, et qui n'est à remplir ni à cliquer (« other »).
NOISE_IN_FORM = [
    '<button type="button">{}</button>', '<a role="button"><button type="button">{}</button></a>',
]
NOISE_BUTTONS = ["Remplir avec LinkedIn", "Autofill with LinkedIn", "Autofill my application", "Joindre", "Attach", "Upload",
                 "Effacer", "Clear", "Supprimer le fichier", "Remove file", "Toggle flyout", "Select country", "Ajouter",
                 "Add another", "Enter manually", "Saisir manuellement", "Dropbox", "Google Drive", "Afficher plus", "Show more"]
NOISE_SELECTS = [("Pays", "country"), ("Country *", "country"), ("Langue", "language"), ("Preferred language", "language"),
                 ("Nationalité", "nationality"), ("Indicatif", "phone_country"), ("Country code", "country_code")]
NOISE_TEXTS = [("How did you hear about us?", "source"), ("Salaire souhaité", "salary"), ("Website", "website"),
               ("Portfolio", "portfolio"), ("Pronouns", "pronouns"), ("Entreprise actuelle", "org"), ("Prénom d'usage", "preferred"),
               ("Nickname", "nickname"), ("GitLab / GitHub", "git"), ("Poste", "extension"), ("Numéro de rue", "street_number"),
               ("Êtes-vous autorisé(e) à travailler en France ?", "q_work"), ("Do you need a work permit?", "q_permit"),
               ("Seriez-vous prêt(e) à déménager ?", "q_move"), ("Have you applied to us before?", "q_before"),
               ("Nom de votre référent", "referrer"), ("Who referred you?", "referral")]
NOISE_TEXTAREAS = ["Informations complémentaires", "Additional comments", "Décrivez un projet dont vous êtes fier",
                   "Tell us about a challenge you solved", "Autre chose à ajouter ?", "Accessibility needs"]
MARKETING_CHECKS = ["Recevoir des alertes emploi par SMS", "Envoyez-moi des offres par e-mail", "Join our talent community",
                    "Keep me informed about future openings", "Enregistrer mes réponses pour mes prochaines candidatures",
                    "Recevoir la newsletter", "Notify me via WhatsApp"]
COOKIE_BUTTONS = ["Accepter tout", "Tout refuser", "Accept all cookies", "Reject all", "Manage preferences", "Personnaliser",
                  "Continue without accepting", "Paramètres des cookies"]
NAV_BUTTONS = ["Menu", "English", "Français", "Mes offres sauvegardées", "My Saved Jobs", "Connexion", "Sign in", "Search",
               "Rechercher", "Navigation menu", "Close", "Fermer", "Share", "Partager", "Save Job"]

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
    # Éléments parasites glissés dans le formulaire : à ne jamais remplir ni cliquer.
    labels = list(labels)
    for _ in range(rng.randint(0, 4)):
        labels.insert(rng.randrange(len(labels) + 1), "_noise")
    for i, lab in enumerate(labels):
        if lab == "_noise":
            kind, n = rng.random(), 1
            if kind < 0.25:
                parts.append(rng.choice(NOISE_IN_FORM).format(rng.choice(NOISE_BUTTONS)))
            elif kind < 0.4:
                text, name = rng.choice(NOISE_SELECTS)
                parts.append(f'<label>{text}<select name="{name}"><option>—</option><option>France</option></select></label>')
            elif kind < 0.6:
                text, name = rng.choice(NOISE_TEXTS)
                parts.append(f'<label>{text}<input name="{name}"></label>')
            elif kind < 0.72:     # liste déroulante « maison » : champ, bouton d'ouverture, champ de saisie sans libellé
                text, name = rng.choice(NOISE_TEXTS + NOISE_SELECTS)
                parts.append(f'<label>{text}<input name="{name}"></label><button type="button">Toggle flyout</button><input>')
                n = 3
            elif kind < 0.84:     # cases marketing (pas un consentement légal)
                parts.append(f'<label><input type="checkbox" name="optin{i}"> {rng.choice(MARKETING_CHECKS)}</label>')
            elif kind < 0.92:     # question oui / non
                q = rng.choice(NOISE_TEXTS)[0]
                parts.append(f'<p>{q}</p><label><input type="radio" name="r{i}"> {rng.choice(["Oui", "Yes"])}</label>'
                             f'<label><input type="radio" name="r{i}"> {rng.choice(["Non", "No"])}</label>')
                n = 2
            else:
                parts.append(f'<label>{rng.choice(NOISE_TEXTAREAS)}<textarea name="info{i}"></textarea></label>')
            truth.extend(["other"] * n)
            continue
        texts, names, kind = FIELDS[lab]
        text, name, fid = rng.choice(texts), rng.choice(names), f"f{rng.randint(100, 999)}_{i}"
        style = rng.random()
        if kind == "textarea":
            field = f'<textarea id="{fid}" name="{name}"></textarea>'
        elif kind == "select" and rng.random() < 0.35:   # genre en liste « maison » (champ + bouton)
            parts.append(f'<label>{text}<input id="{fid}" name="{name}"></label><button type="button">Toggle flyout</button>')
            truth.extend([lab, "other"])
            continue
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
    if rng.random() < 0.25:      # formulaire en plusieurs étapes : le dernier bouton est « Suivant » (type submit)
        parts.append(f'<button type="submit">{rng.choice(BUTTONS["next"])}</button></form>')
        truth.append("next")
        return "".join(parts), truth
    if rng.random() < 0.3:
        parts.append(f'<button type="button">{rng.choice(BUTTONS["next"])}</button>')
        truth.append("next")
    parts.append(f'<button type="submit">{rng.choice(BUTTONS["submit"])}</button></form>')
    truth.append("submit")
    return "".join(parts), truth


def page_html(rng: random.Random, body: str) -> str:
    """Page complète comme sur un vrai site : menu, recherche, bandeau cookies, contenu, pied de page + newsletter."""
    nav = "".join(f"<button>{b}</button>" for b in rng.sample(NAV_BUTTONS, 4))
    search = '<form role="search"><input type="search" placeholder="Rechercher une offre"><input placeholder="Ville, région"><button>Rechercher</button></form>' \
        if rng.random() < 0.6 else ""
    cookie = f'<div id="{rng.choice(["onetrust-banner-sdk", "didomi-popup", "cookie-banner", "axeptio_overlay"])}">' + \
        "".join(f"<button>{b}</button>" for b in rng.sample(COOKIE_BUTTONS, 3)) + "</div>" if rng.random() < 0.7 else ""
    footer = '<footer><form><input type="email" placeholder="Votre e-mail"><button>S\'abonner</button></form></footer>' \
        if rng.random() < 0.5 else "<footer><a href='#'>Mentions légales</a></footer>"
    return f"<header>{nav}{search}</header>{cookie}<main>{body}</main>{footer}"


def labeled_pages(n: int, seed: int = 0) -> list:
    """n formulaires au format des pages collectées (champs du formulaire de candidature + étiquette vérifiée)."""
    rng, pages = random.Random(seed), []
    for i in range(n):
        form, truth = form_html(rng)
        fields = application_fields(extract_fields(page_html(rng, form)))
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
        (folder / f"postuler{k}.html").write_text(page("Candidature", page_html(rng, form_html(rng)[0])), encoding="utf-8")
        offers.append(link(f"offre{k}.html", f"Stage analyste financier {k}"))
    (folder / "carrieres.html").write_text(page("Carrières", "".join(nav[:2] + offers + nav[2:4])), encoding="utf-8")
    return folder / "index.html"
