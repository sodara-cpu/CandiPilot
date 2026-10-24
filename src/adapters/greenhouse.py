"""Adaptateur Greenhouse (boards.greenhouse.io et formulaires embarqués).

Greenhouse autorise souvent la candidature sans compte : champs nom/e-mail/
téléphone, upload de CV, et parfois une lettre de motivation. C'est l'un des
cas les mieux automatisables.
"""
from __future__ import annotations

from .base import BaseAdapter, ApplyResult
from .generic import GenericAdapter
from ..safety import confirm


class GreenhouseAdapter(BaseAdapter):
    name = "greenhouse"

    def matches(self, url: str) -> bool:
        return "greenhouse.io" in url or "grnh.se" in url

    def apply(self, page, offer, profile, message, *, dry_run, confirm_before_submit):
        blocked = self.guard_blocked(page)
        if blocked:
            return blocked

        c = profile.candidate
        filled = []
        if self.try_fill(page, ["#first_name", "input[name='first_name']"], c.get("first_name", "")):
            filled.append("prénom")
        if self.try_fill(page, ["#last_name", "input[name='last_name']"], c.get("last_name", "")):
            filled.append("nom")
        if self.try_fill(page, ["#email", "input[name='email']"], c.get("email", "")):
            filled.append("e-mail")
        if self.try_fill(page, ["#phone", "input[name='phone']"], c.get("phone", "")):
            filled.append("téléphone")
        if self.try_upload(page, ["input[type='file']"], profile.cv_path):
            filled.append("CV")
        # Zone de lettre de motivation (souvent un textarea custom-question)
        if self.try_fill(page, ["textarea"], message["body"]):
            filled.append("motivation")

        if not filled:
            return ApplyResult("failed", "Formulaire Greenhouse non reconnu.")
        detail = "champs remplis : " + ", ".join(filled)

        if dry_run:
            return ApplyResult("filled_dry", detail + " (simulation)")
        if confirm_before_submit and not confirm(
                f"Envoyer {offer.company} - {offer.role} ?"):
            return ApplyResult("needs_human", "Envoi annulé par l'utilisateur.")

        blocked = self.guard_blocked(page)
        if blocked:
            return blocked
        if self.try_click(page, ["#submit_app", "button:has-text('Submit Application')",
                                 "button[type='submit']"]):
            return ApplyResult("submitted", detail + " ; envoyé")
        return ApplyResult("failed", detail + " ; bouton d'envoi introuvable")
