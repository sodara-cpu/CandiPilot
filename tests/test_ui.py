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
EMAIL = f"ui{uuid.uuid4().hex[:6]}@example.com"

errors = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1440, "height": 1000})
    page.on("console", lambda m: m.type == "error" and "401" not in m.text and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    shot = lambda name: page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    banner = page.locator(".guide")

    # Inscription (avec confirmation du mot de passe)
    page.goto(BASE)
    expect(page.get_by_role("heading", name="Connexion")).to_be_visible()
    page.get_by_role("link", name="Créer un compte").click()
    page.fill("input[name=first_name]", "Alex")
    page.fill("input[name=last_name]", "Martin")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", "motdepasse1")
    page.fill("input[name=confirm]", "autre1234")
    page.get_by_role("button", name="Créer mon compte").click()
    expect(page.locator("#toast")).to_contain_text("pas identiques")
    page.fill("input[name=confirm]", "motdepasse1")
    page.get_by_role("button", name="Créer mon compte").click()
    expect(page.get_by_role("heading", name="Ton parcours en 5 étapes")).to_be_visible()
    expect(page.locator(".steps li.now")).to_contain_text("Complète ton profil")
    shot("1_accueil_parcours")
    print("✓ inscription → parcours en 5 étapes")

    # Étape 1
    page.get_by_role("link", name="Y aller →").click()
    expect(banner).to_contain_text("Il manque : téléphone, date de disponibilité, genre")
    page.fill("input[name=phone]", "+33600000000")
    page.fill("input[name=availability]", "Mars 2027")
    page.select_option("select[name=gender]", "Femme")
    page.get_by_role("button", name="Enregistrer le profil").click()
    expect(banner).to_contain_text("Étape 2 sur 5")
    print("✓ étape 1 : profil complet")

    # Étape 2
    page.get_by_role("link", name="Y aller →").click()
    page.locator("form.upform[data-type=cv] input[type=file]").set_input_files(str(PDF))
    page.locator("form.upform[data-type=cv] button[type=submit]").click()
    expect(page.locator("#toast")).to_contain_text("CV importé")
    expect(banner).to_contain_text("le choix de ta lettre")
    page.get_by_role("button", name="Une lettre adaptée à chaque offre").click()
    expect(banner).to_contain_text("Étape 3 sur 5")
    shot("2_documents")
    print("✓ étape 2 : CV + lettre adaptée")

    # Étape 3
    page.get_by_role("link", name="Y aller →").click()
    page.fill("input[data-list=metiers]", "finance")
    page.press("input[data-list=metiers]", "Enter")
    page.get_by_role("button", name="Rechercher des offres").click()
    expect(page.locator("#toast")).to_contain_text("Aucun envoi")
    for _ in range(30):
        if page.locator("[data-sel][data-on='1']").count() >= 2:
            break
        time.sleep(1)
        page.reload()
    page.locator("[data-sel][data-on='1']").first.click()
    expect(page.locator("#toast")).to_contain_text("rien n'est envoyé")
    page.locator("[data-sel][data-on='1']").first.click()
    expect(banner).to_contain_text("Étape 4 sur 5")
    shot("3_criteres_offres")
    print("✓ étape 3 : recherche + 2 offres sélectionnées")

    # Étape 4 : assisté, préparer = oui, relire = oui
    page.get_by_role("link", name="Continuer : étape 4 →").click()
    page.locator("[data-m=assisted]").click()
    expect(page.get_by_text("Souhaites-tu préparer ou personnaliser ta candidature ?")).to_be_visible()
    page.get_by_role("button", name="Enregistrer mon choix").click()
    expect(page.locator("#toast")).to_contain_text("Réponds aux deux questions")
    page.locator("input[name=prep][value='1']").check()
    page.locator("input[name=rev][value='1']").check()
    page.get_by_role("button", name="Enregistrer mon choix").click()
    expect(page.locator("#toast")).to_contain_text("Aucun envoi")
    page.reload()
    expect(page.locator("input[name=prep][value='1']")).to_be_checked()
    expect(page.locator("[data-m=assisted]")).to_have_attribute("aria-pressed", "true")
    shot("4_mode")
    print("✓ étape 4 : mode assisté + 2 réponses, conservés après actualisation")

    # Étape 5 : récapitulatif, personnalisation + relecture de chaque dossier
    page.goto(BASE + "/#/envoi")
    for _ in range(30):
        if page.locator(".litem").count() == 2 and not page.locator(".blk.wait").count():
            break
        time.sleep(1)
        page.reload()
    expect(page.get_by_role("button", name="Valider et démarrer le robot")).to_be_disabled()
    expect(page.locator(".litem").first).to_contain_text("À relire avant l'envoi")
    shot("5_recap_bloque")
    for _ in range(2):
        page.goto(BASE + "/#/envoi")
        page.locator(".litem a.btn", has_text="Personnaliser").first.click()
        page.get_by_role("button", name="J'ai vérifié ce dossier").click()
        expect(page.locator("#toast")).to_contain_text("Dossier vérifié")
        if page.locator("#qForm").count():
            page.locator("#qForm textarea").first.fill("Le métier me passionne.")
            page.get_by_role("button", name="Enregistrer mes réponses").click()
            expect(page.locator("#toast")).to_contain_text("Réponses enregistrées")
        page.get_by_role("button", name="Autoriser l'envoi").click()
        expect(page.locator("#toast")).to_contain_text("Envoi autorisé")
    page.goto(BASE + "/#/envoi")
    expect(page.locator(".blk.ok")).to_have_count(2)
    shot("6_recap_pret")
    print("✓ étape 5 : dossiers vérifiés et relus, aucun envoi avant validation")

    page.get_by_role("button", name="Valider et démarrer le robot").click()
    page.get_by_role("button", name="Confirmer le lancement").click()
    for _ in range(30):
        if "2" == page.locator(".stat .value").first.inner_text():
            break
        time.sleep(1)
        page.reload()
    expect(page.get_by_role("heading", name="Résultat du traitement")).to_be_visible()
    expect(page.locator(".stat .value").first).to_have_text("2")
    shot("7_resultat")
    print("✓ lancement → 2 envoyées avec preuve")

    page.goto(BASE + "/#/")
    expect(page.get_by_role("heading", name="Ton parcours est terminé")).to_be_visible()
    page.get_by_role("link", name="Candidatures", exact=True).click()
    expect(page.get_by_role("heading", name="Mes candidatures")).to_be_visible()
    page.get_by_role("link", name="Toutes les offres", exact=True).click()
    page.get_by_role("button", name="Ajouter une offre").click()
    page.fill(".modal input[name=url]", "https://skema.jobteaser.com/fr/job-offers/ui-test")
    page.fill(".modal input[name=poste]", "Analyste <b>XSS</b>")
    page.fill(".modal input[name=entreprise]", "Banque Orsay")
    page.get_by_role("button", name="Ajouter", exact=True).click()
    expect(page.get_by_text("Analyste <b>XSS</b>")).to_be_visible()
    print("✓ candidatures, offres (HTML échappé)")

    # Compte et connexion
    page.get_by_role("link", name="Mon compte").click()
    expect(page.get_by_role("heading", name="Appareils connectés")).to_be_visible()
    page.get_by_role("button", name="Se déconnecter", exact=True).click()
    expect(page.get_by_role("heading", name="Connexion")).to_be_visible()
    page.goto(BASE + "/#/mode")
    expect(page.get_by_role("heading", name="Connexion")).to_be_visible()
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", "motdepasse1")
    page.get_by_role("button", name="Se connecter").click()
    expect(page.get_by_role("heading", name="4. Mode de soumission")).to_be_visible()
    print("✓ déconnexion, reconnexion et retour à la page demandée")
    page.get_by_role("button", name="Se déconnecter", exact=True).click()
    page.get_by_role("link", name="Mot de passe oublié ?").click()
    page.fill("input[name=email]", EMAIL)
    page.get_by_role("button", name="Recevoir le lien").click()
    expect(page.get_by_role("heading", name="Vérifie ta boîte mail")).to_be_visible()
    expect(page.get_by_role("status")).to_contain_text("valable 1 heure")
    shot("8_mot_de_passe_oublie")
    print("✓ mot de passe oublié")
    b.close()

print("Erreurs console :", errors or "aucune")
sys.exit(1 if errors else 0)
