"""Journal des candidatures : anti-doublon + rapport final (CSV)."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .config import ROOT

LOG_PATH = ROOT / "output" / "applications_log.csv"
FIELDS = ["timestamp", "company", "role", "domain", "url", "status", "detail"]

# Statuts possibles
SUBMITTED = "submitted"       # candidature envoyée
FILLED_DRYRUN = "filled_dry"  # rempli en simulation, non envoyé
SKIPPED = "skipped"           # ignoré (hors critère, déjà fait)
NEEDS_HUMAN = "needs_human"   # login/CAPTCHA/compte requis -> à toi
FAILED = "failed"             # erreur pendant le remplissage


class Tracker:
    def __init__(self, path: Path = LOG_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen = self._load_seen()
        if not self.path.exists():
            with open(self.path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    def _load_seen(self) -> set[str]:
        seen: set[str] = set()
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("status") == SUBMITTED:
                        seen.add((row.get("url") or "").strip())
        return seen

    def already_submitted(self, url: str) -> bool:
        return url.strip() in self._seen

    def record(self, offer, status: str, detail: str = "") -> None:
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "company": offer.company,
            "role": offer.role,
            "domain": offer.domain,
            "url": offer.url,
            "status": status,
            "detail": detail,
        }
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow(row)
        if status == SUBMITTED:
            self._seen.add(offer.url.strip())

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts
