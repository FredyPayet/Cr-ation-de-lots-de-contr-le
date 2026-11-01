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
import regles
import stockage

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
FORMAT_DATE = "DD/MM/YYYY"


def fr(n) -> str:
    """1234 -> '1 234' (séparateur de milliers français)."""
    return f"{int(n):,}".replace(",", " ")


def date_fr(d) -> str:
    return pd.Timestamp(d).strftime("%d/%m/%Y") if pd.notna(d) else "-"


# ============================================================================
# 2. FONCTIONS MISES EN CACHE
# ============================================================================

@st.cache_data(show_spinner="Lecture du fichier Excel… (quelques secondes pour un gros export)")
def charger_export(contenu: bytes) -> pd.DataFrame:
    return regles.lire_export(io.BytesIO(contenu))


@st.cache_data(show_spinner="Calcul des lots…")
def calculer(_df: pd.DataFrame, cle_fichier: str, cle_params: str, _p: regles.Parametres,
             cle_ajouts: str, _ajouts: pd.DataFrame | None) -> dict:
    # Les paramètres précédés de « _ » ne servent pas de clé : les clés texte suffisent.
    return regles.calculer(_df, _p, _ajouts)


@st.cache_data(ttl=600, show_spinner="Lecture du Google Sheet des clients…")
def charger_clients(url: str) -> dict:
    return stockage.lire_clients_gsheet(url)


# ============================================================================
# 3. BARRE LATÉRALE : FICHIER ET RÉGLAGES
# ============================================================================

sauves = stockage.charger_parametres()

with st.sidebar:
    st.markdown("## 📦 Création des lots")
    st.markdown("### 1. Fichier à traiter")
    fichier = st.file_uploader(
        "Export ODICEE (.xlsx)", type=["xlsx"],
        help="Déposez le fichier « Export_Controle_lots_de_travaux_BS_AAAA-MM-JJ.xlsx » sans le modifier.")

df, erreur = None, None
if fichier is not None:
    contenu = fichier.getvalue()
    cle_fichier = hashlib.md5(contenu).hexdigest()
    try:
        df = charger_export(contenu)
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
                            help="Le fichier doit être partagé à « Tous les utilisateurs disposant du lien ».")
        clients = {}
        if url.strip():
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
onglet_creation, onglet_ecartes, onglet_historique = st.tabs(
    ["📦 Création des lots", "🚫 Opérations écartées", "📈 Historique"])

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
        base = calculer(df, cle_fichier, cle_params, p, "toutes", None)

        zone_indicateurs = st.container()
        zone_bureaux = st.container()

        # ---------------- Partie 1 : compléter les lots existants ----------------
        props = base["propositions"]
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
                    vue[["ajouter", "lot", "fiche", "type_lot", "bureau", "dossier", "dcts", "nb_ops",
                         "drr_min", "delai_restant_mois", "ecart"]],
                    key=f"ajouts_{cle_fichier}_{hashlib.md5(cle_params.encode()).hexdigest()}",
                    hide_index=True, width="stretch",
                    disabled=["lot", "fiche", "type_lot", "bureau", "dossier", "dcts", "nb_ops",
                              "drr_min", "delai_restant_mois", "ecart"],
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

        # Calcul final avec les ajouts retenus par l'utilisateur
        cle_ajouts = "|".join(sorted(f"{d}#{f}" for d, f in zip(ajouts["dossier"], ajouts["fiche"])))
        res = calculer(df, cle_fichier, cle_params, p, cle_ajouts, ajouts)
        lots, affect = res["lots"], res["affectations"]

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

@st.dialog("Archiver les résultats du jour")
def confirmer_archive(synthese: dict, detail: pd.DataFrame):
    st.markdown(f"""
Vous allez archiver l'export du **{date_fr(synthese['date_export'])}** :
- **{fr(synthese['nb_operations_a_integrer'])}** opérations à intégrer
- **{fr(synthese['nb_lots_a_creer'])}** nouveaux lots à créer
- **{fr(synthese['nb_operations_ajoutees_lots_existants'])}** opérations ajoutées aux lots existants
- le détail pour **{detail['beneficiaire'].nunique()}** bénéficiaire(s)

Une seule archive est possible par date d'export : vérifiez vos réglages avant de confirmer.
""")
    c1, c2 = st.columns(2)
    if c1.button("Confirmer l'archivage", type="primary", width="stretch"):
        if stockage.archiver(synthese, detail):
            st.session_state["message_archive"] = f"Résultats du {date_fr(synthese['date_export'])} archivés ✅"
        st.rerun()
    if c2.button("Annuler", width="stretch"):
        st.rerun()


with zone_haut:
    titre, bouton = st.columns([3, 1.3], vertical_alignment="center")
    titre.title("Création des lots de contrôle")
    if df is not None and res is not None:
        date_iso = date_export.isoformat()
        if stockage.date_deja_archivee(date_iso):
            bouton.button(f"✅ Export du {date_fr(date_export)} déjà archivé", disabled=True,
                          width="stretch",
                          help="Une seule archive est possible par date d'export.")
        else:
            synthese, detail = regles.resume_archive(df, res, date_export)
            if bouton.button(f"🗄️ Archiver les résultats du {date_fr(date_export)}",
                             width="stretch",
                             disabled=synthese["nb_operations_a_integrer"] == 0,
                             help="Enregistre le nombre d'opérations, de lots et le détail par bénéficiaire."):
                confirmer_archive(synthese, detail)
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

with onglet_historique:
    synthese_h, detail_h = stockage.charger_archive()
    if synthese_h.empty:
        st.info("Aucune archive pour le moment. Après avoir créé vos lots, cliquez sur "
                "« Archiver les résultats » en haut de page. Vous pouvez aussi réimporter "
                "une archive sauvegardée ci-dessous.")
    else:
        synthese_h = synthese_h.sort_values("date_export")
        synthese_h["date"] = pd.to_datetime(synthese_h["date_export"])
        derniere = synthese_h.iloc[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric("Archives enregistrées", fr(len(synthese_h)), border=True)
        c2.metric(f"Opérations intégrées le {date_fr(derniere['date'])}",
                  fr(derniere["nb_operations_a_integrer"]), border=True)
        c3.metric(f"Lots créés le {date_fr(derniere['date'])}", fr(derniere["nb_lots_a_creer"]),
                  border=True)

        etiquettes = synthese_h["date"].dt.strftime("%d/%m/%Y")
        fig = go.Figure()
        fig.add_bar(x=etiquettes, y=synthese_h["nb_operations_a_integrer"],
                    name="Opérations à intégrer", marker_color=COULEURS["apres"],
                    text=[fr(v) for v in synthese_h["nb_operations_a_integrer"]], textposition="outside")
        fig.add_scatter(x=etiquettes, y=synthese_h["nb_lots_a_creer"], name="Lots à créer",
                        yaxis="y2", mode="lines+markers+text", line=dict(color=COULEURS["objectif"], width=3),
                        marker=dict(size=10),
                        hovertemplate="%{x} : %{y} lots<extra></extra>")
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=30, b=0), font=dict(size=14),
                          legend=dict(orientation="h", y=-0.2), plot_bgcolor="white",
                          xaxis=dict(type="category"),
                          yaxis=dict(title="Opérations", gridcolor="#E5E9F0", rangemode="tozero"),
                          yaxis2=dict(title="Lots", overlaying="y", side="right", showgrid=False,
                                      range=[0, synthese_h["nb_lots_a_creer"].max() * 1.6 + 1]))
        if len(synthese_h) < 6:
            fig.update_traces(width=0.35, selector=dict(type="bar"))
        st.plotly_chart(fig, width="stretch")

        st.dataframe(
            synthese_h.assign(date_export=etiquettes).drop(columns="date")
            .iloc[::-1],
            hide_index=True, width="stretch",
            column_config={
                "date_export": "Date de l'export", "date_archivage": "Archivé le",
                "nb_operations_a_integrer": "Opérations à intégrer", "nb_lots_a_creer": "Lots à créer",
                "nb_operations_ajoutees_lots_existants": "Ajoutées aux lots existants",
                "nb_dossiers": "Dossiers"})

        st.markdown("#### Détail par bénéficiaire")
        TOUTES = "Toutes les dates conservées"
        dates = synthese_h.sort_values("date_export", ascending=False)["date_export"].tolist()
        choix = st.selectbox("Date de l'export", [TOUTES] + dates,
                             format_func=lambda x: x if x == TOUTES else date_fr(x))
        d = detail_h if choix == TOUTES else detail_h[detail_h["date_export"] == choix]
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

            synthese_export = (synthese_h if choix == TOUTES
                               else synthese_h[synthese_h["date_export"] == choix])
            synthese_export = synthese_export.assign(
                date_export=synthese_export["date"].dt.strftime("%d/%m/%Y")).drop(columns="date") \
                .rename(columns={
                    "date_export": "Date de l'export", "date_archivage": "Archivé le",
                    "nb_operations_a_integrer": "Opérations à intégrer", "nb_lots_a_creer": "Lots à créer",
                    "nb_operations_ajoutees_lots_existants": "Ajoutées aux lots existants",
                    "nb_dossiers": "Dossiers"})
            nom = "toutes_dates" if choix == TOUTES else choix
            st.download_button(
                "⬇️ Télécharger cet historique (Excel)",
                data=exports.excel_tableau({"Synthèse": synthese_export, "Détail bénéficiaires": pivot}),
                file_name=f"Historique_lots_{nom}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary", width="stretch")

    with st.expander("💾 Sauvegarder ou restaurer l'archive"):
        st.caption("Sur Streamlit Cloud, l'archive peut être effacée lors d'un redémarrage de "
                   "l'application. Téléchargez-la régulièrement pour ne rien perdre.")
        if not synthese_h.empty:
            st.download_button("⬇️ Télécharger l'archive complète (Excel)", data=stockage.archive_en_excel(),
                               file_name=f"Archive_lots_{date.today():%Y-%m-%d}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               width="stretch")
        fichier_archive = st.file_uploader("Réimporter une archive (.xlsx)", type=["xlsx"],
                                           key="import_archive")
        if fichier_archive is not None and st.button("Importer cette archive", type="primary"):
            try:
                ajoutees, ignorees = stockage.importer_archive(fichier_archive)
                st.success(f"{ajoutees} date(s) ajoutée(s)"
                           + (f", {ignorees} déjà présente(s) ignorée(s)." if ignorees else "."))
                st.rerun()
            except Exception as e:
                st.error(f"Import impossible : {e}")
