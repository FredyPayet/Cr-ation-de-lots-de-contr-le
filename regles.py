"""
regles.py — Règles métier de constitution des lots de contrôle CEE
=====================================================================

Ce fichier ne dépend pas de Streamlit : il contient uniquement les calculs.
On peut donc le tester ou le modifier sans toucher à l'interface (app.py).

Organisation :
    1. Constantes (colonnes de l'export, étapes, types de lot)
    2. Outils (dates, textes)
    3. Lecture de l'export ODICEE
    4. Éligibilité des dossiers (regroupement dossier + fiche CEE)
    5. Lots existants « En cours de constitution » (partie 1)
    6. Construction des nouveaux lots (partie 3)
    7. Attribution des bureaux de contrôle
    8. Indicateurs et archive
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

# ============================================================================
# 1. CONSTANTES
# ============================================================================

# Colonnes utilisées : (lettre Excel, intitulé attendu, nom interne)
COLONNES = [
    ("A", "N°dossier", "dossier"),
    ("B", "DCTS", "dcts"),
    ("H", "Organisme", "organisme"),
    ("I", "SIREN de l'organisme", "siren"),
    ("L", "Date réelle d'engagement", "date_engagement"),
    ("M", "Date prévisionnelle de réalisation", "date_prev_realisation"),
    ("N", "Date réelle de réalisation", "date_realisation"),
    ("T", "Fiche CEE", "fiche"),
    ("U", "Autres fiches à contrôler", "autres_fiches"),
    ("V", "Lot de contrôle", "lot"),
    ("W", "Date de création", "date_creation_lot"),
    ("Y", "Etape de contrôle", "etape"),
    ("Z", "Date d'envoi du lot de contrôle", "date_envoi"),
    ("AA", "Date de retour du lot de contrôle", "date_retour"),
    ("AB", "Raison sociale de l'organisme de contrôle", "bureau"),
    ("AH", "Statut AH", "statut_ah"),
    ("BQ", "Opération abandonnée ?", "abandon"),
]

ETAPE_ELIGIBLE = "Eligible"
LOT_VIDE = "NEANT"
STATUT_EXCLU = "Sans objet"

# Étapes qui comptent comme « lots en cours » pour la répartition des bureaux
ETAPE_CONSTITUTION = "En cours de constitution"
ETAPE_COORDONNEES = "En demande de coordonnées"
ETAPE_CONTROLE = "En cours de contrôle"
ETAPE_RESTITUE = "Lot restitué"
ETAPES_EN_COURS = [ETAPE_CONSTITUTION, ETAPE_COORDONNEES, ETAPE_CONTROLE]

# Indicateurs de suivi
SEMAINES_RETARD = 5      # lot en contrôle depuis plus de 5 semaines = retard
DELAI_MIN_MOIS = 2       # délai restant minimum avant la fin du délai de contrôle

# Types de lot (ordre d'affichage). Les bornes sont incluses.
DEBUT_2019 = pd.Timestamp("2019-04-11")
TYPES_LOT = [
    "Engagement 2019",   # 11/04/2019 -> 31/08/2020
    "Engagement 2020-2021",  # 01/09/2020 -> 31/12/2021 (hors Murs 2021)
    "Murs 2021",        # BAR-EN-102 du 01/07/2021 au 31/12/2021
    "P5",                # 01/01/2022 -> 31/12/2025
    "P6",                # après le 31/12/2025
]
FICHE_MURS = "BAR-EN-102"

# Libellés des motifs d'exclusion (affichés à l'utilisateur)
MOTIF_ABANDON = "Opération abandonnée"
MOTIF_SANS_DFT = "Date réelle de réalisation manquante"
MOTIF_DCTS = "Région (DCTS) non sélectionnée"
MOTIF_SANS_OBJET = "Statut AH « Sans objet » sur le dossier"
MOTIF_SANS_ENGAGEMENT = "Date réelle d'engagement manquante"
MOTIF_AVANT_2019 = "Engagement avant le 11/04/2019"
MOTIF_DELAI = "Moins de 2 mois avant la fin du délai de contrôle"
MOTIF_PLAGE = "Hors plage DFT cochée"

# Raisons du choix du bureau de contrôle
RAISON_MEME_BUREAU = "Même bureau qu'une autre fiche du dossier"
RAISON_POURCENTAGES = "Répartition selon les pourcentages"


@dataclass
class Parametres:
    """Réglages choisis par l'utilisateur dans la barre latérale."""
    date_jour: date = field(default_factory=date.today)
    mode: str = "fiche"                   # "fiche" (par défaut) ou "dcts_fiche"
    dcts_retenues: list | None = None     # None = toutes les régions
    fatal: bool = False
    non_fatal: bool = False
    annee_ref: int = field(default_factory=lambda: date.today().year)
    clients_specifiques: dict = field(default_factory=dict)  # SIREN -> nom
    pourcentages: dict = field(default_factory=dict)         # bureau -> %
    taille_cible: int = 20
    ecart_max_mois: int = 4
    delai_min_mois: int = 2


class ErreurFichier(Exception):
    """Erreur lisible par l'utilisateur lors de la lecture du fichier."""


# ============================================================================
# 2. OUTILS
# ============================================================================

def lettre_vers_index(lettre: str) -> int:
    """'A' -> 0, 'AB' -> 27, 'BQ' -> 68."""
    n = 0
    for c in lettre:
        n = n * 26 + (ord(c) - 64)
    return n - 1


def normaliser(texte) -> str:
    """Minuscules, sans accents ni espaces superflus (pour comparer des libellés)."""
    if texte is None or (isinstance(texte, float) and pd.isna(texte)):
        return ""
    t = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().lower()


def normaliser_siren(valeur) -> str:
    """Garde uniquement les chiffres, sans zéros en tête (12345678 == 012345678)."""
    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)):
        return ""
    if isinstance(valeur, float) and valeur.is_integer():
        valeur = int(valeur)
    chiffres = re.sub(r"\D", "", str(valeur))
    return chiffres.lstrip("0")


def convertir_dates(serie: pd.Series) -> pd.Series:
    """Convertit une colonne en dates (dates Excel, texte JJ/MM/AAAA ou numéros de série)."""
    if pd.api.types.is_datetime64_any_dtype(serie):
        return serie.dt.normalize()
    resultat = pd.to_datetime(serie, errors="coerce", dayfirst=True)
    # Numéros de série Excel (ex. 45992) restés en nombre
    nombres = pd.to_numeric(serie, errors="coerce")
    masque = resultat.isna() & nombres.between(20000, 80000)
    if masque.any():
        resultat[masque] = pd.to_datetime(nombres[masque], unit="D", origin="1899-12-30")
    return resultat.dt.normalize()


def mois_entre(debut: pd.Timestamp, fin: pd.Timestamp) -> float:
    """Écart approximatif en mois (pour l'affichage)."""
    if pd.isna(debut) or pd.isna(fin):
        return 0.0
    return round((fin - debut).days / 30.44, 1)


def date_export_depuis_nom(nom_fichier: str) -> date | None:
    """Export_Controle_lots_de_travaux_BS_2026-10-29.xlsx -> 29/10/2026."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", nom_fichier or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def type_de_lot(fiche: str, d_engagement) -> str | None:
    """Renvoie le type de lot selon la date réelle d'engagement (None si hors période)."""
    if pd.isna(d_engagement) or d_engagement < DEBUT_2019:
        return None
    d = d_engagement
    if fiche == FICHE_MURS and pd.Timestamp("2021-07-01") <= d <= pd.Timestamp("2021-12-31"):
        return "Murs 2021"
    if d <= pd.Timestamp("2020-08-31"):
        return "Engagement 2019"
    if d <= pd.Timestamp("2021-12-31"):
        return "Engagement 2020-2021"
    if d <= pd.Timestamp("2025-12-31"):
        return "P5"
    return "P6"


def plages_dft(annee_ref: int) -> dict:
    """Bornes de la « Plage de DFT » pour l'année de référence N."""
    return {
        "fatal": (pd.Timestamp(annee_ref, 1, 1), pd.Timestamp(annee_ref + 1, 3, 31)),
        "non_fatal_apres": pd.Timestamp(annee_ref + 1, 3, 31),
    }


def famille_fiche(fiche: str) -> str:
    """'BAR-EN-101' -> 'BAR', 'BAT-TH-102' -> 'BAT' (les familles ne se mélangent jamais)."""
    return str(fiche).split("-")[0].upper()


# ============================================================================
# 3. LECTURE DE L'EXPORT ODICEE
# ============================================================================

def lire_export(fichier) -> pd.DataFrame:
    """
    Lit l'export Excel et renvoie un tableau avec des noms de colonnes simples.
    Les colonnes sont retrouvées par leur position (lettre), puis par leur nom
    si l'ordre a changé. Lève ErreurFichier avec un message clair sinon.
    """
    try:
        try:
            brut = pd.read_excel(fichier, sheet_name=0, header=0, engine="calamine")
        except ImportError:
            brut = pd.read_excel(fichier, sheet_name=0, header=0)
    except Exception as e:  # fichier illisible
        raise ErreurFichier(
            "Le fichier n'a pas pu être ouvert. Vérifiez qu'il s'agit bien d'un "
            f"fichier Excel (.xlsx) exporté depuis ODICEE. (Détail : {e})"
        ) from e

    entetes = [normaliser(c) for c in brut.columns]
    donnees = {}
    manquantes = []
    for lettre, intitule, interne in COLONNES:
        idx = lettre_vers_index(lettre)
        cible = normaliser(intitule)
        if idx < len(entetes) and entetes[idx].startswith(cible):
            donnees[interne] = brut.iloc[:, idx]
        elif cible in entetes:
            donnees[interne] = brut.iloc[:, entetes.index(cible)]
        else:
            manquantes.append(f"« {intitule} » (colonne {lettre})")
    if manquantes:
        raise ErreurFichier(
            "Colonnes introuvables dans le fichier : " + ", ".join(manquantes)
            + ". Chargez l'export « Export_Controle_lots_de_travaux_BS_… » sans le modifier."
        )

    df = pd.DataFrame(donnees)
    df["ligne_excel"] = df.index + 2  # numéro de ligne dans Excel (en-tête = ligne 1)

    # Nettoyage des textes
    for col in ["dossier", "dcts", "organisme", "fiche", "autres_fiches", "lot",
                "etape", "bureau", "statut_ah", "abandon"]:
        df[col] = df[col].where(df[col].notna(), "").astype(str).str.strip()
        df.loc[df[col].isin(["nan", "None"]), col] = ""
    df["fiche"] = df["fiche"].str.upper()
    df["siren_norm"] = df["siren"].map(normaliser_siren)

    # Dates
    df["date_engagement"] = convertir_dates(df["date_engagement"])
    df["date_realisation"] = convertir_dates(df["date_realisation"])
    df["date_prev_realisation"] = convertir_dates(df["date_prev_realisation"])
    df["date_envoi"] = convertir_dates(df["date_envoi"])
    df["date_creation_lot"] = convertir_dates(df["date_creation_lot"])
    df["date_retour"] = convertir_dates(df["date_retour"])

    # On retire les lignes vides (export pré-formaté)
    df = df[df["dossier"] != ""].reset_index(drop=True)
    if df.empty:
        raise ErreurFichier(
            "Le fichier ne contient aucune opération (colonne « N°dossier » vide). "
            "Vérifiez que l'export n'est pas vide."
        )
    return df


# ============================================================================
# 4. ÉLIGIBILITÉ DES DOSSIERS
# ============================================================================

def candidats(df: pd.DataFrame) -> pd.DataFrame:
    """Opérations « Eligible » et sans lot (Lot de contrôle = NEANT)."""
    return df[
        (df["etape"].map(normaliser) == normaliser(ETAPE_ELIGIBLE))
        & (df["lot"].map(normaliser) == normaliser(LOT_VIDE))
    ].copy()


def preparer_groupes(df: pd.DataFrame, p: Parametres) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Regroupe les opérations éligibles par N° dossier + fiche CEE et applique
    les règles. Renvoie :
        - groupes : les couples dossier + fiche intégrables (une ligne chacun)
        - ecartes : les couples écartés avec leur motif
    """
    ops = candidats(df)
    aujourdhui = pd.Timestamp(p.date_jour)

    # --- 4.a Exclusions au niveau de l'opération -------------------------
    ops["motif"] = ""
    ops.loc[ops["abandon"].map(normaliser) == "oui", "motif"] = MOTIF_ABANDON
    ops.loc[(ops["motif"] == "") & ops["date_realisation"].isna(), "motif"] = MOTIF_SANS_DFT
    if p.dcts_retenues is not None:
        ops.loc[(ops["motif"] == "") & ~ops["dcts"].isin(p.dcts_retenues), "motif"] = MOTIF_DCTS

    ecartes_ops = ops[ops["motif"] != ""]
    ops = ops[ops["motif"] == ""]

    colonnes_groupes = ["dossier", "fiche", "dcts", "nb_ops", "organisme", "client",
                        "type_lot", "drr_min", "drr_max", "date_limite",
                        "delai_restant_mois", "plage"]
    if ops.empty:
        return pd.DataFrame(columns=colonnes_groupes), _formater_ecartes(ecartes_ops, None)

    # --- 4.b Regroupement dossier + fiche ---------------------------------
    # Client spécifique : SIREN présent dans le Google Sheet ; statut « Sans objet »
    clients = p.clients_specifiques or {}
    ops["client_op"] = ops["siren_norm"].map(clients).fillna("")
    ops["est_sans_objet"] = ops["statut_ah"].str.lower().str.strip() == STATUT_EXCLU.lower()

    g = ops.groupby(["dossier", "fiche"], sort=False).agg(
        dcts=("dcts", "first"),
        nb_ops=("dossier", "size"),
        organisme=("organisme", "first"),
        client=("client_op", "max"),          # le nom du client s'il y en a un
        sans_objet=("est_sans_objet", "any"),
        date_engagement=("date_engagement", "min"),
        drr_min=("date_realisation", "min"),
        drr_max=("date_realisation", "max"),
    ).reset_index()
    g["type_lot"] = [type_de_lot(f, d) for f, d in zip(g["fiche"], g["date_engagement"])]
    # Délai de contrôle : date de réalisation (la plus ancienne de la fiche) + 12 mois
    g["date_limite"] = g["drr_min"] + pd.DateOffset(months=12)
    g["delai_restant_mois"] = [mois_entre(aujourdhui, d) for d in g["date_limite"]]

    # Plage DFT (Fatal / Non fatal)
    bornes = plages_dft(p.annee_ref)
    deb_f, fin_f = bornes["fatal"]
    est_fatal = g["drr_min"].between(deb_f, fin_f)
    est_non_fatal = g["drr_min"] > bornes["non_fatal_apres"]
    g["plage"] = "Hors plage"
    g.loc[est_fatal, "plage"] = "Fatal"
    g.loc[est_non_fatal, "plage"] = "Non fatal"

    # --- 4.c Exclusions au niveau du groupe -------------------------------
    g["motif"] = ""
    g.loc[g["sans_objet"], "motif"] = MOTIF_SANS_OBJET
    g.loc[(g["motif"] == "") & g["date_engagement"].isna(), "motif"] = MOTIF_SANS_ENGAGEMENT
    g.loc[(g["motif"] == "") & g["type_lot"].isna(), "motif"] = MOTIF_AVANT_2019
    seuil = aujourdhui + pd.DateOffset(months=p.delai_min_mois)
    g.loc[(g["motif"] == "") & (g["date_limite"] < seuil), "motif"] = MOTIF_DELAI
    if p.fatal or p.non_fatal:
        dans_plage = (est_fatal & p.fatal) | (est_non_fatal & p.non_fatal)
        g.loc[(g["motif"] == "") & ~dans_plage, "motif"] = MOTIF_PLAGE

    groupes = g[g["motif"] == ""][colonnes_groupes].reset_index(drop=True)
    return groupes, _formater_ecartes(ecartes_ops, g[g["motif"] != ""])


def _formater_ecartes(ecartes_ops: pd.DataFrame, ecartes_groupes: pd.DataFrame | None) -> pd.DataFrame:
    """Tableau unique des exclusions, une ligne par dossier + fiche + motif."""
    morceaux = []
    if ecartes_ops is not None and not ecartes_ops.empty:
        morceaux.append(
            ecartes_ops.groupby(["dossier", "fiche", "dcts", "motif"], sort=False)
            .size().rename("nb_ops").reset_index()
        )
    if ecartes_groupes is not None and not ecartes_groupes.empty:
        morceaux.append(ecartes_groupes[["dossier", "fiche", "dcts", "motif", "nb_ops"]])
    if not morceaux:
        return pd.DataFrame(columns=["dossier", "fiche", "dcts", "motif", "nb_ops"])
    return pd.concat(morceaux, ignore_index=True)


def bureaux_imposes(df: pd.DataFrame) -> dict:
    """
    Dossier -> bureau de contrôle déjà utilisé pour une autre fiche du même dossier.
    Priorité aux lots en cours, puis aux lots les plus récents dans le fichier.
    """
    lots = df[(df["lot"].map(normaliser) != normaliser(LOT_VIDE))
              & (df["lot"] != "") & (df["bureau"] != "")].copy()
    if lots.empty:
        return {}
    lots["priorite"] = lots["etape"].isin(ETAPES_EN_COURS).map({True: 0, False: 1})
    lots = lots.sort_values(["priorite", "ligne_excel"], ascending=[True, False])
    return lots.drop_duplicates("dossier").set_index("dossier")["bureau"].to_dict()


# ============================================================================
# 5. LOTS EXISTANTS « EN COURS DE CONSTITUTION » (partie 1)
# ============================================================================

def lots_en_constitution(df: pd.DataFrame) -> pd.DataFrame:
    """Un lot par ligne : fiche, type, bureau, nombre d'opérations, dates, régions."""
    lots = df[df["etape"].map(normaliser) == normaliser(ETAPE_CONSTITUTION)].copy()
    lots = lots[lots["lot"] != ""]
    if lots.empty:
        return pd.DataFrame(columns=["lot", "fiche", "type_lot", "bureau", "nb_ops",
                                     "drr_min", "drr_max", "dcts"])
    lots["type_lot"] = [type_de_lot(f, d) for f, d in zip(lots["fiche"], lots["date_engagement"])]
    res = lots.groupby("lot", sort=True).agg(
        fiche=("fiche", lambda s: s.mode().iat[0]),
        type_lot=("type_lot", lambda s: s.dropna().mode().iat[0] if s.notna().any() else None),
        bureau=("bureau", lambda s: next((b for b in s if b), "")),
        nb_ops=("lot", "size"),
        drr_min=("date_realisation", "min"),
        drr_max=("date_realisation", "max"),
        dcts=("dcts", lambda s: sorted(set(x for x in s if x))),
    ).reset_index()
    return res


def proposer_ajouts(groupes: pd.DataFrame, lots: pd.DataFrame, p: Parametres,
                    imposes: dict | None = None) -> pd.DataFrame:
    """
    Propose des dossiers à ajouter aux lots en cours de constitution :
    même fiche, même type de lot, sans dépasser 20 opérations.
    Les dossiers les plus urgents passent en premier, et on privilégie
    ceux qui gardent un écart de dates de réalisation ≤ 4 mois.
    Les clients spécifiques sont exclus (ils ont leurs propres lots).
    Un dossier déjà suivi par un bureau (autre fiche) ne rejoint qu'un lot de ce bureau.
    """
    imposes = dict(imposes or {})
    colonnes = ["lot", "fiche", "type_lot", "bureau", "dossier", "dcts", "nb_ops",
                "drr_min", "drr_max", "delai_restant_mois", "ecart_ok"]
    if groupes.empty or lots.empty:
        return pd.DataFrame(columns=colonnes)

    # Dossiers disponibles, rangés par fiche + type de lot, les plus urgents d'abord
    dispo = groupes[groupes["client"] == ""].sort_values(["drr_min", "dossier"])
    par_cle: dict[tuple, list[dict]] = {}
    for r in dispo.to_dict("records"):
        par_cle.setdefault((r["fiche"], r["type_lot"]), []).append(r)

    fenetre = pd.DateOffset(months=p.ecart_max_mois)
    pris = set()
    lignes = []
    for lot in lots.to_dict("records"):
        place = p.taille_cible - int(lot["nb_ops"])
        if place <= 0 or lot["type_lot"] is None:
            continue
        cands = [c for c in par_cle.get((lot["fiche"], lot["type_lot"]), [])
                 if (c["dossier"], c["fiche"]) not in pris and c["nb_ops"] <= place
                 and imposes.get(c["dossier"], lot["bureau"]) == lot["bureau"]
                 and not (p.mode == "dcts_fiche" and lot["dcts"] and c["dcts"] not in lot["dcts"])]
        if not cands:
            continue

        dmin, dmax = lot["drr_min"], lot["drr_max"]

        def dans_fenetre(c):
            nmin = min(dmin, c["drr_min"]) if pd.notna(dmin) else c["drr_min"]
            nmax = max(dmax, c["drr_max"]) if pd.notna(dmax) else c["drr_max"]
            return nmax <= nmin + fenetre

        # D'abord ceux qui restent dans la fenêtre de 4 mois, puis les autres (non bloquant)
        cands = [(dans_fenetre(c), c) for c in cands]
        cands.sort(key=lambda x: (not x[0], x[1]["drr_min"]))
        for ok, c in cands:
            if c["nb_ops"] > place:
                continue
            dmin = min(dmin, c["drr_min"]) if pd.notna(dmin) else c["drr_min"]
            dmax = max(dmax, c["drr_max"]) if pd.notna(dmax) else c["drr_max"]
            place -= int(c["nb_ops"])
            pris.add((c["dossier"], c["fiche"]))
            if lot["bureau"]:
                imposes.setdefault(c["dossier"], lot["bureau"])
            lignes.append({
                "lot": lot["lot"], "fiche": lot["fiche"], "type_lot": lot["type_lot"],
                "bureau": lot["bureau"], "dossier": c["dossier"], "dcts": c["dcts"],
                "nb_ops": int(c["nb_ops"]), "drr_min": c["drr_min"], "drr_max": c["drr_max"],
                "delai_restant_mois": c["delai_restant_mois"], "ecart_ok": bool(ok),
            })
            if place <= 0:
                break
    return pd.DataFrame(lignes, columns=colonnes)


# ============================================================================
# 6. CONSTRUCTION DES NOUVEAUX LOTS (partie 3)
# ============================================================================

def _accepte(total: int, nb: int, cible: int) -> bool:
    """On ajoute un dossier s'il ne fait pas dépasser 20, ou s'il rapproche de 20."""
    return total + nb <= cible or abs(total + nb - cible) < abs(total - cible)


def _remplir_partition(groupes: list[dict], p: Parametres) -> list[list[dict]]:
    """
    Répartit les dossiers d'une même partition (fiche + type + …) en lots.
    Les DCTS peuvent être mélangées (mode « par fiche CEE »). Seule limite : deux
    dossiers déjà suivis par deux bureaux différents (via une autre fiche) ne
    peuvent pas partager un lot, puisqu'un lot n'a qu'un seul bureau.
    """
    cible = p.taille_cible
    fenetre = pd.DateOffset(months=p.ecart_max_mois)
    lots = []

    def compatible(bureau_lot: str, g: dict) -> bool:
        b = g.get("bureau_impose", "")
        return not b or not bureau_lot or b == bureau_lot

    # Dossiers de 20 opérations ou plus : un lot à eux seuls
    for g in groupes:
        if g["nb_ops"] >= cible:
            lots.append([g])

    # Les autres : les plus urgents d'abord (date de réalisation la plus ancienne)
    restants = sorted([g for g in groupes if g["nb_ops"] < cible],
                      key=lambda g: (g["drr_min"], g["dossier"]))
    while restants:
        premier = restants.pop(0)
        lot, total = [premier], premier["nb_ops"]
        dmin, dmax = premier["drr_min"], premier["drr_max"]
        bureau_lot = premier.get("bureau_impose", "")

        # Passe 1 : dossiers dont les dates restent dans la fenêtre de 4 mois
        for g in list(restants):
            if total >= cible:
                break
            nmin, nmax = min(dmin, g["drr_min"]), max(dmax, g["drr_max"])
            if (nmax <= nmin + fenetre and compatible(bureau_lot, g)
                    and _accepte(total, g["nb_ops"], cible)):
                lot.append(g); restants.remove(g)
                total += g["nb_ops"]; dmin, dmax = nmin, nmax
                bureau_lot = bureau_lot or g.get("bureau_impose", "")

        # Passe 2 (souhaitable mais pas bloquant) : on complète avec les dates les plus proches
        if total < cible and restants:
            def distance(g):
                if g["drr_max"] < dmin:
                    return (dmin - g["drr_max"]).days
                if g["drr_min"] > dmax:
                    return (g["drr_min"] - dmax).days
                return 0
            for g in sorted(restants, key=distance):
                if total >= cible:
                    break
                if compatible(bureau_lot, g) and _accepte(total, g["nb_ops"], cible):
                    lot.append(g); restants.remove(g)
                    total += g["nb_ops"]
                    dmin, dmax = min(dmin, g["drr_min"]), max(dmax, g["drr_max"])
                    bureau_lot = bureau_lot or g.get("bureau_impose", "")
        lots.append(lot)
    return lots


def construire_lots(groupes: pd.DataFrame, p: Parametres) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Crée les lots à partir des dossiers restants.
    Un lot = une seule fiche CEE, un seul type de lot, un seul client spécifique
    (ou aucun), et une seule DCTS si le mode « par DCTS et fiche » est choisi.
    Renvoie (lots, affectations) : une ligne par lot, une ligne par dossier.
    """
    col_lots = ["num_lot", "fiche", "type_lot", "dcts", "client", "nb_dossiers", "nb_ops",
                "drr_min", "drr_max", "ecart_mois", "ecart_ok", "bureau_impose"]
    col_aff = ["num_lot", "dossier", "fiche", "dcts", "type_lot", "client", "nb_ops",
               "drr_min", "drr_max", "delai_restant_mois", "organisme"]
    if groupes.empty:
        return pd.DataFrame(columns=col_lots), pd.DataFrame(columns=col_aff)

    g = groupes.copy()
    g["ordre_type"] = g["type_lot"].map({t: i for i, t in enumerate(TYPES_LOT)})
    cles = ["fiche", "ordre_type", "client"] + (["dcts"] if p.mode == "dcts_fiche" else [])
    g = g.sort_values(cles + ["drr_min"])

    lots, affect = [], []
    num = 0
    for _, part in g.groupby(cles, sort=False):
        for contenu in _remplir_partition(part.to_dict("records"), p):
            num += 1
            dmin = min(x["drr_min"] for x in contenu)
            dmax = max(x["drr_max"] for x in contenu)
            ecart = mois_entre(dmin, dmax)
            dcts = sorted({x["dcts"] for x in contenu if x["dcts"]})
            lots.append({
                "num_lot": num, "fiche": contenu[0]["fiche"], "type_lot": contenu[0]["type_lot"],
                "dcts": ", ".join(dcts), "client": contenu[0]["client"],
                "nb_dossiers": len(contenu), "nb_ops": sum(x["nb_ops"] for x in contenu),
                "drr_min": dmin, "drr_max": dmax, "ecart_mois": ecart,
                "ecart_ok": dmax <= dmin + pd.DateOffset(months=p.ecart_max_mois),
                "bureau_impose": next((x.get("bureau_impose", "") for x in contenu
                                       if x.get("bureau_impose", "")), ""),
            })
            for x in contenu:
                affect.append({"num_lot": num, **{k: x[k] for k in col_aff if k != "num_lot"}})
    return pd.DataFrame(lots, columns=col_lots), pd.DataFrame(affect, columns=col_aff)


# ============================================================================
# 7. ATTRIBUTION DES BUREAUX DE CONTRÔLE
# ============================================================================

def liste_bureaux(df: pd.DataFrame) -> list[str]:
    """Valeurs uniques de la colonne « Raison sociale de l'organisme de contrôle »."""
    return sorted({b for b in df["bureau"] if b})


def lots_en_cours_par_bureau(df: pd.DataFrame) -> pd.DataFrame:
    """Nombre de lots uniques par bureau et par étape en cours."""
    en_cours = df[df["etape"].isin(ETAPES_EN_COURS) & (df["lot"] != "") & (df["bureau"] != "")]
    tableau = (en_cours.drop_duplicates(["lot", "etape"])
               .pivot_table(index="bureau", columns="etape", values="lot",
                            aggfunc="nunique", fill_value=0))
    for e in ETAPES_EN_COURS:
        if e not in tableau.columns:
            tableau[e] = 0
    tableau = tableau[ETAPES_EN_COURS]
    tableau["Total en cours"] = tableau.sum(axis=1)
    return tableau


def attribuer_bureaux(lots: pd.DataFrame, affect: pd.DataFrame, imposes: dict,
                      bureaux: list[str], existants: dict, pourcentages: dict) -> pd.DataFrame:
    """
    Attribue un bureau à chaque nouveau lot.
    1) Un dossier présent sur plusieurs fiches garde le même bureau (prioritaire).
    2) Les autres lots vont au bureau le plus en retard sur son objectif (%),
       en comptant les lots déjà en cours. Les pourcentages ne sont pas bloquants.
    """
    lots = lots.copy()
    lots["bureau"] = ""
    lots["raison_bureau"] = ""
    if lots.empty or not bureaux:
        return lots

    # Groupes de lots liés par un même dossier (union-find)
    parent = {n: n for n in lots["num_lot"]}

    def racine(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, nums in affect.groupby("dossier")["num_lot"]:
        nums = list(nums)
        for n in nums[1:]:
            parent[racine(n)] = racine(nums[0])
    composantes: dict[int, list[int]] = {}
    for n in lots["num_lot"]:
        composantes.setdefault(racine(n), []).append(n)

    total_pct = sum(max(0, pourcentages.get(b, 0)) for b in bureaux)
    cibles = ({b: max(0, pourcentages.get(b, 0)) / total_pct for b in bureaux} if total_pct > 0
              else {b: 1 / len(bureaux) for b in bureaux})
    compte = {b: int(existants.get(b, 0)) for b in bureaux}
    total_final = sum(compte.values()) + len(lots)
    imposes_lot = dict(zip(lots["num_lot"], lots.get("bureau_impose", pd.Series("", index=lots.index))))

    attribution, raison = {}, {}
    a_repartir = []
    for nums in composantes.values():
        # a) Lots contenant un dossier déjà suivi par un bureau : ce bureau
        connus = [imposes_lot[n] for n in nums if imposes_lot.get(n)]
        for n in nums:
            if imposes_lot.get(n):
                attribution[n] = imposes_lot[n]
                raison[n] = RAISON_MEME_BUREAU
        if connus:
            # b) Les lots liés (même dossier, autre fiche) suivent le même bureau
            b = pd.Series(connus).mode().iat[0]
            for n in nums:
                if n not in attribution:
                    attribution[n] = b
                    raison[n] = RAISON_MEME_BUREAU
            for n in nums:
                compte[attribution[n]] = compte.get(attribution[n], 0) + 1
        else:
            a_repartir.append(nums)

    # c) Les autres : les plus grands groupes d'abord, vers le bureau le plus en retard
    for nums in sorted(a_repartir, key=len, reverse=True):
        candidats_b = [b for b in bureaux if cibles[b] > 0] or bureaux
        b = max(candidats_b, key=lambda x: (cibles[x] * total_final - compte[x], -compte[x]))
        for n in nums:
            attribution[n] = b
            raison[n] = RAISON_POURCENTAGES if len(nums) == 1 else RAISON_MEME_BUREAU
        compte[b] += len(nums)

    lots["bureau"] = lots["num_lot"].map(attribution)
    lots["raison_bureau"] = lots["num_lot"].map(raison)
    return lots


# ============================================================================
# 8. CALCUL COMPLET, INDICATEURS ET ARCHIVE
# ============================================================================

def calculer(df: pd.DataFrame, p: Parametres, ajouts_retenus: pd.DataFrame | None = None) -> dict:
    """
    Enchaîne toutes les étapes. `ajouts_retenus` = propositions de la partie 1
    que l'utilisateur a gardées (None = toutes les propositions).
    """
    groupes, ecartes = preparer_groupes(df, p)
    existants = lots_en_constitution(df)
    imposes = bureaux_imposes(df)          # dossier -> bureau déjà utilisé (autre fiche)
    propositions = proposer_ajouts(groupes, existants, p, imposes)
    ajouts = propositions if ajouts_retenus is None else ajouts_retenus

    # Les dossiers ajoutés aux lots existants ne sont plus disponibles (partie 2 et 3)
    cles_ajout = set(zip(ajouts["dossier"], ajouts["fiche"]))
    restants = groupes[[(d, f) not in cles_ajout
                        for d, f in zip(groupes["dossier"], groupes["fiche"])]].copy()

    # Un ajout à un lot existant fixe aussi le bureau des autres fiches du dossier
    for d, b in zip(ajouts["dossier"], ajouts["bureau"]):
        if b:
            imposes.setdefault(d, b)
    restants["bureau_impose"] = restants["dossier"].map(imposes).fillna("")

    lots, affect = construire_lots(restants, p)

    bureaux = liste_bureaux(df)
    par_bureau = lots_en_cours_par_bureau(df)
    existants_bureau = par_bureau["Total en cours"].to_dict() if not par_bureau.empty else {}
    lots = attribuer_bureaux(lots, affect, imposes, bureaux, existants_bureau, p.pourcentages)
    if not affect.empty:
        affect = affect.merge(lots[["num_lot", "bureau"]], on="num_lot", how="left")
    lots["lots_lies"] = calculer_lots_lies(df, lots, affect, ajouts)
    # Partie 1 : mêmes informations pour les dossiers proposés aux lots existants
    propositions = propositions.copy()
    propositions["lots_lies"] = calculer_lots_lies_ajouts(df, propositions, ajouts, affect)
    ajouts = ajouts.copy()
    ajouts["lots_lies"] = calculer_lots_lies_ajouts(df, ajouts, ajouts, affect)

    return {
        "groupes": groupes, "ecartes": ecartes, "lots_existants": existants,
        "propositions": propositions, "ajouts": ajouts, "restants": restants,
        "lots": lots, "affectations": affect, "bureaux": bureaux,
        "par_bureau": par_bureau,
    }


def _index_liens(df: pd.DataFrame, ajouts: pd.DataFrame, affect: pd.DataFrame):
    """
    Prépare, pour chaque dossier :
      - existants  : {(nom du lot, fiche, bureau)} lots existants + ajouts de la partie 1
      - incompletes: {fiches} en statut AH « Sans objet » et sans lot
      - nouveaux   : {(n° de nouveau lot, fiche)}
    """
    avec_lot = df[(df["lot"] != "") & (df["lot"].map(normaliser) != normaliser(LOT_VIDE))]
    existants: dict[str, set] = {}
    for d, l, f, b in zip(avec_lot["dossier"], avec_lot["lot"], avec_lot["fiche"], avec_lot["bureau"]):
        existants.setdefault(d, set()).add((l, f, b))
    for d, l, f, b in zip(ajouts["dossier"], ajouts["lot"], ajouts["fiche"], ajouts["bureau"]):
        existants.setdefault(d, set()).add((l, f, b))

    sans_lot = df[(df["lot"] == "") | (df["lot"].map(normaliser) == normaliser(LOT_VIDE))]
    so = sans_lot[sans_lot["statut_ah"].str.lower().str.strip() == STATUT_EXCLU.lower()]
    incompletes: dict[str, set] = {}
    for d, f in zip(so["dossier"], so["fiche"]):
        incompletes.setdefault(d, set()).add(f)

    nouveaux: dict[str, set] = {}
    if not affect.empty:
        for d, n, f in zip(affect["dossier"], affect["num_lot"], affect["fiche"]):
            nouveaux.setdefault(d, set()).add((n, f))
    return existants, incompletes, nouveaux


def calculer_lots_lies_ajouts(df: pd.DataFrame, table: pd.DataFrame, ajouts: pd.DataFrame,
                              affect: pd.DataFrame) -> pd.Series:
    """
    Colonne « Lots liés » de la partie 1 (une ligne = un dossier proposé pour un lot existant) :
    les autres lots du même bureau qui contiennent ce dossier (lots existants, autres
    ajouts ou nouveaux lots), « RAS » s'il n'y en a pas, et un ⚠️ si une autre fiche
    du dossier est en statut AH « Sans objet ».
    """
    if table.empty:
        return pd.Series(dtype=str)
    existants, incompletes, nouveaux = _index_liens(df, ajouts, affect)
    textes = []
    for r in table.to_dict("records"):
        d, lot, fiche, bureau = r["dossier"], r["lot"], r["fiche"], r["bureau"]
        lies = sorted({l for l, f, b in existants.get(d, set()) if l != lot and b == bureau})
        lies = [f"Lot {n}" for n in sorted(n for n, f in nouveaux.get(d, set()) if f != fiche)] + lies
        autres = sorted(f for f in incompletes.get(d, set()) if f != fiche)
        alerte = [f"⚠️ fiche {', '.join(autres)} pas complète (Sans objet)"] if autres else []
        textes.append(" ; ".join(([", ".join(lies)] if lies else []) + alerte) or "RAS")
    return pd.Series(textes, index=table.index)


def calculer_lots_lies(df: pd.DataFrame, lots: pd.DataFrame, affect: pd.DataFrame,
                       ajouts: pd.DataFrame) -> pd.Series:
    """
    Colonne « Lots liés » de chaque nouveau lot :
      - les lots (nouveaux ou existants) du même bureau qui contiennent une autre
        fiche d'un des dossiers du lot ;
      - « RAS » si le bureau a été choisi selon les pourcentages ;
      - un avertissement si une autre fiche d'un dossier est en statut AH
        « Sans objet » (fiche pas complète, donc pas intégrable).
    """
    if lots.empty:
        return pd.Series(dtype=str)
    existants, incompletes, nouveaux = _index_liens(df, ajouts, affect)
    dossiers_lot = affect.groupby("num_lot")["dossier"].apply(list).to_dict()

    textes = []
    for lot in lots.to_dict("records"):
        n, fiche, bureau = lot["num_lot"], lot["fiche"], lot["bureau"]
        lies_nouveaux, lies_existants, alertes = set(), set(), []
        for d in dossiers_lot.get(n, []):
            lies_nouveaux |= {m for m, f in nouveaux.get(d, set()) if m != n and f != fiche}
            lies_existants |= {l for l, f, b in existants.get(d, set()) if b == bureau}
            autres = sorted(f for f in incompletes.get(d, set()) if f != fiche)
            if autres:
                alertes.append(f"⚠️ {d} : fiche {', '.join(autres)} pas complète (Sans objet)")
        lies = [f"Lot {m}" for m in sorted(lies_nouveaux)] + sorted(lies_existants)
        if lot.get("raison_bureau") == RAISON_POURCENTAGES and not lies:
            morceaux = ["RAS"] if not alertes else []
        else:
            morceaux = [", ".join(lies)] if lies else []
        textes.append(" ; ".join(morceaux + alertes) or "RAS")
    return pd.Series(textes, index=lots.index)


def tableau_fiche_dcts(restants: pd.DataFrame) -> pd.DataFrame:
    """Nombre d'opérations intégrables par fiche CEE (lignes) et DCTS (colonnes)."""
    if restants.empty:
        return pd.DataFrame()
    t = restants.pivot_table(index="fiche", columns="dcts", values="nb_ops",
                             aggfunc="sum", fill_value=0)
    t["Total"] = t.sum(axis=1)
    t.loc["Total"] = t.sum()
    t.index.name = "Fiche CEE"
    t.columns.name = None
    return t


def repartition_bureaux(res: dict, pourcentages: dict) -> pd.DataFrame:
    """Pour le graphique : lots en cours, nouveaux lots et objectif par bureau (%)."""
    bureaux = res["bureaux"]
    if not bureaux:
        return pd.DataFrame()
    pb = res["par_bureau"]
    actuels = pb["Total en cours"].reindex(bureaux, fill_value=0) if not pb.empty \
        else pd.Series(0, index=bureaux)
    nouveaux = res["lots"]["bureau"].value_counts().reindex(bureaux, fill_value=0) \
        if not res["lots"].empty else pd.Series(0, index=bureaux)
    apres = actuels + nouveaux
    total_pct = sum(pourcentages.get(b, 0) for b in bureaux)
    t = pd.DataFrame({
        "Bureau": bureaux,
        "Lots en cours": actuels.values,
        "Nouveaux lots": nouveaux.values,
        "Total après création": apres.values,
    })
    t["% actuel"] = (t["Lots en cours"] / max(t["Lots en cours"].sum(), 1) * 100).round(1)
    t["% après création"] = (t["Total après création"] / max(t["Total après création"].sum(), 1) * 100).round(1)
    t["% objectif"] = [round(pourcentages.get(b, 0) / total_pct * 100, 1) if total_pct else
                       round(100 / len(bureaux), 1) for b in bureaux]
    return t


def indicateurs_suivi(df: pd.DataFrame, date_jour: date) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """
    Indicateurs de suivi du contrôle, calculés sur tout l'export :
      1. lots_en_constitution        : lots uniques (V) à l'étape « En cours de constitution »
      2. lots_demande_coordonnees    : lots uniques à l'étape « En demande de coordonnées »
      3. lots_en_controle            : lots uniques à l'étape « En cours de contrôle »
      4. ops_en_controle             : opérations (N° dossier renseigné) « En cours de contrôle »
      5. lots_controle_plus_5_sem    : dont lots envoyés depuis plus de 5 semaines
                                       (« Date d'envoi du lot de contrôle » -> aujourd'hui)
      6. ops_controle_plus_5_sem     : dont opérations de ces lots
      7. lots_restitues              : lots uniques à l'étape « Lot restitué »
      8. ops_eligibles               : opérations à l'étape « Eligible »
      9. ops_integrables_delai_2_mois: opérations « Eligible » dont la date de réalisation
                                       (ou à défaut la date prévisionnelle) + 12 mois
                                       dépasse aujourd'hui + 2 mois
    Renvoie aussi deux tableaux pour les graphiques :
      - etapes  : nombre de lots par étape de contrôle (hors « Eligible » et vide)
      - bureaux : lots « En cours de contrôle » par bureau, délai respecté / > 5 semaines
    Un lot sans date d'envoi est compté dans « délai respecté ».
    """
    aujourdhui = pd.Timestamp(date_jour)
    etape = df["etape"].map(normaliser)
    a_un_lot = (df["lot"] != "") & (df["lot"].map(normaliser) != normaliser(LOT_VIDE))

    def nb_lots(nom_etape: str) -> int:
        return int(df.loc[a_un_lot & (etape == normaliser(nom_etape)), "lot"].nunique())

    # Lots en cours de contrôle et retard (> 5 semaines depuis l'envoi)
    controle = df[etape == normaliser(ETAPE_CONTROLE)]
    controle_lots = controle[a_un_lot.loc[controle.index]]
    envoi_par_lot = controle_lots.groupby("lot")["date_envoi"].min()
    en_retard = (aujourdhui - envoi_par_lot) > pd.Timedelta(weeks=SEMAINES_RETARD)
    lots_retard = set(en_retard[en_retard].index)

    # Opérations éligibles et intégrables (délai > 2 mois)
    eligibles = df[etape == normaliser(ETAPE_ELIGIBLE)]
    reference = eligibles["date_realisation"].fillna(eligibles["date_prev_realisation"])
    limite = reference + pd.DateOffset(months=12)
    integrables = limite > aujourdhui + pd.DateOffset(months=DELAI_MIN_MOIS)

    indicateurs = {
        "lots_en_constitution": nb_lots(ETAPE_CONSTITUTION),
        "lots_demande_coordonnees": nb_lots(ETAPE_COORDONNEES),
        "lots_en_controle": nb_lots(ETAPE_CONTROLE),
        "ops_en_controle": int((controle["dossier"] != "").sum()),
        "lots_controle_plus_5_sem": len(lots_retard),
        "ops_controle_plus_5_sem": int(controle["lot"].isin(lots_retard).sum()),
        "lots_restitues": nb_lots(ETAPE_RESTITUE),
        # Opérations des stocks du schéma de flux (lignes à ces étapes)
        "ops_en_creation": int(((etape.isin([normaliser(ETAPE_CONSTITUTION), normaliser(ETAPE_COORDONNEES)]))
                                & (df["dossier"] != "")).sum()),
        "ops_restitues": int(((etape == normaliser(ETAPE_RESTITUE)) & (df["dossier"] != "")).sum()),
        "ops_eligibles": int((eligibles["dossier"] != "").sum()),
        "ops_integrables_delai_2_mois": int(integrables.sum()),
    }

    # Graphique 1 : lots par étape de contrôle (hors « Eligible » et vide)
    hors = df[a_un_lot & ~etape.isin(["", normaliser(ETAPE_ELIGIBLE)])]
    etapes = (hors.groupby("etape")["lot"].nunique().rename("nb_lots")
              .sort_values(ascending=False).reset_index())

    # Graphique 2 : lots en cours de contrôle par bureau, délai respecté / > 5 semaines
    par_lot = controle_lots.drop_duplicates("lot")[["lot", "bureau"]].copy()
    par_lot["bureau"] = par_lot["bureau"].replace("", "(non renseigné)")
    par_lot["retard"] = par_lot["lot"].isin(lots_retard)
    bureaux = pd.DataFrame({"bureau": sorted(set(liste_bureaux(df)) | set(par_lot["bureau"]))})
    comptes = par_lot.groupby(["bureau", "retard"]).size().unstack(fill_value=0)
    bureaux["lots_delai_ok"] = bureaux["bureau"].map(comptes.get(False, pd.Series(dtype=int))).fillna(0).astype(int)
    bureaux["lots_plus_5_sem"] = bureaux["bureau"].map(comptes.get(True, pd.Series(dtype=int))).fillna(0).astype(int)
    return indicateurs, etapes, bureaux


# ----------------------------------------------------------------------------
# FLUX D'ACTIVITÉ : lots créés / envoyés / restitués le jour ouvré précédent
# ----------------------------------------------------------------------------

def _paques(annee: int) -> date:
    """Date du dimanche de Pâques (algorithme grégorien)."""
    a, b, c = annee % 19, annee // 100, annee % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois = (h + l - 7 * m + 114) // 31
    jour = (h + l - 7 * m + 114) % 31 + 1
    return date(annee, mois, jour)


def jours_feries(annee: int) -> set[date]:
    """Jours fériés en France métropolitaine (dont lundi de Pâques, Ascension, lundi de Pentecôte)."""
    p = pd.Timestamp(_paques(annee))
    fixes = [(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)]
    return {date(annee, m, j) for m, j in fixes} | {
        (p + pd.Timedelta(days=n)).date() for n in (1, 39, 50)}


def jour_ouvre_precedent(d: date) -> date:
    """Jour ouvré (lundi-vendredi, hors fériés) qui précède la date. Lundi -> vendredi."""
    j = pd.Timestamp(d).date() - pd.Timedelta(days=1)
    while j.weekday() >= 5 or j in jours_feries(j.year):
        j -= pd.Timedelta(days=1)
    return j


def lots_et_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Une ligne par lot unique (colonne V) : date de création (W), date d'envoi (Z),
    date de retour (AA) et nombre d'opérations (nombre de lignes du lot).
    Si un lot a plusieurs dates différentes, la plus ancienne est retenue.
    """
    avec_lot = df[(df["lot"] != "") & (df["lot"].map(normaliser) != normaliser(LOT_VIDE))]
    return avec_lot.groupby("lot").agg(
        date_creation=("date_creation_lot", "min"),
        date_envoi=("date_envoi", "min"),
        date_retour=("date_retour", "min"),
        nb_ops=("lot", "size"),
    ).reset_index()


ETAPES_FLUX = [("creation", "date_creation", "Lots créés"),
               ("envoi", "date_envoi", "Lots envoyés"),
               ("retour", "date_retour", "Lots restitués")]


def indicateurs_flux(df: pd.DataFrame, date_export: date) -> dict:
    """
    Flux du jour ouvré précédant la date de l'export :
    lots (et opérations) créés, envoyés et restitués ce jour-là.
    """
    jour = pd.Timestamp(jour_ouvre_precedent(date_export))
    lots = lots_et_dates(df)
    resultat = {"date_reference_flux": jour.date().isoformat()}
    for cle, col, _ in ETAPES_FLUX:
        du_jour = lots[lots[col] == jour]
        resultat[f"flux_lots_{cle}"] = int(len(du_jour))
        resultat[f"flux_ops_{cle}"] = int(du_jour["nb_ops"].sum())
    return resultat


def indicateurs_flux_periode(df: pd.DataFrame, debut: date, fin: date) -> dict:
    """
    Flux entre deux exports : lots (et opérations) dont la date de création, d'envoi ou
    de retour est comprise entre `debut` (date de l'export précédent) et `fin` (date de
    l'export actuel), BORNES INCLUSES (>= debut et <= fin).
    """
    d0, d1 = pd.Timestamp(debut), pd.Timestamp(fin)
    lots = lots_et_dates(df)
    resultat = {"periode_debut": pd.Timestamp(debut).date().isoformat(),
                "periode_fin": pd.Timestamp(fin).date().isoformat()}
    for cle, col, _ in ETAPES_FLUX:
        dans_periode = lots[lots[col].between(d0, d1, inclusive="both")]
        resultat[f"flux_periode_lots_{cle}"] = int(len(dans_periode))
        resultat[f"flux_periode_ops_{cle}"] = int(dans_periode["nb_ops"].sum())
    return resultat


def flux_jours_periode(df: pd.DataFrame, debut: date, fin: date) -> pd.DataFrame:
    """
    Flux jour par jour entre deux exports, bornes incluses. Les jours ouvrés sont tous
    affichés ; un samedi, dimanche ou jour férié n'apparaît que s'il a eu de l'activité.
    Les totaux sont donc égaux à ceux de indicateurs_flux_periode().
    """
    lots = lots_et_dates(df)
    jours = pd.date_range(pd.Timestamp(debut), pd.Timestamp(fin), freq="D")
    t = pd.DataFrame({"jour": jours})
    for cle, col, _ in ETAPES_FLUX:
        par_jour = lots.groupby(col).agg(n=("lot", "size"), o=("nb_ops", "sum"))
        t[f"lots_{cle}"] = t["jour"].map(par_jour["n"]).fillna(0).astype(int)
        t[f"ops_{cle}"] = t["jour"].map(par_jour["o"]).fillna(0).astype(int)
    ouvre = [j.weekday() < 5 and j.date() not in jours_feries(j.year) for j in t["jour"]]
    activite = t[[c for c in t.columns if c != "jour"]].sum(axis=1) > 0
    return t[pd.Series(ouvre, index=t.index) | activite].reset_index(drop=True)


def flux_par_jour(df: pd.DataFrame, date_export: date, nb_jours: int = 20) -> pd.DataFrame:
    """
    Flux des `nb_jours` derniers jours ouvrés avant la date de l'export, calculé
    directement à partir des dates de l'export (pas besoin d'archives).
    Colonnes : jour, puis lots_/ops_ creation, envoi, retour.
    """
    jours, j = [], date_export
    for _ in range(nb_jours):
        j = jour_ouvre_precedent(j)
        jours.append(pd.Timestamp(j))
    jours = jours[::-1]
    lots = lots_et_dates(df)
    t = pd.DataFrame({"jour": jours})
    for cle, col, _ in ETAPES_FLUX:
        par_jour = lots.groupby(col).agg(n=("lot", "size"), o=("nb_ops", "sum"))
        t[f"lots_{cle}"] = t["jour"].map(par_jour["n"]).fillna(0).astype(int)
        t[f"ops_{cle}"] = t["jour"].map(par_jour["o"]).fillna(0).astype(int)
    return t


def resume_archive(df: pd.DataFrame, res: dict, date_export: date,
                   date_jour: date | None = None, date_precedente: date | None = None) -> dict:
    """
    Prépare tout ce qui est archivé pour un export :
      - synthese : une ligne (création des lots + 9 indicateurs de suivi)
      - detail   : opérations intégrées par bénéficiaire (colonne « Organisme ») et fiche CEE
      - etapes   : lots par étape de contrôle (camembert)
      - bureaux  : lots en contrôle par bureau, délai respecté / > 5 semaines (histogramme)
    """
    integres = set(zip(res["ajouts"]["dossier"], res["ajouts"]["fiche"])) | \
        set(zip(res["affectations"]["dossier"], res["affectations"]["fiche"]))
    groupes_ok = set(zip(res["groupes"]["dossier"], res["groupes"]["fiche"]))
    ops = candidats(df)
    ops = ops[[(d, f) in integres and (d, f) in groupes_ok
               for d, f in zip(ops["dossier"], ops["fiche"])]]
    ops = ops[ops["date_realisation"].notna() & (ops["abandon"].map(normaliser) != "oui")]

    synthese = {
        "date_export": date_export.isoformat(),
        "date_archivage": pd.Timestamp.now().strftime("%d/%m/%Y %H:%M"),
        "nb_operations_a_integrer": int(res["lots"]["nb_ops"].sum() + res["ajouts"]["nb_ops"].sum()),
        "nb_lots_a_creer": int(len(res["lots"])),
        "nb_operations_ajoutees_lots_existants": int(res["ajouts"]["nb_ops"].sum()),
        "nb_dossiers": int(len({d for d, _ in integres})),
    }
    indicateurs, etapes, bureaux = indicateurs_suivi(df, date_jour or date.today())
    synthese.update(indicateurs)
    synthese.update(indicateurs_flux(df, date_export))
    # Flux entre l'export archivé précédent et celui-ci (s'il y en a un)
    if date_precedente:
        synthese.update(indicateurs_flux_periode(df, date_precedente, date_export))

    detail = (ops.assign(beneficiaire=ops["organisme"].replace("", "(non renseigné)"))
              .groupby(["beneficiaire", "fiche"]).size().rename("nb_operations").reset_index())
    for t in (detail, etapes, bureaux):
        t.insert(0, "date_export", date_export.isoformat())
    return {"synthese": synthese, "detail": detail, "etapes": etapes, "bureaux": bureaux}
