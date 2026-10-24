"""Fichiers des utilisateurs (CV, lettres) stockés dans la base : table files.

Une référence de fichier s'écrit "file:<id>" (colonnes documents.chemin et applications.letter_path).
Les anciennes bases locales contenaient des chemins sur le disque : ils restent lisibles et sont
importés dans la base au démarrage (migrate_legacy).
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import db

PREFIX = "file:"


def save(conn, user_id: str, name: str, data: bytes) -> str:
    fid = db.new_id()
    conn.execute("INSERT INTO files(id, user_id, name, contenu, created_at) VALUES (?,?,?,?,?)",
                 (fid, user_id, Path(name).name or "document.pdf", data, db.now()))
    return PREFIX + fid


def read(conn, ref: str):
    """(nom, contenu) ou None."""
    if not ref:
        return None
    if ref.startswith(PREFIX):
        r = conn.execute("SELECT name, contenu FROM files WHERE id=?", (ref[len(PREFIX):],)).fetchone()
        return (r["name"], bytes(r["contenu"])) if r else None
    p = Path(ref)
    return (p.name, p.read_bytes()) if p.is_file() else None


def exists(conn, ref: str) -> bool:
    if not ref:
        return False
    if ref.startswith(PREFIX):
        return bool(conn.execute("SELECT 1 FROM files WHERE id=?", (ref[len(PREFIX):],)).fetchone())
    return Path(ref).is_file()


def delete(conn, ref: str) -> None:
    if ref and ref.startswith(PREFIX):
        conn.execute("DELETE FROM files WHERE id=?", (ref[len(PREFIX):],))


def materialize(conn, ref: str) -> str:
    """Écrit le fichier dans un dossier temporaire (robot local Playwright) et renvoie son chemin."""
    got = read(conn, ref)
    if not got:
        return ""
    name, data = got
    folder = Path(tempfile.mkdtemp(prefix="candipilot-"))
    out = folder / (re.sub(r"[^\w.\- ]+", "_", name) or "document.pdf")
    out.write_bytes(data)
    return str(out)


def migrate_legacy(conn) -> None:
    """Importe dans la base les fichiers des anciennes versions (chemins sur le disque)."""
    for d in conn.execute("SELECT id, user_id, name, chemin FROM documents").fetchall():
        if d["chemin"] and not d["chemin"].startswith(PREFIX) and Path(d["chemin"]).is_file():
            ref = save(conn, d["user_id"], d["name"], Path(d["chemin"]).read_bytes())
            conn.execute("UPDATE documents SET chemin=? WHERE id=?", (ref, d["id"]))
    for a in conn.execute("SELECT id, user_id, letter_path FROM applications WHERE letter_path<>''").fetchall():
        if not a["letter_path"].startswith(PREFIX) and Path(a["letter_path"]).is_file():
            p = Path(a["letter_path"])
            conn.execute("UPDATE applications SET letter_path=? WHERE id=?", (save(conn, a["user_id"], p.name, p.read_bytes()), a["id"]))
