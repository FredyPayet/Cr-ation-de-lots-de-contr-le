"""
stockage.py — Archive, réglages et liste des clients
====================================================

Deux façons d'enregistrer l'archive et les réglages :

    1. GOOGLE SHEET (recommandé, permanent)
       Activé automatiquement quand les « secrets » Streamlit contiennent un compte de
       service Google et le lien du Google Sheet (voir README.md). Onglets créés :
         - « Synthèse »             : une ligne par export archivé (création + suivi)
         - « Détail bénéficiaires » : opérations intégrées par bénéficiaire et fiche CEE
         - « Lots par étape »       : nombre de lots par étape de contrôle
         - « Bureaux en contrôle »  : lots en contrôle par bureau (≤ / > 5 semaines)
         - « Graphiques »           : camembert + histogramme du dernier export archivé
         - « Paramètres »           : pourcentages des bureaux, lien des clients

    2. LOCAL (secours) : dossier « donnees/ » à côté de l'application.
       ⚠️ Sur Streamlit Cloud, ce dossier est vidé à chaque redémarrage.

Organisation : 1. Description des tables  2. Stockage local  3. Stockage Google Sheet
               4. Fonctions utilisées par l'application  5. Liste des clients spécifiques
"""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError

import pandas as pd

from regles import normaliser, normaliser_siren

DOSSIER = Path(__file__).parent / "donnees"
FICHIER_PARAMETRES = DOSSIER / "parametres.json"

# ============================================================================
# 1. DESCRIPTION DES TABLES DE L'ARCHIVE
#    nom interne -> (nom de l'onglet, [(colonne interne, libellé dans le Google Sheet)])
# ============================================================================

TABLES = {
    "synthese": ("Synthèse", [
        ("date_export", "Date de l'export"),
        ("date_archivage", "Archivé le"),
        ("nb_operations_a_integrer", "Opérations à intégrer"),
        ("nb_lots_a_creer", "Lots à créer"),
        ("nb_operations_ajoutees_lots_existants", "Ajoutées aux lots existants"),
        ("nb_dossiers", "Dossiers"),
        ("lots_en_constitution", "Lots en constitution"),
        ("lots_demande_coordonnees", "Lots demande de coordonnées"),
        ("lots_en_controle", "Lots en cours de contrôle"),
        ("ops_en_controle", "Nb d'opé en contrôle"),
        ("lots_controle_plus_5_sem", "Dont lots en contrôle > 5 semaines"),
        ("ops_controle_plus_5_sem", "Dont opé en contrôle > 5 semaines"),
        ("lots_restitues", "Lots restitués"),
        ("ops_eligibles", "Nb d'OP éligibles"),
        ("ops_integrables_delai_2_mois", "Nb d'OP intégrables (délai > 2 mois)"),
        ("ops_en_creation", "Nb d'opé en création (constitution + coordonnées)"),
        ("ops_restitues", "Nb d'opé restituées (lots à traiter)"),
        ("date_reference_flux", "Flux : jour de référence"),
        ("flux_lots_creation", "Flux : lots créés"),
        ("flux_ops_creation", "Flux : opé. des lots créés"),
        ("flux_lots_envoi", "Flux : lots envoyés"),
        ("flux_ops_envoi", "Flux : opé. des lots envoyés"),
        ("flux_lots_retour", "Flux : lots restitués"),
        ("flux_ops_retour", "Flux : opé. des lots restitués"),
        ("periode_debut", "Flux période : du (export précédent)"),
        ("periode_fin", "Flux période : au (export)"),
        ("flux_periode_lots_creation", "Flux période : lots créés"),
        ("flux_periode_ops_creation", "Flux période : opé. des lots créés"),
        ("flux_periode_lots_envoi", "Flux période : lots envoyés"),
        ("flux_periode_ops_envoi", "Flux période : opé. des lots envoyés"),
        ("flux_periode_lots_retour", "Flux période : lots restitués"),
        ("flux_periode_ops_retour", "Flux période : opé. des lots restitués"),
    ]),
    "detail": ("Détail bénéficiaires", [
        ("date_export", "Date de l'export"),
        ("beneficiaire", "Bénéficiaire"),
        ("fiche", "Fiche CEE"),
        ("nb_operations", "Nb opérations"),
    ]),
    "etapes": ("Lots par étape", [
        ("date_export", "Date de l'export"),
        ("etape", "Étape de contrôle"),
        ("nb_lots", "Nb lots"),
    ]),
    "bureaux": ("Bureaux en contrôle", [
        ("date_export", "Date de l'export"),
        ("bureau", "Bureau de contrôle"),
        ("lots_delai_ok", "Lots délai respecté (≤ 5 semaines)"),
        ("lots_plus_5_sem", "Lots > 5 semaines"),
    ]),
}
COLONNES_TEXTE = {"date_export", "date_archivage", "beneficiaire", "fiche", "etape", "bureau",
                  "date_reference_flux", "periode_debut", "periode_fin"}
ONGLET_GRAPHIQUES = "Graphiques"
ONGLET_PARAMETRES = "Paramètres"


def _table_vide(table: str) -> pd.DataFrame:
    return pd.DataFrame(columns=[c for c, _ in TABLES[table][1]])


def _date_iso(valeur) -> str:
    """'2026-11-03', '03/11/2026' ou une date -> '2026-11-03'."""
    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)) or valeur == "":
        return ""
    texte = str(valeur).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", texte):
        d = pd.to_datetime(texte[:10], format="%Y-%m-%d", errors="coerce")
    else:
        d = pd.to_datetime(texte, dayfirst=True, errors="coerce")
    return "" if pd.isna(d) else d.strftime("%Y-%m-%d")


def _vers_interne(tableau: pd.DataFrame, table: str) -> pd.DataFrame:
    """Renomme les libellés (ou anciens noms) en noms internes et nettoie les valeurs."""
    colonnes = TABLES[table][1]
    correspondance = {}
    for interne, libelle in colonnes:
        correspondance[normaliser(libelle)] = interne
        correspondance[normaliser(interne)] = interne
    t = tableau.rename(columns={c: correspondance.get(normaliser(c), c) for c in tableau.columns})
    for interne, _ in colonnes:
        if interne not in t.columns:
            t[interne] = None
    t = t[[c for c, _ in colonnes]].copy()
    t["date_export"] = t["date_export"].map(_date_iso)
    t = t[t["date_export"] != ""]
    for c, _ in colonnes:
        if c in COLONNES_TEXTE:
            t[c] = t[c].fillna("").astype(str)
        else:
            t[c] = pd.to_numeric(t[c], errors="coerce").astype("Int64")
    return t.reset_index(drop=True)


def _vers_libelles(tableau: pd.DataFrame, table: str) -> pd.DataFrame:
    colonnes = TABLES[table][1]
    t = tableau.copy()
    for c, _ in colonnes:
        if c not in t.columns:
            t[c] = None
    return t[[c for c, _ in colonnes]].rename(columns=dict(colonnes))


def _cellule(v):
    """Valeur envoyée au Google Sheet (nombres en nombres, vides en texte vide)."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if hasattr(v, "item"):          # nombres numpy / pandas
        return v.item()
    return v


# ============================================================================
# 2. STOCKAGE LOCAL (secours)
# ============================================================================

class StockageLocal:
    mode = "local"

    def lire(self, table: str) -> pd.DataFrame:
        try:
            return _vers_interne(pd.read_csv(DOSSIER / f"archive_{table}.csv", dtype=str), table)
        except (FileNotFoundError, pd.errors.EmptyDataError):
            return _table_vide(table)

    def ajouter(self, table: str, lignes: pd.DataFrame) -> None:
        DOSSIER.mkdir(exist_ok=True)
        tout = pd.concat([self.lire(table), _vers_interne(lignes.astype(object), table)],
                         ignore_index=True)
        tout.sort_values("date_export").to_csv(DOSSIER / f"archive_{table}.csv", index=False)

    def completer_ligne(self, table: str, date_iso: str, valeurs: dict) -> None:
        """Remplit les cellules vides de la ligne d'une date (sans modifier les autres)."""
        tout = self.lire(table)
        masque = tout["date_export"] == date_iso
        for col, v in valeurs.items():
            vides = masque & (tout[col].isna() | (tout[col].astype(str) == ""))
            tout.loc[vides, col] = v
        DOSSIER.mkdir(exist_ok=True)
        tout.to_csv(DOSSIER / f"archive_{table}.csv", index=False)

    def maj_graphiques(self, archive: dict) -> None:
        pass  # les graphiques sont affichés dans l'application

    def lire_parametres(self) -> dict:
        try:
            return json.loads(FICHIER_PARAMETRES.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def ecrire_parametres(self, valeurs: dict) -> None:
        DOSSIER.mkdir(exist_ok=True)
        FICHIER_PARAMETRES.write_text(json.dumps(valeurs, ensure_ascii=False, indent=2),
                                      encoding="utf-8")


# ============================================================================
# 3. STOCKAGE GOOGLE SHEET (compte de service)
# ============================================================================

class StockageGoogle:
    mode = "google"

    def __init__(self, identifiants: dict, url: str):
        import gspread  # importé ici : inutile si Google Sheet n'est pas configuré
        self._gspread = gspread
        self.url = url
        self.email = identifiants.get("client_email", "")
        try:
            self.client = gspread.service_account_from_dict(dict(identifiants))
        except Exception as e:
            raise ValueError("La clé du compte de service Google est invalide. Vérifiez la "
                             "section [gcp_service_account] des secrets Streamlit "
                             f"(Détail : {e}).") from e
        try:
            self.classeur = self.client.open_by_url(url)
        except gspread.exceptions.NoValidUrlKeyFound as e:
            raise ValueError("Le lien du Google Sheet d'archive n'est pas valide "
                             "(secrets Streamlit, section [archive]).") from e
        except (gspread.exceptions.SpreadsheetNotFound, gspread.exceptions.APIError,
                PermissionError) as e:
            raise ValueError(
                f"Le Google Sheet d'archive n'est pas accessible. Partagez-le avec "
                f"« {self.email} » en tant qu'Éditeur (bouton Partager du Google Sheet), puis "
                f"redémarrez l'application. Vérifiez aussi que l'API Google Sheets est activée "
                f"dans votre projet Google Cloud. (Détail : {e})") from e

    # --- outils -------------------------------------------------------------
    def _onglet(self, titre: str, entetes: list[str] | None = None):
        try:
            return self.classeur.worksheet(titre)
        except self._gspread.exceptions.WorksheetNotFound:
            ws = self.classeur.add_worksheet(title=titre, rows=1000,
                                             cols=max(len(entetes or []), 12))
            if entetes:
                ws.update(values=[entetes], range_name="A1")
                ws.format("1:1", {"textFormat": {"bold": True},
                                  "backgroundColor": {"red": 0.87, "green": 0.92, "blue": 0.97}})
                ws.freeze(rows=1)
            return ws

    # --- archive ------------------------------------------------------------
    def lire(self, table: str) -> pd.DataFrame:
        titre, _ = TABLES[table]
        try:
            # Valeurs brutes (sans mise en forme régionale : 1008 et non « 1 008 »)
            valeurs = self.classeur.worksheet(titre).get_all_values(
                value_render_option="UNFORMATTED_VALUE")
        except self._gspread.exceptions.WorksheetNotFound:
            return _table_vide(table)
        if len(valeurs) < 2:
            return _table_vide(table)
        return _vers_interne(pd.DataFrame(valeurs[1:], columns=valeurs[0]), table)

    def ajouter(self, table: str, lignes: pd.DataFrame) -> None:
        if lignes.empty:
            return
        ws, entete = self._onglet_et_entete(table)
        donnees = _vers_libelles(lignes, table)
        ws.append_rows([[_cellule(r.get(c)) for c in entete] for r in donnees.to_dict("records")],
                       value_input_option="RAW")

    def _onglet_et_entete(self, table: str):
        """Onglet de la table et sa ligne d'en-tête (complétée si de nouvelles colonnes existent)."""
        titre, colonnes = TABLES[table]
        libelles = [l for _, l in colonnes]
        ws = self._onglet(titre, libelles)
        entete = ws.row_values(1)
        if not entete:
            entete = libelles
            ws.update(values=[entete], range_name="A1")
        manquantes = [l for l in libelles if l not in entete]
        if manquantes:  # nouvelles colonnes ajoutées à droite, sans toucher aux anciennes
            entete = entete + manquantes
            if len(entete) > ws.col_count:
                ws.add_cols(len(entete) - ws.col_count)
            ws.update(values=[entete], range_name="A1")
        return ws, entete

    def completer_ligne(self, table: str, date_iso: str, valeurs: dict) -> None:
        """Remplit les cellules vides de la ligne d'une date (sans modifier les autres)."""
        ws, entete = self._onglet_et_entete(table)
        libelles = dict(TABLES[table][1])
        lignes = ws.get_all_values()
        col_date = entete.index(libelles["date_export"])
        mises_a_jour = []
        for i, ligne in enumerate(lignes[1:], start=2):
            if col_date < len(ligne) and _date_iso(ligne[col_date]) == date_iso:
                for col, v in valeurs.items():
                    j = entete.index(libelles[col])
                    if j >= len(ligne) or ligne[j] == "":
                        mises_a_jour.append({"range": self._gspread.utils.rowcol_to_a1(i, j + 1),
                                             "values": [[_cellule(v)]]})
        if mises_a_jour:
            ws.batch_update(mises_a_jour, value_input_option="RAW")

    # --- graphiques ---------------------------------------------------------
    def maj_graphiques(self, archive: dict) -> None:
        """
        Onglet « Graphiques » : situation du dernier export archivé.
        Les tableaux sont réécrits et les 2 graphiques recréés à chaque archivage.
        """
        synthese = archive["synthese"]
        if synthese.empty:
            return
        derniere = max(synthese["date_export"])
        etapes = archive["etapes"][archive["etapes"]["date_export"] == derniere]
        bureaux = archive["bureaux"][archive["bureaux"]["date_export"] == derniere]

        ws = self._onglet(ONGLET_GRAPHIQUES)
        ws.clear()
        # Supprimer les anciens graphiques de l'onglet
        meta = self.classeur.fetch_sheet_metadata()
        anciens = [c["chartId"] for s in meta.get("sheets", [])
                   if s["properties"]["sheetId"] == ws.id for c in s.get("charts", [])]
        if anciens:
            self.classeur.batch_update({"requests": [
                {"deleteEmbeddedObject": {"objectId": i}} for i in anciens]})

        date_txt = pd.Timestamp(derniere).strftime("%d/%m/%Y")
        lignes_e = [[e, _cellule(n)] for e, n in zip(etapes["etape"], etapes["nb_lots"])]
        lignes_b = [[b, _cellule(o), _cellule(r)] for b, o, r in
                    zip(bureaux["bureau"], bureaux["lots_delai_ok"], bureaux["lots_plus_5_sem"])]
        ws.update(values=[[f"Situation de l'export du {date_txt} (dernier export archivé)"]],
                  range_name="A1")
        ws.update(values=[["Étape de contrôle", "Nb lots"]] + lignes_e, range_name="A3")
        ws.update(values=[["Bureau de contrôle", "Délai respecté (≤ 5 semaines)",
                           "> 5 semaines"]] + lignes_b, range_name="D3")
        ws.format("A1", {"textFormat": {"bold": True, "fontSize": 13}})
        ws.format("A3:F3", {"textFormat": {"bold": True}})

        sid = ws.id

        def plage(l0, l1, c0, c1):  # ChartData de l'API Google Sheets (index à 0, fin exclue)
            return {"sourceRange": {"sources": [{
                "sheetId": sid, "startRowIndex": l0, "endRowIndex": l1,
                "startColumnIndex": c0, "endColumnIndex": c1}]}}

        def position(ligne, colonne):
            return {"overlayPosition": {"anchorCell": {"sheetId": sid, "rowIndex": ligne,
                                                       "columnIndex": colonne},
                                        "widthPixels": 620, "heightPixels": 380}}

        requetes = []
        if lignes_e:
            n = len(lignes_e)
            requetes.append({"addChart": {"chart": {
                "spec": {"title": f"Répartition des lots par étape de contrôle — {date_txt}",
                         "pieChart": {"legendPosition": "RIGHT_LEGEND",
                                      "domain": plage(3, 3 + n, 0, 1),
                                      "series": plage(3, 3 + n, 1, 2)}},
                "position": position(0, 7)}}})
        if lignes_b:
            m = len(lignes_b)
            requetes.append({"addChart": {"chart": {
                "spec": {"title": f"Lots en cours de contrôle par bureau — {date_txt}",
                         "basicChart": {
                             "chartType": "COLUMN", "stackedType": "STACKED",
                             "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
                             "axis": [{"position": "BOTTOM_AXIS", "title": "Bureau de contrôle"},
                                      {"position": "LEFT_AXIS", "title": "Nombre de lots"}],
                             "domains": [{"domain": plage(2, 3 + m, 3, 4)}],
                             "series": [
                                 {"series": plage(2, 3 + m, 4, 5), "targetAxis": "LEFT_AXIS",
                                  "colorStyle": {"rgbColor": {"red": 0.12, "green": 0.37, "blue": 0.68}}},
                                 {"series": plage(2, 3 + m, 5, 6), "targetAxis": "LEFT_AXIS",
                                  "colorStyle": {"rgbColor": {"red": 0.85, "green": 0.33, "blue": 0.10}}},
                             ]}},
                "position": position(21, 7)}}})

        # Flux d'activité : un jour de référence par export archivé (30 derniers)
        flux = synthese[synthese["flux_lots_creation"].notna()].sort_values("date_export").tail(30)
        if not flux.empty:
            debut = 3 + max(len(lignes_e), len(lignes_b)) + 3   # ligne (index à 0) du tableau
            entete_flux = ["Période", "Lots créés", "Opé. des lots créés", "Lots envoyés",
                           "Opé. des lots envoyés", "Lots restitués", "Opé. des lots restitués"]
            cles_flux = ("lots_creation", "ops_creation", "lots_envoi", "ops_envoi",
                         "lots_retour", "ops_retour")

            def ligne_flux(r):
                # Entre deux exports (bornes incluses) si connu, sinon jour ouvré précédent
                if r.get("periode_debut") and not pd.isna(r.get("flux_periode_lots_creation")):
                    libelle = (f"{pd.Timestamp(r['periode_debut']):%d/%m} → "
                               f"{pd.Timestamp(r['periode_fin']):%d/%m/%Y}")
                    return [libelle] + [_cellule(r[f"flux_periode_{c}"]) for c in cles_flux]
                jour_f = r["date_reference_flux"] or r["date_export"]
                return [f"le {pd.Timestamp(jour_f):%d/%m/%Y}"] + [_cellule(r[f"flux_{c}"]) for c in cles_flux]

            lignes_f = [ligne_flux(r) for r in flux.to_dict("records")]
            ws.update(values=[["Flux d'activité entre deux exports archivés (dates incluses) — "
                               "à défaut, jour ouvré précédant l'export"]],
                      range_name=f"A{debut}")
            ws.update(values=[entete_flux] + lignes_f, range_name=f"A{debut + 1}")
            ws.format(f"A{debut}", {"textFormat": {"bold": True, "fontSize": 12}})
            ws.format(f"A{debut + 1}:G{debut + 1}", {"textFormat": {"bold": True}})
            l0, l1 = debut, debut + 1 + len(lignes_f)   # en-tête (index à 0) -> dernière ligne
            couleurs = [(0.12, 0.37, 0.68), (0.88, 0.54, 0.0), (0.18, 0.49, 0.20)]
            requetes.append({"addChart": {"chart": {
                "spec": {"title": "Flux d'activité : lots créés, envoyés et restitués entre deux exports",
                         "basicChart": {
                             "chartType": "COLUMN", "legendPosition": "BOTTOM_LEGEND",
                             "headerCount": 1,
                             "axis": [{"position": "BOTTOM_AXIS", "title": "Période entre deux exports"},
                                      {"position": "LEFT_AXIS", "title": "Nombre de lots"}],
                             "domains": [{"domain": plage(l0, l1, 0, 1)}],
                             "series": [
                                 {"series": plage(l0, l1, c, c + 1), "targetAxis": "LEFT_AXIS",
                                  "colorStyle": {"rgbColor": {"red": r, "green": g, "blue": b}}}
                                 for c, (r, g, b) in zip((1, 3, 5), couleurs)]}},
                "position": position(42, 7)}}})
        if requetes:
            self.classeur.batch_update({"requests": requetes})

    # --- paramètres ---------------------------------------------------------
    def lire_parametres(self) -> dict:
        try:
            valeurs = self.classeur.worksheet(ONGLET_PARAMETRES).get_all_values()
        except self._gspread.exceptions.WorksheetNotFound:
            return {}
        resultat = {}
        for ligne in valeurs[1:]:
            if len(ligne) >= 2 and ligne[0]:
                try:
                    resultat[ligne[0]] = json.loads(ligne[1])
                except json.JSONDecodeError:
                    resultat[ligne[0]] = ligne[1]
        return resultat

    def ecrire_parametres(self, valeurs: dict) -> None:
        ws = self._onglet(ONGLET_PARAMETRES, ["Clé", "Valeur"])
        ws.clear()
        ws.update(values=[["Clé", "Valeur"]] + [[k, json.dumps(v, ensure_ascii=False)]
                                                for k, v in valeurs.items()], range_name="A1")

    # --- lecture d'un autre Google Sheet partagé avec le compte de service --
    def lire_tableau(self, url: str) -> pd.DataFrame:
        classeur = self.client.open_by_url(url)
        gid = re.search(r"[#&?]gid=(\d+)", url)
        ws = classeur.get_worksheet_by_id(int(gid.group(1))) if gid else classeur.sheet1
        valeurs = ws.get_all_values()
        return pd.DataFrame(valeurs[1:], columns=valeurs[0]) if valeurs else pd.DataFrame()


# ============================================================================
# 4. FONCTIONS UTILISÉES PAR L'APPLICATION
# ============================================================================

_stockage: StockageLocal | StockageGoogle = StockageLocal()
_cache: dict = {}
DUREE_CACHE = 120  # secondes : évite de relire le Google Sheet à chaque clic


def utiliser(stockage) -> None:
    """Choisit où enregistrer (appelé par l'application au démarrage)."""
    global _stockage
    if stockage is not _stockage:
        _stockage = stockage
        _cache.clear()


def mode_stockage() -> str:
    return _stockage.mode


def infos_stockage() -> dict:
    return {"mode": _stockage.mode, "url": getattr(_stockage, "url", ""),
            "email": getattr(_stockage, "email", "")}


# --- Réglages ---------------------------------------------------------------

def charger_parametres() -> dict:
    if "parametres" not in _cache or time.time() - _cache["parametres"][0] > DUREE_CACHE:
        try:
            _cache["parametres"] = (time.time(), _stockage.lire_parametres())
        except Exception:
            return {}
    return dict(_cache["parametres"][1])


def enregistrer_parametres(valeurs: dict) -> None:
    actuels = _stockage.lire_parametres()
    actuels.update(valeurs)
    _stockage.ecrire_parametres(actuels)
    _cache.pop("parametres", None)


# --- Archive ----------------------------------------------------------------

def charger_archive(forcer: bool = False) -> dict:
    """Renvoie {table: tableau} pour les 4 tables de l'archive."""
    if forcer or "archive" not in _cache or time.time() - _cache["archive"][0] > DUREE_CACHE:
        _cache["archive"] = (time.time(), {t: _stockage.lire(t) for t in TABLES})
    return {t: v.copy() for t, v in _cache["archive"][1].items()}


def date_deja_archivee(date_iso: str) -> bool:
    return date_iso in set(charger_archive()["synthese"]["date_export"])


def archiver(archive_jour: dict) -> tuple[bool, str | None]:
    """
    Ajoute l'archive d'un export. Renvoie (archivé ?, avertissement éventuel).
    Refuse si la date d'export est déjà archivée (vérifiée sur les données à jour).
    """
    date_iso = archive_jour["synthese"]["date_export"]
    if date_iso in set(charger_archive(forcer=True)["synthese"]["date_export"]):
        return False, None
    _stockage.ajouter("synthese", pd.DataFrame([archive_jour["synthese"]]))
    for t in ("detail", "etapes", "bureaux"):
        _stockage.ajouter(t, archive_jour[t])
    _cache.pop("archive", None)
    return True, _maj_graphiques()


# Indicateurs ajoutés après coup (versions 4 et 5) : absents des exports archivés avant,
# ils peuvent être ajoutés avec le bouton « Compléter l'archive ».
INDICATEURS_FLUX = ["flux_lots_creation", "flux_ops_creation", "flux_lots_envoi",
                    "flux_ops_envoi", "flux_lots_retour", "flux_ops_retour"]
INDICATEURS_SUIVI = ["lots_en_constitution", "lots_demande_coordonnees", "lots_en_controle",
                     "ops_en_controle", "lots_controle_plus_5_sem", "ops_controle_plus_5_sem",
                     "lots_restitues", "ops_eligibles", "ops_integrables_delai_2_mois",
                     "ops_en_creation", "ops_restitues", *INDICATEURS_FLUX]
INDICATEURS_PERIODE = ["periode_debut", "periode_fin", "flux_periode_lots_creation",
                       "flux_periode_ops_creation", "flux_periode_lots_envoi", "flux_periode_ops_envoi",
                       "flux_periode_lots_retour", "flux_periode_ops_retour"]
INDICATEURS_A_COMPLETER = INDICATEURS_SUIVI + ["date_reference_flux"] + INDICATEURS_PERIODE


def date_precedente(date_iso: str) -> str | None:
    """Date du dernier export archivé AVANT `date_iso` (pour le flux entre deux exports)."""
    dates = sorted(d for d in charger_archive()["synthese"]["date_export"] if d < date_iso)
    return dates[-1] if dates else None


def archive_incomplete(date_iso: str) -> bool:
    """Vrai si la date est archivée mais sans les indicateurs de suivi ou les graphiques."""
    archive = charger_archive()
    ligne = archive["synthese"][archive["synthese"]["date_export"] == date_iso]
    if ligne.empty:
        return False
    manque_indicateurs = ligne[INDICATEURS_SUIVI].isna().any(axis=None)
    # Flux entre deux exports : seulement s'il existe un export archivé avant celui-ci
    if (archive["synthese"]["date_export"] < date_iso).any():
        manque_indicateurs = manque_indicateurs or bool(ligne["flux_periode_lots_creation"].isna().any())
    manque_graphiques = (archive["etapes"]["date_export"] == date_iso).sum() == 0 \
        and (archive["bureaux"]["date_export"] == date_iso).sum() == 0
    return bool(manque_indicateurs or manque_graphiques)


def completer_archive(archive_jour: dict) -> tuple[bool, str | None]:
    """
    Complète un export déjà archivé avec les indicateurs de suivi et les données des
    graphiques, SANS modifier les chiffres déjà enregistrés (création des lots, détail).
    Renvoie (complété ?, avertissement éventuel).
    """
    date_iso = archive_jour["synthese"]["date_export"]
    archive = charger_archive(forcer=True)
    if date_iso not in set(archive["synthese"]["date_export"]):
        return False, None
    valeurs = {k: archive_jour["synthese"][k] for k in INDICATEURS_A_COMPLETER
               if k in archive_jour["synthese"]}
    _stockage.completer_ligne("synthese", date_iso, valeurs)
    for t in ("etapes", "bureaux"):
        if (archive[t]["date_export"] == date_iso).sum() == 0:
            _stockage.ajouter(t, archive_jour[t])
    _cache.pop("archive", None)
    return True, _maj_graphiques()


def _maj_graphiques() -> str | None:
    try:
        _stockage.maj_graphiques(charger_archive(forcer=True))
        return None
    except Exception as e:  # l'archive est enregistrée même si les graphiques échouent
        return f"L'archive est enregistrée, mais les graphiques n'ont pas pu être mis à jour ({e})."


def archive_en_excel() -> bytes:
    """Archive complète en un fichier Excel (un onglet par table) pour la sauvegarder."""
    archive = charger_archive()
    tampon = io.BytesIO()
    with pd.ExcelWriter(tampon, engine="openpyxl") as w:
        for t, (titre, _) in TABLES.items():
            _vers_libelles(archive[t], t).to_excel(w, sheet_name=titre, index=False)
    return tampon.getvalue()


def _table_depuis_onglet(nom_onglet: str) -> str | None:
    """Associe un onglet d'un fichier importé à une table (accepte les anciens noms)."""
    n = normaliser(nom_onglet)
    for t, (titre, _) in TABLES.items():
        if n == normaliser(titre) or n == t or n.startswith(t[:5]):
            return t
    return None


def importer_archive(fichier) -> tuple[int, int]:
    """
    Réimporte une archive Excel (téléchargée depuis l'application, ancienne ou nouvelle
    version). Les dates déjà présentes sont ignorées.
    Renvoie (dates ajoutées, dates ignorées car déjà présentes).
    """
    feuilles = pd.read_excel(fichier, sheet_name=None, dtype=str)
    tables = {}
    for nom, contenu in feuilles.items():
        t = _table_depuis_onglet(nom)
        if t:
            tables[t] = _vers_interne(contenu, t)
    if "synthese" not in tables:
        raise ValueError("Ce fichier ne ressemble pas à une archive : il doit contenir un "
                         "onglet « Synthèse ».")
    connues = set(charger_archive(forcer=True)["synthese"]["date_export"])
    nouvelles = [d for d in tables["synthese"]["date_export"].unique() if d not in connues]
    for t, contenu in tables.items():
        _stockage.ajouter(t, contenu[contenu["date_export"].isin(nouvelles)])
    _cache.pop("archive", None)
    if nouvelles:
        _maj_graphiques()
    return len(nouvelles), tables["synthese"]["date_export"].nunique() - len(nouvelles)


def migrer_archive_locale() -> int:
    """
    Si le Google Sheet est actif et qu'une archive locale existe encore (ancienne version),
    copie ses dates absentes du Google Sheet. Renvoie le nombre de dates copiées.
    """
    if _stockage.mode != "google":
        return 0
    local = StockageLocal()
    archive_locale = {t: local.lire(t) for t in TABLES}
    if archive_locale["synthese"].empty:
        return 0
    connues = set(charger_archive(forcer=True)["synthese"]["date_export"])
    nouvelles = [d for d in archive_locale["synthese"]["date_export"].unique() if d not in connues]
    for t, contenu in archive_locale.items():
        _stockage.ajouter(t, contenu[contenu["date_export"].isin(nouvelles)])
    # Les réglages locaux sont repris s'il n'y en a pas encore dans le Google Sheet
    if not _stockage.lire_parametres() and local.lire_parametres():
        _stockage.ecrire_parametres(local.lire_parametres())
    _cache.clear()
    if nouvelles:
        _maj_graphiques()
    return len(nouvelles)


# ============================================================================
# 5. LISTE DES CLIENTS SPÉCIFIQUES (Google Sheet ou fichier déposé)
# ============================================================================

def lien_vers_csv(url: str) -> str:
    """
    Transforme un lien Google Sheet en lien de téléchargement CSV. Deux cas acceptés :
      - lien de partage  : https://docs.google.com/spreadsheets/d/<ID>/edit…
      - lien « Publier sur le Web » : https://docs.google.com/spreadsheets/d/e/<ID>/pub…
    """
    url = (url or "").strip()
    gid = re.search(r"[#&?]gid=(\d+)", url)

    # Lien « Publier sur le Web » (Fichier → Partager → Publier sur le Web)
    publie = re.search(r"/spreadsheets/d/e/([a-zA-Z0-9-_]+)", url)
    if publie:
        return (f"https://docs.google.com/spreadsheets/d/e/{publie.group(1)}/pub?output=csv"
                + (f"&gid={gid.group(1)}" if gid else ""))

    # Lien de partage classique
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        raise ValueError("Ce lien ne ressemble pas à un lien Google Sheet. Copiez le lien "
                         "depuis le bouton « Partager » du fichier.")
    return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
            + (f"&gid={gid.group(1)}" if gid else ""))


def clients_depuis_tableau(tableau: pd.DataFrame) -> dict:
    """Repère les colonnes SIREN et nom du client, renvoie {SIREN: nom}."""
    colonnes = {normaliser(c): c for c in tableau.columns}
    col_siren = next((colonnes[c] for c in colonnes if "siren" in c), None)
    if col_siren is None:
        raise ValueError("La liste des clients doit contenir une colonne « SIREN » "
                         "(et une colonne « Nom » ou « Client »).")
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
    """
    Lit le Google Sheet des clients (SIREN + nom du client).
    1) Si le compte de service Google est configuré et que le fichier est partagé avec
       lui, lecture directe (aucun partage public nécessaire).
    2) Sinon, lecture par le lien public.
    """
    if _stockage.mode == "google":
        try:
            return clients_depuis_tableau(_stockage.lire_tableau(url))
        except Exception:
            pass  # fichier non partagé avec le compte de service : on essaie le lien public
    lien = lien_vers_csv(url)
    try:
        tableau = pd.read_csv(lien, dtype=str)
    except HTTPError as e:
        if e.code in (401, 403):
            raise ValueError(
                "Google refuse l'accès à ce fichier : il est privé ou réservé aux comptes de "
                "votre entreprise. Deux solutions : ① dans le Google Sheet, Partager → Accès "
                "général → « Tous les utilisateurs disposant du lien » (et non « Restreint » "
                "ni le nom de votre entreprise) ; ② si votre entreprise l'interdit, Fichier → "
                "Partager → Publier sur le Web → format CSV, puis collez ce nouveau lien. "
                "Vous pouvez aussi déposer le fichier des clients juste en dessous."
                + (f" Le plus simple : partagez ce Google Sheet avec « {_stockage.email} » "
                   "(lecteur), comme le Google Sheet d'archive." if _stockage.mode == "google" else "")
            ) from e
        if e.code == 404:
            raise ValueError("Ce Google Sheet est introuvable. Vérifiez le lien (il a peut-être "
                             "été supprimé ou déplacé).") from e
        raise ValueError(f"Google a renvoyé une erreur ({e.code}). Réessayez dans quelques "
                         "minutes ou déposez le fichier des clients juste en dessous.") from e
    except Exception as e:
        raise ValueError("Le Google Sheet n'a pas pu être lu. Réessayez, ou déposez le fichier "
                         f"des clients juste en dessous. (Détail : {e})") from e
    if tableau.columns.size and str(tableau.columns[0]).lower().startswith("<!doctype"):
        raise ValueError("Google a renvoyé une page de connexion au lieu des données : le "
                         "fichier n'est pas partagé publiquement (voir les solutions ci-dessus).")
    return clients_depuis_tableau(tableau)


def lire_clients_fichier(fichier) -> dict:
    """Lit la liste des clients depuis un fichier déposé (Excel ou CSV)."""
    nom = getattr(fichier, "name", "").lower()
    try:
        if nom.endswith(".csv"):
            contenu = fichier.getvalue()
            texte = contenu.decode("utf-8-sig", errors="replace")
            sep = ";" if texte.count(";") > texte.count(",") else ","
            tableau = pd.read_csv(io.StringIO(texte), sep=sep, dtype=str)
        else:
            tableau = pd.read_excel(fichier, dtype=str)
    except Exception as e:
        raise ValueError(f"Le fichier des clients n'a pas pu être lu. (Détail : {e})") from e
    return clients_depuis_tableau(tableau)
