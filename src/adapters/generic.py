"""Remplisseur heuristique : couvre les formulaires de candidature simples.

Cherche par attributs usuels (name/id/placeholder/label) les champs nom, e-mail,
téléphone, LinkedIn, upload de CV et zone de motivation, puis le bouton d'envoi.
"""
from __future__ import annotations

from .base import BaseAdapter, ApplyResult
from ..safety import confirm


class GenericAdapter(BaseAdapter):
    name = "generic"

    def matches(self, url: str) -> bool:
        return True  # repli universel

    def apply(self, page, offer, profile, message, *, dry_run, confirm_before_submit):
        blocked = self.guard_blocked(page)
        if blocked:
            return blocked

        c = profile.candidate
        filled = []

        if self.try_fill(page, _S["first_name"], c.get("first_name", "")):
            filled.append("prénom")
        if self.try_fill(page, _S["last_name"], c.get("last_name", "")):
            filled.append("nom")
        if self.try_fill(page, _S["full_name"], c.get("full_name", "")):
            filled.append("nom complet")
        if self.try_fill(page, _S["email"], c.get("email", "")):
            filled.append("e-mail")
        if self.try_fill(page, _S["phone"], c.get("phone", "")):
            filled.append("téléphone")
        if self.try_fill(page, _S["linkedin"], c.get("linkedin", "")):
            filled.append("linkedin")
        if self.try_fill(page, _S["cover"], message["body"]):
            filled.append("motivation")
        if self.try_upload(page, _S["cv"], profile.cv_path):
            filled.append("CV")

        if not filled:
            return ApplyResult("failed", "Aucun champ reconnu (formulaire non standard).")

        detail = "champs remplis : " + ", ".join(filled)

        if dry_run:
            return ApplyResult("filled_dry", detail + " (simulation, non envoyé)")

        if confirm_before_submit and not confirm(
                f"Envoyer la candidature {offer.company} - {offer.role} ?"):
            return ApplyResult("needs_human", "Envoi annulé par l'utilisateur.")

        # Re-vérifie l'absence de CAPTCHA juste avant d'envoyer.
        blocked = self.guard_blocked(page)
        if blocked:
            return blocked

        if self.try_click(page, _S["submit"]):
            return ApplyResult("submitted", detail + " ; envoyé")
        return ApplyResult("failed", detail + " ; bouton d'envoi introuvable")


# Sélecteurs heuristiques (CSS). Ordre = priorité.
_S = {
    "first_name": ["input[name*='first' i]", "input[id*='first' i]",
                   "input[placeholder*='prénom' i]", "input[autocomplete='given-name']"],
    "last_name": ["input[name*='last' i]", "input[id*='last' i]",
                  "input[placeholder*='nom' i]", "input[autocomplete='family-name']"],
    "full_name": ["input[name*='full' i][name*='name' i]", "input[name='name']",
                  "input[placeholder*='nom complet' i]"],
    "email": ["input[type='email']", "input[name*='mail' i]", "input[id*='mail' i]"],
    "phone": ["input[type='tel']", "input[name*='phone' i]", "input[name*='tel' i]",
              "input[placeholder*='téléphone' i]"],
    "linkedin": ["input[name*='linkedin' i]", "input[placeholder*='linkedin' i]",
                 "input[name*='url' i]"],
    "cover": ["textarea[name*='cover' i]", "textarea[name*='motiv' i]",
              "textarea[id*='message' i]", "textarea[placeholder*='motivation' i]",
              "textarea"],
    "cv": ["input[type='file'][name*='cv' i]", "input[type='file'][name*='resume' i]",
           "input[type='file']"],
    "submit": ["button[type='submit']", "button:has-text('Postuler')",
               "button:has-text('Envoyer')", "button:has-text('Soumettre')",
               "button:has-text('Submit')", "button:has-text('Apply')",
               "input[type='submit']"],
}
