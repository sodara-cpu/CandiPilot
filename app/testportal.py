"""Faux portail JobTeaser et faux formulaire de candidature (tests uniquement).

Activé si CANDIPILOT_FAKE_ROBOT=1 ou CANDIPILOT_TEST_PORTAL=1. Reproduit la structure réelle de
JobTeaser (mêmes data-testid que src/adapters/jobteaser.py) : connexion, liste d'offres, page
d'offre, panneau « Postuler à l'offre » (genre, CV, lettre, questions, « Postuler maintenant »).
Les candidatures reçues sont visibles sur /fake-jobteaser/_submissions.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
P = "/fake-jobteaser"
T = "jobad-DetailView__"
OFFERS = {
    "101": {"title": "Analyste M&A - Mars 2027", "company": "Banque Orsay", "contract": "Stage", "location": "Paris, France",
            "start": "mars 2027", "easy": True, "letter": True, "questions": []},
    "102": {"title": "Analyste Private Debt (H/F)", "company": "Acme SA", "contract": "Stage", "location": "Paris, France",
            "start": "avril 2027", "easy": True, "letter": False, "questions": ["Pourquoi notre fonds ?"]},
    "103": {"title": "Analyste crédit", "company": "Groupe Externe", "contract": "Stage", "location": "Lyon, France",
            "start": "mars 2027", "easy": False, "letter": False, "questions": []},
    "104": {"title": "Assistant trésorerie", "company": "Trop Tôt SA", "contract": "Stage", "location": "Paris, France",
            "start": "septembre 2026", "easy": True, "letter": False, "questions": []},
}
SUBMISSIONS: list = []


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head>"
                        f"<body style='font:15px sans-serif;margin:24px'>{body}</body></html>")


def _logged(request: Request) -> bool:
    return request.cookies.get("fakejt") == "1"


@router.get(P + "/login")
def login_page():
    return _page("Connexion", f"""<form method="post" action="{P}/login" style="margin:60px">
      <input name="email" placeholder="E-mail" style="display:block;font-size:20px;margin:10px;width:400px">
      <input name="password" type="password" placeholder="Mot de passe" style="display:block;font-size:20px;margin:10px;width:400px">
      <button style="font-size:20px;margin:10px;width:400px;height:50px">Se connecter</button></form>""")


@router.post(P + "/login")
def login(password: str = Form("")):
    r = RedirectResponse(P + "/fr/job-offers", status_code=303)
    if password == "bonmotdepasse":
        r.set_cookie("fakejt", "1", max_age=3600)
    return r


@router.get(P + "/fr/job-offers")
def offers(request: Request, q: str = "", page: int = 1):
    if not _logged(request):
        return RedirectResponse(P + "/login")
    cards = "" if page > 1 else "".join(f"""
      <div data-testid="jobad-card" style="border:1px solid #ccc;margin:8px;padding:8px">
        <h3><a href="{P}/fr/job-offers/{i}">{o['title']}</a></h3>
        <div data-testid="jobad-card-company-name">{o['company']}</div>
        <div data-testid="jobad-card-contract">{o['contract']}</div>
        <div data-testid="jobad-card-location">{o['location']}</div>
        {'<div data-testid="jobad-card-internal">Candidature simplifiée</div>' if o['easy'] else ''}
      </div>""" for i, o in OFFERS.items())
    return _page("JobTeaser | Offres d'emplois", f"<h1>Offres ({q})</h1>{cards}")


@router.get(P + "/fr/job-offers/{oid}")
def offer(oid: str, request: Request):
    o = OFFERS.get(oid)
    if not o:
        return _page("Introuvable", "Offre introuvable")
    if not _logged(request):
        return RedirectResponse(P + "/login")
    if request.cookies.get(f"applied{oid}") == "1":
        action = "<p>Vous avez postulé à cette offre</p>"
    elif o["easy"]:
        action = f'<button data-testid="{T}CandidateActions__Buttons_apply_internal_candidacy" id="apply">Candidature simplifiée</button>'
    else:
        action = f'<button data-testid="{T}CandidateActions__Buttons_apply_external_candidacy">Postuler ↗</button>'
    questions = "".join(f'<div data-testid="input-wrapper-{k}"><label>{q}</label><textarea name="q{k}"></textarea></div>'
                        for k, q in enumerate(o["questions"]))
    return _page(o["title"], f"""
      <h1>{o['title']}</h1><p>{o['company']}</p>
      <div data-testid="{T}CandidacyDetails__start_date">Date de début {o['start']}</div>
      {action}
      {'<div class="g-recaptcha" data-sitekey="test"></div>' if oid in CAPTCHA else ''}
      <section role="dialog" id="dlg" style="display:none;border:2px solid #333;padding:12px;margin-top:12px">
        <h2>Postuler à l'offre</h2>
        <div id="genderBlock">
          <input type="hidden" data-testid="hiddenInput-gender" id="genderValue" value="">
          <div id="gender" role="combobox" tabindex="0" style="border:1px solid #999;padding:4px;width:200px">Genre</div>
          <div id="gender-dropdown" style="display:none">
            <div data-value="GENDER_MALE">Homme</div><div data-value="GENDER_FEMALE">Femme</div><div data-value="GENDER_OTHER">Autre</div>
          </div>
          <button type="button" id="saveInfo">Sauvegarder les informations</button>
        </div>
        <div id="cvZone">CV actuel : ancien_cv.pdf
          <button type="button" data-testid="{T}ApplicationFlow__Buttons__use_another_resume" id="otherCv">Utiliser un autre CV</button></div>
        {'<label>Lettre de motivation<input type="file" name="coverLetterContent" id="cover"></label><div id="coverName"></div>' if o['letter'] else ''}
        <form data-testid="ScreeningQuestions" id="sq" style="display:none">{questions}</form>
        {f'<button type="button" data-testid="{T}ApplicationFlow__Buttons__continue_button" id="cont">Continuer</button>' if o['questions'] else ''}
        <button type="button" data-testid="{T}ApplicationFlow__Buttons__apply_button" id="send" style="{'display:none' if o['questions'] else ''}">Postuler maintenant</button>
      </section>
      <script>
        const $ = (s) => document.querySelector(s);
        $('#apply') && ($('#apply').onclick = () => {{ $('#dlg').style.display = 'block'; }});
        $('#gender').onclick = () => {{ $('#gender-dropdown').style.display = 'block'; }};
        document.querySelectorAll('#gender-dropdown [data-value]').forEach(d => d.onclick = () => {{
          $('#genderValue').value = d.dataset.value; $('#gender').textContent = d.textContent; $('#gender-dropdown').style.display = 'none'; }});
        $('#saveInfo').onclick = () => {{ if ($('#genderValue').value) {{ window.savedGender = $('#genderValue').value; $('#genderBlock').remove(); }} }};
        $('#otherCv').onclick = () => {{
          if ($('#resume')) return;
          const i = document.createElement('input'); i.type = 'file'; i.name = 'resume'; i.id = 'resume';
          i.onchange = () => {{ $('#cvZone').append(' Nouveau CV : ' + i.files[0].name); }};
          $('#cvZone').append(i); }};
        $('#cover') && ($('#cover').onchange = () => {{ $('#coverName').textContent = $('#cover').files[0].name; }});
        $('#cont') && ($('#cont').onclick = () => {{ $('#sq').style.display = 'block'; $('#send').style.display = 'inline'; }});
        $('#send').onclick = async () => {{
          if ($('#genderBlock')) {{ alert('Genre manquant'); return; }}
          const answers = [...document.querySelectorAll('#sq textarea')].map(t => t.value);
          const body = {{ offer: '{oid}', gender: window.savedGender || '', resume: $('#resume')?.files[0]?.name || '',
            resume_size: $('#resume')?.files[0]?.size || 0, cover: $('#cover')?.files[0]?.name || '', answers }};
          await fetch('{P}/_submit', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(body) }});
          document.cookie = 'applied{oid}=1; path=/';
          $('#dlg').innerHTML = '<p>Vous avez postulé à cette offre</p>';
        }};
      </script>""")


@router.post(P + "/_submit")
async def submit(request: Request):
    SUBMISSIONS.append(await request.json())
    return {"ok": True}


CAPTCHA: set = set()        # offres sur lesquelles le site affiche une vérification anti-robot


@router.post(P + "/_captcha/{oid}")
def toggle_captcha(oid: str, on: int = 1):
    (CAPTCHA.add if on else CAPTCHA.discard)(oid)
    return {"captcha": sorted(CAPTCHA)}


@router.get(P + "/_submissions")
def submissions():
    return JSONResponse(SUBMISSIONS)


@router.get("/fake-form")
def fake_form():
    """Formulaire de candidature d'un site quelconque (remplissage par l'extension)."""
    return _page("Candidature — Société Exemple", """
      <h1>Postuler : Analyste financier</h1>
      <form id="f" onsubmit="window.submitted = true; return false;">
        <label for="fn">Prénom *</label><input id="fn" name="firstname"><br>
        <label for="ln">Nom de famille *</label><input id="ln" name="lastname"><br>
        <input type="email" name="courriel" placeholder="Adresse e-mail"><br>
        <input type="tel" aria-label="Téléphone portable"><br>
        <label>Profil LinkedIn<input name="linkedin_url"></label><br>
        <label>Ville<input name="city"></label><br>
        <label for="cvf">Votre CV (PDF)</label><input type="file" id="cvf" accept=".pdf"><br>
        <label for="q1">Comment avez-vous connu ce poste ?</label><input id="q1" name="source"><br>
        <label><input type="checkbox" name="rgpd"> J'accepte la politique de confidentialité</label><br>
        <button type="submit">Envoyer ma candidature</button>
      </form>""")


_SUBMIT = """<button type="submit">Submit application</button></form>"""


@router.get("/fake-ats/apply/greenhouse")
def fake_apply_greenhouse():
    """Formulaire au balisage de Greenhouse (job-boards) : champ du CV caché derrière un bouton, questions maison."""
    return _page("Apply — Acme (Greenhouse)", """
      <form id="application-form" class="application--form" onsubmit="window.submitted = true; return false;">
        <label for="first_name">First Name*</label><input id="first_name" type="text"><br>
        <label for="last_name">Last Name*</label><input id="last_name" type="text"><br>
        <label for="email">Email*</label><input id="email" type="text"><br>
        <label for="phone">Phone</label><input id="phone" type="text"><br>
        <div class="file-upload"><label id="upload-label-resume" for="resume">Resume/CV*</label>
          <button type="button">Attach</button><input id="resume" type="file" style="display:none"></div>
        <label for="question_901">LinkedIn Profile</label><input id="question_901" type="text"><br>
        <label for="question_902">Are you legally authorized to work in France?*</label><input id="question_902" type="text"><br>
        <label><input type="checkbox" id="gdpr"> I consent to the processing of my data</label><br>
        """ + _SUBMIT)


@router.get("/fake-ats/apply/lever")
def fake_apply_lever():
    """Formulaire au balisage de Lever : nom complet en un seul champ, liens nommés urls[...]."""
    return _page("Acme — Finance Intern (Lever)", """
      <form id="application-form" onsubmit="window.submitted = true; return false;">
        <div class="application-question"><div class="application-label">Resume/CV</div><input type="file" name="resume"></div>
        <div class="application-question"><div class="application-label">Full name</div><input type="text" name="name"></div>
        <div class="application-question"><div class="application-label">Email</div><input type="email" name="email"></div>
        <div class="application-question"><div class="application-label">Phone</div><input type="text" name="phone"></div>
        <div class="application-question"><div class="application-label">Current company</div><input type="text" name="org"></div>
        <div class="application-question"><div class="application-label">LinkedIn URL</div><input type="text" name="urls[LinkedIn]"></div>
        """ + _SUBMIT)


# Balisage reconnu par les adaptateurs de l'extension (attributs propres à chaque plateforme).
_ATS_FORMS = {
    "workday": """<label>Given Name</label><input data-automation-id="legalNameSection_firstName">
        <label>Family Name</label><input data-automation-id="legalNameSection_lastName">
        <label>Email Address</label><input data-automation-id="email"><label>Phone Number</label><input data-automation-id="phone-number">
        <input type="file" data-automation-id="file-upload-input-ref" style="display:none">""",
    "smartrecruiters": """<sr-form id="sr"></sr-form><script>customElements.define("sr-form", class extends HTMLElement {
        constructor() { super(); this.attachShadow({mode: "open"}).innerHTML = '<input id="first-name-input"><input id="last-name-input">' +
          '<input id="email-input"><input id="phone-number-input">'; } });</script>""",
    "workable": """<input name="firstname"><input name="lastname"><input name="email"><input name="phone">
        <div data-ui="resume"><input type="file"></div>""",
    "ashby": """<input name="_systemfield_name"><input name="_systemfield_email"><input type="file" name="_systemfield_resume">""",
    "teamtailor": """<input name="candidate[first_name]"><input name="candidate[last_name]"><input name="candidate[email]">
        <input name="candidate[phone]"><input type="file" name="candidate[resume]">""",
}


@router.get("/fake-ats/apply/{platform}")
def fake_apply_ats(platform: str):
    if platform not in _ATS_FORMS:
        raise HTTPException(404)
    marker = ' data-ui="application-form"' if platform == "workable" else ""
    return _page(f"Apply ({platform})", f"""<form id="f"{marker} onsubmit="window.submitted = true; return false;">
        {_ATS_FORMS[platform]}<button type="submit">Submit</button></form>""")


@router.get("/fake-form/autre-site")
def fake_form_other_site():
    """Formulaire intégré depuis un site auquel l'extension n'a pas encore accès : l'autorisation sera demandée."""
    return _page("Carrières — Exemple", """<iframe src="https://apply.example.org/job/1" style="width:600px;height:200px"></iframe>""")


# ---------- Faux site carrières (Workday, site maison) : lu comme un moteur de recherche ----------
CAREERS = {"R1": ("Paris", "Stage Analyste Crédit", "INTERN"), "R2": ("Madrid", "Finance Intern", "INTERN"),
           "R4": ("Lyon", "Stage Contrôle de gestion", "INTERN")}


@router.get("/robots.txt")
def robots(request: Request):
    base = str(request.base_url).rstrip("/")
    return HTMLResponse(f"User-agent: *\nDisallow: /fake-careers/private/\nSitemap: {base}/fake-careers/sitemap.xml\n",
                        media_type="text/plain")


@router.get("/fake-careers/sitemap.xml")
def careers_index(request: Request):
    base = str(request.base_url).rstrip("/")
    return HTMLResponse(f'<?xml version="1.0"?><sitemapindex><sitemap><loc>{base}/fake-careers/sitemap-jobs.xml</loc></sitemap></sitemapindex>',
                        media_type="application/xml")


@router.get("/fake-careers/sitemap-jobs.xml")
def careers_jobs(request: Request):
    base = str(request.base_url).rstrip("/")
    urls = [f"{base}/fake-careers/job/{c}/{t.replace(' ', '-')}_{k}" for k, (c, t, _) in CAREERS.items()]
    urls.append(f"{base}/fake-careers/private/job/Paris/Secret_R3")       # interdite par robots.txt
    return HTMLResponse('<?xml version="1.0"?><urlset>' + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>",
                        media_type="application/xml")


@router.get("/fake-careers/job/{city}/{slug}")
def careers_job(city: str, slug: str):
    ref = slug.rsplit("_", 1)[-1]
    c, title, kind = CAREERS[ref]
    ld = json.dumps({"@context": "https://schema.org", "@type": "JobPosting", "title": title, "employmentType": kind,
                     "hiringOrganization": {"@type": "Organization", "name": "(0042) Maison Y Filiale SAS"},
                     "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": c,
                                                                   "addressCountry": {"@type": "Country", "name": "FR" if c != "Madrid" else "ES"}}},
                     "identifier": {"@type": "PropertyValue", "value": ref}, "description": "<p>Analyse, Excel, reporting.</p>"})
    return _page(title, f'<script type="application/ld+json">{ld}</script><h1>{title}</h1>')


@router.get("/fake-careers/private/job/{city}/{slug}")
def careers_private(city: str, slug: str):
    raise HTTPException(500, "Page interdite aux robots : ne doit jamais être lue")


@router.get("/fake-form/etapes")
def fake_form_steps():
    """Formulaire en deux étapes ; l'étape 1 contient un composant (shadow DOM). L'étape 2 n'existe qu'après « Suivant »."""
    return _page("Candidature en étapes", """
      <form id="f" onsubmit="window.submitted = true; return false;">
        <div id="step"><h2>Étape 1 sur 2</h2>
          <label for="fn">Prénom</label><input id="fn"><br>
          <cp-field id="comp"></cp-field></div>
        <button type="button" id="next">Suivant</button>
      </form>
      <script>
        customElements.define("cp-field", class extends HTMLElement {
          constructor() { super(); this.attachShadow({mode: "open"}).innerHTML =
            '<label for="mail">Adresse e-mail</label><input id="mail" name="mail">'; }
        });
        document.getElementById("next").onclick = () => {
          document.getElementById("step").innerHTML = '<h2>Étape 2 sur 2</h2>' +
            '<label for="tel">Téléphone</label><input id="tel"><br>' +
            '<label for="cv2">Ton CV</label><input type="file" id="cv2"><br>' +
            '<button type="submit">Envoyer</button>';
        };
      </script>""")


@router.get("/fake-form/cadre")
def fake_form_frame():
    """Page carrières qui intègre le formulaire Greenhouse dans un cadre (iframe), comme beaucoup d'entreprises."""
    return _page("Carrières — Acme", """<h1>Rejoins Acme</h1>
      <iframe id="gh" src="/fake-ats/apply/greenhouse" style="width:700px;height:600px"></iframe>""")


# ---------- Fausses API de plateformes de recrutement (découverte multi-sources, lot 2) ----------
ATS_JOBS = {   # mêmes offres vues par plusieurs plateformes pour tester les doublons
    "gh1": {"title": "Finance Intern (H/F)", "location": "Paris, France", "contract": ""},
    "gh2": {"title": "Senior Backend Engineer", "location": "Paris, France", "contract": ""},
    "gh3": {"title": "M&A Analyst Internship", "location": "London, UK", "contract": ""},
}
ATS_EXPIRED: set = set()


def _live():
    return {k: v for k, v in ATS_JOBS.items() if k not in ATS_EXPIRED}


@router.get("/fake-ats/greenhouse/{token}")
def fake_greenhouse(token: str):
    return {"jobs": [{"id": k, "title": v["title"], "absolute_url": f"https://boards.greenhouse.io/{token}/jobs/{k}",
                      "location": {"name": v["location"]}, "content": "&lt;p&gt;Analyse financière, Excel.&lt;/p&gt;",
                      "updated_at": "2026-10-20"} for k, v in _live().items()]}


@router.get("/fake-ats/lever/{token}")
def fake_lever(token: str):          # la même offre « Finance Intern » publiée aussi sur Lever (doublon)
    return [{"id": "lv1", "text": "Finance Intern", "hostedUrl": f"https://jobs.lever.co/{token}/lv1",
             "categories": {"location": "Paris, France", "commitment": "Internship"}, "descriptionPlain": "Stage finance."}]


@router.get("/fake-ats/ashby/{token}")
def fake_ashby(token: str):
    return {"jobs": [{"id": "as1", "title": "Corporate Finance Intern", "location": "Paris", "employmentType": "Intern",
                      "jobUrl": f"https://jobs.ashbyhq.com/{token}/as1", "descriptionPlain": "Stage.", "isRemote": False}]}


@router.get("/fake-ats/smartrecruiters/{token}")
def fake_smartrecruiters(token: str):
    return {"content": [{"id": "sr1", "name": "Stage Analyste Financier", "company": {"name": "Ubi Test"},
                         "location": {"city": "Lyon", "country": "fr"}, "typeOfEmployment": {"label": "Internship"}}]}


@router.get("/fake-ats/workable/{token}")
def fake_workable(token: str):
    return {"name": "Hug Test", "jobs": [{"title": "Finance Operations Intern", "shortcode": "wk1",
                                         "url": f"https://apply.workable.com/{token}/j/wk1", "city": "Paris", "country": "France",
                                         "employment_type": "Internship"}]}


@router.get("/fake-ats/page/{company}")
def fake_jsonld_page(company: str):
    data = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Stage Contrôle de gestion",
            "url": f"https://www.{company}.example/jobs/42", "hiringOrganization": {"@type": "Organization", "name": company.title()},
            "jobLocation": {"@type": "Place", "address": {"addressLocality": "Paris", "addressCountry": "FR"}},
            "employmentType": "INTERN", "description": "<p>Contrôle de gestion, finance.</p>", "datePosted": "2026-10-01"}
    return HTMLResponse(f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head><body>Carrières</body></html>')


@router.post("/fake-ats/_expire/{ref}")
def fake_expire(ref: str):
    ATS_EXPIRED.add(ref)
    return {"ok": True}
