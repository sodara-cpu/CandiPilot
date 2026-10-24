"""Routeur d'adaptateurs : choisit le bon selon l'URL / le type d'offre."""
from __future__ import annotations

from .base import BaseAdapter, ApplyResult
from .generic import GenericAdapter
from .jobteaser import JobTeaserAdapter
from .workday import WorkdayAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .taleo import TaleoAdapter

# Ordre important : du plus spécifique au plus générique.
_ADAPTERS: list[BaseAdapter] = [
    JobTeaserAdapter(),
    WorkdayAdapter(),
    GreenhouseAdapter(),
    LeverAdapter(),
    SmartRecruitersAdapter(),
    TaleoAdapter(),
]
_GENERIC = GenericAdapter()


def pick_adapter(offer) -> BaseAdapter:
    # 1) type explicite dans le CSV
    for a in _ADAPTERS:
        if offer.apply_type and offer.apply_type == a.name:
            return a
    # 2) détection par URL
    for a in _ADAPTERS:
        if a.matches(offer.url):
            return a
    # 3) repli générique
    return _GENERIC


__all__ = ["pick_adapter", "BaseAdapter", "ApplyResult"]
