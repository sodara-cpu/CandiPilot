"""Extraction des champs de formulaire d'une page HTML (sans dépendance externe).

Chaque champ devient un dictionnaire de caractéristiques que le modèle apprend à étiqueter :
balise, type, name, id, placeholder, aria-label, texte du <label> associé, texte proche, options.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

FIELD_TAGS = {"input", "select", "textarea", "button"}
IGNORED_INPUTS = {"hidden"}
_SPACES = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text or "").strip()


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields: list = []
        self.labels_for: dict = {}      # id -> texte du <label for=id>
        self._label = None              # label en cours : {"for": ..., "text": [...], "fields": [...]}
        self._select = None
        self._button = None
        self._recent_text: list = []    # derniers textes vus (contexte du champ)
        self._skip = 0                  # dans <script>/<style>

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "label":
            self._label = {"for": a.get("for", ""), "text": [], "fields": []}
        elif tag == "option" and self._select is not None:
            self._select["options"].append(a.get("value", ""))
        if tag not in FIELD_TAGS:
            return
        kind = a.get("type", "text" if tag == "input" else "").lower()
        if tag == "input" and kind in IGNORED_INPUTS:
            return
        field = {
            "tag": tag, "type": kind, "name": a.get("name", ""), "id": a.get("id", ""),
            "placeholder": a.get("placeholder", ""), "aria_label": a.get("aria-label", ""),
            "autocomplete": a.get("autocomplete", ""), "accept": a.get("accept", ""),
            "required": "required" in a or a.get("aria-required") == "true",
            "label": "", "context": _clean(" ".join(self._recent_text[-3:]))[-120:], "text": "", "options": [],
        }
        self.fields.append(field)
        if self._label is not None:
            self._label["fields"].append(field)
        if tag == "select":
            self._select = field
        elif tag == "button":
            self._button = field

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "label" and self._label is not None:
            text = _clean(" ".join(self._label["text"]))
            if self._label["for"]:
                self.labels_for[self._label["for"]] = text
            for f in self._label["fields"]:
                f["label"] = f["label"] or text
            self._label = None
        elif tag == "select":
            self._select = None
        elif tag == "button":
            self._button = None

    def handle_data(self, data):
        if self._skip or not data.strip():
            return
        if self._label is not None and self._select is None:   # le texte des options n'est pas le libellé
            self._label["text"].append(data)
        if self._button is not None:
            self._button["text"] = _clean(self._button["text"] + " " + data)
        self._recent_text.append(data.strip())
        del self._recent_text[:-6]


def extract_fields(html: str) -> list:
    """Liste des champs du formulaire, dans l'ordre de la page."""
    p = _Parser()
    p.feed(html)
    p.close()
    for f in p.fields:
        if not f["label"] and f["id"] in p.labels_for:
            f["label"] = p.labels_for[f["id"]]
    return p.fields


def describe(field: dict) -> str:
    """Texte unique décrivant un champ : entrée naturelle pour un modèle de langue ou de classification."""
    parts = [field["tag"], field["type"], field["name"], field["id"], field["placeholder"], field["aria_label"],
             field["autocomplete"], field["label"], field["text"], field["context"]]
    return _clean(" | ".join(p for p in parts if p))
