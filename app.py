"""
app.py — Interface Streamlit « Création des lots de contrôle CEE »
==================================================================

Lancer en local :      streamlit run app.py
Les calculs sont dans regles.py, les fichiers Excel dans exports.py,
la sauvegarde (réglages, archive, Google Sheet) dans stockage.py.

Organisation de ce fichier :
    1. Configuration de la page et styles
    2. Fonctions mises en cache (lecture du fichier, calculs)
    3. Barre latérale : fichier et réglages
    4. Onglet « Création des lots » (indicateurs, bureaux, parties 1, 2, 3)
    5. Bouton d'archivage (en haut de page)
    6. Onglet « Opérations écartées »
    7. Onglet « Historique »
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import exports
import messagerie
import regles
import stockage

# Le rapport PDF a besoin de matplotlib et reportlab (requirements.txt). S'ils manquent,
# seul l'onglet « Rapport hebdomadaire » est indisponible : le reste de l'outil fonctionne.
try:
    import rapport_pdf
    ERREUR_PDF = None
except ImportError as _e:
    rapport_pdf = None
    ERREUR_PDF = str(_e)

# ============================================================================
# 1. CONFIGURATION DE LA PAGE
# ============================================================================

st.set_page_config(page_title="Création des lots de contrôle", page_icon="📦", layout="wide")

st.markdown("""
<style>
  html, body, [data-testid="stAppViewContainer"] { font-size: 16px; }
  .stButton button, .stDownloadButton button { min-height: 44px; font-weight: 600; }
  [data-testid="stMetricValue"] { font-size: 2rem; }
  [data-testid="stExpander"] summary p { font-size: 1.1rem; font-weight: 600; }
  .etape-vide { background:#F3F6FA; border-radius:12px; padding:1.5rem 2rem; line-height:1.9; }
</style>
""", unsafe_allow_html=True)

COULEURS = {"actuel": "#8A9BB0", "apres": "#1F5FAD", "objectif": "#E08A00"}
JOURS_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
FORMAT_DATE = "DD/MM/YYYY"


def fr(n) -> str:
    """1234 -> '1 234' (séparateur de milliers français)."""
    return f"{int(n):,}".replace(",", " ")


def date_fr(d) -> str:
    return pd.Timestamp(d).strftime("%d/%m/%Y") if pd.notna(d) else "-"


def periode_flux(ligne: dict) -> tuple[str, str]:
    """
    Quel flux afficher ? Entre deux exports (si un export précédent était archivé) :
    lots dont la date est >= date de l'export précédent ET <= date de l'export.
    Sinon : jour ouvré précédant l'export. Renvoie (préfixe des colonnes, libellé).
    """
    if ligne.get("periode_debut") and not pd.isna(ligne.get("flux_periode_lots_creation")):
        return ("flux_periode_", f"du {date_fr(ligne['periode_debut'])} au {date_fr(ligne['periode_fin'])} "
                                 "(dates des deux exports incluses)")
    jour = ligne.get("date_reference_flux")
    return "flux_", (f"du {date_fr(jour)} (jour ouvré précédent)" if jour else "—")


# ============================================================================
# 2. FONCTIONS MISES EN CACHE
# ============================================================================

# Empreinte du code des règles : quand regles.py, exports.py ou stockage.py change
# (nouvelle version déployée), les calculs gardés en mémoire sont automatiquement ignorés.
VERSION_CODE = hashlib.md5(b"".join(
    open(m.__file__, "rb").read() for m in (regles, exports, stockage))).hexdigest()[:10]

# Vérification que tous les fichiers ont bien été mis à jour ensemble
if not (hasattr(regles, "indicateurs_flux") and hasattr(stockage, "INDICATEURS_FLUX") and hasattr(messagerie, "creer_brouillon")
        and hasattr(exports, "excel_tableau")):
    st.error("⚠️ Mise à jour incomplète : le fichier « regles.py » est d'une ancienne version. "
             "Remplacez tous les fichiers de l'application (app.py, regles.py, exports.py, "
             "stockage.py) par ceux de la dernière version, puis redémarrez l'application.")
    st.stop()


# max_entries : limite la mémoire utilisée (Streamlit Cloud gratuit ≈ 1 Go)
@st.cache_data(show_spinner="Lecture du fichier Excel… (quelques secondes pour un gros export)", max_entries=2)
def charger_export(contenu: bytes, version: str = VERSION_CODE) -> pd.DataFrame:
    return regles.lire_export(io.BytesIO(contenu))


@st.cache_data(show_spinner="Calcul des lots…", max_entries=4)
def calculer(_df: pd.DataFrame, cle_fichier: str, cle_params: str, _p: regles.Parametres,
             cle_ajouts: str, _ajouts: pd.DataFrame | None, version: str = VERSION_CODE) -> dict:
    # Les paramètres précédés de « _ » ne servent pas de clé : les clés texte suffisent.
    return regles.calculer(_df, _p, _ajouts)


@st.cache_data(ttl=600, show_spinner="Lecture du Google Sheet des clients…", max_entries=2)
def charger_clients(url: str) -> dict:
    return stockage.lire_clients_gsheet(url)


# ============================================================================
# 3. BARRE LATÉRALE : FICHIER ET RÉGLAGES
# ============================================================================

# --- Où enregistrer l'archive et les réglages ? -------------------------------
# Google Sheet si les secrets Streamlit contiennent [gcp_service_account] et [archive],
# sinon dossier local (temporaire sur Streamlit Cloud). Voir README.md.

@st.cache_resource(show_spinner="Connexion au Google Sheet d'archive…")
def connexion_google(identifiants_json: str, url: str):
    return stockage.StockageGoogle(json.loads(identifiants_json), url)


def secrets_gmail() -> tuple[str, str] | None:
    """Adresse Gmail et mot de passe d'application (secrets Streamlit, section [gmail])."""
    try:
        if "gmail" in st.secrets:
            g = st.secrets["gmail"]
            if g.get("adresse") and g.get("mot_de_passe_application"):
                return g["adresse"], g["mot_de_passe_application"]
    except Exception:
        pass
    return None


def secrets_google() -> tuple[dict, str] | None:
    try:
        if "gcp_service_account" in st.secrets and "archive" in st.secrets:
            return dict(st.secrets["gcp_service_account"]), st.secrets["archive"]["google_sheet_url"]
    except Exception:  # pas de fichier de secrets (utilisation en local)
        pass
    return None


erreur_stockage = None
config_google = secrets_google()
if config_google:
    try:
        stockage.utiliser(connexion_google(json.dumps(config_google[0]), config_google[1]))
        if not st.session_state.get("migration_faite"):
            st.session_state["migration_faite"] = True
            copiees = stockage.migrer_archive_locale()
            if copiees:
                st.toast(f"{copiees} archive(s) locale(s) copiée(s) dans le Google Sheet ✅")
    except Exception as e:
        erreur_stockage = str(e)
        stockage.utiliser(stockage.StockageLocal())

sauves = stockage.charger_parametres()

with st.sidebar:
    st.markdown("## 📦 Création des lots")
    infos = stockage.infos_stockage()
    if infos["mode"] == "google":
        st.caption(f"💾 Archive et réglages enregistrés dans le "
                   f"[Google Sheet d'archive]({infos['url']}) ✅")
    elif erreur_stockage:
        st.error(f"⚠️ Google Sheet d'archive inaccessible : {erreur_stockage} "
                 "L'archive est enregistrée temporairement en attendant.")
    else:
        st.caption("⚠️ Archive temporaire : le Google Sheet n'est pas configuré "
                   "(voir le guide dans README.md). Téléchargez l'archive après chaque archivage.")
    st.markdown("### 1. Fichier à traiter")
    fichier = st.file_uploader(
        "Export ODICEE (.xlsx)", type=["xlsx"],
        help="Déposez le fichier « Export_Controle_lots_de_travaux_BS_AAAA-MM-JJ.xlsx » sans le modifier.")

df, erreur = None, None
if fichier is not None:
    contenu = fichier.getvalue()
    cle_fichier = hashlib.md5(contenu).hexdigest()
    try:
        df = charger_export(contenu, VERSION_CODE)
    except regles.ErreurFichier as e:
        erreur = str(e)

date_export = regles.date_export_depuis_nom(fichier.name) if fichier else None

with st.sidebar:
    if fichier is not None and df is not None and date_export is None:
        st.warning("La date d'export n'a pas été trouvée dans le nom du fichier. Indiquez-la :")
        date_export = st.date_input("Date de l'export", value=date.today(), format="DD/MM/YYYY")

    if df is not None:
        st.success(f"✅ {fr(len(df))} opérations chargées — export du {date_fr(date_export)}")

        st.markdown("### 2. Réglages de création")
        mode_libelle = st.radio(
            "Comment regrouper les lots ?",
            ["Par fiche CEE (toutes DCTS confondues)", "Par DCTS et fiche CEE"],
            help="Par fiche CEE : les régions sont mélangées pour remplir les lots jusqu'à 20 "
                 "opérations. Par DCTS : un lot ne contient que des dossiers d'une même région.")
        mode = "dcts_fiche" if mode_libelle.startswith("Par DCTS") else "fiche"

        toutes_dcts = sorted(d for d in df["dcts"].unique() if d)
        dcts_choisies = st.multiselect("Régions (DCTS) à inclure", toutes_dcts, default=toutes_dcts,
                                       placeholder="Choisissez au moins une région")
        if not dcts_choisies:
            st.error("Sélectionnez au moins une région pour créer des lots.")

        st.markdown("**Plage de DFT** — sans case cochée, aucun filtre n'est appliqué.")
        annee = st.number_input("Année de référence (N)", min_value=2019, max_value=2100,
                                value=date.today().year, step=1)
        bornes = regles.plages_dft(int(annee))
        fatal = st.checkbox(f"Fatal (réalisation du {date_fr(bornes['fatal'][0])} "
                            f"au {date_fr(bornes['fatal'][1])})")
        non_fatal = st.checkbox(f"Non fatal (réalisation après le {date_fr(bornes['non_fatal_apres'])})")

        st.markdown("### 3. Clients à lots réservés")
        url = st.text_input("Lien du Google Sheet (SIREN + nom du client)",
                            value=sauves.get("lien_gsheet", ""),
                            placeholder="https://docs.google.com/spreadsheets/d/…",
                            help="Lien de partage (« Tous les utilisateurs disposant du lien ») "
                                 "ou lien « Publier sur le Web ».")
        clients = {}
        fichier_clients = st.file_uploader(
            "… ou déposez la liste des clients (Excel ou CSV)", type=["xlsx", "csv"],
            key="fichier_clients",
            help="À utiliser si le Google Sheet ne peut pas être partagé publiquement. "
                 "Colonnes attendues : « SIREN » et « Nom » (ou « Client »). "
                 "Le fichier déposé est prioritaire sur le lien.")
        if fichier_clients is not None:
            try:
                clients = stockage.lire_clients_fichier(fichier_clients)
                st.caption(f"✅ {len(clients)} client(s) chargé(s) depuis le fichier. "
                           "Leurs dossiers auront des lots réservés.")
            except ValueError as e:
                st.error(str(e))
        elif url.strip():
            try:
                clients = charger_clients(url.strip())
                st.caption(f"✅ {len(clients)} client(s) chargé(s). Leurs dossiers auront des lots réservés.")
            except ValueError as e:
                st.error(str(e))
            c1, c2 = st.columns(2)
            if c1.button("Enregistrer le lien", width="stretch"):
                stockage.enregistrer_parametres({"lien_gsheet": url.strip()})
                st.toast("Lien enregistré ✅")
            if c2.button("Actualiser", width="stretch",
                         help="Relit le Google Sheet (utile si vous venez de le modifier)."):
                charger_clients.clear()
                st.rerun()

        st.markdown("### 4. Part de marché des bureaux (%)")
        bureaux = regles.liste_bureaux(df)
        pct_sauves = sauves.get("pourcentages", {})
        pourcentages = {}
        if not bureaux:
            st.info("Aucun bureau de contrôle trouvé dans la colonne AB du fichier.")
        for b in bureaux:
            pourcentages[b] = st.number_input(
                b, min_value=0, max_value=100, step=1, key=f"pct_{b}",
                value=int(pct_sauves.get(b, round(100 / len(bureaux)))))
        total_pct = sum(pourcentages.values())
        if bureaux:
            if total_pct == 100:
                st.caption("✅ Total : 100 %")
            else:
                st.warning(f"Total : {total_pct} %. Les lots seront répartis au prorata, "
                           "mais vérifiez vos chiffres.")
            if st.button("Enregistrer les pourcentages", width="stretch"):
                stockage.enregistrer_parametres({"pourcentages": {**pct_sauves, **pourcentages}})
                st.toast("Pourcentages enregistrés ✅")


# ============================================================================
# EN-TÊTE ET ONGLETS
# ============================================================================

zone_haut = st.container()
onglet_creation, onglet_ecartes, onglet_historique, onglet_mail = st.tabs(
    ["📦 Création des lots", "🚫 Opérations écartées", "📈 Historique", "📧 Rapport hebdomadaire"])

res = None

# ============================================================================
# 4. ONGLET « CRÉATION DES LOTS »
# ============================================================================

with onglet_creation:
    if fichier is None or df is None:
        if erreur:
            st.error(f"⚠️ {erreur}")
        st.markdown("""
<div class="etape-vide">
<b>Pour commencer :</b><br>
① Déposez l'export ODICEE dans le panneau de gauche (rubrique « 1. Fichier à traiter »).<br>
② Vérifiez les réglages : regroupement, régions, plage de DFT, parts des bureaux.<br>
③ Contrôlez les propositions ci-dessous puis téléchargez les fichiers Excel des lots.<br>
④ Archivez les résultats du jour avec le bouton en haut de page.
</div>""", unsafe_allow_html=True)
    else:
        p = regles.Parametres(
            date_jour=date.today(), mode=mode, dcts_retenues=dcts_choisies,
            fatal=fatal, non_fatal=non_fatal, annee_ref=int(annee),
            clients_specifiques=clients, pourcentages=pourcentages)
        cle_params = json.dumps({"m": mode, "d": dcts_choisies, "f": fatal, "nf": non_fatal,
                                 "a": int(annee), "c": clients, "p": pourcentages,
                                 "j": str(p.date_jour)}, sort_keys=True)

        # Premier calcul : toutes les propositions d'ajout aux lots existants
        base = calculer(df, cle_fichier, cle_params, p, "toutes", None, VERSION_CODE)

        zone_indicateurs = st.container()
        zone_bureaux = st.container()

        # ---------------- Partie 1 : compléter les lots existants ----------------
        props = base["propositions"]
        if "lots_lies" not in props.columns:   # sécurité si un ancien calcul est encore en mémoire
            props = props.assign(lots_lies="RAS")
        existants = base["lots_existants"]
        with st.expander(f"1. Compléter les lots « En cours de constitution » — "
                         f"{len(existants)} lot(s), {fr(props['nb_ops'].sum())} opération(s) proposée(s)"):
            if existants.empty:
                st.info("Aucun lot « En cours de constitution » dans ce fichier : rien à compléter.")
                ajouts = props
            elif props.empty:
                st.info("Aucun dossier ne correspond aux lots en cours de constitution "
                        "(même fiche CEE, même type de lot et place disponible jusqu'à 20 opérations).")
                ajouts = props
            else:
                st.caption("Dossiers proposés : même fiche CEE et même type de lot, les plus urgents "
                           "d'abord, sans dépasser 20 opérations. **Décochez** un dossier pour le "
                           "laisser aux nouveaux lots (partie 3).")
                vue = props.assign(
                    ajouter=True,
                    ecart=props["ecart_ok"].map({True: "✅ Oui", False: "⚠️ Non"}))
                edite = st.data_editor(
                    vue[["ajouter", "lot", "fiche", "type_lot", "bureau", "dossier", "nb_ops",
                         "lots_lies", "dcts", "drr_min", "delai_restant_mois", "ecart"]],
                    key=f"ajouts_{cle_fichier}_{hashlib.md5(cle_params.encode()).hexdigest()}",
                    hide_index=True, width="stretch",
                    disabled=["lot", "fiche", "type_lot", "bureau", "dossier", "dcts", "nb_ops",
                              "drr_min", "delai_restant_mois", "ecart", "lots_lies"],
                    column_config={
                        "ajouter": st.column_config.CheckboxColumn("Ajouter ?", width="small"),
                        "lot": "Lot existant", "fiche": "Fiche CEE", "type_lot": "Type de lot",
                        "bureau": "Bureau", "dossier": "N° dossier", "dcts": "DCTS",
                        "nb_ops": st.column_config.NumberColumn("Opérations", format="%d"),
                        "drr_min": st.column_config.DateColumn("Réalisation", format=FORMAT_DATE),
                        "delai_restant_mois": st.column_config.NumberColumn(
                            "Délai restant (mois)", format="%.1f",
                            help="Mois restants avant la date de réalisation + 12 mois."),
                        "ecart": st.column_config.TextColumn(
                            "Écart ≤ 4 mois", help="Les dates de réalisation du lot restent dans 4 mois."),
                        "lots_lies": st.column_config.TextColumn(
                            "Lots liés", width="medium",
                            help="Autres lots du même bureau contenant ce dossier (une autre fiche). "
                                 "RAS = aucun. ⚠️ = une autre fiche du dossier n'est pas complète "
                                 "(statut AH « Sans objet »). Un dossier n'est jamais proposé à un "
                                 "lot d'un autre bureau que celui de ses autres fiches."),
                    })
                ajouts = props[edite["ajouter"].values]

                bilan = existants.merge(
                    ajouts.groupby("lot")["nb_ops"].sum().rename("ajout").reset_index(),
                    on="lot", how="left").fillna({"ajout": 0})
                bilan["apres"] = bilan["nb_ops"] + bilan["ajout"]
                bilan = bilan.sort_values(["ajout", "lot"], ascending=[False, True])
                st.markdown("**Bilan par lot existant** (lots complétés en premier)")
                st.dataframe(
                    bilan[["lot", "fiche", "type_lot", "bureau", "nb_ops", "ajout", "apres"]],
                    hide_index=True, width="stretch",
                    column_config={
                        "lot": "Lot", "fiche": "Fiche CEE", "type_lot": "Type de lot", "bureau": "Bureau",
                        "nb_ops": st.column_config.NumberColumn("Opérations actuelles", format="%d"),
                        "ajout": st.column_config.NumberColumn("Ajoutées", format="%d"),
                        "apres": st.column_config.ProgressColumn(
                            "Après ajout (sur 20)", min_value=0, max_value=20, format="%d"),
                    })
            zone_export_p1 = st.empty()   # bouton d'export, rempli après le calcul final

        # Calcul final avec les ajouts retenus par l'utilisateur
        cle_ajouts = "|".join(sorted(f"{d}#{f}" for d, f in zip(ajouts["dossier"], ajouts["fiche"])))
        res = calculer(df, cle_fichier, cle_params, p, cle_ajouts, ajouts, VERSION_CODE)
        lots, affect = res["lots"], res["affectations"]
        ajouts = res["ajouts"]   # avec la colonne « Lots liés » recalculée

        if not ajouts.empty:
            zone_export_p1.download_button(
                "⬇️ Télécharger les dossiers à ajouter aux lots existants (Excel)",
                data=exports.excel_ajouts(ajouts, res["lots_existants"], date_export),
                file_name=f"Ajouts_lots_existants_{date_export:%Y-%m-%d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary", width="stretch")

        # ---------------- Indicateurs ----------------
        with zone_indicateurs:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Opérations à intégrer", fr(lots["nb_ops"].sum() + ajouts["nb_ops"].sum()),
                      border=True, help="Nouveaux lots + ajouts aux lots existants.")
            c2.metric("Nouveaux lots à créer", fr(len(lots)), border=True)
            c3.metric("Ajoutées aux lots existants", fr(ajouts["nb_ops"].sum()), border=True,
                      help="Nombre d'opérations ajoutées aux lots « En cours de constitution ».")
            c4.metric("Opérations écartées", fr(res["ecartes"]["nb_ops"].sum()), border=True,
                      help="Détail et motifs dans l'onglet « Opérations écartées ».")

        # ---------------- Répartition par bureau ----------------
        with zone_bureaux:
            st.markdown("#### Répartition des lots par bureau de contrôle")
            rep = regles.repartition_bureaux(res, pourcentages)
            if rep.empty:
                st.info("Aucun bureau de contrôle dans le fichier : la répartition ne peut pas être calculée.")
            else:
                g, d = st.container(), st.container()
                with g:
                    # Histogramme groupé : lots en cours / lots à créer ; objectif écrit au-dessus
                    fig = go.Figure()
                    for col, nom, coul in [("Lots en cours", "Lots en cours", COULEURS["actuel"]),
                                           ("Nouveaux lots", "Lots à créer", COULEURS["apres"])]:
                        fig.add_bar(x=rep["Bureau"], y=rep[col], name=nom, marker_color=coul,
                                    text=rep[col], textposition="outside",
                                    hovertemplate="%{x} — " + nom + " : %{y} lot(s)<extra></extra>")
                    haut = max(rep[["Lots en cours", "Nouveaux lots"]].values.max(), 1)
                    for _, r in rep.iterrows():
                        fig.add_annotation(
                            x=r["Bureau"], y=max(r["Lots en cours"], r["Nouveaux lots"]) + haut * 0.12,
                            text=f"<b>Objectif {r['% objectif']:.0f} %</b><br>"
                                 f"après création : {r['% après création']:.0f} %",
                            showarrow=False, yanchor="bottom", font=dict(size=13, color=COULEURS["objectif"]))
                    fig.update_layout(
                        barmode="group", bargap=0.3, height=440, margin=dict(l=0, r=0, t=10, b=0),
                        legend=dict(orientation="h", y=-0.15), yaxis_title="Nombre de lots",
                        font=dict(size=14, color="#1A202C"), plot_bgcolor="white",
                        xaxis=dict(tickfont=dict(size=14, color="#1A202C")),
                        yaxis=dict(gridcolor="#E5E9F0", range=[0, haut * 1.45]))
                    st.plotly_chart(fig, width="stretch")
                    st.caption("Lots en cours = lots uniques aux étapes « En cours de constitution », "
                               "« En demande de coordonnées » et « En cours de contrôle ». "
                               "Au-dessus de chaque bureau : la part de marché saisie (objectif) et la "
                               "part réelle une fois les nouveaux lots créés.")
                with d:
                    pb = res["par_bureau"].reindex(res["bureaux"], fill_value=0)
                    pb["Nouveaux lots"] = rep.set_index("Bureau")["Nouveaux lots"]
                    pb = pb.rename(columns={
                        "En cours de constitution": "Constitution",
                        "En demande de coordonnées": "Coordonnées",
                        "En cours de contrôle": "Contrôle",
                        "Total en cours": "Total", "Nouveaux lots": "Nouveaux"})
                    pb.index.name = "Bureau"
                    with st.expander("Détail des lots en cours par étape"):
                        st.dataframe(pb, width="stretch")

        # ---------------- Partie 2 : opérations intégrables ----------------
        with st.expander(f"2. Opérations intégrables par fiche CEE et par DCTS — "
                         f"{fr(res['restants']['nb_ops'].sum())} opération(s)"):
            st.caption("Hors dossiers ajoutés aux lots existants (partie 1).")
            tableau = regles.tableau_fiche_dcts(res["restants"])
            if tableau.empty:
                st.info("Aucune opération intégrable avec les réglages actuels. "
                        "Consultez l'onglet « Opérations écartées » pour comprendre pourquoi.")
            else:
                st.dataframe(tableau, width="stretch")

        # ---------------- Partie 3 : nouveaux lots ----------------
        st.markdown("### 3. Nouveaux lots à créer")
        st.caption(f"Regroupement : **{mode_libelle}**. Un lot = une fiche CEE et un type de lot. "
                   "Objectif : environ 20 opérations, dossiers les plus urgents d'abord, "
                   "écart de 4 mois maximum entre dates de réalisation si possible.")
        if lots.empty:
            st.info("Aucun nouveau lot à créer avec les réglages actuels. Essayez d'élargir les "
                    "régions ou de décocher la plage de DFT, ou consultez l'onglet « Opérations écartées ».")
        else:
            fiches = sorted(lots["fiche"].unique())
            filtre = st.multiselect("Afficher les fiches CEE", fiches, default=fiches,
                                    placeholder="Toutes les fiches")
            vue = lots[lots["fiche"].isin(filtre or fiches)].copy()
            vue["ecart_txt"] = [("✅ " if ok else "⚠️ ") + f"{e:.1f} mois".replace(".", ",")
                                for ok, e in zip(vue["ecart_ok"], vue["ecart_mois"])]
            st.dataframe(
                vue[["num_lot", "fiche", "type_lot", "nb_ops", "bureau", "lots_lies", "raison_bureau",
                     "nb_dossiers", "dcts", "client", "drr_min", "drr_max", "ecart_txt"]],
                hide_index=True, width="stretch", height=min(38 * (len(vue) + 1), 520),
                column_config={
                    "num_lot": st.column_config.NumberColumn("Lot", format="%d", width="small"),
                    "fiche": "Fiche CEE", "type_lot": "Type de lot", "dcts": "DCTS",
                    "client": "Client réservé", "bureau": "Bureau de contrôle",
                    "nb_dossiers": st.column_config.NumberColumn("Dossiers", format="%d"),
                    "nb_ops": st.column_config.ProgressColumn("Opérations (cible 20)", min_value=0,
                                                              max_value=20, format="%d"),
                    "drr_min": st.column_config.DateColumn("Réalisation du", format=FORMAT_DATE),
                    "drr_max": st.column_config.DateColumn("au", format=FORMAT_DATE),
                    "ecart_txt": st.column_config.TextColumn(
                        "Écart des dates", help="✅ = 4 mois ou moins ; ⚠️ = plus de 4 mois (non bloquant)."),
                    "raison_bureau": "Choix du bureau",
                    "lots_lies": st.column_config.TextColumn(
                        "Lots liés", width="large",
                        help="Lots du même bureau contenant une autre fiche d'un des dossiers. "
                             "RAS = bureau choisi selon les pourcentages. ⚠️ = une autre fiche du "
                             "dossier n'est pas complète (statut AH « Sans objet »)."),
                })

            with st.expander("🔎 Voir les dossiers d'un lot"):
                num = st.selectbox("Lot", vue["num_lot"].tolist(),
                                   format_func=lambda n: f"Lot {n} — " + " · ".join(
                                       lots.loc[lots["num_lot"] == n, ["fiche", "type_lot", "bureau"]]
                                       .iloc[0].astype(str)))
                st.dataframe(
                    affect[affect["num_lot"] == num][["dossier", "nb_ops", "dcts", "organisme",
                                                      "drr_min", "drr_max", "delai_restant_mois"]],
                    hide_index=True, width="stretch",
                    column_config={
                        "dossier": "N° dossier", "nb_ops": "Opérations", "dcts": "DCTS",
                        "organisme": "Bénéficiaire",
                        "drr_min": st.column_config.DateColumn("Réalisation du", format=FORMAT_DATE),
                        "drr_max": st.column_config.DateColumn("au", format=FORMAT_DATE),
                        "delai_restant_mois": st.column_config.NumberColumn("Délai restant (mois)",
                                                                            format="%.1f"),
                    })

            st.download_button(
                "⬇️ Télécharger les lots (ZIP : un fichier Excel par fiche CEE)",
                data=exports.zip_lots(lots, affect, ajouts, date_export),
                file_name=f"Lots_de_controle_{date_export:%Y-%m-%d}.zip",
                mime="application/zip", type="primary", width="stretch")
            st.caption("Chaque fichier contient un onglet « Synthèse » puis un onglet par lot "
                       "(numéros de dossiers et nombre d'opérations). Les ajouts aux lots existants "
                       "sont dans un fichier séparé.")


# ============================================================================
# 5. BOUTON D'ARCHIVAGE (EN HAUT DE PAGE)
# ============================================================================

@st.dialog("Archiver les résultats du jour", width="large")
def confirmer_archive(archive: dict):
    s = archive["synthese"]
    ou = ("dans le **Google Sheet d'archive**" if stockage.mode_stockage() == "google"
          else "**temporairement** (Google Sheet non configuré)")
    st.markdown(f"Vous allez archiver l'export du **{date_fr(s['date_export'])}** {ou}.")
    g, d = st.columns(2)
    g.markdown(f"""
**Création des lots**
- **{fr(s['nb_operations_a_integrer'])}** opérations à intégrer
- **{fr(s['nb_lots_a_creer'])}** nouveaux lots à créer
- **{fr(s['nb_operations_ajoutees_lots_existants'])}** opérations ajoutées aux lots existants
- détail pour **{archive['detail']['beneficiaire'].nunique()}** bénéficiaire(s)
""")
    d.markdown(f"""
**Suivi du contrôle**
- **{fr(s['lots_en_constitution'])}** lots en constitution
- **{fr(s['lots_demande_coordonnees'])}** lots en demande de coordonnées
- **{fr(s['lots_en_controle'])}** lots en cours de contrôle ({fr(s['ops_en_controle'])} opé.),
  dont **{fr(s['lots_controle_plus_5_sem'])}** depuis plus de 5 semaines ({fr(s['ops_controle_plus_5_sem'])} opé.)
- **{fr(s['lots_restitues'])}** lots restitués
- **{fr(s['ops_eligibles'])}** opérations éligibles, dont **{fr(s['ops_integrables_delai_2_mois'])}** intégrables (délai > 2 mois)

**Flux {periode_flux(s)[1]}**
- 🆕 {fr(s[periode_flux(s)[0] + 'lots_creation'])} lots créés ({fr(s[periode_flux(s)[0] + 'ops_creation'])} opé.)
- 📤 {fr(s[periode_flux(s)[0] + 'lots_envoi'])} lots envoyés ({fr(s[periode_flux(s)[0] + 'ops_envoi'])} opé.)
- 📥 {fr(s[periode_flux(s)[0] + 'lots_retour'])} lots restitués ({fr(s[periode_flux(s)[0] + 'ops_retour'])} opé.)
""")
    st.caption("Une seule archive est possible par date d'export : vérifiez vos réglages avant de confirmer.")
    c1, c2 = st.columns(2)
    if c1.button("Confirmer l'archivage", type="primary", width="stretch"):
        try:
            with st.spinner("Enregistrement…"):
                ok, avertissement = stockage.archiver(archive)
        except Exception as e:
            st.error(f"L'archivage a échoué : {e}. Réessayez dans un instant ; si le problème "
                     "continue, téléchargez l'archive depuis l'onglet Historique.")
            return
        if ok:
            st.session_state["message_archive"] = (
                f"Résultats du {date_fr(s['date_export'])} archivés ✅"
                + (f" — {avertissement}" if avertissement else ""))
        else:
            st.session_state["message_archive"] = "Cet export avait déjà été archivé : rien n'a été modifié."
        st.rerun()
    if c2.button("Annuler", width="stretch"):
        st.rerun()


with zone_haut:
    titre, bouton = st.columns([3, 1.3], vertical_alignment="center")
    titre.title("Création des lots de contrôle")
    if df is not None and res is not None:
        date_iso = date_export.isoformat()
        if stockage.date_deja_archivee(date_iso) and stockage.archive_incomplete(date_iso):
            # Export archivé avec une version précédente : on peut ajouter les indicateurs
            # de suivi sans modifier les chiffres déjà archivés.
            if bouton.button(f"➕ Compléter l'archive du {date_fr(date_export)}",
                             type="primary", width="stretch",
                             help="Cet export a été archivé avant l'ajout des indicateurs de suivi "
                                  "ou du flux d'activité. "
                                  "Ce bouton les ajoute (ainsi que les graphiques) sans modifier "
                                  "les chiffres de création des lots déjà archivés."):
                archive_jour = regles.resume_archive(df, res, date_export, date.today(),
                                                     stockage.date_precedente(date_iso))
                try:
                    with st.spinner("Ajout des indicateurs de suivi…"):
                        ok, avertissement = stockage.completer_archive(archive_jour)
                    st.session_state["message_archive"] = (
                        f"Archive du {date_fr(date_export)} complétée (indicateurs de suivi et flux) ✅"
                        + (f" — {avertissement}" if avertissement else ""))
                except Exception as e:
                    st.session_state["message_archive"] = f"⚠️ L'archive n'a pas pu être complétée : {e}"
                st.rerun()
        elif stockage.date_deja_archivee(date_iso):
            bouton.button(f"✅ Export du {date_fr(date_export)} déjà archivé", disabled=True,
                          width="stretch",
                          help="Une seule archive est possible par date d'export.")
        else:
            archive_jour = regles.resume_archive(df, res, date_export, date.today(),
                                                     stockage.date_precedente(date_iso))
            if bouton.button(f"🗄️ Archiver les résultats du {date_fr(date_export)}",
                             width="stretch",
                             help="Enregistre la création des lots, les indicateurs de suivi du "
                                  "contrôle et le détail par bénéficiaire."):
                confirmer_archive(archive_jour)
    if "message_archive" in st.session_state:
        st.success(st.session_state.pop("message_archive"))


# ============================================================================
# 6. ONGLET « OPÉRATIONS ÉCARTÉES »
# ============================================================================

with onglet_ecartes:
    if res is None:
        st.info("Chargez un export pour voir les opérations écartées et leur motif.")
    elif res["ecartes"].empty:
        st.success("Aucune opération éligible n'a été écartée. 🎉")
    else:
        ec = res["ecartes"]
        st.markdown("Opérations **Eligible** sans lot (NEANT) qui ne peuvent pas être intégrées, "
                    "avec la raison.")
        par_motif = ec.groupby("motif")["nb_ops"].sum().sort_values()
        fig = go.Figure(go.Bar(x=par_motif.values, y=par_motif.index, orientation="h",
                               marker_color="#B45309", text=[fr(v) for v in par_motif.values],
                               textposition="outside"))
        fig.update_layout(height=80 + 45 * len(par_motif), margin=dict(l=0, r=40, t=10, b=0),
                          xaxis_title="Nombre d'opérations", font=dict(size=14), plot_bgcolor="white")
        st.plotly_chart(fig, width="stretch")
        motifs = st.multiselect("Filtrer par motif", sorted(ec["motif"].unique()),
                                placeholder="Tous les motifs")
        vue_ec = ec[ec["motif"].isin(motifs)] if motifs else ec
        st.dataframe(vue_ec, hide_index=True,
                     width="stretch",
                     column_config={"dossier": "N° dossier", "fiche": "Fiche CEE", "dcts": "DCTS",
                                    "motif": "Motif", "nb_ops": st.column_config.NumberColumn(
                                        "Opérations", format="%d")})
        export_ec = vue_ec.rename(columns={"dossier": "N° dossier", "fiche": "Fiche CEE",
                                           "dcts": "DCTS", "motif": "Motif", "nb_ops": "Opérations"})
        export_ec.insert(0, "Date de l'export", date_fr(date_export))
        st.download_button(
            "⬇️ Télécharger les opérations écartées (Excel)" + (" — motifs filtrés" if motifs else ""),
            data=exports.excel_tableau({"Opérations écartées": export_ec}),
            file_name=f"Operations_ecartees_{date_export:%Y-%m-%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", width="stretch")


# ============================================================================
# 7. ONGLET « HISTORIQUE »
# ============================================================================

LIBELLES = {t: dict(cols) for t, (_, cols) in stockage.TABLES.items()}


def valeur(ligne, colonne) -> str:
    """Nombre formaté, ou « — » si l'indicateur n'existait pas encore lors de l'archivage."""
    v = ligne.get(colonne)
    return "—" if v is None or pd.isna(v) else fr(v)


def couper(texte: str, largeur: int = 14) -> str:
    """Coupe un libellé long sur plusieurs lignes (axes des graphiques)."""
    lignes, courante = [], ""
    for mot in str(texte).split():
        if courante and len(courante) + 1 + len(mot) > largeur:
            lignes.append(courante)
            courante = mot
        else:
            courante = f"{courante} {mot}".strip()
    return "<br>".join(lignes + [courante])


PALETTE_ETAPES = ["#1F5FAD", "#6A9BD8", "#E08A00", "#2E7D32", "#8A9BB0", "#7C3AED", "#0E7490", "#B45309"]


# Styles du schéma de flux (chaîne simple : pas d'accolades à doubler)
CSS_FLUX = """
<style>
.fx-zone { container-type: inline-size; }
.fx-carte { background: linear-gradient(135deg, #F8FAFD 0%, #EEF3FA 100%); border: 1px solid #E2E8F0;
  border-radius: 20px; padding: 22px 18px 18px; margin-bottom: 8px; }
.fx-entete { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap;
  gap: 6px; margin: 0 6px 14px; }
.fx-entete-titre { font-size: 15px; font-weight: 700; color: #1A202C; letter-spacing: .2px; }
.fx-entete-jour { font-size: 14px; color: #4A5568; }
.fx-schema { display: flex; align-items: flex-start; justify-content: center; }

/* Étapes (cercles) */
.fx-etape { flex: 0 0 172px; display: flex; flex-direction: column; align-items: center; text-align: center; }
.fx-rond { width: 164px; height: 164px; border-radius: 50%; display: flex; flex-direction: column;
  align-items: center; justify-content: center; position: relative;
  background: radial-gradient(circle at 30% 25%, #FFFFFF 0%, var(--fond) 75%);
  border: 6px solid var(--c); box-shadow: 0 10px 28px var(--ombre), inset 0 0 0 6px #FFFFFF; }
.fx-icone { font-size: 24px; line-height: 1; margin-bottom: 2px; }
.fx-grand { font-size: 46px; font-weight: 800; color: #1A202C; line-height: 1; letter-spacing: -1px; }
.fx-unite { font-size: 14px; color: #4A5568; margin-top: 2px; }
.fx-pastille { margin-top: 6px; font-size: 13px; font-weight: 700; color: var(--c); background: #FFFFFF;
  border: 1px solid var(--c); border-radius: 999px; padding: 2px 10px; }
.fx-nom { margin-top: 12px; font-size: 16px; font-weight: 700; color: var(--texte); }
.fx-detail { font-size: 13px; color: #4A5568; line-height: 1.3; max-width: 170px; }
.fx-alerte { margin-top: 6px; font-size: 13px; font-weight: 700; color: #9A3412; background: #FFF4E5;
  border: 1px solid #F6C48A; border-radius: 999px; padding: 2px 10px; }

/* Flux (flèches animées) */
.fx-flux { flex: 1 1 120px; min-width: 112px; max-width: 150px; height: 164px; position: relative;
  display: flex; align-items: center; justify-content: center; }
.fx-badge { position: absolute; bottom: calc(50% + 16px); left: 50%; transform: translateX(-50%);
  background: #FFFFFF; border-radius: 12px; padding: 6px 10px; text-align: center; white-space: nowrap;
  box-shadow: 0 4px 14px rgba(26, 32, 44, .10); border: 1px solid #E2E8F0; }
.fx-badge-titre { font-size: 12px; font-weight: 600; color: #4A5568; text-transform: uppercase;
  letter-spacing: .4px; }
.fx-badge-chiffre { font-size: 20px; font-weight: 800; color: #1A202C; line-height: 1.15; }
.fx-badge-ops { font-size: 13px; color: #4A5568; }
.fx-trait { stroke-dasharray: 7 6; animation: fx-defile 0.9s linear infinite; }
@keyframes fx-defile { to { stroke-dashoffset: -26; } }
@media (prefers-reduced-motion: reduce) { .fx-trait { animation: none; } }
.fx-v { display: none; }

/* Zone étroite (tablette, mobile) : schéma de haut en bas */
@container (max-width: 960px) {
  .fx-schema { flex-direction: column; align-items: center; }
  .fx-flux { flex: 0 0 auto; height: auto; max-width: none; flex-direction: column; padding: 10px 0; }
  .fx-badge { position: static; transform: none; margin-bottom: 6px; }
  .fx-h { display: none; }
  .fx-v { display: block; }
}
</style>
"""

ETAPES_SCHEMA = {  # couleur principale, fond du cercle, ombre, couleur du titre
    "creation": ("#1F5FAD", "#E6EEFA", "rgba(31, 95, 173, .22)", "#1A4C8B"),
    "controle": ("#D97706", "#FDF0DC", "rgba(217, 119, 6, .22)", "#92400E"),
    "traiter": ("#2E7D32", "#E3F2E4", "rgba(46, 125, 50, .22)", "#1B5E20"),
}


def schema_flux(ligne: dict, jour_ref: str) -> str:
    """
    Schéma du flux du contrôle (HTML) : 3 cercles = stocks de lots, flèches animées = flux
    du jour ouvré de référence. De gauche à droite sur ordinateur, de haut en bas sur mobile.
      Lots créés → [Lots en création] → Lots envoyés → [En cours de contrôle]
                 → Lots restitués → [Lots à traiter]
    """
    prefixe, periode = periode_flux(ligne)

    def nb(col):
        return valeur(ligne, col)

    def fleche(n, titre, icone, cle, de, vers):
        """Badge (lots + opérations du jour) posé sur une flèche dégradée et animée."""
        c1, c2 = ETAPES_SCHEMA[de][0] if de else "#94A3B8", ETAPES_SCHEMA[vers][0]
        return (
            f'<div class="fx-flux">'
            f'<div class="fx-badge"><div class="fx-badge-titre">{icone} {titre}</div>'
            f'<div class="fx-badge-chiffre">{nb(f"{prefixe}lots_{cle}")} lots</div>'
            f'<div class="fx-badge-ops">{nb(f"{prefixe}ops_{cle}")} opé.</div></div>'
            # flèche horizontale
            f'<svg class="fx-h" width="100%" height="28" viewBox="0 0 130 28" preserveAspectRatio="none" aria-hidden="true">'
            f'<defs><linearGradient id="fxg{n}" gradientUnits="userSpaceOnUse" x1="4" y1="14" x2="112" y2="14">'
            f'<stop offset="0" stop-color="{c1}"/>'
            f'<stop offset="1" stop-color="{c2}"/></linearGradient></defs>'
            f'<line class="fx-trait" x1="4" y1="14" x2="112" y2="14" stroke="url(#fxg{n})" stroke-width="5" stroke-linecap="round"/>'
            f'<path d="M110 4 L127 14 L110 24 Z" fill="{c2}"/></svg>'
            # flèche verticale (mobile)
            f'<svg class="fx-v" width="28" height="64" viewBox="0 0 28 64" aria-hidden="true">'
            f'<defs><linearGradient id="fxv{n}" gradientUnits="userSpaceOnUse" x1="14" y1="2" x2="14" y2="48">'
            f'<stop offset="0" stop-color="{c1}"/>'
            f'<stop offset="1" stop-color="{c2}"/></linearGradient></defs>'
            f'<line class="fx-trait" x1="14" y1="2" x2="14" y2="48" stroke="url(#fxv{n})" stroke-width="5" stroke-linecap="round"/>'
            f'<path d="M4 46 L14 62 L24 46 Z" fill="{c2}"/></svg>'
            f'</div>')

    def cercle(cle, icone, nom, lots, ops, detail, extra=""):
        c, fond, ombre, texte = ETAPES_SCHEMA[cle]
        return (
            f'<div class="fx-etape" style="--c:{c};--fond:{fond};--ombre:{ombre};--texte:{texte}">'
            f'<div class="fx-rond"><div class="fx-icone">{icone}</div>'
            f'<div class="fx-grand">{lots}</div><div class="fx-unite">lots</div>'
            f'<div class="fx-pastille">{ops} opé.</div></div>'
            f'<div class="fx-nom">{nom}</div><div class="fx-detail">{detail}</div>{extra}</div>')

    lots_creation = "—"
    if not pd.isna(ligne.get("lots_en_constitution")) and not pd.isna(ligne.get("lots_demande_coordonnees")):
        lots_creation = fr(ligne["lots_en_constitution"] + ligne["lots_demande_coordonnees"])
    retard = ("" if pd.isna(ligne.get("lots_controle_plus_5_sem")) else
              f'<div class="fx-alerte">⏱️ {nb("lots_controle_plus_5_sem")} lots &gt; 5 semaines</div>')

    return CSS_FLUX.replace("\n", "") + (
        f'<div class="fx-zone"><div class="fx-carte" role="group" aria-label="Schéma du flux du contrôle">'
        f'<div class="fx-entete"><span class="fx-entete-titre">Stock de lots et activité</span>'
        f'<span class="fx-entete-jour">Flèches : activité <b>{periode}</b></span></div>'
        f'<div class="fx-schema">'
        + fleche(1, "Créés", "🆕", "creation", None, "creation")
        + cercle("creation", "🧩", "Lots en création", lots_creation, nb("ops_en_creation"),
                 "Constitution + demande de coordonnées")
        + fleche(2, "Envoyés", "📤", "envoi", "creation", "controle")
        + cercle("controle", "🔍", "En cours de contrôle", nb("lots_en_controle"), nb("ops_en_controle"),
                 "Chez les bureaux de contrôle", retard)
        + fleche(3, "Restitués", "📥", "retour", "controle", "traiter")
        + cercle("traiter", "✅", "Lots à traiter", nb("lots_restitues"), nb("ops_restitues"),
                 "Étape « Lot restitué »")
        + '</div></div></div>')


def graphique_etapes(etapes: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Pie(labels=etapes["etape"], values=etapes["nb_lots"], hole=0.35,
                           marker=dict(colors=PALETTE_ETAPES, line=dict(color="white", width=2)),
                           textinfo="percent+value", sort=False,
                           hovertemplate="%{label} : %{value} lots (%{percent})<extra></extra>"))
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), font=dict(size=14),
                      legend=dict(orientation="v"))
    return fig


def graphique_bureaux(bureaux: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    noms = [couper(b) for b in bureaux["bureau"]]
    for col, nom, coul in [("lots_delai_ok", "Délai respecté (≤ 5 semaines)", COULEURS["apres"]),
                           ("lots_plus_5_sem", "Plus de 5 semaines", "#C2410C")]:
        fig.add_bar(x=noms, y=bureaux[col], name=nom, marker_color=coul,
                    text=[v if v else "" for v in bureaux[col]], textposition="inside",
                    hovertemplate="%{x} — " + nom + " : %{y} lot(s)<extra></extra>")
    totaux = bureaux["lots_delai_ok"].fillna(0) + bureaux["lots_plus_5_sem"].fillna(0)
    fig.add_scatter(x=noms, y=totaux, mode="text", text=[f"<b>{int(t)}</b>" for t in totaux],
                    textposition="top center", showlegend=False, hoverinfo="skip")
    fig.update_layout(barmode="stack", height=380, margin=dict(l=0, r=0, t=20, b=0),
                      font=dict(size=14, color="#1A202C"), plot_bgcolor="white",
                      legend=dict(orientation="h", y=-0.25), yaxis_title="Lots en cours de contrôle",
                      xaxis=dict(tickangle=0, tickfont=dict(size=12, color="#1A202C")),
                      yaxis=dict(gridcolor="#E5E9F0", range=[0, max(totaux.max(), 1) * 1.25]))
    return fig


with onglet_historique:
    try:
        archive_h = stockage.charger_archive()
    except Exception as e:
        st.error(f"L'archive n'a pas pu être lue : {e}. Réessayez dans un instant.")
        archive_h = {t: pd.DataFrame(columns=list(LIBELLES[t])) for t in stockage.TABLES}
    synthese_h = archive_h["synthese"].sort_values("date_export").reset_index(drop=True)

    if stockage.mode_stockage() == "google":
        st.caption(f"💾 Archive enregistrée dans le [Google Sheet d'archive]({stockage.infos_stockage()['url']}) "
                   "(onglet « Graphiques » mis à jour à chaque archivage).")
    else:
        st.warning("Archive temporaire : elle peut être effacée au redémarrage de l'application. "
                   "Configurez le Google Sheet (README.md) ou téléchargez l'archive après chaque archivage.")

    if synthese_h.empty:
        st.info("Aucune archive pour le moment. Après avoir créé vos lots, cliquez sur "
                "« Archiver les résultats » en haut de page. Vous pouvez aussi réimporter "
                "une archive sauvegardée ci-dessous.")
    else:
        dates = synthese_h["date_export"].tolist()[::-1]   # plus récente d'abord

        # ---------------- Situation d'un export ----------------
        st.markdown("#### Situation d'un export archivé")
        choix_date = st.selectbox("Export", dates, format_func=lambda d: f"Export du {date_fr(d)}",
                                  key=f"date_situation_{dates[0]}")  # revient au plus récent après un archivage
        ligne = synthese_h[synthese_h["date_export"] == choix_date].iloc[0].to_dict()

        st.markdown("**Création des lots**")
        c = st.columns(4)
        c[0].metric("Archives enregistrées", fr(len(synthese_h)), border=True)
        c[1].metric("Opérations intégrées", valeur(ligne, "nb_operations_a_integrer"), border=True)
        c[2].metric("Lots créés", valeur(ligne, "nb_lots_a_creer"), border=True)
        c[3].metric("Ajoutées aux lots existants", valeur(ligne, "nb_operations_ajoutees_lots_existants"),
                    border=True)

        st.markdown("**Suivi du contrôle**")
        if pd.isna(ligne.get("lots_en_constitution")):
            st.info(f"ℹ️ L'export du {date_fr(choix_date)} a été archivé avant l'ajout des indicateurs "
                    "de suivi, d'où les « — ». Pour les ajouter : rechargez ce même export "
                    f"(Export_Controle_lots_de_travaux_BS_{choix_date}.xlsx) dans le panneau de gauche, "
                    "puis cliquez sur « ➕ Compléter l'archive » en haut de page. Les chiffres déjà "
                    "archivés ne seront pas modifiés.")
        c = st.columns(4)
        c[0].metric("Lots en constitution", valeur(ligne, "lots_en_constitution"), border=True)
        c[1].metric("Lots en demande de coordonnées", valeur(ligne, "lots_demande_coordonnees"), border=True)
        c[2].metric("Lots en cours de contrôle", valeur(ligne, "lots_en_controle"), border=True,
                    help="Dont lots envoyés depuis plus de 5 semaines (date d'envoi du lot → date d'archivage).")
        c[3].metric("Lots restitués", valeur(ligne, "lots_restitues"), border=True)
        c = st.columns(4)
        c[0].metric("Lots en contrôle > 5 semaines", valeur(ligne, "lots_controle_plus_5_sem"), border=True)
        c[1].metric("Opé. en contrôle", valeur(ligne, "ops_en_controle"), border=True,
                    help="Nombre de lignes (N° dossier) à l'étape « En cours de contrôle ».")
        c[2].metric("Opé. en contrôle > 5 semaines", valeur(ligne, "ops_controle_plus_5_sem"), border=True)
        c[3].metric("Opé. éligibles / intégrables",
                    f"{valeur(ligne, 'ops_eligibles')} / {valeur(ligne, 'ops_integrables_delai_2_mois')}",
                    border=True,
                    help="Intégrables = date de réalisation (ou prévisionnelle) + 12 mois au-delà "
                         "d'aujourd'hui + 2 mois.")

        etapes = archive_h["etapes"][archive_h["etapes"]["date_export"] == choix_date]
        bureaux_h = archive_h["bureaux"][archive_h["bureaux"]["date_export"] == choix_date]
        g, d = st.columns(2)
        with g:
            st.markdown("**Répartition des lots par étape de contrôle**")
            if etapes.empty:
                st.info("Pas de données pour cet export (archivé avec une version précédente).")
            else:
                st.plotly_chart(graphique_etapes(etapes), width="stretch")
        with d:
            st.markdown("**Lots en cours de contrôle par bureau**")
            if bureaux_h.empty:
                st.info("Pas de données pour cet export (archivé avec une version précédente).")
            else:
                st.plotly_chart(graphique_bureaux(bureaux_h), width="stretch")

        # ---------------- Flux du contrôle (schéma) ----------------
        st.markdown("#### Flux du contrôle")
        jour_ref = ligne.get("date_reference_flux") or ""
        if pd.isna(ligne.get("flux_lots_creation")) or pd.isna(ligne.get("ops_en_creation")):
            st.info(f"ℹ️ Le flux n'est pas complet pour l'export du {date_fr(choix_date)}. Rechargez cet "
                    "export puis cliquez sur « ➕ Compléter l'archive » en haut de page.")
        st.markdown(schema_flux(ligne, jour_ref), unsafe_allow_html=True)
        prefixe_choisi, libelle_periode = periode_flux(ligne)
        st.caption(f"Cercles : stock de lots dans l'export du {date_fr(choix_date)}. Flèches : lots "
                   f"créés, envoyés et restitués {libelle_periode}. Opé. = nombre de lignes "
                   "(opérations) de ces lots."
                   + ("" if prefixe_choisi == "flux_periode_" else
                      " Dès qu'un export précédent est archivé, les flèches couvrent toute la période "
                      "entre les deux exports."))

        # Graphique : les 20 derniers jours ouvrés (depuis l'export chargé, sinon depuis l'archive)
        unite = st.radio("Afficher le flux en", ["Lots", "Opérations"], horizontal=True,
                         key="unite_flux")
        prefixe = "lots" if unite == "Lots" else "ops"
        export_choisi_charge = (df is not None and date_export is not None
                                and date_export.isoformat() == choix_date)
        if export_choisi_charge and prefixe_choisi == "flux_periode_":
            # Même période que les flèches du schéma : de l'export précédent à celui-ci, inclus
            flux_t = regles.flux_jours_periode(df, ligne["periode_debut"], ligne["periode_fin"])
            source_flux = (f"Jour par jour du {date_fr(ligne['periode_debut'])} au "
                           f"{date_fr(ligne['periode_fin'])} (dates des deux exports incluses).")
        elif df is not None and date_export is not None:
            flux_t = regles.flux_par_jour(df, date_export, 20)
            source_flux = (f"Calculé à partir de l'export chargé (du {date_fr(date_export)}) : "
                           "20 derniers jours ouvrés.")
        else:
            arch_flux = synthese_h[synthese_h["flux_lots_creation"].notna()].copy()
            flux_t = pd.DataFrame({"jour": pd.to_datetime(arch_flux["date_reference_flux"])})
            for cle in ("creation", "envoi", "retour"):
                flux_t[f"lots_{cle}"] = arch_flux[f"flux_lots_{cle}"].values
                flux_t[f"ops_{cle}"] = arch_flux[f"flux_ops_{cle}"].values
            source_flux = ("Calculé à partir des exports archivés (un jour par archive). Chargez un "
                           "export pour voir les 20 derniers jours ouvrés.")
        if flux_t.empty:
            st.info("Pas encore de flux à afficher.")
        else:
            fig = go.Figure()
            for cle, nom, coul in [("creation", "Lots créés", COULEURS["apres"]),
                                   ("envoi", "Lots envoyés", COULEURS["objectif"]),
                                   ("retour", "Lots restitués", "#2E7D32")]:
                fig.add_bar(
                    x=[f"{JOURS_FR[j.weekday()]} {j:%d/%m}" for j in flux_t["jour"]],
                    y=flux_t[f"{prefixe}_{cle}"],
                    name=nom, marker_color=coul,
                    customdata=flux_t[[f"lots_{cle}", f"ops_{cle}"]].values,
                    hovertemplate="%{x} — " + nom + " : %{customdata[0]} lots, "
                                  "%{customdata[1]} opérations<extra></extra>")
            fig.update_layout(barmode="group", height=380, margin=dict(l=0, r=0, t=10, b=0),
                              font=dict(size=14, color="#1A202C"), plot_bgcolor="white",
                              legend=dict(orientation="h", y=-0.25),
                              yaxis=dict(title=f"Nombre de {unite.lower()}", gridcolor="#E5E9F0"),
                              xaxis=dict(type="category", tickangle=-45))
            st.plotly_chart(fig, width="stretch")
            totaux = {cle: int(flux_t[f"{prefixe}_{cle}"].sum()) for cle in ("creation", "envoi", "retour")}
            st.caption(f"{source_flux} Sur la période : {fr(totaux['creation'])} créés, "
                       f"{fr(totaux['envoi'])} envoyés, {fr(totaux['retour'])} restitués "
                       f"({unite.lower()}).")

        # ---------------- Évolution ----------------
        st.markdown("#### Évolution d'un export à l'autre")
        etiquettes = pd.to_datetime(synthese_h["date_export"]).dt.strftime("%d/%m/%Y")
        fig = go.Figure()
        fig.add_bar(x=etiquettes, y=synthese_h["nb_operations_a_integrer"],
                    name="Opérations à intégrer", marker_color=COULEURS["apres"],
                    text=[valeur({"v": v}, "v") for v in synthese_h["nb_operations_a_integrer"]],
                    textposition="outside")
        fig.add_scatter(x=etiquettes, y=synthese_h["nb_lots_a_creer"], name="Lots à créer",
                        yaxis="y2", mode="lines+markers", line=dict(color=COULEURS["objectif"], width=3),
                        marker=dict(size=10), hovertemplate="%{x} : %{y} lots<extra></extra>")
        fig.update_layout(height=340, margin=dict(l=0, r=0, t=30, b=0), font=dict(size=14),
                          legend=dict(orientation="h", y=-0.2), plot_bgcolor="white",
                          xaxis=dict(type="category"),
                          yaxis=dict(title="Opérations", gridcolor="#E5E9F0", rangemode="tozero"),
                          yaxis2=dict(title="Lots", overlaying="y", side="right", showgrid=False,
                                      range=[0, float(synthese_h["nb_lots_a_creer"].max() or 0) * 1.6 + 1]))
        if len(synthese_h) < 6:
            fig.update_traces(width=0.35, selector=dict(type="bar"))
        st.plotly_chart(fig, width="stretch")

        vue_synthese = synthese_h.assign(date_export=etiquettes).iloc[::-1] \
            .rename(columns=LIBELLES["synthese"])
        st.dataframe(vue_synthese, hide_index=True, width="stretch")

        # ---------------- Détail par bénéficiaire ----------------
        st.markdown("#### Détail par bénéficiaire")
        TOUTES = "Toutes les dates conservées"
        choix = st.selectbox("Date de l'export", [TOUTES] + dates,
                             format_func=lambda x: x if x == TOUTES else date_fr(x))
        detail_h = archive_h["detail"]
        d = detail_h if choix == TOUTES else detail_h[detail_h["date_export"] == choix]
        pivot = pd.DataFrame()
        if d.empty:
            st.info("Pas de détail par bénéficiaire pour cette date.")
        else:
            # Une ligne par date d'export et par bénéficiaire, une colonne par fiche CEE
            pivot = d.pivot_table(index=["date_export", "beneficiaire"], columns="fiche",
                                  values="nb_operations", aggfunc="sum", fill_value=0)
            pivot["Total"] = pivot.sum(axis=1)
            pivot = pivot.reset_index().sort_values(["date_export", "Total"], ascending=[False, False])
            pivot["date_export"] = pd.to_datetime(pivot["date_export"]).dt.strftime("%d/%m/%Y")
            pivot = pivot.rename(columns={"date_export": "Date de l'export", "beneficiaire": "Bénéficiaire"})
            pivot.columns.name = None
            st.dataframe(pivot, hide_index=True, width="stretch")

        # Export Excel de l'historique (toutes les dates ou la date choisie)
        def filtre(t):
            tab = archive_h[t] if choix == TOUTES else archive_h[t][archive_h[t]["date_export"] == choix]
            tab = tab.assign(date_export=pd.to_datetime(tab["date_export"]).dt.strftime("%d/%m/%Y"))
            return tab.rename(columns=LIBELLES[t])

        onglets = {"Synthèse": filtre("synthese"), "Détail bénéficiaires": pivot,
                   "Lots par étape": filtre("etapes"), "Bureaux en contrôle": filtre("bureaux")}
        nom = "toutes_dates" if choix == TOUTES else choix
        st.download_button(
            "⬇️ Télécharger cet historique (Excel)",
            data=exports.excel_tableau({k: v for k, v in onglets.items() if not v.empty}),
            file_name=f"Historique_lots_{nom}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", width="stretch")

    with st.expander("💾 Sauvegarder ou restaurer l'archive"):
        st.caption("Le fichier téléchargé contient toute l'archive. Il peut être réimporté à tout "
                   "moment (y compris une archive d'une version précédente) : les dates déjà "
                   "présentes sont ignorées.")
        if not synthese_h.empty:
            st.download_button("⬇️ Télécharger l'archive complète (Excel)", data=stockage.archive_en_excel(),
                               file_name=f"Archive_lots_{date.today():%Y-%m-%d}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               width="stretch")
        fichier_archive = st.file_uploader("Réimporter une archive (.xlsx)", type=["xlsx"],
                                           key="import_archive")
        if fichier_archive is not None and st.button("Importer cette archive", type="primary"):
            try:
                with st.spinner("Import…"):
                    ajoutees, ignorees = stockage.importer_archive(fichier_archive)
                st.session_state["message_archive"] = (
                    f"Import terminé : {ajoutees} date(s) ajoutée(s)"
                    + (f", {ignorees} déjà présente(s) ignorée(s)." if ignorees else "."))
                st.rerun()
            except Exception as e:
                st.error(f"Import impossible : {e}")


# ============================================================================
# 8. ONGLET « RAPPORT HEBDOMADAIRE » (mail aux managers, déposé en brouillon Gmail)
# ============================================================================

@st.cache_data(show_spinner="Préparation du rapport PDF…", max_entries=2)
def preparer_pdf(_archive: dict, cle_archive: str, date_iso: str, _flux: pd.DataFrame | None,
                 cle_flux: str, auteur: str) -> bytes:
    return rapport_pdf.generer_pdf(_archive, date_iso, _flux, auteur)


with onglet_mail:
    gmail = secrets_gmail()
    st.markdown("Préparez le **mail hebdomadaire** pour vos managers, avec le récapitulatif des chiffres "
                "et les graphiques en **PDF**. Le mail est déposé dans vos **brouillons Gmail** : rien "
                "n'est envoyé tant que vous ne cliquez pas sur « Envoyer » dans Gmail.")
    if gmail:
        st.caption(f"📮 Boîte d'envoi : **{gmail[0]}** ✅")
    else:
        st.warning("Gmail n'est pas encore configuré : vous pouvez préparer le mail et télécharger le PDF, "
                   "mais pas créer le brouillon. Ajoutez la section [gmail] aux secrets Streamlit "
                   "(adresse + mot de passe d'application, voir README.md).")

    try:
        archive_m = stockage.charger_archive()
    except Exception as e:
        archive_m = None
        st.error(f"L'archive n'a pas pu être lue : {e}")
    synthese_m = archive_m["synthese"].sort_values("date_export") if archive_m else pd.DataFrame()

    if rapport_pdf is None:
        st.error("⚠️ Le rapport PDF est indisponible : une bibliothèque n'est pas installée "
                 f"({ERREUR_PDF}). Vérifiez que le fichier « requirements.txt » de GitHub contient bien "
                 "les lignes « matplotlib>=3.8 » et « reportlab>=4.0 », puis redémarrez l'application "
                 "(Manage app → ⋮ → Reboot app).")
    elif synthese_m.empty:
        st.info("Le rapport se construit à partir d'un export archivé. Chargez l'export de la semaine, "
                "puis cliquez sur « Archiver les résultats » en haut de page.")
    else:
        parametres_mail = stockage.charger_parametres()
        dates_m = synthese_m["date_export"].tolist()[::-1]

        # ---------------- 1. Export présenté ----------------
        st.markdown("##### 1. Export présenté")
        date_mail = st.selectbox("Export", dates_m, format_func=lambda d: f"Export du {date_fr(d)}",
                                 key=f"date_mail_{dates_m[0]}")
        ligne_m = synthese_m[synthese_m["date_export"] == date_mail].iloc[0].to_dict()
        precedents = synthese_m[synthese_m["date_export"] < date_mail]
        avant_m = precedents.iloc[-1].to_dict() if not precedents.empty else None
        if pd.isna(ligne_m.get("lots_en_constitution")) or pd.isna(ligne_m.get("flux_lots_creation")):
            st.warning(f"Certains indicateurs manquent pour l'export du {date_fr(date_mail)} (archivé avec une "
                       "version précédente) : ils apparaîtront en « — ». Rechargez cet export et cliquez sur "
                       "« ➕ Compléter l'archive » en haut de page.")

        # ---------------- 2. Destinataires ----------------
        st.markdown("##### 2. Destinataires")
        c1, c2 = st.columns(2)
        texte_a = c1.text_area("À (une adresse par ligne)", value=parametres_mail.get("mail_a", ""),
                               height=120, placeholder="manager1@entreprise.fr\nmanager2@entreprise.fr")
        texte_cc = c2.text_area("Copie (facultatif)", value=parametres_mail.get("mail_cc", ""),
                                height=120, placeholder="collegue@entreprise.fr")
        a_valides, a_invalides = messagerie.lire_adresses(texte_a)
        cc_valides, cc_invalides = messagerie.lire_adresses(texte_cc)
        if a_invalides or cc_invalides:
            st.error("Adresse(s) à corriger : " + ", ".join(f"« {x} »" for x in a_invalides + cc_invalides)
                     + ". Format attendu : prenom.nom@entreprise.fr")
        elif not a_valides:
            st.info("Indiquez au moins une adresse dans « À ».")
        else:
            st.caption(f"✅ {len(a_valides)} destinataire(s)" + (f" + {len(cc_valides)} en copie" if cc_valides else ""))
        signature = st.text_input("Signature", value=parametres_mail.get("signature", "Fredy PAYET"))
        if st.button("Enregistrer les destinataires et la signature",
                     help="Ils seront proposés automatiquement les semaines suivantes."):
            try:
                stockage.enregistrer_parametres({"mail_a": "\n".join(a_valides), "mail_cc": "\n".join(cc_valides),
                                                 "signature": signature})
                st.toast("Destinataires enregistrés ✅")
            except Exception as e:
                st.error(f"Enregistrement impossible : {e}")

        # ---------------- 3. Contenu du mail ----------------
        st.markdown("##### 3. Mail")
        cle_objet, cle_corps = f"objet_{date_mail}", f"corps_{date_mail}"
        if cle_objet not in st.session_state:
            st.session_state[cle_objet] = messagerie.objet_par_defaut(date_mail)
        if cle_corps not in st.session_state:
            st.session_state[cle_corps] = messagerie.corps_par_defaut(ligne_m, avant_m, signature)
        st.text_input("Objet", key=cle_objet)
        st.text_area("Texte du mail (modifiable)", key=cle_corps, height=460)
        if st.button("↺ Rétablir le texte proposé",
                     help="Remplace le texte par la version générée à partir des chiffres (vos modifications "
                          "seront perdues)."):
            st.session_state[cle_objet] = messagerie.objet_par_defaut(date_mail)
            st.session_state[cle_corps] = messagerie.corps_par_defaut(ligne_m, avant_m, signature)
            st.rerun()

        # ---------------- 4. Pièce jointe et brouillon ----------------
        st.markdown("##### 4. Pièce jointe et brouillon")
        flux_m = None
        if df is not None and date_export is not None and date_export.isoformat() == date_mail:
            if ligne_m.get("periode_debut") and not pd.isna(ligne_m.get("flux_periode_lots_creation")):
                flux_m = regles.flux_jours_periode(df, ligne_m["periode_debut"], ligne_m["periode_fin"])
            else:
                flux_m = regles.flux_par_jour(df, date_export, 20)
        semaine_m = pd.Timestamp(date_mail).isocalendar().week
        nom_pdf = f"Suivi_controle_lots_S{semaine_m}_{date_mail}.pdf"
        try:
            cle_archive = hashlib.md5(pd.util.hash_pandas_object(synthese_m.astype(str)).values.tobytes()
                                      + str(len(archive_m["etapes"]) + len(archive_m["bureaux"])).encode()).hexdigest()
            pdf = preparer_pdf(archive_m, cle_archive, date_mail, flux_m,
                               "20j" if flux_m is not None else "archive", signature)
        except Exception as e:
            pdf = None
            st.error(f"Le PDF n'a pas pu être créé : {e}")
        if flux_m is None:
            st.caption("ℹ️ Pour un graphique de flux sur 20 jours ouvrés dans le PDF, chargez l'export de cette "
                       "date dans le panneau de gauche (sinon : un jour par export archivé).")

        g, d = st.columns(2)
        if pdf:
            g.download_button(f"⬇️ Aperçu du PDF ({nom_pdf})", data=pdf, file_name=nom_pdf,
                              mime="application/pdf", width="stretch")
        pret = bool(gmail and a_valides and not a_invalides and not cc_invalides and pdf
                    and st.session_state[cle_objet].strip() and st.session_state[cle_corps].strip())
        if d.button("📝 Créer le brouillon dans Gmail", type="primary", width="stretch", disabled=not pret,
                    help="Dépose le mail (avec le PDF) dans vos brouillons Gmail. Il ne sera pas envoyé."):
            try:
                with st.spinner("Création du brouillon dans Gmail…"):
                    message = messagerie.construire_message(
                        gmail[0], a_valides, cc_valides, st.session_state[cle_objet].strip(),
                        st.session_state[cle_corps], pdf, nom_pdf)
                    dossier = messagerie.creer_brouillon(gmail[0], gmail[1], message)
                st.success(f"✅ Brouillon créé dans « {dossier} » de {gmail[0]}, avec le PDF en pièce jointe. "
                           f"[Ouvrir les brouillons Gmail](https://mail.google.com/mail/u/?authuser={gmail[0]}#drafts) "
                           "pour le relire puis l'envoyer.")
            except messagerie.ErreurMessagerie as e:
                st.error(f"⚠️ {e}")
            except Exception as e:
                st.error(f"⚠️ Le brouillon n'a pas pu être créé : {e}")
        if not pret and gmail:
            st.caption("Le bouton s'active quand au moins un destinataire valide, un objet et un texte sont renseignés.")
