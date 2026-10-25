"""Exporte le réseau des champs vers l'application CandiPilot (inférence numpy, sans PyTorch sur le serveur).

    cd apprentissage && ../.venv/bin/python export_modele.py [version]

1. Charge modeles/sauvegardes/champs.pt (PyTorch).
2. Compresse les tables d'embeddings en int8 (une échelle par ligne) : ~25 Mo au lieu de 100 Mo.
3. Vérifie sur le juge (donnees/verifiees, vrais formulaires vérifiés à la main, jamais vus à l'entraînement)
   que la version exportée prédit comme PyTorch, et note sa précision.
4. Écrit ../app/models/champs-<version>.npz et l'inscrit dans ../app/models/registry.json (avec sa note).
   La version active n'est remplacée que si sa note sur le juge n'est pas moins bonne (retour arrière possible).
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from evaluation import load_pages  # noqa: E402
from modeles.reseau import LABEL_LIST, ModeleReseau  # noqa: E402

OUT = HERE.parent / "app" / "models"


def quantize(w: np.ndarray) -> tuple:
    scale = np.abs(w).max(axis=1) / 127.0
    scale[scale == 0] = 1.0
    return np.round(w / scale[:, None]).astype(np.int8), scale.astype(np.float32)


def main(version: str) -> None:
    m = ModeleReseau()
    if not m.charger():
        raise SystemExit("Aucun modèle entraîné (modeles/sauvegardes/champs.pt).")
    sd = {k: v.detach().numpy() for k, v in m.net.state_dict().items()}
    own_q, own_s = quantize(sd["own.weight"])
    ctx_q, ctx_s = quantize(sd["ctx.weight"])
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"champs-{version}.npz"
    np.savez_compressed(path, own_q=own_q, own_s=own_s, ctx_q=ctx_q, ctx_s=ctx_s,
                        w1=sd["mlp.0.weight"], b1=sd["mlp.0.bias"], w2=sd["mlp.3.weight"], b2=sd["mlp.3.bias"],
                        labels=np.array(LABEL_LIST))

    # Vérification sur le juge : PyTorch contre l'inférence numpy de l'application.
    from app import fieldmodel
    fieldmodel.load(path)
    judge = load_pages(HERE / "donnees" / "verifiees")
    total = same = ok_torch = ok_np = 0
    for page in judge:
        champs = [c for c in page["champs"] if c.get("etiquette")]
        if not champs:
            continue
        pt = m.predire(champs)
        npy = [label for label, _ in fieldmodel.predict(champs)]
        for c, a, b in zip(champs, pt, npy):
            total += 1
            same += a == b
            ok_torch += a == c["etiquette"]
            ok_np += b == c["etiquette"]
    score = round(100 * ok_np / max(1, total), 1)
    print(f"Juge : {total} champs vérifiés à la main. PyTorch {100 * ok_torch / total:.1f} % ; "
          f"exporté {score} % ; prédictions identiques : {100 * same / total:.1f} %")
    print(f"Fichier : {path.relative_to(HERE.parent)} ({path.stat().st_size / 1e6:.1f} Mo)")

    reg_path = OUT / "registry.json"
    reg = json.loads(reg_path.read_text()) if reg_path.exists() else {"active": "", "versions": {}}
    reg["versions"][version] = {"file": path.name, "judge_accuracy": score, "judge_fields": total, "exported": date.today().isoformat(),
                                "same_as_pytorch": round(100 * same / total, 1)}
    best = reg["versions"].get(reg["active"], {}).get("judge_accuracy", -1)
    if score >= best:
        reg["active"] = version
        print(f"Version active : {version}")
    else:
        print(f"Moins bonne que la version active ({reg['active']} : {best} %) : non activée.")
    reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v1")
