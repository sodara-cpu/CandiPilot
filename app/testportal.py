"""Faux portail JobTeaser et faux formulaire de candidature (tests uniquement).

Activé si CANDIPILOT_FAKE_ROBOT=1 ou CANDIPILOT_TEST_PORTAL=1. Reproduit la structure réelle de
JobTeaser (mêmes data-testid que src/adapters/jobteaser.py) : connexion, liste d'offres, page
d'offre, panneau « Postuler à l'offre » (genre, CV, lettre, questions, « Postuler maintenant »).
Les candidatures reçues sont visibles sur /fake-jobteaser/_submissions.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
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
