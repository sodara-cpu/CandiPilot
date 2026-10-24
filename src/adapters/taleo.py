"""Adaptateur Taleo (*.taleo.net).

Taleo (Oracle) impose quasi systématiquement la CRÉATION D'UN COMPTE candidat
avant de postuler. On ne crée pas de compte à ta place : on lance le flux,
on détecte le mur d'authentification et on te rend la main. Si une session
est déjà ouverte (profil persistant), on tente le remplissage standard.
"""
from __future__ import annotations

from .base import BaseAdapter, ApplyResult
from .generic import GenericAdapter


class TaleoAdapter(BaseAdapter):
    name = "taleo"

    def matches(self, url: str) -> bool:
        return "taleo.net" in url or "taleo" in url

    def apply(self, page, offer, profile, message, *, dry_run, confirm_before_submit):
        self.try_click(page, ["a:has-text('Apply')", "button:has-text('Apply')",
                              "a:has-text('Postuler')", "button:has-text('Postuler')",
                              "a:has-text('Apply Online')"])
        page.wait_for_timeout(2000)

        blocked = self.guard_blocked(page)
        if blocked:
            return blocked  # cas dominant : compte requis -> needs_human

        gen = GenericAdapter()
        res = gen.apply(page, offer, profile, message,
                        dry_run=dry_run, confirm_before_submit=confirm_before_submit)
        if res.status == "failed":
            res.detail += " (Taleo multi-étapes : termine à la main)"
        return res
