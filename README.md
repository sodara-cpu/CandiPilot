# Job-RPA — Assistant de candidature semi-automatisé

Framework RPA modulaire (Python + Playwright) pour accélérer les candidatures à des
offres d'emploi et de stage. Il ouvre chaque offre, remplit les champs connus, joint
le CV, génère un message de motivation adapté au poste, et soumet — avec garde-fous.

> Copie `config/profile.example.yaml` vers `config/profile.yaml` et `config/offers.sample.csv`
> vers `config/offers.csv`, puis complète-les. Les données de profil par défaut sont dans
> `config/profile.yaml` (à compléter : e-mail, téléphone, chemin du CV).

---

## Philosophie : semi-automatisé, pas « spam »

Cet outil est volontairement **human-in-the-loop** et **respectueux** :

- **Aucun mot de passe stocké ni saisi par le robot.** Il réutilise TA session
  navigateur (profil Chrome/Chromium persistant). Tu te connectes une fois, à la main,
  aux sites et aux ATS ; le robot hérite de tes cookies.
- **Il ne contourne jamais un CAPTCHA ni une détection de bot.** Quand il en voit un,
  il **met en pause** et te rend la main. C'est une limite assumée, pas un bug.
- **Confirmation avant chaque envoi** par défaut (`confirm_before_submit: true`), et un
  mode **simulation** (`dry_run: true`) qui remplit sans soumettre.
- **Rythme poli** : délais aléatoires entre actions, pas de rafales.
- **Journal complet** : chaque tentative est écrite dans `output/applications_log.csv`
  (statut, raison, horodatage) pour faire ton rapport de candidatures.

Avant de l'utiliser à grande échelle, vérifie les **conditions d'utilisation** des sites
visés : beaucoup interdisent l'automatisation. Cet outil t'assiste, il ne te dédouane
pas. Utilise-le sur des offres où postuler toi-même serait simplement fastidieux, pas
pour inonder des recruteurs.

---

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Configuration

1. **`config/profile.yaml`** — tes informations, le chemin de ton CV, le modèle de
   message et les « phrases de rattachement » par domaine.
2. **`config/offers.csv`** — la liste des offres à traiter. Colonnes :
   `url, company, role, domain, start_date, apply_type, status`.
   Pars de `config/offers.sample.csv`.

## Utilisation

```bash
# 1) Simulation (remplit, ne soumet rien) — à faire en premier
python -m src.main --dry-run

# 2) Réel, avec confirmation avant chaque envoi
python -m src.main

# 3) Réel, sans confirmation (à réserver aux sites que tu maîtrises)
python -m src.main --no-confirm

# Filtrer les offres traitées
python -m src.main --domain "Private Debt" --start-from 2027-03-01
```

À la fin, le rapport est dans `output/applications_log.csv` et un résumé est imprimé.

---

## Quel navigateur ? (important)

Réglé dans `config/profile.yaml` → `settings.browser.mode`. Défaut : **`system_chrome`**.

| mode | ce qu'il fait | pour qui |
|---|---|---|
| `system_chrome` | lance **TON Google Chrome** avec **TON profil** : tes sessions déjà ouvertes et le gestionnaire de mots de passe de Chrome sont disponibles | recommandé si tu veux réutiliser tes connexions et ton gestionnaire |
| `persistent` | un Chromium **isolé** dédié à l'outil (`./.user-profile/`) ; tu t'y connectes une fois | si tu ne veux rien mêler à ton Chrome perso |
| `cdp` | se connecte à un Chrome que tu as lancé avec `--remote-debugging-port=9222` | avancé, pour garder Chrome ouvert |

**Mode `system_chrome` (défaut) — à savoir :**
- **FERME complètement Google Chrome avant de lancer l'outil** (⌘Q). Sinon le profil
  est verrouillé et le lancement échoue.
- Chrome affichera un bandeau « contrôlé par un logiciel de test automatisé » : normal.
- L'outil agit dans ta vraie session connectée. C'est ce qui permet d'éviter de
  ressaisir tes identifiants, mais sois conscient qu'il navigue en « toi ».
- **Trouver ton profil** : si tu as plusieurs profils Chrome, ouvre `chrome://version`,
  regarde « Chemin du profil » : le dernier dossier (`Default`, `Profile 1`, …) est à
  mettre dans `chrome_profile_directory`.
- Chemin macOS déjà pré-rempli : `~/Library/Application Support/Google/Chrome`.

Rien de secret ne passe par l'outil : c'est **ton** navigateur et **ton** gestionnaire
qui gèrent l'authentification.

---

## Architecture

```
src/
  main.py            orchestrateur : charge la config, boucle sur les offres
  config.py          chargement profil + offres
  message.py         génère le message de motivation adapté au poste
  tracker.py         journal CSV des candidatures (anti-doublon + rapport)
  browser.py         session Playwright sur un profil persistant (tes logins)
  safety.py          dry-run, confirmation, anti-CAPTCHA (pause), rythme
  adapters/
    base.py          interface commune d'un adaptateur de site
    generic.py       remplisseur heuristique (marche sur beaucoup de formulaires)
    jobteaser.py     candidature simplifiée JobTeaser (sinon détecte la redirection)
    greenhouse.py    ATS Greenhouse (souvent sans compte : bien automatisable)
    lever.py         ATS Lever (souvent sans compte : bien automatisable)
    smartrecruiters.py  ATS SmartRecruiters (parfois login : détecté)
    workday.py       ATS Workday (compte quasi systématique : détecté, pause)
    taleo.py         ATS Taleo/Oracle (compte quasi systématique : détecté, pause)
```

### Ajouter un site

Crée `src/adapters/monsite.py`, hérite de `BaseAdapter`, implémente `matches(url)` et
`apply(page, offer, profile, message)`. Enregistre-le dans `src/adapters/__init__.py`.
Le routeur choisit automatiquement le premier adaptateur dont `matches()` renvoie vrai,
sinon il utilise `generic`.

---

## Limites honnêtes

- Chaque ATS a un formulaire différent ; l'adaptateur générique couvre les cas simples
  (nom, e-mail, upload CV, zone de motivation) mais échouera sur des formulaires
  complexes (questions spécifiques, étapes multiples). Il le signale et passe à la suite.
- Les sites avec création de compte obligatoire, SSO ou CAPTCHA demandent ton
  intervention : le robot s'arrête et te laisse finir.
- Rien ne remplace une vraie personnalisation sur les offres qui comptent.

---

## Application web CandiPilot

Interface de la maquette CandiPilot branchée sur le robot (dossier `app/`).

```bash
./start.sh                                  # puis ouvrir http://127.0.0.1:8000
CANDIPILOT_FAKE_ROBOT=1 ./start.sh          # démonstration : robot simulé, aucun envoi réel
./deploy.sh                                 # production (branche main) : lien https public
./deploy.sh recette                         # recette (branche develop) : robot simulé, port 8001
```

`deploy.sh` (nécessite `brew install cloudflared`) affiche une adresse `https://….trycloudflare.com`.
Le lien fonctionne tant que le script tourne sur le Mac, et change à chaque lancement.
« Mot de passe oublié » envoie un e-mail si `CANDIPILOT_SMTP_HOST` (+ `_PORT`, `_USER`, `_PASSWORD`, `_FROM`)
est défini ; sinon le lien est écrit dans `data/server.log`.

### Compte JobTeaser de chaque utilisateur

Le robot travaille avec le compte JobTeaser **de la personne connectée**, jamais avec celui d'un autre :
chaque utilisateur a son propre navigateur robot (`data/navigateurs/<id>`). À l'étape 3, il indique
l'adresse JobTeaser de son école (ex. `https://skema.jobteaser.com`) puis clique « Connecter mon
JobTeaser » : l'écran du navigateur robot s'affiche dans l'appli, il s'y connecte en cliquant et en
tapant (SSO de l'école, double authentification…). La connexion est détectée seule ; CandiPilot ne
voit ni ne garde le mot de passe. Sans compte connecté, aucune recherche ni aucun envoi réel.

### E-mails (mot de passe oublié)

Copier `.env.example` en `.env` (jamais publié) et le remplir, par exemple avec un mot de passe
d'application Gmail. Tester : `set -a; . ./.env; set +a; .venv/bin/python -m app.mailtest ton@email.fr`.

### Parcours du candidat (5 étapes)

1. **Profil** — prénom, nom, téléphone, disponibilité, genre obligatoires.
2. **CV et lettre** — CV PDF + lettre PDF importée (jamais modifiée) *ou* lettre adaptée à chaque offre.
3. **Critères et offres** — recherche (aucun envoi), puis sélection des offres ; la sélection lance la préparation technique.
4. **Mode de soumission** — Automatique (le robot envoie), Assisté (le robot pré-remplit, tu cliques « Postuler »),
   Semi-assisté (tu postules toi-même). En Assisté / Semi-assisté : « préparer ? » et « relire ? » (Oui / Non).
5. **Valider l'envoi** — récapitulatif, blocages, puis « Valider et démarrer le robot » : le seul bouton qui envoie.
   Résultat : Envoyées / En cours / À vérifier / Non envoyées (avec raison et action).

Tests (robot simulé) : `./tests/run_all.sh` (lancés aussi par GitHub Actions à chaque push).
Architecture de déploiement, environnements et branches : [docs/DEPLOIEMENT.md](docs/DEPLOIEMENT.md).

- **Compte** : inscription, connexion, déconnexion, changement de mot de passe, suppression
  (mots de passe PBKDF2, session par cookie HttpOnly, limite anti-force brute).
- **Profil & documents** : coordonnées, réponses préparées, modèle de lettre, CV/lettres PDF
  (versions, document par défaut). Bouton « Importer le profil du robot » = `config/profile.yaml`.
- **Critères** : métiers, zones, contrats, date de début, score minimal, fréquence de recherche.
- **Offres** : recherche JobTeaser par le robot, ajout/modification/suppression manuelle,
  % de compatibilité (`app/matching.py`).
- **Candidatures** : Découverte → À préparer → À valider → En cours d'envoi → Envoyée / Échec /
  Incertaine / Ignorée. « Envoyée » uniquement avec une preuve (bandeau JobTeaser).
  Modes : automatique, assisté (tu cliques « Postuler maintenant »), manuel.
- Données : `data/candipilot.db` (SQLite), CV dans `data/uploads/`, lettres dans `data/letters/`.
- API documentée : http://127.0.0.1:8000/api/docs
- Tests : `tests/test_api.py` (62 vérifications) et `tests/test_ui.py`, avec un serveur lancé en
  robot simulé (`CANDIPILOT_FAKE_ROBOT=1 CANDIPILOT_DATA=/tmp/cp`).

## Mini-projet « apprentissage » (deep learning)

Le dossier [apprentissage/](apprentissage/README.md) contient un projet séparé : des réseaux de neurones
qui explorent les sites carrières et apprennent seuls la structure de leurs formulaires.
Il n'est pas encore branché sur l'application.
