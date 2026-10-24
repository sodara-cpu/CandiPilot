# Architecture de déploiement — CandiPilot

## Vue d'ensemble

Le serveur est hébergé sur **Render** (site + base PostgreSQL). Le robot ne tourne plus sur un
serveur : il tourne dans **l'extension CandiPilot du navigateur de chaque utilisateur**, avec les
sessions que l'utilisateur a déjà (JobTeaser, LinkedIn…). Aucun mot de passe n'est confié à CandiPilot,
les protections anti-robots ne bloquent pas un vrai navigateur, et chaque utilisateur fait son propre
travail : le service passe à l'échelle sans machine dédiée.

```mermaid
flowchart LR
  subgraph U[Navigateur de chaque utilisateur]
    W[Appli CandiPilot<br/>onglet web]
    X[Extension CandiPilot<br/>robot + remplissage]
    S[Sites où il est déjà connecté<br/>JobTeaser, LinkedIn, Workday…]
    W -- liaison automatique --> X
    X -- lit / remplit / postule --> S
  end
  subgraph R[Render]
    API[FastAPI<br/>comptes, parcours, matching,<br/>lettres PDF, file de tâches]
    DB[(PostgreSQL<br/>données + CV + lettres)]
    API --> DB
  end
  W -- HTTPS --> API
  X -- jeton : tâches / résultats --> API
  GH[GitHub<br/>main / develop] -- déploiement auto --> API
  GH --> CI[GitHub Actions<br/>tests SQLite, PostgreSQL,<br/>extension, image Docker]
```

## Le robot dans l'extension

1. **Installation** : l'utilisateur télécharge l'extension depuis l'appli (`/extension.zip`) et la charge
   dans Chrome (mode développeur), en attendant sa publication sur le Chrome Web Store.
2. **Liaison** : dès qu'il ouvre CandiPilot en étant connecté, l'extension reçoit un jeton
   (`POST /api/agent/link`). Rien à saisir.
3. **Sites** : l'extension vérifie sur quels sites l'utilisateur est connecté et le signale
   (`PUT /api/agent/sites`). Le portail JobTeaser de son école est déduit de ses cookies.
4. **Tâches** : toutes les 30 s, elle demande ses tâches (`GET /api/agent/next`) — recherche, lecture
   d'un formulaire, candidature, vérification — et les exécute dans un onglet « robot » réduit, puis
   renvoie le résultat. Les règles restent sur le serveur : pas d'envoi sans « Valider et démarrer »,
   pas de doublon, « Envoyée » seulement avec une preuve.
5. **N'importe quel site** : le bouton « Remplir ce formulaire » de l'extension remplit le formulaire de
   l'onglet ouvert (Workday, Greenhouse, site d'entreprise…) sans jamais l'envoyer.

Fichiers : `extension/` (manifest, `background.js`, robot `jobteaser.js`, remplisseur `filler.js`,
liaison `link.js`, fenêtre `popup.*`) ; côté serveur `app/agent.py`.

## Environnements

| | Production | Recette | Développement local |
|---|---|---|---|
| Hébergement | Render (`render.yaml`) | Mac : `./deploy.sh recette` | Mac : `./start.sh` |
| Branche | `main` (déploiement automatique) | `develop` | branche de travail |
| Base | PostgreSQL Render | SQLite `data-recette/` | SQLite `data/` |
| Robot | extension des utilisateurs | simulé | extension, ou `CANDIPILOT_ROBOT=local` |

## Mettre en ligne sur Render (une fois)

1. Sur [render.com](https://render.com), connecté au compte qui héberge déjà WeSkool :
   **New → Blueprint**, choisir le dépôt **sodara-cpu/CandiPilot**, branche **main**, puis **Apply**.
   Render crée le site `candipilot` (Docker) et la base `candipilot-db`.
2. Dans le service `candipilot` → **Environment** : remplir `CANDIPILOT_SMTP_USER`,
   `CANDIPILOT_SMTP_PASSWORD` (mot de passe d'application Gmail) et `CANDIPILOT_SMTP_FROM`.
3. Chaque push sur `main` redéploie automatiquement.

Offre gratuite : le site se met en veille après 15 min sans visite (réveil en ~1 min) et la base
gratuite expire au bout de 30 jours ; passer aux offres payantes pour un vrai lancement.

## Branches et cycle de livraison

1. On développe sur `develop`. Chaque push lance les tests GitHub Actions (`.github/workflows/ci.yml`) :
   SQLite, PostgreSQL, extension de bout en bout, construction de l'image Docker, mini-projet d'apprentissage.
2. On vérifie en recette (`git checkout develop && ./deploy.sh recette`).
3. On fusionne dans `main` : Render redéploie la production.

## Configuration (variables d'environnement)

| Variable | Rôle |
|---|---|
| `DATABASE_URL` | PostgreSQL (fourni par Render) ; sans elle, SQLite dans `CANDIPILOT_DATA` |
| `CANDIPILOT_ROBOT` | `extension` (défaut), `local` (robot Playwright sur le serveur) ou `simule` |
| `CANDIPILOT_FAKE_ROBOT=1` | robot simulé (tests) |
| `CANDIPILOT_SECURE_COOKIE=1` | cookie de session en HTTPS uniquement |
| `CANDIPILOT_TRUST_PROXY=1` | adresse IP réelle des visiteurs derrière le proxy de Render |
| `CANDIPILOT_PUBLIC_URL` | adresse publique des e-mails (Render : `RENDER_EXTERNAL_URL` automatique) |
| `CANDIPILOT_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` | e-mails « mot de passe oublié » |

En local, ces réglages privés se mettent dans `.env` (modèle `.env.example`), chargé par `start.sh` et `deploy.sh`.

## Données et sécurité

- CV et lettres sont stockés dans la base (table `files`) : le disque de Render est effacé à chaque déploiement.
- Le jeton de l'extension ne donne accès qu'aux données de son propriétaire ; seule son empreinte est stockée.
- Jamais publiés sur GitHub (`.gitignore`) : `.env`, `.user-profile/`, `config/profile.yaml`,
  `config/offers.csv`, `data/`, `data-recette/`, `output/`.

## Limites connues

- L'extension doit être chargée en mode développeur tant qu'elle n'est pas publiée sur le Chrome Web Store
  (compte développeur Google : 5 $ une fois).
- Les tâches attendent que le navigateur de l'utilisateur soit ouvert (l'extension travaille quand Chrome tourne).
- Robot complet : JobTeaser. LinkedIn et Welcome to the Jungle : détection de connexion. Autres sites :
  remplissage du formulaire ouvert, l'utilisateur envoie lui-même.
