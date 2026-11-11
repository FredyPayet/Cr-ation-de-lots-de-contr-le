"""
planning.py — Charge de travail de la semaine (onglet « Planning »)
===================================================================

Règles pures (aucun affichage). À partir de l'export ODICEE :

  1. Lots à passer en demande de coordonnées : lots « En cours de constitution »,
     âge = date de l'export − date de création du lot, à faire en moins de 2 semaines.
  2. Lots à envoyer : lots « En demande de coordonnées »,
     âge = date de l'export − date de création du lot, à faire en moins de 5 semaines.
  3. Lots à traiter : lots « Lot restitué »,
     âge = date de l'export − date de retour du lot, à faire en moins de 3 semaines.
  4. Dossiers à relancer sans AH : dossiers dont le Statut AH est « Attente AH ».
  5. Opérations non satisfaisantes à relancer : Statut du contrôle « Contrôlé KO sur site ».

Priorité d'un lot (du plus urgent au moins urgent) :
  1. Prioritaire — l'échéance du lot (date réelle de réalisation la plus ancienne + 12 mois)
     tombe dans 3 mois ou moins (ou est déjà passée) ;
  2. Délai dépassé — l'âge du lot a atteint le délai ;
  3. À planifier — le délai sera atteint dans la semaine ;
  4. Dans les délais.
Les priorités 1 à 3 sont « au planning de la semaine ».
Tous les délais sont modifiables (voir Reglages).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date

import pandas as pd

import regles
from regles import normaliser

STATUT_ATTENTE_AH = "Attente AH"
# Libellés acceptés pour une opération non satisfaisante (colonne AC « Statut du contrôle »)
STATUT_KO = "Contrôlé KO sur site"                      # libellé affiché
STATUTS_KO = [STATUT_KO, "Contrôle KO sur site", "Contrôlé KO par site", "Contrôle KO par site"]

PRIORITAIRE, DEPASSE, A_PLANIFIER, DANS_DELAIS = (
    "1 - Prioritaire (échéance proche)", "2 - Délai dépassé", "3 - À planifier cette semaine",
    "4 - Dans les délais")
PRIORITES = [PRIORITAIRE, DEPASSE, A_PLANIFIER, DANS_DELAIS]


@dataclass
class Reglages:
    """Délais du planning (modifiables dans l'onglet)."""
    semaines_coordonnees: int = 2   # constitution -> demande de coordonnées
    semaines_envoi: int = 5         # demande de coordonnées -> envoi au bureau de contrôle
    semaines_traitement: int = 3    # lot restitué -> lot traité
    mois_priorite: int = 3          # échéance dans X mois ou moins = prioritaire
    jours_alerte: int = 7           # « approche du délai » : délai atteint dans X jours ou moins

    def en_dict(self) -> dict:
        return asdict(self)


# Les trois activités sur les lots : clé, titre, étape, date de départ, réglage du délai
ACTIVITES = [
    ("coordonnees", "Lots à passer en demande de coordonnées", regles.ETAPE_CONSTITUTION,
     "date_creation_lot", "Date de création", "semaines_coordonnees"),
    ("envoi", "Lots à envoyer", regles.ETAPE_COORDONNEES,
     "date_creation_lot", "Date de création", "semaines_envoi"),
    ("traitement", "Lots à traiter", regles.ETAPE_RESTITUE,
     "date_retour", "Date de retour", "semaines_traitement"),
]


def _avec_lot(df: pd.DataFrame) -> pd.Series:
    return (df["lot"] != "") & (df["lot"].map(normaliser) != normaliser(regles.LOT_VIDE))


def _uniques(valeurs) -> str:
    return ", ".join(sorted({str(v) for v in valeurs if str(v) not in ("", "nan")}))


def echeances_lots(df: pd.DataFrame) -> pd.DataFrame:
    """Par lot : date réelle de réalisation la plus ancienne et échéance (+ 12 mois)."""
    t = df[_avec_lot(df)].groupby("lot")["date_realisation"].min().rename("drr_plus_ancienne").reset_index()
    t["echeance"] = t["drr_plus_ancienne"] + pd.DateOffset(months=12)
    return t


def lots_activite(df: pd.DataFrame, cle: str, date_export: date, date_jour: date,
                  r: Reglages) -> pd.DataFrame:
    """
    Tous les lots d'une activité (même ceux dans les délais), triés par priorité.
    Une ligne par lot : opérations, âge, délai restant, échéance, priorité.
    """
    _, _, etape, col_date, _, champ = next(a for a in ACTIVITES if a[0] == cle)
    limite = int(getattr(r, champ)) * 7
    lignes = df[_avec_lot(df) & (df["etape"].map(normaliser) == normaliser(etape))]
    colonnes = ["lot", "fiche", "dcts", "bureau", "nb_ops", "volume", "date_depart", "age_jours",
                "jours_restants", "drr_plus_ancienne", "echeance", "jours_avant_echeance", "priorite",
                "au_planning"]
    if lignes.empty:
        return pd.DataFrame(columns=colonnes)

    t = lignes.groupby("lot").agg(
        fiche=("fiche", _uniques), dcts=("dcts", _uniques), bureau=("bureau", _uniques),
        nb_ops=("lot", "size"), volume=("volume", "sum"),
        date_depart=(col_date, "min"), drr_plus_ancienne=("date_realisation", "min"),
    ).reset_index()
    t["volume"] = t["volume"].fillna(0).round().astype(int)
    t["echeance"] = t["drr_plus_ancienne"] + pd.DateOffset(months=12)
    t["age_jours"] = (pd.Timestamp(date_export) - t["date_depart"]).dt.days
    t["jours_restants"] = limite - t["age_jours"]
    t["jours_avant_echeance"] = (t["echeance"] - pd.Timestamp(date_jour)).dt.days

    seuil = pd.Timestamp(date_jour) + pd.DateOffset(months=int(r.mois_priorite))
    t["priorite"] = DANS_DELAIS
    t.loc[t["jours_restants"] <= int(r.jours_alerte), "priorite"] = A_PLANIFIER
    t.loc[t["jours_restants"] <= 0, "priorite"] = DEPASSE
    t.loc[t["echeance"].notna() & (t["echeance"] <= seuil), "priorite"] = PRIORITAIRE
    t["au_planning"] = t["priorite"] != DANS_DELAIS
    # Tri : priorité, puis échéance la plus proche (prioritaires) ou délai restant le plus court
    t["_tri"] = t["jours_avant_echeance"].where(t["priorite"] == PRIORITAIRE, t["jours_restants"])
    t = t.sort_values(["priorite", "_tri", "lot"], na_position="last").drop(columns="_tri")
    return t[colonnes].reset_index(drop=True)


def _non_abandonnees(df: pd.DataFrame) -> pd.Series:
    return df["abandon"].map(normaliser) != "oui"


def _avec_echeance_lot(lignes: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute à chaque ligne la date la plus ancienne et l'échéance de son lot
    (à défaut de lot : celles de la ligne elle-même)."""
    t = lignes.merge(echeances_lots(df), on="lot", how="left")
    t["drr_plus_ancienne"] = t["drr_plus_ancienne"].fillna(t["date_realisation"])
    t["echeance"] = t["drr_plus_ancienne"] + pd.DateOffset(months=12)
    return t


def dossiers_attente_ah(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dossiers à relancer : au moins une opération (non abandonnée) au Statut AH « Attente AH ».
    Une ligne par dossier : DCTS, organisme, opérations concernées, lots associés, date réelle
    de réalisation la plus ancienne de ces lots et échéance (+ 12 mois). Les plus urgents d'abord.
    """
    masque = (df["statut_ah"].map(normaliser) == normaliser(STATUT_ATTENTE_AH)) \
        & _non_abandonnees(df) & (df["dossier"] != "")
    colonnes = ["dossier", "dcts", "organisme", "nb_ops", "fiches", "lots", "etapes",
                "drr_plus_ancienne", "echeance"]
    if not masque.any():
        return pd.DataFrame(columns=colonnes)
    t = _avec_echeance_lot(df[masque], df)
    t["lot"] = t["lot"].where(t["lot"].map(normaliser) != normaliser(regles.LOT_VIDE), "")
    t = t.groupby("dossier").agg(
        dcts=("dcts", _uniques), organisme=("organisme", _uniques), nb_ops=("dossier", "size"),
        fiches=("fiche", _uniques), lots=("lot", _uniques), etapes=("etape", _uniques),
        drr_plus_ancienne=("drr_plus_ancienne", "min"), echeance=("echeance", "min"),
    ).reset_index()
    return t.sort_values(["echeance", "dossier"], na_position="last")[colonnes].reset_index(drop=True)


def operations_ko(df: pd.DataFrame) -> pd.DataFrame:
    """
    Opérations non satisfaisantes à relancer : Statut du contrôle « Contrôlé KO sur site ».
    Une ligne par opération, avec la date la plus ancienne et l'échéance de son lot.
    """
    masque = df["statut_controle"].map(normaliser).isin({normaliser(s) for s in STATUTS_KO}) \
        & _non_abandonnees(df)
    colonnes = ["dossier", "dcts", "organisme", "fiche", "lot", "etape", "bureau", "statut_controle",
                "date_realisation", "drr_plus_ancienne", "echeance"]
    if not masque.any():
        return pd.DataFrame(columns=colonnes)
    t = _avec_echeance_lot(df[masque], df)
    return t.sort_values(["echeance", "dossier"], na_position="last")[colonnes].reset_index(drop=True)


def charge(df: pd.DataFrame, date_export: date, date_jour: date, r: Reglages) -> dict:
    """Tout le planning : {clé d'activité: tableau des lots, "ah": dossiers, "ko": opérations}."""
    resultat = {cle: lots_activite(df, cle, date_export, date_jour, r) for cle, *_ in ACTIVITES}
    resultat["ah"] = dossiers_attente_ah(df)
    resultat["ko"] = operations_ko(df)
    return resultat


def resume(charge_: dict, nb_lots_a_creer: int, nb_ops_a_integrer: int, date_export: date,
           r: Reglages) -> dict:
    """Les chiffres du planning d'un export, sur une ligne (pour l'archive, le PDF et le mail)."""
    ligne = {"date_export": pd.Timestamp(date_export).date().isoformat(),
             "semaine_export": int(pd.Timestamp(date_export).isocalendar().week),
             "date_archivage": pd.Timestamp.now().strftime("%d/%m/%Y %H:%M")}
    for cle, *_ in ACTIVITES:
        t = charge_[cle]
        ligne.update({
            f"{cle}_planning": int(t["au_planning"].sum()), f"{cle}_total": int(len(t)),
            f"{cle}_prioritaires": int((t["priorite"] == PRIORITAIRE).sum()),
            f"{cle}_depasses": int((t["priorite"] == DEPASSE).sum()),
            f"{cle}_a_planifier": int((t["priorite"] == A_PLANIFIER).sum()),
            f"{cle}_ops_planning": int(t.loc[t["au_planning"].astype(bool), "nb_ops"].sum()),
            f"{cle}_ops_total": int(t["nb_ops"].sum())})
    ligne.update({
        "ah_dossiers": int(len(charge_["ah"])), "ah_ops": int(charge_["ah"]["nb_ops"].sum()),
        "ko_ops": int(len(charge_["ko"])), "ko_dossiers": int(charge_["ko"]["dossier"].nunique()),
        "lots_a_creer": int(nb_lots_a_creer), "ops_a_integrer": int(nb_ops_a_integrer),
        "delai_coordonnees_sem": int(r.semaines_coordonnees), "delai_envoi_sem": int(r.semaines_envoi),
        "delai_traitement_sem": int(r.semaines_traitement), "priorite_mois": int(r.mois_priorite)})
    return ligne


def phrase_mail(ligne: dict) -> str:
    """Paragraphe du mail hebdomadaire sur la charge de la semaine (ton formel)."""
    def n(v):
        return f"{int(v):,}".replace(",", " ")
    return ("S'agissant de la charge de travail de la semaine, détaillée dans le second document joint "
            f"(planning) : {n(ligne['coordonnees_planning'])} lots sont à passer en demande de coordonnées "
            f"(sur {n(ligne['coordonnees_total'])} en cours de constitution), "
            f"{n(ligne['envoi_planning'])} lots sont à envoyer (sur {n(ligne['envoi_total'])} en demande de "
            f"coordonnées) et {n(ligne['traitement_planning'])} lots restitués sont à traiter "
            f"(sur {n(ligne['traitement_total'])}). Par ailleurs, {n(ligne['ah_dossiers'])} dossiers sont à "
            f"relancer en attente d'AH, {n(ligne['ko_ops'])} opérations non satisfaisantes sont à relancer "
            f"et {n(ligne['lots_a_creer'])} lots sont à créer.")


# --- Tableaux prêts à afficher ou à exporter (libellés en français) -----------

def tableau_lots(t: pd.DataFrame, cle: str) -> pd.DataFrame:
    libelle_date = next(a for a in ACTIVITES if a[0] == cle)[4]
    v = t.rename(columns={
        "priorite": "Priorité", "lot": "Lot", "fiche": "Fiche CEE", "dcts": "DCTS",
        "bureau": "Bureau de contrôle", "nb_ops": "Opérations", "volume": "Volume (MWhc)",
        "date_depart": libelle_date, "age_jours": "Âge (jours)", "jours_restants": "Jours restants avant le délai",
        "drr_plus_ancienne": "Date réelle de réalisation la plus ancienne",
        "echeance": "Échéance (+ 12 mois)", "jours_avant_echeance": "Jours avant l'échéance"})
    v["Au planning de la semaine"] = t["au_planning"].map({True: "Oui", False: "Non"}).values
    ordre = ["Priorité", "Au planning de la semaine", "Lot", "Opérations", libelle_date, "Âge (jours)",
             "Jours restants avant le délai", "Échéance (+ 12 mois)", "Jours avant l'échéance",
             "Date réelle de réalisation la plus ancienne", "Fiche CEE", "Volume (MWhc)", "DCTS",
             "Bureau de contrôle"]
    for c in (libelle_date, "Date réelle de réalisation la plus ancienne", "Échéance (+ 12 mois)"):
        v[c] = pd.to_datetime(v[c]).dt.date
    return v[ordre]


def tableau_ah(t: pd.DataFrame) -> pd.DataFrame:
    v = t.rename(columns={
        "dossier": "N° dossier", "dcts": "DCTS", "organisme": "Organisme", "nb_ops": "Opérations en attente AH",
        "fiches": "Fiches CEE", "lots": "Lots associés", "etapes": "Étapes de contrôle",
        "drr_plus_ancienne": "Date réelle de réalisation la plus ancienne du lot",
        "echeance": "Échéance (+ 12 mois)"})
    for c in ("Date réelle de réalisation la plus ancienne du lot", "Échéance (+ 12 mois)"):
        v[c] = pd.to_datetime(v[c]).dt.date
    return v


def tableau_ko(t: pd.DataFrame) -> pd.DataFrame:
    v = t.rename(columns={
        "dossier": "N° dossier", "dcts": "DCTS", "organisme": "Organisme", "fiche": "Fiche CEE",
        "lot": "Lot", "etape": "Étape de contrôle", "bureau": "Bureau de contrôle",
        "statut_controle": "Statut du contrôle", "date_realisation": "Date réelle de réalisation",
        "drr_plus_ancienne": "Date réelle de réalisation la plus ancienne du lot",
        "echeance": "Échéance (+ 12 mois)"})
    for c in ("Date réelle de réalisation", "Date réelle de réalisation la plus ancienne du lot",
              "Échéance (+ 12 mois)"):
        v[c] = pd.to_datetime(v[c]).dt.date
    return v
