"""Parcours complet dans l'interface (navigateur invisible), serveur en robot simulé.

    .venv/bin/python tests/test_ui.py http://127.0.0.1:8765 <dossier_captures>
"""
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "ui_shots")
OUT.mkdir(parents=True, exist_ok=True)
PDF = OUT / "cv_test.pdf"
PDF.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")

errors = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda m: m.type == "error" and "401" not in m.text and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    shot = lambda name: page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)

    page.goto(BASE)
    expect(page.get_by_role("heading", name="Connexion")).to_be_visible()
    page.get_by_role("link", name="Créer un compte").click()
    page.fill("input[name=first_name]", "Christian")
    page.fill("input[name=last_name]", "Sodara")
    page.fill("input[name=email]", f"ui{uuid.uuid4().hex[:6]}@example.com")
    page.fill("input[name=password]", "motdepasse1")
    page.get_by_role("button", name="Créer mon compte").click()
    expect(page.get_by_role("heading", name="Profil & documents")).to_be_visible()
    print("✓ inscription → profil")

    page.fill("input[name=phone]", "+33600000000")
    page.fill("input[name=availability]", "Mars 2027")
    page.fill("input[name=duration]", "6 mois")
    page.fill("input[name=languages]", "Français (natif), anglais (courant)")
    page.fill("input[name=internship_agreement]", "Oui, fournie par l'école")
    page.fill("input[name=competences]", "data python finance")
    page.select_option("select[name=gender]", "Homme")
    page.get_by_role("button", name="Enregistrer le profil").click()
    expect(page.locator("#toast")).to_contain_text("Profil enregistré")
    page.set_input_files("#up input[type=file]", str(PDF))
    page.get_by_role("button", name="Importer (PDF)").click()
    expect(page.get_by_text("Par défaut", exact=True)).to_be_visible()
    shot("1_profil")
    print("✓ profil + CV")

    page.get_by_role("link", name="Critères", exact=True).click()
    for kw in ("data", "finance"):
        page.fill("input[data-list=metiers]", kw)
        page.press("input[data-list=metiers]", "Enter")
    page.fill("input[name=start_from]", "2027-03-01")
    page.get_by_role("button", name="Enregistrer et lancer une recherche").click()
    expect(page.locator("#toast")).to_contain_text("Recherche lancée")
    shot("2_criteres")
    print("✓ critères + recherche")

    page.get_by_role("link", name="Accueil", exact=True).click()
    for _ in range(30):
        if page.locator("[data-prepare]").count():
            break
        time.sleep(1)
        page.reload()
    shot("3_accueil_offres")
    page.locator("[data-prepare]").first.click()
    expect(page.get_by_role("heading", name="Votre décision")).to_be_visible()
    expect(page.get_by_role("button", name="Valider la candidature")).to_be_visible(timeout=30000)
    print("✓ préparation d'un dossier")

    if page.locator("#qForm").count():
        expect(page.get_by_role("button", name="Valider la candidature")).to_be_disabled()
        page.locator("#qForm textarea").first.fill("Le métier me passionne.")
        page.get_by_role("button", name="Enregistrer mes réponses").click()
        expect(page.locator("#toast")).to_contain_text("Réponses enregistrées")
        print("✓ question ouverte bloque puis débloque la validation")
    shot("4_validation")
    expect(page.get_by_role("button", name="Valider la candidature")).to_be_enabled()
    page.get_by_role("button", name="Valider la candidature").click()
    expect(page.get_by_text("Candidature envoyée", exact=True)).to_be_visible(timeout=30000)
    shot("5_envoyee")
    print("✓ validation → envoyée avec preuve")

    page.get_by_role("link", name="Candidatures", exact=True).click()
    expect(page.get_by_role("heading", name="Mes candidatures")).to_be_visible()
    shot("6_kanban")
    page.get_by_role("button", name="Liste").click()
    expect(page.locator("table")).to_be_visible()
    page.get_by_role("button", name="Kanban").click()
    print("✓ kanban / liste")

    page.get_by_role("link", name="Offres", exact=True).click()
    page.get_by_role("button", name="Ajouter une offre").click()
    page.fill(".modal input[name=url]", "https://skema.jobteaser.com/fr/job-offers/ui-test")
    page.fill(".modal input[name=poste]", "Analyste <b>XSS</b>")
    page.fill(".modal input[name=entreprise]", "Banque Orsay")
    page.get_by_role("button", name="Ajouter", exact=True).click()
    expect(page.get_by_text("Analyste <b>XSS</b>")).to_be_visible()
    shot("7_offres")
    print("✓ ajout d'offre (HTML échappé)")

    page.get_by_role("link", name="Accueil", exact=True).click()
    expect(page.get_by_role("heading", name="Votre copilote")).to_be_visible()
    page.get_by_role("button", name="Mettre en pause").click()
    expect(page.get_by_role("button", name="Reprendre")).to_be_visible()
    page.get_by_role("button", name="Reprendre").click()
    shot("8_accueil")
    print("✓ pause / reprise du copilote")

    page.get_by_role("button", name="Se déconnecter").click()
    expect(page.get_by_role("heading", name="Connexion")).to_be_visible()
    print("✓ déconnexion")
    b.close()

print("Erreurs console :", errors or "aucune")
sys.exit(1 if errors else 0)
