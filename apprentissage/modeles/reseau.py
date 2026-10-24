"""Réseaux de neurones (PyTorch) : comprendre les champs d'un formulaire et choisir les liens à suivre.

Les deux réseaux lisent le texte comme fastText : mots et morceaux de 3 lettres, hachés dans une
grande table d'embeddings (aucun vocabulaire à construire, robuste aux fautes et aux langues).

- ReseauChamps : description du champ + texte voisin + structure (balise, type, position…) → étiquette.
- ReseauLiens  : texte et adresse d'un lien → probabilité qu'il mène à un formulaire de candidature.
"""
from __future__ import annotations

import random
import re
import zlib
from pathlib import Path

import torch
from torch import nn

from etiquettes import LABELS
from extraction import describe

BUCKETS = 2 ** 18
DIM = 64
LABEL_LIST = list(LABELS)
TAGS = ["input", "select", "textarea", "button"]
TYPES = ["text", "email", "tel", "url", "file", "checkbox", "radio", "date", "number", "submit", "button", "search", "password"]
SAVE_DIR = Path(__file__).resolve().parent / "sauvegardes"
torch.manual_seed(0)


def tokens(text: str) -> list:
    """Identifiants hachés des mots et des trigrammes de lettres."""
    ids = []
    for w in re.findall(r"[a-zà-ÿ0-9]+", (text or "").lower()):
        ids.append(zlib.crc32(("w:" + w).encode()) % BUCKETS)
        padded = f"<{w}>"
        ids.extend(zlib.crc32(("c:" + padded[i:i + 3]).encode()) % BUCKETS for i in range(len(padded) - 2))
    return ids or [0]


def _bag(seqs: list) -> tuple:
    """Lot de séquences → (identifiants concaténés, débuts) pour nn.EmbeddingBag."""
    offsets, flat = [], []
    for s in seqs:
        offsets.append(len(flat))
        flat.extend(s)
    return torch.tensor(flat, dtype=torch.long), torch.tensor(offsets, dtype=torch.long)


# ---------- champs ----------

def field_struct(field: dict, index: int, total: int) -> list:
    v = [float(field["tag"] == t) for t in TAGS] + [float(field["type"] == t) for t in TYPES]
    v += [float(field["type"] not in TYPES), float(bool(field.get("required"))), float(bool(field.get("options"))),
          index / max(1, total - 1), float(index == total - 1)]
    return v


STRUCT_DIM = len(TAGS) + len(TYPES) + 5


class ReseauChamps(nn.Module):
    def __init__(self):
        super().__init__()
        self.own = nn.EmbeddingBag(BUCKETS, DIM, mode="mean")
        self.ctx = nn.EmbeddingBag(BUCKETS, DIM // 2, mode="mean")
        self.mlp = nn.Sequential(nn.Linear(DIM + DIM // 2 + STRUCT_DIM, 128), nn.ReLU(), nn.Dropout(0.2),
                                 nn.Linear(128, len(LABEL_LIST)))

    def forward(self, own, ctx, struct):
        return self.mlp(torch.cat([self.own(*own), self.ctx(*ctx), struct], dim=1))


def encode_fields(champs: list) -> tuple:
    own = [tokens(describe(dict(c, context=""))) for c in champs]
    ctx = [tokens(c.get("context", "")) for c in champs]
    struct = torch.tensor([field_struct(c, i, len(champs)) for i, c in enumerate(champs)], dtype=torch.float)
    return _bag(own), _bag(ctx), struct


class ModeleReseau:
    """Modèle de champs au contrat de modeles/__init__.py (entrainer / predire)."""
    nom = "reseau"

    def __init__(self, epochs: int = 25, lr: float = 3e-3):
        self.net, self.epochs, self.lr = ReseauChamps(), epochs, lr

    def entrainer(self, pages: list, weights: list = None) -> None:
        """pages : champs étiquetés ; weights : confiance de chaque page (1 = vérifiée à la main)."""
        samples = []
        for p, w in zip(pages, weights or [1.0] * len(pages)):
            for i, c in enumerate(p["champs"]):
                if c.get("etiquette") in LABELS:
                    samples.append((p["champs"], i, LABEL_LIST.index(c["etiquette"]), w))
        if not samples:
            return
        opt = torch.optim.Adam(self.net.parameters(), lr=self.lr)
        loss_fn = nn.CrossEntropyLoss(reduction="none")
        rng = random.Random(0)
        self.net.train()
        for _ in range(self.epochs):
            rng.shuffle(samples)
            for k in range(0, len(samples), 64):
                batch = samples[k:k + 64]
                fields = [dict(page[i], _pos=(i, len(page))) for page, i, _, _ in batch]
                own = _bag([tokens(describe(dict(f, context=""))) for f in fields])
                ctx = _bag([tokens(f.get("context", "")) for f in fields])
                struct = torch.tensor([field_struct(f, *f["_pos"]) for f in fields], dtype=torch.float)
                y = torch.tensor([b[2] for b in batch])
                w = torch.tensor([b[3] for b in batch], dtype=torch.float)
                loss = (loss_fn(self.net(own, ctx, struct), y) * w).sum() / w.sum()
                opt.zero_grad()
                loss.backward()
                opt.step()

    def probabilites(self, champs: list) -> list:
        if not champs:
            return []
        self.net.eval()
        with torch.no_grad():
            p = torch.softmax(self.net(*encode_fields(champs)), dim=1)
        return [(LABEL_LIST[int(row.argmax())], float(row.max())) for row in p]

    def predire(self, champs: list) -> list:
        return [label for label, _ in self.probabilites(champs)]

    def sauver(self, path: Path = SAVE_DIR / "champs.pt") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.net.state_dict(), path)

    def charger(self, path: Path = SAVE_DIR / "champs.pt") -> bool:
        if not path.exists():
            return False
        self.net.load_state_dict(torch.load(path))
        return True


# ---------- liens ----------

def link_tokens(href: str, text: str) -> list:
    path = re.sub(r"https?://[^/]+", "", href or "")
    return tokens(text) + [(t + BUCKETS // 2) % BUCKETS for t in tokens(" ".join(re.split(r"[/_\-.?=&]+", path)))]


class ReseauLiens(nn.Module):
    def __init__(self):
        super().__init__()
        self.emb = nn.EmbeddingBag(BUCKETS, DIM // 2, mode="mean")
        self.mlp = nn.Sequential(nn.Linear(DIM // 2 + 1, 32), nn.ReLU(), nn.Linear(32, 1))

    def forward(self, bag, depth):
        return self.mlp(torch.cat([self.emb(*bag), depth], dim=1)).squeeze(1)


class Navigateur:
    """Politique d'exploration : apprend en ligne quels liens mènent à des formulaires.

    Chaque lien suivi est mémorisé ; sa cible vaut 1 s'il mène à un formulaire, gamma^k s'il y mène
    en k clics de plus, 0 sinon. Le réseau est réentraîné régulièrement sur cette mémoire.
    """

    def __init__(self, gamma: float = 0.6, lr: float = 1e-2):
        self.net, self.gamma = ReseauLiens(), gamma
        self.opt = torch.optim.Adam(self.net.parameters(), lr=lr)
        self.memory = {}          # url -> [tokens, profondeur, cible]

    def scores(self, links: list) -> list:
        """links : [(href, texte, profondeur)] → probabilités."""
        if not links:
            return []
        self.net.eval()
        with torch.no_grad():
            bag = _bag([link_tokens(h, t) for h, t, _ in links])
            depth = torch.tensor([[d / 5] for _, _, d in links], dtype=torch.float)
            return torch.sigmoid(self.net(bag, depth)).tolist()

    def observe(self, url: str, href_text: tuple, depth: int) -> None:
        if url not in self.memory:
            self.memory[url] = [link_tokens(*href_text), depth, 0.0]

    def reward(self, chain: list) -> None:
        """chain : adresses du formulaire trouvé jusqu'à la racine ; récompense dégressive en remontant."""
        for k, url in enumerate(chain):
            if url in self.memory:
                self.memory[url][2] = max(self.memory[url][2], self.gamma ** k)

    def learn(self, steps: int = 30) -> float:
        items = list(self.memory.values())
        if len(items) < 4:
            return 0.0
        self.net.train()
        loss_fn, loss = nn.BCEWithLogitsLoss(), torch.tensor(0.0)
        for _ in range(steps):
            batch = random.sample(items, min(64, len(items)))
            bag = _bag([b[0] for b in batch])
            depth = torch.tensor([[b[1] / 5] for b in batch], dtype=torch.float)
            loss = loss_fn(self.net(bag, depth), torch.tensor([b[2] for b in batch]))
            self.opt.zero_grad()
            loss.backward()
            self.opt.step()
        return float(loss)

    def sauver(self, path: Path = SAVE_DIR / "liens.pt") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.net.state_dict(), path)

    def charger(self, path: Path = SAVE_DIR / "liens.pt") -> bool:
        if not path.exists():
            return False
        self.net.load_state_dict(torch.load(path))
        return True
