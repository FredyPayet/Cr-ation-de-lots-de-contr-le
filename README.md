# Création des lots de contrôle CEE

Application Streamlit qui transforme l'export ODICEE
(`Export_Controle_lots_de_travaux_BS_AAAA-MM-JJ.xlsx`) en lots de contrôle prêts à envoyer
aux bureaux de contrôle, et qui archive les résultats de chaque export.

## Contenu du dossier

| Fichier | Rôle |
|---|---|
| `app.py` | Interface (écrans, boutons, graphiques) |
| `regles.py` | **Toutes les règles métier** (éligibilité, types de lot, construction des lots, bureaux) |
| `exports.py` | Fichiers Excel téléchargés (un fichier par fiche CEE, un onglet par lot) |
| `stockage.py` | Archive et réglages (Google Sheet ou dossier local), lecture de la liste des clients |
| `rapport_pdf.py` | Rapport PDF hebdomadaire (schéma du flux, indicateurs, graphiques) |
| `messagerie.py` | Mail hebdomadaire : texte formel et création du brouillon Gmail |
| `requirements.txt` | Bibliothèques Python nécessaires |
| `.streamlit/config.toml` | Couleurs et taille maximale des fichiers chargés |
| `.streamlit/secrets.toml.exemple` | Modèle des « secrets » pour le Google Sheet d'archive |

Sans Google Sheet configuré, l'archive et les réglages sont enregistrés dans le dossier
`donnees/`, créé automatiquement. Ce stockage est **temporaire** sur Streamlit Cloud.

## Tester sur votre ordinateur

```bash
pip install -r requirements.txt
streamlit run app.py
```
L'application s'ouvre dans le navigateur (http://localhost:8501).

## Déployer sur Streamlit Community Cloud

1. Créez un dépôt GitHub (privé de préférence) et déposez-y tout le contenu de ce dossier,
   **y compris le dossier caché `.streamlit`**.
2. Connectez-vous sur https://share.streamlit.io avec votre compte GitHub.
3. Cliquez sur **Create app**, puis choisissez le dépôt, la branche `main` et le fichier `app.py`.
4. Cliquez sur **Deploy**. Comptez 2 à 3 minutes pour la première installation.
5. Dans **Settings → Sharing**, limitez l'accès aux personnes autorisées par e-mail. Les fichiers
   chargés contiennent des SIREN et des noms de clients.

## Archive permanente dans un Google Sheet (≈ 15 minutes, une seule fois)

Streamlit Cloud vide ses fichiers à chaque redémarrage (mise à jour du code, veille). Pour
conserver toutes les archives, l'application les écrit dans un Google Sheet grâce à un
**compte de service** : une sorte d'utilisateur « robot » Google, gratuit, à qui vous
donnez accès au fichier.

### Étape 1 — Créer le Google Sheet d'archive
1. Créez un Google Sheet vide, par exemple « Archive création des lots ».
2. Copiez son lien depuis la barre d'adresse.

### Étape 2 — Créer le compte de service
1. Ouvrez https://console.cloud.google.com et connectez-vous.
2. En haut, menu des projets → **Nouveau projet** → nom « creation-lots » → **Créer**.
   Sélectionnez ensuite ce projet.
3. Menu ☰ → **API et services → Bibliothèque** → cherchez **Google Sheets API** → **Activer**.
4. Menu ☰ → **IAM et administration → Comptes de service** → **Créer un compte de service**.
   Nommez-le « archive-lots », puis cliquez sur **Créer et continuer**, puis **OK**.
   Aucun rôle n'est nécessaire.
5. Cliquez sur le compte créé → onglet **Clés** → **Ajouter une clé → Créer une clé → JSON**.
   Un fichier `.json` est téléchargé. **Gardez-le secret** : il donne accès au compte.
   ⚠️ Si votre entreprise bloque la création de clés, demandez à votre administrateur Google
   Cloud d'autoriser les clés de compte de service pour ce projet.

### Étape 3 — Donner accès au Google Sheet
1. Ouvrez le fichier `.json` avec le Bloc-notes et copiez l'adresse `client_email`
   (elle ressemble à `archive-lots@creation-lots.iam.gserviceaccount.com`).
2. Dans le Google Sheet d'archive : **Partager** → collez cette adresse → rôle **Éditeur** →
   décochez « Envoyer une notification » → **Partager**.
3. *(Facultatif)* Partagez aussi le Google Sheet des **clients à lots réservés** avec cette
   adresse, en **Lecteur**. Il pourra alors rester privé, sans lien public.

### Étape 4 — Coller les « secrets » dans Streamlit Cloud
1. Sur https://share.streamlit.io, ouvrez votre application → **⋮ → Settings → Secrets**.
2. Copiez le contenu de `.streamlit/secrets.toml.exemple`, puis remplacez chaque valeur par celle
   du fichier `.json` (même nom de champ). Collez le lien du Google Sheet dans `google_sheet_url`.
   - Pour `private_key`, recopiez la valeur telle quelle, **avec les `\n`**, entre guillemets.
3. **Save**. L'application redémarre. En haut du panneau de gauche, vous devez voir :
   « 💾 Archive et réglages enregistrés dans le Google Sheet d'archive ✅ ».

En cas de problème, un message rouge à cet endroit indique quoi corriger. Le plus souvent, le
Google Sheet n'a pas été partagé avec l'adresse `client_email`.

### Ce que contient le Google Sheet
| Onglet | Contenu |
|---|---|
| Synthèse | Une ligne par export archivé : création des lots, 9 indicateurs de suivi et flux d'activité |
| Détail bénéficiaires | Opérations intégrées par bénéficiaire et par fiche CEE |
| Lots par étape | Nombre de lots par étape de contrôle (pour le camembert) |
| Bureaux en contrôle | Lots en cours de contrôle par bureau : ≤ 5 semaines / > 5 semaines |
| Graphiques | Camembert et histogramme empilé du **dernier export archivé**, plus l'histogramme du flux d'activité (un jour par export archivé), mis à jour à chaque archivage |
| Paramètres | Pourcentages des bureaux et lien du Google Sheet clients |

Ne renommez pas les onglets. Vous pouvez ajouter vos propres onglets ou graphiques à côté.

### Reprendre l'archive existante
Avant de mettre la nouvelle version en ligne : onglet **Historique → Sauvegarder ou restaurer
l'archive → Télécharger l'archive complète**. Une fois le Google Sheet configuré, réimportez ce
fichier au même endroit. Les dates déjà présentes ne sont pas dupliquées. Si l'archive locale
existe encore au démarrage, l'application la copie aussi automatiquement dans le Google Sheet.

## Mail hebdomadaire aux managers (brouillon Gmail)

L'onglet **📧 Rapport hebdomadaire** prépare un mail formel. Il contient les chiffres clés de
l'export, leur évolution depuis l'export précédent et le flux de la veille, avec en pièce
jointe un **PDF** : schéma du flux, tableau des indicateurs, camembert, histogramme des
bureaux, flux sur 20 jours ouvrés et évolution des stocks.
Le mail est déposé dans les **Brouillons** de la boîte Gmail. Rien n'est envoyé : vous le
relisez dans Gmail, puis vous cliquez sur Envoyer.

**Chaque semaine** :
1. Chargez l'export et archivez-le.
2. Ouvrez l'onglet 📧 et vérifiez les destinataires et le texte.
3. Cliquez sur **Créer le brouillon dans Gmail**.
4. Dans Gmail → Brouillons, relisez puis envoyez.

### Configuration (≈ 5 minutes, une seule fois)
La connexion utilise un **mot de passe d'application** Google : un code de 16 lettres, propre
à cette application. Ce n'est pas votre mot de passe habituel, et vous pouvez le révoquer à
tout moment.
1. Connectez-vous à **fredy.payet.ps@gmail.com**, puis ouvrez https://myaccount.google.com/security.
2. Activez la **Validation en deux étapes** si ce n'est pas déjà fait (obligatoire).
3. Ouvrez https://myaccount.google.com/apppasswords → nom « Création des lots » → **Créer**.
   Copiez le code de 16 lettres affiché.
4. Dans Streamlit Cloud → **Settings → Secrets**, ajoutez :
   ```toml
   [gmail]
   adresse = "fredy.payet.ps@gmail.com"
   mot_de_passe_application = "abcd efgh ijkl mnop"
   ```
5. **Save**. L'onglet 📧 affiche alors « Boîte d'envoi : fredy.payet.ps@gmail.com ✅ ».

Les destinataires (À / Copie) et la signature s'enregistrent avec le bouton prévu. Avec le
Google Sheet d'archive, ils sont conservés dans l'onglet « Paramètres ».

## Indicateurs de suivi archivés
| Indicateur | Calcul |
|---|---|
| Lots en constitution | Lots uniques (colonne V) à l'étape « En cours de constitution » |
| Lots demande de coordonnées | Lots uniques à l'étape « En demande de coordonnées » |
| Lots en cours de contrôle | Lots uniques à l'étape « En cours de contrôle » |
| Nb d'opé en contrôle | Lignes (N° dossier) à l'étape « En cours de contrôle » |
| Dont lots en contrôle > 5 semaines | Lots en contrôle dont la date d'envoi (colonne Z) date de plus de 35 jours |
| Dont opé en contrôle > 5 semaines | Lignes de ces lots |
| Lots restitués | Lots uniques à l'étape « Lot restitué » |
| Nb d'OP éligibles | Lignes à l'étape « Eligible » |
| Nb d'OP intégrables (délai > 2 mois) | Lignes « Eligible » dont la date réelle de réalisation (à défaut prévisionnelle, colonne M) + 12 mois dépasse aujourd'hui + 2 mois |

Les délais sont calculés à la date du jour de l'archivage. Un lot en contrôle sans date
d'envoi est compté dans « délai respecté ».

## Flux d'activité archivé
Pour chaque lot unique (colonne V), l'application lit la date de création (W), la date d'envoi
(Z) et la date de retour (AA). Si un lot a plusieurs dates, elle garde la plus ancienne. Le
jour de référence est le **jour ouvré qui précède la date de l'export**. Un export du lundi
renvoie au vendredi, et les jours fériés français sont sautés, lundi de Pentecôte compris.

| Indicateur | Calcul |
|---|---|
| Flux : lots créés / opé. | Lots dont la date de création = jour de référence, et nombre de lignes de ces lots |
| Flux : lots envoyés / opé. | Lots dont la date d'envoi = jour de référence, et nombre de lignes |
| Flux : lots restitués / opé. | Lots dont la date de retour = jour de référence, et nombre de lignes |

Le flux dépend seulement de la date de l'export : il est identique quel que soit le jour où
vous archivez.

### Flux entre deux exports
Dès qu'un export plus ancien est archivé, l'application calcule aussi le flux sur **toute la
période entre les deux exports, dates incluses**. Elle compte les lots dont la date de création
(W), d'envoi (Z) ou de retour (AA) est **≥ date de l'export précédent** et **≤ date de l'export
présenté**. Exemple : exports du 03/11 et du 10/11 → période du 03/11 au 10/11 inclus.
- Les flèches du schéma, le graphique jour par jour, le mail et le PDF utilisent alors ce
  flux. Sans export précédent, ils montrent le flux du jour ouvré précédent.
- Comme les deux bornes sont incluses, un lot daté du jour d'un export est compté dans les deux
  périodes qui encadrent cet export.
- Le calcul se fait sur l'export présenté : un lot supprimé entre les deux exports n'est pas compté.
- Pour un export archivé avant cette version, rechargez-le puis cliquez sur
  « ➕ Compléter l'archive ».

Dans l'onglet Historique, le **schéma « Flux du contrôle »** relie trois stocks de lots, avec
les opérations correspondantes. Les flèches portent les flux du jour de référence :
lots créés → **Lots en création** (« En cours de constitution » + « En demande de
coordonnées ») → lots envoyés → **Lots en cours de contrôle** → lots restitués →
**Lots à traiter** (« Lot restitué »). Dans l'onglet Historique, le graphique « Flux d'activité » montre les
20 derniers jours ouvrés, calculés directement à partir de l'export chargé. Dans le Google
Sheet, l'onglet « Graphiques » montre un jour par export archivé (30 derniers).
Pour un export archivé avant cette version : rechargez-le, puis cliquez sur
« ➕ Compléter l'archive ».

## Règles appliquées (résumé)

1. **Opérations retenues** : « Etape de contrôle » = Eligible, « Lot de contrôle » = NEANT,
   non abandonnées, avec une date réelle de réalisation, et dans les régions (DCTS) cochées.
2. **Regroupement par N° dossier + fiche CEE** (nombre d'opérations du dossier pour la fiche).
   - Exclu si une ligne a le statut AH « Sans objet ».
   - Délai de contrôle = date réelle de réalisation (la plus ancienne de la fiche) + 12 mois.
     Le dossier est exclu s'il reste moins de 2 mois.
3. **Type de lot** selon la date réelle d'engagement :
   - Engagement 2019 : du 11/04/2019 au 31/08/2020
   - Engagement 2020-2021 : du 01/09/2020 au 31/12/2021
   - Murs 2021 : BAR-EN-102 du 01/07/2021 au 31/12/2021 (prioritaire sur Engagement 2020-2021)
   - P5 : du 01/01/2022 au 31/12/2025
   - P6 : après le 31/12/2025
4. **Plage de DFT** (facultative) : Fatal = réalisation du 01/01/N au 31/03/N+1 ;
   Non fatal = réalisation après le 31/03/N+1 (N = année de référence, par défaut l'année en cours).
5. **Partie 1** : les dossiers sont d'abord proposés pour compléter les lots « En cours de
   constitution », s'ils ont la même fiche et le même type de lot, sans dépasser 20 opérations.
6. **Partie 3, nouveaux lots** : un lot = une fiche CEE + un type de lot. Par défaut, les DCTS
   sont mélangées ; le mode « Par DCTS et fiche CEE » sépare les régions.
   - Deux dossiers déjà suivis par deux bureaux différents (via une autre fiche) ne peuvent pas
     partager un lot, puisqu'un lot n'a qu'un bureau.
   - Les dossiers les plus urgents sont placés d'abord, pour viser 20 opérations.
   - On cherche un écart de 4 mois maximum entre dates de réalisation, sans en faire une règle bloquante.
   - Un dossier de 20 opérations ou plus forme un lot à lui seul. Un dossier n'est jamais coupé.
   - Les clients du Google Sheet (SIREN) ont des lots réservés.
7. **Bureaux de contrôle** :
   - Un dossier présent sur plusieurs fiches garde le même bureau (prioritaire). Cela vaut
     aussi si une autre fiche est déjà dans un lot existant.
   - Sinon, chaque lot va au bureau le plus en retard sur son pourcentage, en comptant les lots
     en cours (constitution, demande de coordonnées, contrôle).
   - La colonne **Lots liés** liste les lots du même bureau qui contiennent une autre fiche du
     dossier (« RAS » si le bureau vient des pourcentages). Un ⚠️ signale une autre fiche du
     dossier en statut AH « Sans objet », donc pas complète.

Pour modifier une règle (dates, taille cible, délai de 2 mois…), ouvrez `regles.py`. Les
constantes sont regroupées en haut du fichier et dans la classe `Parametres`.

## Google Sheet des clients à lots réservés

- Il faut une colonne contenant le mot **SIREN** et une colonne contenant **nom**, **client**
  ou **raison**.
- Partage, au choix :
  - avec l'adresse du compte de service, en Lecteur (le fichier reste privé) ;
  - « Tous les utilisateurs disposant du lien → Lecteur » ;
  - Fichier → Partager → Publier sur le Web → CSV.
- Collez le lien dans la barre latérale, puis cliquez sur « Enregistrer le lien ».
- Autre solution : déposez directement la liste (Excel ou CSV) dans la barre latérale.
