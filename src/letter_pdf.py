"""Génère la lettre de motivation en PDF (Chromium headless, déjà installé par Playwright).

Lancé dans un processus séparé car Playwright sync ne peut pas être imbriqué
dans la session navigateur déjà ouverte par main.py.

    python -m src.letter_pdf <lettre.txt> <sortie.pdf>
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from .config import ROOT, load_profile

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre"]

CSS = """
@page { size: A4; margin: 22mm 22mm 20mm 22mm; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 11pt; line-height: 1.38; color: #000; }
.contact { margin-bottom: 18pt; }
.date { text-align: right; margin-bottom: 22pt; }
p { text-align: justify; margin: 0 0 11pt 0; }
.sign { font-weight: bold; margin-top: 22pt; }
"""


def build_html(body: str, candidate: dict) -> str:
    today = date.today()
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    signature = paragraphs.pop() if paragraphs and len(paragraphs[-1]) < 60 else ""
    contact = "<br>".join(html.escape(x) for x in [
        candidate.get("full_name", ""), candidate.get("email", ""),
        candidate.get("phone", ""), candidate.get("linkedin", "")] if x)
    city = (candidate.get("location", "") or "Paris").split(",")[0]
    return (f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            f"<div class='contact'>{contact}</div>"
            f"<div class='date'>{html.escape(city)}, le {today.day} {MONTHS[today.month - 1]} {today.year}</div>"
            + "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
            + (f"<p class='sign'>{html.escape(signature)}</p>" if signature else "")
            + "</body></html>")


def make_pdf(body: str, out: Path, candidate: dict = None) -> Path:
    """Écrit le texte puis lance le rendu PDF dans un sous-processus.

    `candidate` : coordonnées pour l'en-tête ; par défaut, celles de config/profile.yaml.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    txt = out.with_suffix(".txt")
    txt.write_text(body, encoding="utf-8")
    contact = out.with_suffix(".contact.json")
    if candidate is not None:
        contact.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    elif contact.exists():
        contact.unlink()
    subprocess.run([sys.executable, "-m", "src.letter_pdf", str(txt), str(out)],
                   cwd=ROOT, check=True, capture_output=True, timeout=120)
    return out


def _render(txt: Path, out: Path) -> None:
    from playwright.sync_api import sync_playwright
    contact = out.with_suffix(".contact.json")
    candidate = (json.loads(contact.read_text(encoding="utf-8")) if contact.exists()
                 else load_profile().candidate)
    page_html = build_html(txt.read_text(encoding="utf-8"), candidate)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(page_html, wait_until="load")
        page.pdf(path=str(out), format="A4", print_background=True)
        browser.close()


if __name__ == "__main__":
    _render(Path(sys.argv[1]), Path(sys.argv[2]))
