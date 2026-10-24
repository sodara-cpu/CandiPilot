"""Interface commune d'un adaptateur de site + utilitaires de remplissage."""
from __future__ import annotations

from dataclasses import dataclass

from ..safety import detect_block


@dataclass
class ApplyResult:
    status: str          # voir tracker : SUBMITTED / FILLED_DRYRUN / NEEDS_HUMAN / FAILED
    detail: str = ""


class BaseAdapter:
    name: str = "base"

    def matches(self, url: str) -> bool:
        return False

    def apply(self, page, offer, profile, message, *, dry_run: bool,
              confirm_before_submit: bool) -> ApplyResult:
        raise NotImplementedError

    # ---- helpers partagés ----

    @staticmethod
    def guard_blocked(page) -> ApplyResult | None:
        block = detect_block(page)
        if block == "captcha":
            return ApplyResult("needs_human", "CAPTCHA détecté — à compléter par toi.")
        if block == "login":
            return ApplyResult("needs_human", "Connexion / création de compte requise — à faire par toi.")
        return None

    @staticmethod
    def try_fill(page, selectors: list[str], value: str) -> bool:
        if not value:
            return False
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.fill(value, timeout=4000)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def try_upload(page, selectors: list[str], path: str) -> bool:
        if not path:
            return False
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.set_input_files(path, timeout=6000)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def try_click(page, selectors: list[str]) -> bool:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=4000)
                    return True
            except Exception:
                continue
        return False
