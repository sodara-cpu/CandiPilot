"""Adaptateur Workday (ATS très répandu : *.myworkdayjobs.com).

Workday impose presque toujours un compte candidat (création / connexion).
On détecte ce mur et on rend la main plutôt que de tenter de le contourner.
Quand une session est déjà ouverte (profil persistant), on remplit les champs
standards du formulaire 'My Information'.
"""
from __future__ import annotations

from .base import BaseAdapter, ApplyResult
from .generic import GenericAdapter


class WorkdayAdapter(BaseAdapter):
    name = "workday"

    def matches(self, url: str) -> bool:
        return "myworkdayjobs.com" in url or "workday" in url

    def apply(self, page, offer, profile, message, *, dry_run, confirm_before_submit):
        # Lance le flux "Apply"
        self.try_click(page, ["a:has-text('Apply')", "button:has-text('Apply')",
                              "a:has-text('Postuler')", "button:has-text('Postuler')"])
        page.wait_for_timeout(2000)

        # Workday propose "Apply Manually" / "Use My Last Application" / "Autofill with Resume"
        self.try_click(page, ["a:has-text('Apply Manually')",
                              "button:has-text('Apply Manually')"])
        page.wait_for_timeout(1500)

        blocked = self.guard_blocked(page)
        if blocked:
            return blocked  # création de compte quasi systématique -> needs_human

        # Si on est passé (session existante), on remplit comme un formulaire standard.
        gen = GenericAdapter()
        res = gen.apply(page, offer, profile, message,
                        dry_run=dry_run, confirm_before_submit=confirm_before_submit)
        if res.status == "failed":
            res.detail += " (Workday multi-étapes : termine la candidature à la main)"
        return res
