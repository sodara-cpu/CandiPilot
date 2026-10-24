"""Adaptateur JobTeaser.

- « Candidature simplifiée » (bouton apply_internal_candidacy) : panneau latéral
  « Postuler à l'offre » avec profil (genre), CV, lettre de motivation en PDF si
  le recruteur la demande, éventuelles questions du recruteur
  (ScreeningQuestions), puis « Postuler maintenant » (apply_button). Quand il y a
  des questions, « Continuer » (continue_button) mène à l'étape des questions,
  et c'est « Postuler maintenant » qui envoie.
- « Postuler » ↗ (apply_external_candidacy) : redirection vers l'ATS de
  l'entreprise -> needs_human.

Modes (paramètre `mode`) :
- "dry"      : simulation, ne clique jamais « Continuer » ni l'envoi final.
- "assisted" : remplit tout ce qu'il peut puis rend la main ; c'est la personne
               qui clique « Postuler maintenant » (voir wait_for_user_submit).
- "auto"     : remplit et envoie, puis vérifie la preuve d'envoi.

On ne devine jamais une réponse personnelle : seules les réponses fournies dans
profile.answers["screening"] (libellé de question -> réponse) sont utilisées.
"""
from __future__ import annotations

import re
import time

from .base import BaseAdapter, ApplyResult
from ..config import ROOT
from ..letter_pdf import make_pdf
from ..safety import confirm

T = "jobad-DetailView__"
APPLY_INTERNAL = f"[data-testid='{T}CandidateActions__Buttons_apply_internal_candidacy']"
APPLY_EXTERNAL = f"[data-testid='{T}CandidateActions__Buttons_apply_external_candidacy']"
DIALOG = "section[role='dialog']"
GENDER = "[data-testid='hiddenInput-gender']"
GENDER_VALUES = {"homme": "GENDER_MALE", "masculin": "GENDER_MALE",
                 "femme": "GENDER_FEMALE", "féminin": "GENDER_FEMALE", "autre": "GENDER_OTHER"}
USE_OTHER_CV = f"[data-testid='{T}ApplicationFlow__Buttons__use_another_resume']"
CV_INPUT = "input[type='file'][name='resume']"
COVER_INPUT = "input[type='file'][name='coverLetterContent']"
APPLY_BUTTON = f"[data-testid='{T}ApplicationFlow__Buttons__apply_button']"
CONTINUE = f"[data-testid='{T}ApplicationFlow__Buttons__continue_button']"
SCREENING_FORM = "[data-testid='ScreeningQuestions']"
SCREENING = f"{SCREENING_FORM} [data-testid^='input-wrapper-']"
ALREADY_APPLIED = "Vous avez postulé à cette offre"
SUCCESS_TEXTS = ["vous avez postulé à cette offre", "candidature a été envoyée", "candidature envoyée",
                 "merci pour votre candidature", "application has been sent"]
LETTERS_DIR = ROOT / "output" / "lettres"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


class JobTeaserAdapter(BaseAdapter):
    name = "jobteaser"

    def matches(self, url: str) -> bool:
        return "jobteaser.com" in url

    # ---------- lecture seule ----------

    def inspect(self, page) -> dict:
        """Ouvre le panneau de candidature et décrit le formulaire, sans rien modifier.

        Renvoie {"kind": "already"|"external"|"easy"|"unknown", "letter": bool,
        "questions": [libellés], "detail": str}.
        """
        info = {"kind": "unknown", "letter": False, "questions": [], "detail": ""}
        if page.get_by_text(ALREADY_APPLIED).count():
            info.update(kind="already", detail="Déjà postulé (indiqué par JobTeaser).")
            return info
        if page.locator(APPLY_EXTERNAL).count() and not page.locator(APPLY_INTERNAL).count():
            info.update(kind="external", detail="Candidature externe (bouton ↗) — à faire sur le site de l'entreprise.")
            return info
        if not self.try_click(page, [APPLY_INTERNAL, "button:has-text('Candidature simplifiée')"]):
            info["detail"] = "Bouton « Candidature simplifiée » introuvable (connexion JobTeaser expirée ?)."
            return info
        try:
            page.wait_for_selector(DIALOG, timeout=10000)
        except Exception:
            info["detail"] = "Le panneau « Postuler à l'offre » ne s'est pas ouvert."
            return info
        dialog = page.locator(DIALOG).first
        info["kind"] = "easy"
        info["letter"] = dialog.locator(COVER_INPUT).count() > 0
        info["questions"] = self._question_labels(dialog)
        info["detail"] = "Formulaire lu."
        return info

    @staticmethod
    def _question_labels(dialog) -> list:
        # textContent : les questions sont dans un formulaire encore caché à l'étape 1.
        labels = dialog.locator(f"{SCREENING} label").evaluate_all(
            "els => els.map(e => (e.textContent || '').trim())")
        return [l for l in labels if l]

    # ---------- candidature ----------

    def apply(self, page, offer, profile, message, *, dry_run, confirm_before_submit, mode=None):
        mode = mode or ("dry" if dry_run else "auto")
        blocked = self.guard_blocked(page)
        if blocked:
            return blocked

        ctx = page.context
        before = len(ctx.pages)

        if page.get_by_text(ALREADY_APPLIED).count():
            return ApplyResult("skipped", "Déjà postulé (indiqué par JobTeaser).")

        if page.locator(APPLY_EXTERNAL).count() and not page.locator(APPLY_INTERNAL).count():
            return ApplyResult("needs_human", "Candidature externe (bouton ↗) — à faire par toi.")

        if not self.try_click(page, [APPLY_INTERNAL, "button:has-text('Candidature simplifiée')"]):
            return ApplyResult("failed", "Bouton « Candidature simplifiée » introuvable "
                                         "(connexion JobTeaser expirée ?).")
        page.wait_for_timeout(2500)

        if len(ctx.pages) > before:
            return ApplyResult("needs_human", f"Redirection inattendue vers {ctx.pages[-1].url} — à faire par toi.")

        try:
            page.wait_for_selector(DIALOG, timeout=10000)
        except Exception:
            return ApplyResult("failed", "Le panneau « Postuler à l'offre » ne s'est pas ouvert.")
        dialog = page.locator(DIALOG).first
        filled = []

        # 1) Genre : JobTeaser le redemande à chaque candidature ; valeur = answers.gender.
        gender = dialog.locator(GENDER)
        if gender.count() and not (gender.first.get_attribute("value") or "").strip():
            wanted = str(profile.answers.get("gender", "")).strip().lower()
            value = GENDER_VALUES.get(wanted)
            if not value:
                return ApplyResult("needs_human", "Genre non renseigné : choisis-le dans ton profil "
                                                  "(Homme / Femme / Autre).")
            try:
                dialog.locator("#gender[role='combobox']").first.click()
                page.locator(f"#gender-dropdown [data-value='{value}']").first.click(timeout=5000)
                dialog.get_by_role("button", name="Sauvegarder les informations").first.click()
                page.wait_for_timeout(2500)
            except Exception as e:
                return ApplyResult("needs_human", f"Impossible de choisir le genre : {e}")
            # Une fois sauvegardé, le champ disparaît du panneau.
            if gender.count() and not (gender.first.get_attribute("value") or "").strip():
                return ApplyResult("needs_human", "Genre non pris en compte par JobTeaser.")
            filled.append("genre")

        # 2) Questions du recruteur : uniquement avec les réponses fournies par la personne.
        questions = self._question_labels(dialog)
        answers = {_norm(k): v for k, v in (profile.answers.get("screening") or {}).items() if str(v).strip()}
        missing = [q for q in questions if _norm(q) not in answers]
        if questions and mode == "dry":
            return ApplyResult("needs_human", "Questions du recruteur : " + " / ".join(questions))
        if missing and mode == "auto":
            return ApplyResult("needs_human", "Questions du recruteur sans réponse : " + " / ".join(missing))

        # 3) CV : remplace le CV JobTeaser par celui du profil.
        cv = profile.cv_path
        if cv:
            try:
                # « Utiliser un autre CV » fait apparaître un champ fichier name="resume".
                dialog.locator(USE_OTHER_CV).first.click()
                page.wait_for_selector(CV_INPUT, state="attached", timeout=6000)
                dialog.locator(CV_INPUT).first.set_input_files(cv)
                page.wait_for_timeout(3000)
            except Exception as e:
                return ApplyResult("failed", f"Upload du CV impossible : {e}")
            cv_name = cv.rsplit("/", 1)[-1]
            if cv_name[:20] not in dialog.inner_text():
                return ApplyResult("failed", f"CV envoyé mais « {cv_name} » n'apparaît pas dans le panneau.")
            filled.append("CV")

        # 4) Lettre de motivation en PDF, si le recruteur en demande une.
        if dialog.locator(COVER_INPUT).count():
            pdf = message.get("pdf_path")
            try:
                if not pdf:
                    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{offer.company}_{offer.role}").strip("_")[:80]
                    pdf = str(make_pdf(message["body"], LETTERS_DIR / f"Lettre_{slug}.pdf"))
                dialog.locator(COVER_INPUT).first.set_input_files(str(pdf))
                page.wait_for_timeout(3000)
            except Exception as e:
                return ApplyResult("failed", f"Lettre PDF impossible : {e}")
            filled.append(f"lettre ({str(pdf).rsplit('/', 1)[-1]})")

        # 5) Étape des questions : « Continuer » n'envoie pas, il affiche les questions.
        if questions:
            cont = dialog.locator(CONTINUE).first
            if not cont.count() or not cont.is_enabled():
                return ApplyResult("failed", "Bouton « Continuer » inactif (champ obligatoire manquant ?)")
            cont.click()
            page.wait_for_timeout(2500)
            unanswered = self._fill_questions(page, dialog, answers)
            filled.append(f"{len(questions) - len(unanswered)} réponse(s)")
            if unanswered and mode == "auto":
                return ApplyResult("needs_human", "Réponses non saisies : " + " / ".join(unanswered))

        detail = "rempli : " + ", ".join(filled or ["rien à remplir"])

        final = dialog.locator(APPLY_BUTTON).first
        if mode == "assisted":
            return ApplyResult("filled_dry", detail + " ; à toi de cliquer « Postuler maintenant »")
        if not final.count():
            return ApplyResult("needs_human", detail + " ; bouton « Postuler maintenant » absent "
                                                       "(étape supplémentaire ?) — à finir par toi.")
        if not final.is_enabled():
            return ApplyResult("failed", detail + " ; « Postuler maintenant » inactif (champ obligatoire manquant ?)")

        if mode == "dry":
            return ApplyResult("filled_dry", detail + " ; « Postuler maintenant » prêt (simulation, rien envoyé)")

        if confirm_before_submit and not confirm(f"Envoyer la candidature {offer.company} - {offer.role} ?"):
            return ApplyResult("needs_human", "Envoi annulé par l'utilisateur.")

        blocked = self.guard_blocked(page)
        if blocked:
            return blocked

        final.click()
        page.wait_for_timeout(5000)
        if self._succeeded(page):
            return ApplyResult("submitted", detail + " ; envoyé")
        return ApplyResult("needs_human", detail + " ; envoi non confirmé par JobTeaser — vérifie dans "
                                                   "« Mes candidatures ».")

    def _fill_questions(self, page, dialog, answers: dict) -> list:
        """Remplit les questions visibles avec les réponses connues ; renvoie les libellés restés vides."""
        unanswered = []
        wrappers = dialog.locator(SCREENING)
        for i in range(wrappers.count()):
            w = wrappers.nth(i)
            label = (w.locator("label").first.text_content() or "").strip()
            answer = answers.get(_norm(label))
            if not answer:
                unanswered.append(label)
                continue
            try:
                if w.locator("textarea").count():
                    w.locator("textarea").first.fill(str(answer))
                elif w.locator("[role='combobox']").count():
                    w.locator("[role='combobox']").first.click()
                    page.get_by_role("option", name=re.compile(re.escape(str(answer)), re.I)).first.click(timeout=4000)
                elif w.locator("input:not([type='hidden']):not([tabindex='-1'])").count():
                    w.locator("input:not([type='hidden']):not([tabindex='-1'])").first.fill(str(answer))
                else:
                    unanswered.append(label)
            except Exception:
                unanswered.append(label)
        return unanswered

    def wait_for_user_submit(self, page, timeout_s: int = 900) -> bool:
        """Mode assisté : attend que la personne envoie elle-même (preuve affichée par JobTeaser)."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            try:
                if page.is_closed():
                    return False
                if self._succeeded(page):
                    return True
            except Exception:
                return False
            page.wait_for_timeout(2000)
        return False

    @staticmethod
    def _succeeded(page) -> bool:
        try:
            text = page.inner_text("body").lower()
        except Exception:
            return False
        return any(s in text for s in SUCCESS_TEXTS)
