"""Orchestrateur : boucle sur les offres et lance l'adaptateur adapté.

Exemples :
    python -m src.main --dry-run
    python -m src.main
    python -m src.main --no-confirm --domain "Private Debt" --start-from 2027-03-01
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from .adapters import pick_adapter
from .browser import browser_session, login_hint
from .config import load_offers, load_profile
from .message import build_message
from .safety import polite_delay
from . import tracker as T


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Assistant de candidature semi-automatisé.")
    p.add_argument("--offers", default=None, help="Chemin du CSV d'offres.")
    p.add_argument("--dry-run", action="store_true", help="Remplit sans envoyer.")
    p.add_argument("--no-confirm", action="store_true", help="N'exige pas de confirmation.")
    p.add_argument("--confirm", action="store_true", help="Exige une confirmation avant envoi.")
    p.add_argument("--domain", default=None, help="Ne traite que les offres dont le domaine contient ce texte.")
    p.add_argument("--start-from", default=None, help="Date mini de début (YYYY-MM-DD).")
    p.add_argument("--limit", type=int, default=0, help="Nombre max d'offres à traiter.")
    return p.parse_args(argv)


def _passes_filters(offer, args) -> tuple[bool, str]:
    if args.domain and args.domain.lower() not in (offer.domain or "").lower():
        return False, "hors domaine"
    if args.start_from and offer.start_date:
        try:
            if date.fromisoformat(offer.start_date) < date.fromisoformat(args.start_from):
                return False, f"début {offer.start_date} < {args.start_from}"
        except ValueError:
            pass
    return True, ""


def main(argv=None) -> int:
    args = parse_args(argv)
    profile = load_profile()
    s = profile.settings

    dry_run = args.dry_run or bool(s.get("dry_run", False))
    confirm_before = bool(s.get("confirm_before_submit", False))
    if args.no_confirm:
        confirm_before = False
    if args.confirm:
        confirm_before = True

    try:
        offers = load_offers(args.offers)
    except FileNotFoundError as e:
        print(e); return 2

    if not profile.cv_path or profile.cv_path.startswith("<"):
        print("ATTENTION : chemin du CV non renseigné dans config/profile.yaml "
              "(documents.cv). L'upload sera ignoré.\n")

    tracker = T.Tracker()
    print(login_hint(s))
    print(f"\nMode : {'SIMULATION' if dry_run else 'RÉEL'} | "
          f"confirmation avant envoi : {'oui' if confirm_before else 'non'}")
    print(f"{len(offers)} offres chargées.\n")

    processed = 0
    with browser_session(s) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for offer in offers:
            if args.limit and processed >= args.limit:
                break

            ok, why = _passes_filters(offer, args)
            if not ok:
                tracker.record(offer, T.SKIPPED, why)
                print(f"— SKIP   {offer.company} · {offer.role} ({why})")
                continue

            if s.get("skip_already_applied", True) and tracker.already_submitted(offer.url):
                print(f"— SKIP   {offer.company} · {offer.role} (déjà postulé)")
                continue

            print(f"→ OUVRE  {offer.company} · {offer.role}")
            try:
                page.goto(offer.url, wait_until="domcontentloaded")
                polite_delay(s)
            except Exception as e:
                tracker.record(offer, T.FAILED, f"navigation: {e}")
                print(f"  ✗ échec navigation : {e}")
                continue

            message = build_message(profile, offer)
            adapter = pick_adapter(offer)
            try:
                res = adapter.apply(page, offer, profile, message,
                                    dry_run=dry_run, confirm_before_submit=confirm_before)
            except Exception as e:
                res = T_result_failed(e)

            tracker.record(offer, res.status, res.detail)
            print(f"  [{adapter.name}] {res.status} — {res.detail}")
            processed += 1
            polite_delay(s)

    print("\n===== RÉSUMÉ =====")
    for status, n in sorted(tracker.summary().items()):
        print(f"  {status:14s} : {n}")
    print(f"\nJournal détaillé : {T.LOG_PATH}")
    return 0


def T_result_failed(e):
    from .adapters.base import ApplyResult
    return ApplyResult("failed", f"exception : {e}")


if __name__ == "__main__":
    sys.exit(main())
