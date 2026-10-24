"""Chargement du profil (YAML) et des offres (CSV)."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Offer:
    url: str
    company: str = ""
    role: str = ""
    domain: str = ""
    start_date: str = ""      # ISO 'YYYY-MM-DD' ; vide si inconnu
    apply_type: str = ""      # external | jobteaser | workday | greenhouse | generic
    status: str = ""          # rempli par le journal

    @property
    def key(self) -> str:
        return self.url.strip()


@dataclass
class Profile:
    data: dict[str, Any]

    # Raccourcis
    @property
    def candidate(self) -> dict: return self.data.get("candidate", {})

    @property
    def documents(self) -> dict: return self.data.get("documents", {})

    @property
    def answers(self) -> dict: return self.data.get("answers", {})

    @property
    def settings(self) -> dict: return self.data.get("settings", {})

    @property
    def cv_path(self) -> str:
        return os.path.expanduser(str(self.documents.get("cv", "")).strip("<> "))

    def domain_line(self, domain: str) -> str:
        lines = self.data.get("domain_lines", {})
        d = (domain or "").lower()
        for key, text in lines.items():
            if key == "default":
                continue
            if key in d:
                return text
        return lines.get("default", "l'analyse financière et l'investissement")


def load_profile(path: str | Path | None = None) -> Profile:
    path = Path(path) if path else ROOT / "config" / "profile.yaml"
    with open(path, encoding="utf-8") as f:
        return Profile(yaml.safe_load(f))


def load_offers(path: str | Path | None = None) -> list[Offer]:
    path = Path(path) if path else ROOT / "config" / "offers.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} introuvable. Copie config/offers.sample.csv vers config/offers.csv."
        )
    offers: list[Offer] = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = (row.get("url") or "").strip()
            if not url or url.startswith("#"):
                continue
            offers.append(Offer(
                url=url,
                company=(row.get("company") or "").strip(),
                role=(row.get("role") or "").strip(),
                domain=(row.get("domain") or "").strip(),
                start_date=(row.get("start_date") or "").strip(),
                apply_type=(row.get("apply_type") or "").strip().lower(),
                status=(row.get("status") or "").strip(),
            ))
    return offers
