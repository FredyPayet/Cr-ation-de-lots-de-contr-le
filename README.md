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
| `stockage.py` | Sauvegarde des réglages et de l'archive, lecture du Google Sheet clients |
| `requirements.txt` | Bibliothèques Python nécessaires |
| `.streamlit/config.toml` | Couleurs et taille maximale des fichiers chargés |

Le dossier `donnees/` est créé automatiquement : il contient les pourcentages enregistrés
et l'archive.

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

⚠️ **Sauvegarde de l'archive** : Streamlit Cloud peut vider le dossier `donnees/` quand
l'application redémarre (mise à jour du code, longue inactivité). Dans l'onglet
**Historique**, utilisez **« Télécharger l'archive complète »** après chaque archivage. Le
bouton **« Réimporter une archive »** restaure tout en un clic. Il en va de même pour les
pourcentages : si besoin, ressaisissez-les puis cliquez sur « Enregistrer les pourcentages ».

## Règles appliquées (résumé)

1. **Opérations retenues** : « Etape de contrôle » = Eligible, « Lot de contrôle » = NEANT,
   non abandonnées, avec une date réelle de réalisation, et dans les régions (DCTS) cochées.
2. **Regroupement par N° dossier + fiche CEE** (nombre d'opérations du dossier pour la fiche).
   - Exclu si une ligne a le statut AH « Sans objet ».
   - Délai de contrôle = date réelle de réalisation (la plus ancienne de la fiche) + 12 mois.
     Le dossier est exclu s'il reste moins de 2 mois.
3. **Type de lot** selon la date réelle d'engagement :
   - Engagement 2019 : du 11/04/2019 au 31/08/2020
   - Engagement 2020 : du 01/09/2020 au 30/06/2021
   - Engagement 2021 : du 01/07/2021 au 31/12/2021
   - Murs 2021 : BAR-EN-102 du 01/07/2021 au 31/12/2021 (prioritaire sur Engagement 2021)
   - P5 : du 01/01/2022 au 31/12/2025
   - P6 : après le 31/12/2025
4. **Plage de DFT** (facultative) : Fatal = réalisation du 01/01/N au 31/03/N+1 ;
   Non fatal = réalisation après le 31/03/N+1 (N = année de référence, par défaut l'année en cours).
5. **Partie 1** : les dossiers sont d'abord proposés pour compléter les lots « En cours de
   constitution », s'ils ont la même fiche et le même type de lot, sans dépasser 20 opérations.
6. **Partie 3, nouveaux lots** : un lot = une fiche CEE + un type de lot (+ une DCTS selon le mode).
   - Les dossiers les plus urgents sont placés d'abord, pour viser 20 opérations.
   - On cherche un écart de 4 mois maximum entre dates de réalisation, sans en faire une règle bloquante.
   - Un dossier de 20 opérations ou plus forme un lot à lui seul. Un dossier n'est jamais coupé.
   - Les clients du Google Sheet (SIREN) ont des lots réservés.
7. **Bureaux de contrôle** :
   - Un dossier présent sur plusieurs fiches garde le même bureau (prioritaire). Cela vaut
     aussi si une autre fiche est déjà dans un lot existant.
   - Sinon, chaque lot va au bureau le plus en retard sur son pourcentage, en comptant les lots
     en cours (constitution, demande de coordonnées, contrôle).

Pour modifier une règle (dates, taille cible, délai de 2 mois…), ouvrez `regles.py`. Les
constantes sont regroupées en haut du fichier et dans la classe `Parametres`.

## Google Sheet des clients à lots réservés

- Il faut une colonne contenant le mot **SIREN** et une colonne contenant **nom**, **client**
  ou **raison**.
- Partage : **Tous les utilisateurs disposant du lien → Lecteur**.
- Collez le lien dans la barre latérale, puis cliquez sur « Enregistrer le lien ».
