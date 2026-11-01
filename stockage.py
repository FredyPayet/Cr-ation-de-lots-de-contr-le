"""
stockage.py — Sauvegarde des réglages et de l'archive, lecture du Google Sheet
==============================================================================

Les données sont enregistrées dans le dossier « donnees/ » à côté de l'application :
    - parametres.json      : pourcentages par bureau, lien Google Sheet
    - archive_synthese.csv : une ligne par date d'export archivée
    - archive_detail.csv   : détail par bénéficiaire et fiche CEE

⚠️ Sur Streamlit Community Cloud, ce dossier peut être vidé lors d'un redémarrage
de l'application. Pensez à télécharger l'archive régulièrement (bouton prévu) :
elle peut être réimportée à tout moment.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd

from regles import normaliser, normaliser_siren

DOSSIER = Path(__file__).parent / "donnees"
FICHIER_PARAMETRES = DOSSIER / "parametres.json"
FICHIER_SYNTHESE = DOSSIER / "archive_synthese.csv"
FICHIER_DETAIL = DOSSIER / "archive_detail.csv"

COLONNES_SYNTHESE = ["date_export", "date_archivage", "nb_operations_a_integrer",
                     "nb_lots_a_creer", "nb_operations_ajoutees_lots_existants", "nb_dossiers"]
COLONNES_DETAIL = ["date_export", "beneficiaire", "fiche", "nb_operations"]


# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------

def charger_parametres() -> dict:
    try:
        return json.loads(FICHIER_PARAMETRES.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def enregistrer_parametres(valeurs: dict) -> None:
    DOSSIER.mkdir(exist_ok=True)
    actuels = charger_parametres()
    actuels.update(valeurs)
    FICHIER_PARAMETRES.write_text(json.dumps(actuels, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def charger_archive() -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        synthese = pd.read_csv(FICHIER_SYNTHESE, dtype={"date_export": str})
    except (FileNotFoundError, pd.errors.EmptyDataError):
        synthese = pd.DataFrame(columns=COLONNES_SYNTHESE)
    try:
        detail = pd.read_csv(FICHIER_DETAIL, dtype={"date_export": str})
    except (FileNotFoundError, pd.errors.EmptyDataError):
        detail = pd.DataFrame(columns=COLONNES_DETAIL)
    return synthese, detail


def date_deja_archivee(date_iso: str) -> bool:
    synthese, _ = charger_archive()
    return date_iso in set(synthese["date_export"].astype(str))


def archiver(synthese_jour: dict, detail_jour: pd.DataFrame) -> bool:
    """Ajoute l'archive du jour. Renvoie False si cette date d'export existe déjà."""
    if date_deja_archivee(synthese_jour["date_export"]):
        return False
    synthese, detail = charger_archive()
    synthese = pd.concat([synthese, pd.DataFrame([synthese_jour])], ignore_index=True)
    detail = pd.concat([detail, detail_jour], ignore_index=True)
    _ecrire(synthese, detail)
    return True


def _ecrire(synthese: pd.DataFrame, detail: pd.DataFrame) -> None:
    DOSSIER.mkdir(exist_ok=True)
    synthese.sort_values("date_export").to_csv(FICHIER_SYNTHESE, index=False)
    detail.sort_values(["date_export", "beneficiaire", "fiche"]).to_csv(FICHIER_DETAIL, index=False)


def archive_en_excel() -> bytes:
    """Archive complète en un fichier Excel (2 onglets) pour la sauvegarder."""
    synthese, detail = charger_archive()
    tampon = io.BytesIO()
    with pd.ExcelWriter(tampon, engine="openpyxl") as w:
        synthese.to_excel(w, sheet_name="Synthese", index=False)
        detail.to_excel(w, sheet_name="Detail", index=False)
    return tampon.getvalue()


def importer_archive(fichier) -> tuple[int, int]:
    """
    Réimporte une archive Excel téléchargée auparavant.
    Les dates déjà présentes ne sont pas dupliquées.
    Renvoie (dates ajoutées, dates ignorées car déjà présentes).
    """
    feuilles = pd.read_excel(fichier, sheet_name=None, dtype={"date_export": str})
    if "Synthese" not in feuilles or "Detail" not in feuilles:
        raise ValueError("Ce fichier ne ressemble pas à une archive : il doit contenir "
                         "les onglets « Synthese » et « Detail ».")
    nouv_s, nouv_d = feuilles["Synthese"], feuilles["Detail"]
    for t in (nouv_s, nouv_d):
        t["date_export"] = pd.to_datetime(t["date_export"]).dt.strftime("%Y-%m-%d")
    synthese, detail = charger_archive()
    connues = set(synthese["date_export"].astype(str))
    a_ajouter = [d for d in nouv_s["date_export"].unique() if d not in connues]
    synthese = pd.concat([synthese, nouv_s[nouv_s["date_export"].isin(a_ajouter)]], ignore_index=True)
    detail = pd.concat([detail, nouv_d[nouv_d["date_export"].isin(a_ajouter)]], ignore_index=True)
    _ecrire(synthese, detail)
    return len(a_ajouter), nouv_s["date_export"].nunique() - len(a_ajouter)


# ---------------------------------------------------------------------------
# Google Sheet des clients spécifiques (lecture seule, lien public)
# ---------------------------------------------------------------------------

def lien_vers_csv(url: str) -> str:
    """Transforme le lien de partage Google Sheet en lien de téléchargement CSV."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url or "")
    if not m:
        raise ValueError("Ce lien ne ressemble pas à un lien Google Sheet. Copiez le lien "
                         "depuis le bouton « Partager » du fichier.")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
            + (f"&gid={gid.group(1)}" if gid else ""))


def clients_depuis_tableau(tableau: pd.DataFrame) -> dict:
    """Repère les colonnes SIREN et nom du client, renvoie {SIREN: nom}."""
    colonnes = {normaliser(c): c for c in tableau.columns}
    col_siren = next((colonnes[c] for c in colonnes if "siren" in c), None)
    if col_siren is None:
        raise ValueError("Le Google Sheet doit contenir une colonne « SIREN ».")
    col_nom = next((colonnes[c] for c in colonnes if c != normaliser(col_siren)
                    and any(m in c for m in ("nom", "client", "raison"))), None)
    resultat = {}
    for _, ligne in tableau.iterrows():
        s = normaliser_siren(ligne[col_siren])
        if s:
            nom = str(ligne[col_nom]).strip() if col_nom and pd.notna(ligne[col_nom]) else ""
            resultat[s] = nom or f"SIREN {s}"
    return resultat


def lire_clients_gsheet(url: str) -> dict:
    """Lit le Google Sheet public (SIREN + nom du client)."""
    try:
        tableau = pd.read_csv(lien_vers_csv(url), dtype=str)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("Le Google Sheet n'a pas pu être lu. Vérifiez que le partage est "
                         "réglé sur « Tous les utilisateurs disposant du lien ». "
                         f"(Détail : {e})") from e
    return clients_depuis_tableau(tableau)
