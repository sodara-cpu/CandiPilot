"""Réseau de neurones des champs (apprentissage/modeles/reseau.py), exécuté dans l'application en numpy.

Entraîné dans apprentissage/ (PyTorch), exporté par apprentissage/export_modele.py vers app/models/ : mêmes
calculs (mots et trigrammes hachés → embeddings moyennés, structure du champ, MLP), sans PyTorch sur le serveur.
Rôle : identifier les champs d'un formulaire que les règles de l'extension n'ont pas reconnus.

Registre (app/models/registry.json) : chaque version exportée avec sa note sur le juge (vrais formulaires
vérifiés à la main) ; « active » = version utilisée. Revenir en arrière = remettre l'ancienne version active.
"""
from __future__ import annotations

import json
import re
import threading
import zlib
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parent / "models"
BUCKETS = 2 ** 18
TAGS = ["input", "select", "textarea", "button"]
TYPES = ["text", "email", "tel", "url", "file", "checkbox", "radio", "date", "number", "submit", "button", "search", "password"]
_W: dict = {}
_lock = threading.Lock()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def describe(field: dict) -> str:
    """Identique à apprentissage/extraction.describe (même ordre des parties)."""
    parts = [field.get(k, "") for k in ("tag", "type", "name", "id", "placeholder", "aria_label", "autocomplete", "label", "text", "context")]
    return _clean(" | ".join(p for p in parts if p))


def tokens(text: str) -> list:
    ids = []
    for w in re.findall(r"[a-zà-ÿ0-9]+", (text or "").lower()):
        ids.append(zlib.crc32(("w:" + w).encode()) % BUCKETS)
        padded = f"<{w}>"
        ids.extend(zlib.crc32(("c:" + padded[i:i + 3]).encode()) % BUCKETS for i in range(len(padded) - 2))
    return ids or [0]


def _struct(field: dict, index: int, total: int) -> list:
    v = [float(field.get("tag") == t) for t in TAGS] + [float(field.get("type") == t) for t in TYPES]
    v += [float(field.get("type") not in TYPES), float(bool(field.get("required"))), float(bool(field.get("options"))),
          index / max(1, total - 1), float(index == total - 1)]
    return v


def active() -> dict:
    reg = MODELS / "registry.json"
    if not reg.exists():
        return {}
    r = json.loads(reg.read_text(encoding="utf-8"))
    return dict(r["versions"].get(r.get("active", ""), {}), version=r.get("active", ""))


def load(path: Path = None) -> bool:
    """Charge une version (par défaut : la version active du registre)."""
    if path is None:
        info = active()
        if not info.get("file"):
            return False
        path = MODELS / info["file"]
    if not Path(path).exists():
        return False
    d = np.load(path, allow_pickle=False)
    with _lock:
        _W.clear()
        _W.update({k: d[k] for k in d.files})
        _W["labels"] = [str(x) for x in d["labels"]]
    return True


def available() -> bool:
    return bool(_W) or load()


def _bag(table: str, ids: list) -> np.ndarray:
    q, s = _W[table + "_q"], _W[table + "_s"]
    rows = np.asarray(ids)
    return (q[rows].astype(np.float32) * s[rows][:, None]).mean(axis=0)


def predict(champs: list) -> list:
    """[(rôle, probabilité)] pour chaque champ (dans l'ordre du formulaire)."""
    if not champs or not available():
        return []
    n, out = len(champs), []
    with np.errstate(all="ignore"):         # avertissements parasites de BLAS sur macOS (résultats identiques à PyTorch)
        return _predict(champs, n, out)


def _predict(champs: list, n: int, out: list) -> list:
    for i, c in enumerate(champs):
        own = _bag("own", tokens(describe(dict(c, context=""))))
        ctx = _bag("ctx", tokens(c.get("context", "")))
        x = np.concatenate([own, ctx, np.asarray(_struct(c, i, n), dtype=np.float32)])
        h = np.maximum(_W["w1"] @ x + _W["b1"], 0)
        z = _W["w2"] @ h + _W["b2"]
        p = np.exp(z - z.max())
        p /= p.sum()
        k = int(p.argmax())
        out.append((_W["labels"][k], float(p[k])))
    return out
