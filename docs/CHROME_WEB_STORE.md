# Publier l'extension CandiPilot sur le Chrome Web Store

Objectif : que les utilisateurs installent l'extension **en un clic** (« Ajouter à Chrome »), sans mode
développeur. Seul le propriétaire du projet peut le faire (compte Google + 5 $ une seule fois).

## Ce qui est prêt

| Élément | Où |
|---|---|
| Paquet à envoyer | `dist/candipilot-extension-0.1.0.zip` (régénérer : `.venv/bin/python scripts/build_extension_store.py`) |
| Captures 1280×800 | `dist/store/1_accueil.png`, `2_offres.png`, `3_envoi.png`, `4_extension.png` |
| Icône 128×128 | `extension/icon128.png` |
| Politique de confidentialité | `https://candipilot.onrender.com/confidentialite` (en ligne après la prochaine mise en production) |

Le paquet « Store » n'autorise que le site de production (`https://candipilot.onrender.com`) et les sites
d'offres (JobTeaser, LinkedIn, Welcome to the Jungle). Le remplissage des autres formulaires utilise
`activeTab` : une autorisation ponctuelle, donnée seulement quand l'utilisateur clique sur l'extension.

## Étapes (environ 30 minutes, puis quelques jours de vérification par Google)

1. **Compte développeur** : https://chrome.google.com/webstore/devconsole → se connecter avec le compte
   Google du projet → accepter le contrat → payer les **5 $** d'inscription.
2. **Nouvel élément** → importer `dist/candipilot-extension-0.1.0.zip`.
3. **Fiche du Store** : copier les textes ci-dessous, ajouter l'icône et les 4 captures.
4. **Confidentialité** : remplir avec les réponses ci-dessous.
5. **Distribution** : « Non répertoriée » pour commencer (installable avec le lien, invisible dans la
   recherche du Store), puis « Publique » quand tout est validé.
6. **Envoyer pour examen.** Une fois l'extension publiée, copier son adresse
   (`https://chromewebstore.google.com/detail/candipilot/...`) et la mettre dans Render, variable
   `CANDIPILOT_EXTENSION_URL` : le bouton « Ajouter à Chrome » apparaît alors dans l'appli.

## Textes de la fiche

**Nom** : CandiPilot

**Résumé (132 caractères max)** :
Ton copilote de candidatures : il cherche les offres et prépare tes candidatures avec tes propres sessions, sans jamais envoyer seul.

**Description** :
CandiPilot aide les étudiants et jeunes diplômés à trouver un stage ou un emploi, sans y passer leurs soirées.

• Cherche les offres qui correspondent à tes critères sur les sites où tu es déjà connecté (JobTeaser…).
• Prépare chaque dossier : ton CV, une lettre adaptée à l'offre, les réponses aux questions.
• Remplit n'importe quel formulaire de candidature en un clic (Workday, Greenhouse, site d'entreprise…) ; tu vérifies et tu envoies toi-même.
• Suit tes candidatures : envoyées, en cours, à vérifier.

Tu gardes le contrôle : aucune candidature n'est envoyée sans ta validation, et une candidature n'est
« envoyée » qu'avec une preuve de réception. CandiPilot ne voit jamais tes mots de passe : il travaille
dans ton navigateur, avec tes propres sessions.

Fonctionne avec ton compte CandiPilot : https://candipilot.onrender.com

**Catégorie** : Productivité  **Langue** : Français

## Confidentialité (formulaire du Store)

**Objectif unique** :
Aider l'utilisateur à chercher des offres d'emploi et à préparer ou remplir ses candidatures, à sa demande, sur les sites de recrutement.

**Justification des permissions** :

| Permission | Justification |
|---|---|
| `storage` | Mémoriser la liaison au compte CandiPilot de l'utilisateur et l'état de ses sites. |
| `alarms` | Vérifier régulièrement si l'utilisateur a demandé une tâche (recherche, candidature) dans CandiPilot. |
| `tabs` | Ouvrir un onglet réduit pour exécuter les recherches et candidatures demandées, et suivre son chargement. |
| `scripting` | Lire les offres et remplir les formulaires de candidature dans cet onglet, à la demande de l'utilisateur. |
| `cookies` | Savoir si l'utilisateur est connecté à JobTeaser et trouver le portail de son école. Les cookies ne sont jamais transmis. |
| `activeTab` | Remplir le formulaire de l'onglet ouvert quand l'utilisateur clique sur « Remplir ce formulaire ». |
| Accès aux sites (JobTeaser, LinkedIn, Welcome to the Jungle, CandiPilot) | Rechercher des offres et postuler sur ces sites ; se relier au compte CandiPilot de l'utilisateur. |

**Code distant** : Non, aucun code n'est chargé à distance.

**Données utilisées** (cocher) :
- Informations permettant d'identifier l'utilisateur : **oui** (nom, e-mail, téléphone saisis dans son profil, pour remplir ses candidatures).
- Contenu de sites web : **oui** (offres et formulaires de candidature lus pour l'utilisateur).
- Identifiants d'authentification, historique de navigation, communications personnelles, localisation, données financières ou de santé : **non**.

**Engagements** (cocher les trois) : pas de vente à des tiers ; pas d'usage sans lien avec l'objectif unique ;
pas d'usage pour évaluer la solvabilité ou accorder des prêts.

**Politique de confidentialité** : https://candipilot.onrender.com/confidentialite

## Avant d'envoyer

- La page `/confidentialite` doit être en ligne : elle partira avec la prochaine mise en production (à valider).
- Google peut demander des précisions sur l'automatisation de JobTeaser. Le registre des sites (lot 1) permettra
  de désactiver une plateforme qui n'autorise pas l'automatisation, sans republier l'extension.
