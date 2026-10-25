"""Extraction des champs de formulaire d'une page HTML (sans dépendance externe).

Chaque champ devient un dictionnaire de caractéristiques que le modèle apprend à étiqueter :
balise, type, name, id, placeholder, aria-label, texte du <label> associé, texte proche, options.

Chaque champ connaît aussi sa ZONE dans la page : menu (nav), bandeau cookies (cookie), recherche
(search), formulaire (form) ou reste de la page (page). application_fields() ne garde que le
formulaire de candidature : les menus, bandeaux et barres de recherche n'y entrent jamais.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

FIELD_TAGS = {"input", "select", "textarea", "button"}
IGNORED_INPUTS = {"hidden"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "param"}
NAV_TAGS = {"nav", "header", "footer"}
NAV_ROLES = {"navigation", "banner", "contentinfo", "menu", "menubar"}
COOKIE = re.compile(r"cookie|onetrust|didomi|consent-banner|cookiebot|tarteaucitron|axeptio|\bcmp\b|gdpr-banner", re.I)
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
        self._stack: list = []          # éléments ouverts : (balise, zone propre, n° de formulaire)
        self._forms = 0

    def _zone(self):
        zones = {z for _, z, _ in self._stack if z}
        for z in ("cookie", "search", "nav", "form"):
            if z in zones:
                return z
        return "page"

    def _form(self):
        return next((f for _, _, f in reversed(self._stack) if f is not None), None)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag not in VOID:
            role = a.get("role", "").lower()
            marks = " ".join((a.get("id", ""), a.get("class", ""), a.get("aria-label", "")))
            zone, form = None, None
            if COOKIE.search(marks):
                zone = "cookie"
            elif role == "search" or (tag == "form" and re.search(r"search|recherche", marks + a.get("action", ""), re.I)):
                zone = "search"
            elif tag in NAV_TAGS or role in NAV_ROLES:
                zone = "nav"
            elif tag == "form":
                zone = "form"
            if tag == "form":
                self._forms += 1
                form = self._forms
            self._stack.append((tag, zone, form))
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
            "zone": "search" if kind == "search" else self._zone(), "form": self._form(),
        }
        self.fields.append(field)
        if self._label is not None:
            self._label["fields"].append(field)
        if tag == "select":
            self._select = field
        elif tag == "button":
            self._button = field

    def handle_endtag(self, tag):
        if tag not in VOID and any(t == tag for t, _, _ in self._stack):   # HTML mal fermé : on dépile jusqu'à la balise
            while self._stack and self._stack.pop()[0] != tag:
                pass
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


def application_fields(fields: list) -> list:
    """Champs du formulaire de candidature : sans menus, bandeaux cookies ni recherches.
    S'il y a plusieurs formulaires, garde le plus « candidature » (CV, e-mail, nombre de champs)."""
    keep = [f for f in fields if f["zone"] not in ("nav", "cookie", "search")]
    # Formulaire de recherche = formulaire fait surtout de champs de recherche (pas un champ « pays » cherchable).
    by_form: dict = {}
    for f in fields:
        if f["form"]:
            by_form.setdefault(f["form"], []).append(f["zone"] == "search")
    searchy = {k for k, v in by_form.items() if sum(v) * 2 >= len(v)}
    keep = [f for f in keep if f["form"] not in searchy]
    forms: dict = {}
    for f in keep:
        if f["form"] is not None:
            forms.setdefault(f["form"], []).append(f)
    score = lambda fs: len(fs) + 6 * any(f["type"] == "file" for f in fs) + \
        3 * any(f["type"] == "email" or "mail" in (f["name"] + f["label"]).lower() for f in fs)
    best = max(forms.values(), key=score, default=[])
    return best if len(best) >= 3 else keep


def describe(field: dict) -> str:
    """Texte unique décrivant un champ : entrée naturelle pour un modèle de langue ou de classification."""
    parts = [field["tag"], field["type"], field["name"], field["id"], field["placeholder"], field["aria_label"],
             field["autocomplete"], field["label"], field["text"], field["context"]]
    return _clean(" | ".join(p for p in parts if p))
