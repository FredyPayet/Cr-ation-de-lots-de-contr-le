"""
exports.py — Fichiers Excel à télécharger
=========================================

Un fichier Excel par fiche CEE, avec un onglet par lot. Chaque onglet liste
les numéros de dossiers (valeurs uniques) et leur nombre d'opérations.
Le tout est regroupé dans un fichier ZIP.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ENTETE = PatternFill("solid", fgColor="1F4E79")
POLICE_ENTETE = Font(color="FFFFFF", bold=True)


def _mettre_en_forme(feuille, ligne_entete: int = 1) -> None:
    """En-tête coloré, largeur des colonnes adaptée, volets figés."""
    for cellule in feuille[ligne_entete]:
        cellule.fill = ENTETE
        cellule.font = POLICE_ENTETE
        cellule.alignment = Alignment(vertical="center", wrap_text=True)
    for ligne in feuille.iter_rows(min_row=ligne_entete + 1):
        for cellule in ligne:
            if isinstance(cellule.value, (date, datetime)):
                cellule.number_format = "DD/MM/YYYY"
    for i, colonne in enumerate(feuille.columns, start=1):
        largeur = max(len(str(c.value)) if c.value is not None else 0 for c in colonne)
        feuille.column_dimensions[get_column_letter(i)].width = min(max(12, largeur + 2), 45)
    feuille.freeze_panes = feuille.cell(row=ligne_entete + 1, column=1)


def _dates(t: pd.DataFrame, colonnes: list[str]) -> pd.DataFrame:
    t = t.copy()
    for c in colonnes:
        t[c] = pd.to_datetime(t[c]).dt.date
    return t


def excel_fiche(fiche: str, lots: pd.DataFrame, affect: pd.DataFrame) -> bytes:
    """Fichier Excel d'une fiche CEE : un onglet « Synthèse » puis un onglet par lot."""
    tampon = io.BytesIO()
    lots_f = lots[lots["fiche"] == fiche].sort_values("num_lot")
    with pd.ExcelWriter(tampon, engine="openpyxl") as w:
        synthese = _dates(lots_f, ["drr_min", "drr_max"]).rename(columns={
            "num_lot": "Lot", "type_lot": "Type de lot", "dcts": "DCTS", "client": "Client spécifique",
            "bureau": "Bureau de contrôle", "nb_dossiers": "Nb dossiers", "nb_ops": "Nb opérations",
            "drr_min": "Réalisation la plus ancienne", "drr_max": "Réalisation la plus récente",
            "ecart_mois": "Écart (mois)", "lots_lies": "Lots liés"})
        synthese = synthese[["Lot", "Type de lot", "DCTS", "Client spécifique", "Bureau de contrôle",
                             "Nb dossiers", "Nb opérations", "Réalisation la plus ancienne",
                             "Réalisation la plus récente", "Écart (mois)", "Lots liés"]]
        synthese.to_excel(w, sheet_name="Synthèse", index=False)
        _mettre_en_forme(w.sheets["Synthèse"])

        for _, lot in lots_f.iterrows():
            contenu = affect[affect["num_lot"] == lot["num_lot"]]
            tableau = (contenu.groupby("dossier", sort=False)["nb_ops"].sum().reset_index()
                       .rename(columns={"dossier": "N° dossier", "nb_ops": "Nombre d'opérations"}))
            nom = f"Lot {lot['num_lot']}"
            tableau.to_excel(w, sheet_name=nom, index=False, startrow=3)
            f = w.sheets[nom]
            f["A1"] = f"Lot {lot['num_lot']} — {fiche} — {lot['type_lot']}"
            f["A1"].font = Font(bold=True, size=13)
            f["A2"] = (f"Bureau : {lot['bureau'] or '-'}   |   DCTS : {lot['dcts'] or '-'}"
                       + (f"   |   Client : {lot['client']}" if lot["client"] else "")
                       + f"   |   {lot['nb_ops']} opérations")
            _mettre_en_forme(f, ligne_entete=4)
            f.column_dimensions["A"].width = 18
            f.column_dimensions["B"].width = 22
    return tampon.getvalue()


def excel_ajouts(ajouts: pd.DataFrame) -> bytes:
    """Dossiers à ajouter aux lots existants : un onglet par lot existant."""
    tampon = io.BytesIO()
    with pd.ExcelWriter(tampon, engine="openpyxl") as w:
        for lot, contenu in ajouts.groupby("lot", sort=True):
            tableau = contenu[["dossier", "nb_ops", "fiche", "dcts"]].rename(columns={
                "dossier": "N° dossier", "nb_ops": "Nombre d'opérations",
                "fiche": "Fiche CEE", "dcts": "DCTS"})
            nom = str(lot)[:31].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "") \
                .replace("[", "(").replace("]", ")").replace(":", "-")
            tableau.to_excel(w, sheet_name=nom, index=False)
            _mettre_en_forme(w.sheets[nom])
    return tampon.getvalue()


def excel_tableau(onglets: dict[str, pd.DataFrame]) -> bytes:
    """Fichier Excel simple : {nom d'onglet: tableau}, avec mise en forme."""
    tampon = io.BytesIO()
    with pd.ExcelWriter(tampon, engine="openpyxl") as w:
        for nom, tableau in onglets.items():
            tableau.to_excel(w, sheet_name=nom[:31], index=False)
            _mettre_en_forme(w.sheets[nom[:31]])
    return tampon.getvalue()


def zip_lots(lots: pd.DataFrame, affect: pd.DataFrame, ajouts: pd.DataFrame,
             date_export: date) -> bytes:
    """ZIP : un fichier Excel par fiche CEE + un fichier pour les lots existants à compléter."""
    suffixe = date_export.strftime("%Y-%m-%d")
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for fiche in sorted(lots["fiche"].unique()):
            z.writestr(f"Lots_{fiche}_{suffixe}.xlsx", excel_fiche(fiche, lots, affect))
        if not ajouts.empty:
            z.writestr(f"Ajouts_lots_existants_{suffixe}.xlsx", excel_ajouts(ajouts))
    return tampon.getvalue()
