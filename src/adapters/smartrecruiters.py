"""Adaptateur SmartRecruiters (jobs.smartrecruiters.com / careers.smartrecruiters.com).

Le formulaire 'I'm interested' demande prénom/nom/e-mail/téléphone + CV.
Certaines offres exigent une connexion (LinkedIn/Google) : on la détecte et
on rend la main plutôt que de la contourner.
"""
from __future__ import annotations

from .base import BaseAdapter, ApplyResult
from ..safety import confirm


class SmartRecruitersAdapter(BaseAdapter):
    name = "smartrecruiters"

    def matches(self, url: str) -> bool:
        return "smartrecruiters.com" in url

    def apply(self, page, offer, profile, message, *, dry_run, confirm_before_submit):
        # Ouvre le formulaire de candidature.
        self.try_click(page, ["button:has-text(\"I'm interested\")",
                              "a:has-text(\"I'm interested\")",
                              "button:has-text('Postuler')",
                              "button:has-text('Apply')"])
        page.wait_for_timeout(2000)

        blocked = self.guard_blocked(page)
        if blocked:
            return blocked

        c = profile.candidate
        filled = []
        if self.try_fill(page, ["input[name='firstName']", "#firstName"], c.get("first_name", "")):
            filled.append("prénom")
        if self.try_fill(page, ["input[name='lastName']", "#lastName"], c.get("last_name", "")):
            filled.append("nom")
        if self.try_fill(page, ["input[name='email']", "#email", "input[type='email']"], c.get("email", "")):
            filled.append("e-mail")
        if self.try_fill(page, ["input[name='phoneNumber']", "input[type='tel']"], c.get("phone", "")):
            filled.append("téléphone")
        if self.try_upload(page, ["input[type='file']"], profile.cv_path):
            filled.append("CV")
        if self.try_fill(page, ["textarea"], message["body"]):
            filled.append("motivation")

        if not filled:
            return ApplyResult("failed", "Formulaire SmartRecruiters non reconnu.")
        detail = "champs remplis : " + ", ".join(filled)

        if dry_run:
            return ApplyResult("filled_dry", detail + " (simulation)")
        if confirm_before_submit and not confirm(
                f"Envoyer {offer.company} - {offer.role} ?"):
            return ApplyResult("needs_human", "Envoi annulé par l'utilisateur.")

        blocked = self.guard_blocked(page)
        if blocked:
            return blocked
        if self.try_click(page, ["button:has-text('Submit')", "button:has-text('Apply')",
                                 "button[type='submit']"]):
            return ApplyResult("submitted", detail + " ; envoyé")
        return ApplyResult("failed", detail + " ; bouton d'envoi introuvable")
