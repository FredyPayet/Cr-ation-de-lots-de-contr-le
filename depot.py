"""
depot.py — Onglet « Dépôt et actions sur les dossiers »
=======================================================

Règles pures (aucun affichage), à partir de l'export ODICEE.

1. DÉPÔT DES LOTS
   Lots aux étapes « Lot traité », « En cours de contrôle », « En demande de coordonnées »,
   « En cours de constitution » et « Lot restitué ».
   - Date de réalisation urgente du lot : la « Date réelle de réalisation » la plus ancienne
     du lot dont le délai (date + 12 mois) n'est PAS encore dépassé à la date du jour.
     Les opérations dont le délai est déjà dépassé sont passées : on prend la suivante.
     Si toutes les opérations du lot ont dépassé le délai, la date la plus ancienne est
     gardée et le lot est signalé « Délai dépassé pour toutes les opérations ».
   - Période du lot : « Période 4 » si sa « Date réelle d'engagement » (la plus ancienne
     du lot) est au plus tard le 31/12/2021, sinon « Période 5 ».
   - Volume CUMAC (MWhc, colonne J) : lignes du lot qui ont une date réelle de réalisation.

2. ACTIONS SUR LES DOSSIERS (regroupement N° dossier + Fiche CEE)
   - À superviser             : un « Stade » contient « 3F » ;
   - Incomplet                : un « Stade » vaut 1, 1V, 2 ou 3 ;
       dont à scinder par le service contrôle si le « Statut AH » est « Original reçu »
       ou « Attente original » ;
   - Pièce non conforme       : un « Stade » contient « 3R » ;
   - Opération non conforme   : un « Statut du contrôle » contient « Contrôle KO ».
   Les opérations abandonnées ne sont pas prises en compte.
   Date et période du dossier : celles de son LOT (même règles qu'au point 1). Un dossier
   est retenu si l'un de ses lots est dans la plage de dates de SA période (Période 4 ou 5).
   Un dossier sans lot n'a ni date ni période : il est écarté dès qu'une plage est choisie.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

import regles
from regles import normaliser

# --- 1. Dépôt des lots --------------------------------------------------------
ETAPES_DEPOT = ["Lot traité", "En cours de contrôle", "En demande de coordonnées",
                "En cours de constitution", "Lot restitué"]
FIN_PERIODE_4 = pd.Timestamp("2021-12-31")
PERIODE_4, PERIODE_5, PERIODE_INCONNUE = "Période 4", "Période 5", "Période non déterminée"
PERIODES = [PERIODE_4, PERIODE_5]
DELAI_OK, DELAI_DEPASSE = "En cours", "Dépassé pour toutes les opérations"

# --- 2. Actions sur les dossiers ----------------------------------------------
STADES_INCOMPLETS = ["1", "1V", "2", "3"]
STATUTS_AH_A_SCINDER = ["Original reçu", "Attente original"]
TEXTE_KO = "controle ko"   # « Contrôlé KO sur site », « Contrôle KO… » une fois normalisés

SUPERVISER, INCOMPLET, A_SCINDER, PIECE_NC, OPERATION_NC = (
    "superviser", "incomplet", "a_scinder", "piece_nc", "operation_nc")
# clé, libellé du menu, action à mener
CRITERES = [
    (SUPERVISER, "Dossiers à superviser (stade 3F)", "Superviser le dossier"),
    (INCOMPLET, "Dossiers incomplets (stade 1, 1V, 2 ou 3)", "Compléter le dossier"),
    (A_SCINDER, "Dossiers incomplets à scinder par le service contrôle (AH original reçu ou attendu)",
     "Scinder le dossier (service contrôle)"),
    (PIECE_NC, "Dossiers avec une pièce non conforme (stade 3R)", "Faire corriger la pièce non conforme"),
    (OPERATION_NC, "Dossiers avec une opération non conforme (contrôle KO)",
     "Traiter l'opération non conforme"),
]


def _uniques(valeurs) -> str:
    return ", ".join(sorted({str(v) for v in valeurs if str(v) not in ("", "nan", "NaT")}))


def _avec_lot(df: pd.DataFrame) -> pd.Series:
    return (df["lot"] != "") & (df["lot"].map(normaliser) != normaliser(regles.LOT_VIDE))


def dates_urgentes_lots(df: pd.DataFrame, date_jour: date) -> pd.Series:
    """
    Date de réalisation urgente de chaque lot (toutes étapes) : la date réelle de réalisation
    la plus ancienne dont le délai de 12 mois n'est pas dépassé à date_jour ; si toutes sont
    dépassées, la plus ancienne. Série indexée par le nom du lot.
    """
    lignes = df[_avec_lot(df)]
    if lignes.empty:
        return pd.Series(dtype="datetime64[ns]")
    en_delai = lignes["date_realisation"] + pd.DateOffset(months=12) >= pd.Timestamp(date_jour)
    urgente = lignes["date_realisation"].where(en_delai).groupby(lignes["lot"]).min()
    return urgente.fillna(lignes.groupby("lot")["date_realisation"].min())


def periodes_lots(df: pd.DataFrame) -> pd.Series:
    """Période de chaque lot (toutes étapes) d'après sa date réelle d'engagement la plus ancienne."""
    engagement = df[_avec_lot(df)].groupby("lot")["date_engagement"].min()
    periode = pd.Series(PERIODE_INCONNUE, index=engagement.index)
    periode[engagement.notna() & (engagement <= FIN_PERIODE_4)] = PERIODE_4
    periode[engagement > FIN_PERIODE_4] = PERIODE_5
    return periode


def lots_depot(df: pd.DataFrame, date_jour: date) -> pd.DataFrame:
    """Une ligne par lot des étapes concernées : période, date urgente, volume, opérations."""
    colonnes = ["lot", "periode", "etape", "fiche", "date_engagement", "date_urgente", "echeance",
                "statut_delai", "volume", "nb_ops", "ops_hors_delai"]
    etapes = {normaliser(e) for e in ETAPES_DEPOT}
    lignes = df[_avec_lot(df) & df["etape"].map(normaliser).isin(etapes)].copy()
    if lignes.empty:
        return pd.DataFrame(columns=colonnes)

    jour = pd.Timestamp(date_jour)
    # Délai encore en cours pour l'opération : date réelle de réalisation + 12 mois >= aujourd'hui
    lignes["en_delai"] = lignes["date_realisation"] + pd.DateOffset(months=12) >= jour
    lignes["drr_en_delai"] = lignes["date_realisation"].where(lignes["en_delai"])
    lignes["hors_delai"] = lignes["date_realisation"].notna() & ~lignes["en_delai"]
    lignes["volume_retenu"] = lignes["volume"].where(lignes["date_realisation"].notna(), 0.0)

    t = lignes.groupby("lot").agg(
        etape=("etape", _uniques), fiche=("fiche", _uniques),
        date_engagement=("date_engagement", "min"),
        drr_plus_ancienne=("date_realisation", "min"), date_urgente=("drr_en_delai", "min"),
        volume=("volume_retenu", "sum"), nb_ops=("lot", "size"), ops_hors_delai=("hors_delai", "sum"),
    ).reset_index()

    t["statut_delai"] = DELAI_OK
    tous_depasses = t["date_urgente"].isna() & t["drr_plus_ancienne"].notna()
    t.loc[tous_depasses, "statut_delai"] = DELAI_DEPASSE
    t["date_urgente"] = t["date_urgente"].fillna(t["drr_plus_ancienne"])
    t["echeance"] = t["date_urgente"] + pd.DateOffset(months=12)

    t["periode"] = PERIODE_INCONNUE
    t.loc[t["date_engagement"].notna() & (t["date_engagement"] <= FIN_PERIODE_4), "periode"] = PERIODE_4
    t.loc[t["date_engagement"] > FIN_PERIODE_4, "periode"] = PERIODE_5

    t["volume"] = t["volume"].round(3)
    t["ops_hors_delai"] = t["ops_hors_delai"].astype(int)
    return t.sort_values(["date_urgente", "lot"], na_position="last")[colonnes].reset_index(drop=True)


def filtrer_lots(lots: pd.DataFrame, periode: str, debut: date, fin: date) -> pd.DataFrame:
    """Lots d'une période dont la date de réalisation urgente est entre debut et fin (inclus)."""
    return lots[(lots["periode"] == periode)
                & lots["date_urgente"].between(pd.Timestamp(debut), pd.Timestamp(fin), inclusive="both")
                ].reset_index(drop=True)


def tableau_lots(t: pd.DataFrame) -> pd.DataFrame:
    """Tableau à afficher / exporter : les 4 colonnes demandées, puis des informations utiles."""
    v = pd.DataFrame({
        "Lot de contrôle": t["lot"],
        "Date de réalisation urgente du lot": pd.to_datetime(t["date_urgente"]).dt.date,
        "CUMAC MWhc TOTAL du lot": t["volume"].astype(float),
        "Nombre d'opérations du lot": t["nb_ops"].astype(int),
        "Échéance (+ 12 mois)": pd.to_datetime(t["echeance"]).dt.date,
        "Délai de 12 mois": t["statut_delai"],
        "Opérations au délai dépassé": t["ops_hors_delai"].astype(int),
        "Étape de contrôle": t["etape"],
        "Fiche CEE": t["fiche"],
        "Date réelle d'engagement": pd.to_datetime(t["date_engagement"]).dt.date,
    })
    return v


# --- 2. Actions sur les dossiers ----------------------------------------------

def dossiers_actions(df: pd.DataFrame, date_jour: date) -> pd.DataFrame:
    """
    Une ligne par couple N° dossier + Fiche CEE (opérations non abandonnées), avec un
    indicateur vrai / faux par critère et le texte des actions à mener.
    """
    colonnes = (["dossier", "fiche", "dcts", "organisme", "stades", "statuts_ah", "statuts_controle",
                 "lots", "periodes", "etapes", "nb_ops", "date_urgente_lot", "drr_plus_ancienne", "echeance"]
                + [c for c, *_ in CRITERES] + ["actions"])
    t = df[(df["abandon"].map(normaliser) != "oui") & (df["dossier"] != "")].copy()
    if t.empty:
        return pd.DataFrame(columns=colonnes)

    stade = t["stade"].fillna("").astype(str).str.upper()
    t[SUPERVISER] = stade.str.contains("3F", regex=False)
    t[INCOMPLET] = stade.isin(STADES_INCOMPLETS)
    t[PIECE_NC] = stade.str.contains("3R", regex=False)
    t[OPERATION_NC] = t["statut_controle"].map(normaliser).str.replace("_", " ").str.contains(TEXTE_KO,
                                                                                              regex=False)
    t["ah_scinder"] = t["statut_ah"].map(normaliser).isin({normaliser(s) for s in STATUTS_AH_A_SCINDER})
    t["lot_affiche"] = t["lot"].where(t["lot"].map(normaliser) != normaliser(regles.LOT_VIDE), "")
    # Date urgente et période du lot associé à chaque ligne (vides si la ligne n'est dans aucun lot)
    t["date_urgente_lot"] = t["lot"].map(dates_urgentes_lots(df, date_jour))
    t["periode_lot"] = t["lot"].map(periodes_lots(df)).fillna("")

    g = t.groupby(["dossier", "fiche"]).agg(
        dcts=("dcts", _uniques), organisme=("organisme", _uniques), stades=("stade", _uniques),
        statuts_ah=("statut_ah", _uniques), statuts_controle=("statut_controle", _uniques),
        lots=("lot_affiche", _uniques), etapes=("etape", _uniques), nb_ops=("dossier", "size"),
        drr_plus_ancienne=("date_realisation", "min"), date_urgente_lot=("date_urgente_lot", "min"),
        periodes=("periode_lot", _uniques),
        superviser=(SUPERVISER, "any"), incomplet=(INCOMPLET, "any"), piece_nc=(PIECE_NC, "any"),
        operation_nc=(OPERATION_NC, "any"), ah_scinder=("ah_scinder", "any"),
    ).reset_index()
    g[A_SCINDER] = g[INCOMPLET] & g["ah_scinder"]
    g["echeance"] = g["drr_plus_ancienne"] + pd.DateOffset(months=12)

    def actions(r) -> str:
        texte = []
        if r[SUPERVISER]:
            texte.append("Superviser le dossier")
        if r[INCOMPLET]:
            texte.append("Scinder le dossier (service contrôle)" if r[A_SCINDER] else "Compléter le dossier")
        if r[PIECE_NC]:
            texte.append("Faire corriger la pièce non conforme")
        if r[OPERATION_NC]:
            texte.append("Traiter l'opération non conforme")
        return " ; ".join(texte)

    g["actions"] = g.apply(actions, axis=1)
    g = g[g["actions"] != ""]
    return g.sort_values(["date_urgente_lot", "echeance", "dossier", "fiche"],
                         na_position="last")[colonnes].reset_index(drop=True)


def filtrer_dossiers(df: pd.DataFrame, dossiers: pd.DataFrame, date_jour: date,
                     plages: dict) -> pd.DataFrame:
    """
    Dossiers dont au moins un lot est dans la plage de dates de sa période.
    plages : {"Période 4": (debut, fin) ou None, "Période 5": (debut, fin) ou None}
             None = toutes les dates de cette période.
    La date urgente et la période affichées sont alors celles des lots retenus.
    """
    lignes = df[_avec_lot(df) & (df["dossier"] != "")][["dossier", "fiche", "lot"]].drop_duplicates()
    lignes = lignes.assign(date_urgente_lot=lignes["lot"].map(dates_urgentes_lots(df, date_jour)),
                           periode=lignes["lot"].map(periodes_lots(df)))
    garde = pd.Series(False, index=lignes.index)
    for periode in PERIODES:
        de_la_periode = lignes["periode"] == periode
        plage = plages.get(periode)
        if plage is None:
            garde |= de_la_periode
        else:
            garde |= de_la_periode & lignes["date_urgente_lot"].between(
                pd.Timestamp(plage[0]), pd.Timestamp(plage[1]), inclusive="both")
    retenus = lignes[garde].groupby(["dossier", "fiche"]).agg(
        date_retenue=("date_urgente_lot", "min"), periodes_retenues=("periode", _uniques)).reset_index()
    t = dossiers.merge(retenus, on=["dossier", "fiche"], how="inner")
    t["date_urgente_lot"], t["periodes"] = t["date_retenue"], t["periodes_retenues"]
    return (t.drop(columns=["date_retenue", "periodes_retenues"])
            .sort_values(["date_urgente_lot", "echeance", "dossier", "fiche"], na_position="last")
            .reset_index(drop=True))


def liste_critere(dossiers: pd.DataFrame, cle: str | None) -> pd.DataFrame:
    """Dossiers d'un critère (None = tous les dossiers qui ont au moins une action)."""
    return dossiers if cle is None else dossiers[dossiers[cle].astype(bool)].reset_index(drop=True)


def tableau_dossiers(t: pd.DataFrame) -> pd.DataFrame:
    """Tableau à afficher / exporter, libellés en français."""
    vide = pd.Series("", index=t.index)
    for col in ("periodes", "date_urgente_lot"):      # résultats d'une version précédente
        if col not in t:
            t = t.assign(**{col: vide if col == "periodes" else pd.NaT})
    return pd.DataFrame({
        "N° dossier": t["dossier"], "Fiche CEE": t["fiche"], "Action(s) à mener": t["actions"],
        "Stade": t["stades"], "Statut AH": t["statuts_ah"], "Statut du contrôle": t["statuts_controle"],
        "Lot de contrôle": t["lots"], "Période": t["periodes"],
        "Date de réalisation urgente du lot": pd.to_datetime(t["date_urgente_lot"]).dt.date,
        "Étape de contrôle": t["etapes"],
        "Opérations": t["nb_ops"].astype(int), "DCTS": t["dcts"], "Organisme": t["organisme"],
        "Date réelle de réalisation la plus ancienne": pd.to_datetime(t["drr_plus_ancienne"]).dt.date,
        "Échéance (+ 12 mois)": pd.to_datetime(t["echeance"]).dt.date,
    })
