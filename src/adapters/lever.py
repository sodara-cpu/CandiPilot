"""Adaptateur Lever (jobs.lever.co).

Lever autorise souvent la candidature SANS compte : formulaire unique avec
nom, e-mail, téléphone, société actuelle, liens (LinkedIn), upload de CV et
une zone 'Additional information'. C'est l'un des mieux automatisables.
"""
from __future__ import annotations

from .base import BaseAdapter, ApplyResult
from ..safety import confirm


class LeverAdapter(BaseAdapter):
    name = "lever"

    def matches(self, url: str) -> bool:
        return "lever.co" in url

    def apply(self, page, offer, profile, message, *, dry_run, confirm_before_submit):
        blocked = self.guard_blocked(page)
        if blocked:
            return blocked

        # Depuis la fiche de poste, aller au formulaire /apply si besoin.
        if "/apply" not in (page.url or ""):
            self.try_click(page, ["a:has-text('Apply for this job')",
                                  "a.postings-btn", "a:has-text('Apply')"])
            page.wait_for_timeout(1500)

        c = profile.candidate
        filled = []
        if self.try_fill(page, ["input[name='name']"], c.get("full_name", "")):
            filled.append("nom")
        if self.try_fill(page, ["input[name='email']"], c.get("email", "")):
            filled.append("e-mail")
        if self.try_fill(page, ["input[name='phone']"], c.get("phone", "")):
            filled.append("téléphone")
        if self.try_fill(page, ["input[name='org']"], c.get("school", "")):
            filled.append("établissement")
        if self.try_fill(page, ["input[name='urls[LinkedIn]']",
                                "input[name*='LinkedIn']"], c.get("linkedin", "")):
            filled.append("linkedin")
        if self.try_upload(page, ["input[name='resume']", "input[type='file']"],
                           profile.cv_path):
            filled.append("CV")
        if self.try_fill(page, ["textarea[name='comments']", "textarea"],
                         message["body"]):
            filled.append("motivation")

        if not filled:
            return ApplyResult("failed", "Formulaire Lever non reconnu.")
        detail = "champs remplis : " + ", ".join(filled)

        if dry_run:
            return ApplyResult("filled_dry", detail + " (simulation)")
        if confirm_before_submit and not confirm(
                f"Envoyer {offer.company} - {offer.role} ?"):
            return ApplyResult("needs_human", "Envoi annulé par l'utilisateur.")

        blocked = self.guard_blocked(page)
        if blocked:
            return blocked
        if self.try_click(page, ["button:has-text('Submit application')",
                                 "button[type='submit']", "input[type='submit']"]):
            return ApplyResult("submitted", detail + " ; envoyé")
        return ApplyResult("failed", detail + " ; bouton d'envoi introuvable")
