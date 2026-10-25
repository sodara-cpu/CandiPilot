"""Paquet de l'extension pour le Chrome Web Store (dist/candipilot-extension-<version>.zip).

Version « Store » du manifeste : seulement l'adresse de production de CandiPilot (pas 127.0.0.1,
localhost ni *.onrender.com, réservés au développement), comme l'exige la revue de Google.

    .venv/bin/python scripts/build_extension_store.py [https://candipilot.onrender.com]
"""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "extension"
SITE = (sys.argv[1] if len(sys.argv) > 1 else "https://candipilot.onrender.com").rstrip("/")

manifest = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
app = SITE + "/*"
manifest["host_permissions"] = [h for h in manifest["host_permissions"]
                                if not any(k in h for k in ("127.0.0.1", "localhost", "onrender.com"))] + [app]
manifest["content_scripts"][0]["matches"] = [app]
out = ROOT / "dist" / f"candipilot-extension-{manifest['version']}.zip"
out.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    for f in sorted(SRC.iterdir()):
        if f.is_file() and f.name != "manifest.json" and not f.name.startswith("."):
            z.write(f, f.name)
print(out.relative_to(ROOT), "—", ", ".join(manifest["host_permissions"]))
