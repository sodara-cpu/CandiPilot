"""Lecture du CV : ce qui est proposé au candidat, et rien n'est enregistré sans sa validation.

    .venv/bin/python tests/test_cv.py http://127.0.0.1:8765
"""
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app.cv import extract, pdf_text  # noqa: E402
from test_api import Client  # noqa: E402

passed = 0


def simple_pdf(lines: list) -> bytes:
    """PDF minimal (texte seul, police standard) : un vrai PDF, sans dépendre d'aucun profil."""
    esc = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def check(cond, label):
    global passed
    if not cond:
        print("✗", label)
        sys.exit(1)
    passed += 1
    print("✓", label)


CV_TEXT = """Jean DUPONT
jean.dupont@gmail.com | +33 6 12 34 56 78 | linkedin.com/in/jean-dupont
Étudiant en Master Finance — SKEMA Business School, Lille
Expériences : Analyste M&A (stage), BNP Paribas, Paris
Langues : Français (natif), Anglais (courant), Espagnol (intermédiaire)"""

# 1. Extraction depuis le texte
f = extract(CV_TEXT)
check(f.get("first_name") == "Jean" and f.get("last_name") == "Dupont", "prénom et nom (NOM en majuscules)")
check(f.get("email") == "jean.dupont@gmail.com", "e-mail")
check(f.get("phone") == "+33 6 12 34 56 78", "téléphone")
check(f.get("linkedin") == "https://linkedin.com/in/jean-dupont", "profil LinkedIn")
check("SKEMA Business School" in f.get("school", ""), "école")
check(f.get("languages") == "Français, Anglais, Espagnol", "langues")
check("gender" not in f and "availability" not in f, "rien n'est deviné (genre, disponibilité)")
check(extract("") == {}, "CV vide : aucune proposition")

# 2. Vrai PDF : lecture du texte
tmp = Path(tempfile.mkdtemp())
pdf = tmp / "cv.pdf"
pdf.write_bytes(simple_pdf(["Jean DUPONT", "jean.dupont@gmail.com | +33 6 12 34 56 78", "Master Finance - SKEMA Business School, Lille",
                            "Langues : Francais (natif), Anglais (courant)"]))
check("jean.dupont@gmail.com" in pdf_text(pdf.read_bytes()), "texte lu dans un vrai PDF")

# 3. API : propositions seulement, aucune écriture sans validation
if len(sys.argv) > 1:
    c = Client()
    c.opener.addheaders.append(("CF-Connecting-IP", f"10.7.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"))
    c.req("POST", "/api/auth/register", {"email": f"cv{uuid.uuid4().hex[:6]}@example.com", "password": "motdepasse1"})
    s, d = c.req("POST", "/api/documents", {"type": "cv"}, files={"file": ("cv.pdf", pdf.read_bytes())})
    s, r = c.req("POST", f"/api/documents/{d['id']}/extract")
    fields = {x["field"]: x["value"] for x in r["suggestions"]}
    check(s == 200 and fields.get("phone") == "+33 6 12 34 56 78" and fields.get("first_name") == "Jean", "API : propositions tirées du CV")
    check("email" not in fields, "API : l'e-mail de connexion n'est pas proposé")
    check(c.req("GET", "/api/profile")[1]["phone"] == "", "API : rien n'est enregistré sans validation")
    c.req("PUT", "/api/profile", {"phone": fields["phone"]})
    s, r = c.req("POST", f"/api/documents/{d['id']}/extract")
    check("phone" not in {x["field"] for x in r["suggestions"]}, "API : un champ déjà rempli à l'identique n'est plus proposé")
    check(c.req("POST", "/api/documents/inconnu/extract")[0] == 404, "API : CV d'un autre ou inexistant refusé")

print(f"\n{passed} vérifications réussies.")
