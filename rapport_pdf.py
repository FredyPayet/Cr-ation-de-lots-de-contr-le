"""
rapport_pdf.py — Rapport hebdomadaire au format PDF
===================================================

Construit le PDF joint au mail hebdomadaire, à partir de l'archive :
    Page 1 : schéma du flux du contrôle + tableau des indicateurs
             (valeur de l'export, valeur de l'export précédent, évolution)
    Page 2 : lots par étape (camembert), lots en contrôle par bureau (histogramme empilé),
             flux d'activité des 20 derniers jours ouvrés, évolution d'un export à l'autre

Les graphiques sont dessinés avec matplotlib (images), la mise en page avec reportlab.
"""
from __future__ import annotations

import io
import textwrap

import matplotlib
matplotlib.use("Agg")  # pas d'écran sur le serveur
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer,  # noqa: E402
                                Table, TableStyle, KeepTogether)

# Couleurs (identiques à l'application)
BLEU, ORANGE, VERT, GRIS, ROUGE = "#1F5FAD", "#D97706", "#2E7D32", "#8A9BB0", "#C2410C"
TEXTE, TEXTE_DOUX = "#1A202C", "#4A5568"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": "#CBD5E0",
                     "axes.labelcolor": TEXTE, "xtick.color": TEXTE, "ytick.color": TEXTE})

JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


# ============================================================================
# Outils
# ============================================================================

def _n(v) -> str:
    """Nombre au format français, « — » si absent."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "—"
    return f"{int(v):,}".replace(",", " ")


def _d(iso) -> str:
    return pd.Timestamp(iso).strftime("%d/%m/%Y") if iso else "—"


def _ecart(v, avant) -> str:
    """Évolution par rapport à l'export précédent : +3, −2, = ou —."""
    if any(x is None or pd.isna(x) for x in (v, avant)):
        return "—"
    e = int(v) - int(avant)
    return "=" if e == 0 else (f"+{e:,}" if e > 0 else f"−{abs(e):,}").replace(",", " ")


def _image(fig, largeur_cm: float) -> Image:
    tampon = io.BytesIO()
    fig.savefig(tampon, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    tampon.seek(0)
    img = Image(tampon)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth, img.drawHeight = largeur_cm * cm, largeur_cm * cm * ratio
    return img


def _somme(ligne, *cols):
    vals = [ligne.get(c) for c in cols]
    return None if any(v is None or pd.isna(v) for v in vals) else sum(int(v) for v in vals)


# ============================================================================
# Graphiques
# ============================================================================

def schema_flux(ligne: dict):
    """Schéma : flèche « créés » → 3 cercles (stocks) reliés par « envoyés » et « restitués »."""
    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3.5)
    ax.set_aspect("equal")
    ax.axis("off")
    etapes = [
        (2.7, BLEU, "#E6EEFA", "Lots en création", "Constitution + coordonnées",
         _somme(ligne, "lots_en_constitution", "lots_demande_coordonnees"), ligne.get("ops_en_creation")),
        (5.85, ORANGE, "#FDF0DC", "En cours de contrôle", "Chez les bureaux de contrôle",
         ligne.get("lots_en_controle"), ligne.get("ops_en_controle")),
        (9.0, VERT, "#E3F2E4", "Lots à traiter", "Étape « Lot restitué »",
         ligne.get("lots_restitues"), ligne.get("ops_restitues")),
    ]
    y, r = 2.05, 0.95
    for x, c, fond, nom, detail, lots, ops in etapes:
        ax.add_patch(Circle((x, y), r, facecolor=fond, edgecolor=c, linewidth=4))
        ax.text(x, y + 0.2, _n(lots), ha="center", va="center", fontsize=24, fontweight="bold", color=TEXTE)
        ax.text(x, y - 0.25, "lots", ha="center", va="center", fontsize=10, color=TEXTE_DOUX)
        ax.text(x, y - 0.55, f"{_n(ops)} opé.", ha="center", va="center", fontsize=10,
                fontweight="bold", color=c)
        ax.text(x, y - r - 0.25, nom, ha="center", va="center", fontsize=11, fontweight="bold", color=c)
        ax.text(x, y - r - 0.55, detail, ha="center", va="center", fontsize=8.5, color=TEXTE_DOUX)
    if ligne.get("lots_controle_plus_5_sem") is not None and not pd.isna(ligne.get("lots_controle_plus_5_sem")):
        ax.text(5.85, y - r - 0.85, f"dont {_n(ligne['lots_controle_plus_5_sem'])} lots > 5 semaines",
                ha="center", va="center", fontsize=8.5, fontweight="bold", color=ROUGE)

    jour = _d(ligne.get("date_reference_flux"))
    fleches = [((0.15, y), (2.7 - r - 0.08, y), GRIS, BLEU, "Créés", "creation", 0.9),
               ((2.7 + r + 0.08, y), (5.85 - r - 0.08, y), BLEU, ORANGE, "Envoyés", "envoi", 4.27),
               ((5.85 + r + 0.08, y), (9.0 - r - 0.08, y), ORANGE, VERT, "Restitués", "retour", 7.42)]
    for debut, fin, c1, c2, titre, cle, xt in fleches:
        ax.add_patch(FancyArrowPatch(debut, fin, arrowstyle="-|>", mutation_scale=18, linewidth=2.5,
                                     color=c2, linestyle=(0, (4, 3))))
        ax.text(xt, y + 0.62, titre.upper(), ha="center", va="bottom", fontsize=8, color=TEXTE_DOUX,
                fontweight="bold")
        ax.text(xt, y + 0.28, f"{_n(ligne.get(f'flux_lots_{cle}'))} lots", ha="center", va="bottom",
                fontsize=11, fontweight="bold", color=TEXTE)
        ax.text(xt, y + 0.08, f"{_n(ligne.get(f'flux_ops_{cle}'))} opé.", ha="center", va="bottom",
                fontsize=8.5, color=TEXTE_DOUX)
    ax.text(0.0, 3.45, f"Flèches : activité du {jour} (jour ouvré précédant l'export)",
            ha="left", va="top", fontsize=8.5, color=TEXTE_DOUX, style="italic")
    return fig


def camembert_etapes(etapes: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(5.2, 3.8))
    if etapes.empty:
        ax.text(0.5, 0.5, "Pas de données", ha="center", va="center", color=TEXTE_DOUX)
        ax.axis("off")
        return fig
    palette = [BLEU, "#6A9BD8", ORANGE, VERT, GRIS, "#7C3AED", "#0E7490", "#B45309"]
    valeurs = etapes["nb_lots"].astype(int).tolist()
    total = sum(valeurs)
    _, _, textes = ax.pie(valeurs, colors=palette[:len(valeurs)], startangle=90, counterclock=False,
                          wedgeprops=dict(width=0.45, edgecolor="white", linewidth=2),
                          autopct=lambda p: f"{p:.0f} %\n({round(p * total / 100)})" if p >= 4 else "",
                          pctdistance=0.78, textprops=dict(fontsize=8.5, color="white", fontweight="bold"))
    ax.text(0, 0, f"{total}\nlots", ha="center", va="center", fontsize=12, fontweight="bold", color=TEXTE)
    ax.legend(etapes["etape"], loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=8.5)
    ax.set_title("Lots par étape de contrôle", fontsize=11, fontweight="bold", color=TEXTE, loc="left")
    return fig


def histogramme_bureaux(bureaux: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(5.2, 3.8))
    if bureaux.empty:
        ax.text(0.5, 0.5, "Pas de données", ha="center", va="center", color=TEXTE_DOUX)
        ax.axis("off")
        return fig
    noms = [textwrap.fill(b, 11) for b in bureaux["bureau"]]
    ok = bureaux["lots_delai_ok"].fillna(0).astype(int).values
    retard = bureaux["lots_plus_5_sem"].fillna(0).astype(int).values
    ax.bar(noms, ok, color=BLEU, label="5 semaines ou moins")
    ax.bar(noms, retard, bottom=ok, color=ROUGE, label="Plus de 5 semaines")
    for i, (a, b) in enumerate(zip(ok, retard)):
        if a:
            ax.text(i, a / 2, str(a), ha="center", va="center", color="white", fontsize=8.5, fontweight="bold")
        if b:
            ax.text(i, a + b / 2, str(b), ha="center", va="center", color="white", fontsize=8.5, fontweight="bold")
        ax.text(i, a + b + 0.2, str(a + b), ha="center", va="bottom", fontsize=9, fontweight="bold", color=TEXTE)
    ax.set_ylim(0, max((ok + retard).max(), 1) * 1.2)
    ax.tick_params(axis="x", labelsize=7.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylabel("Lots en cours de contrôle")
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2)
    ax.set_title("Lots en contrôle par bureau", fontsize=11, fontweight="bold", color=TEXTE, loc="left")
    return fig


def histogramme_flux(flux: pd.DataFrame):
    """Lots créés / envoyés / restitués par jour ouvré."""
    fig, ax = plt.subplots(figsize=(10, 3.2))
    if flux.empty:
        ax.text(0.5, 0.5, "Pas de données", ha="center", va="center", color=TEXTE_DOUX)
        ax.axis("off")
        return fig
    x = range(len(flux))
    l = 0.27
    for k, (cle, nom, c) in enumerate([("creation", "Lots créés", BLEU), ("envoi", "Lots envoyés", ORANGE),
                                       ("retour", "Lots restitués", VERT)]):
        ax.bar([i + (k - 1) * l for i in x], flux[f"lots_{cle}"], width=l, color=c, label=nom)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{JOURS[j.weekday()]}\n{j:%d/%m}" for j in pd.to_datetime(flux["jour"])], fontsize=7.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylabel("Nombre de lots")
    ax.yaxis.grid(True, color="#E5E9F0")
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8.5, ncol=3, loc="upper left")
    ax.set_title("Flux d'activité par jour ouvré", fontsize=11, fontweight="bold", color=TEXTE, loc="left")
    return fig


def courbes_evolution(synthese: pd.DataFrame):
    """Évolution des stocks de lots sur les derniers exports archivés."""
    fig, ax = plt.subplots(figsize=(10, 3.0))
    s = synthese.sort_values("date_export").tail(12).copy()
    s = s[s["lots_en_controle"].notna()]
    if len(s) < 2:
        ax.text(0.5, 0.5, "L'évolution s'affichera à partir de deux exports archivés.",
                ha="center", va="center", color=TEXTE_DOUX)
        ax.axis("off")
        return fig
    etiquettes = [pd.Timestamp(d).strftime("%d/%m") for d in s["date_export"]]
    creation = s["lots_en_constitution"].astype(float) + s["lots_demande_coordonnees"].astype(float)
    for valeurs, nom, c, style in [(creation, "Lots en création", BLEU, "-"),
                                   (s["lots_en_controle"].astype(float), "Lots en contrôle", ORANGE, "-"),
                                   (s["lots_controle_plus_5_sem"].astype(float), "dont > 5 semaines", ROUGE, "--"),
                                   (s["lots_restitues"].astype(float), "Lots à traiter", VERT, "-")]:
        ax.plot(etiquettes, valeurs, style, marker="o", color=c, linewidth=2, label=nom)
        ax.annotate(f"{int(valeurs.iloc[-1])}", (len(etiquettes) - 1, valeurs.iloc[-1]),
                    textcoords="offset points", xytext=(6, 0), va="center", fontsize=8.5, color=c,
                    fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, color="#E5E9F0")
    ax.set_axisbelow(True)
    ax.set_ylabel("Nombre de lots")
    ax.legend(frameon=False, fontsize=8.5, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    ax.set_title("Évolution des stocks de lots (exports archivés)", fontsize=11, fontweight="bold",
                 color=TEXTE, loc="left")
    return fig


# ============================================================================
# Document PDF
# ============================================================================

def generer_pdf(archive: dict, date_iso: str, flux_jours: pd.DataFrame | None = None,
                auteur: str = "") -> bytes:
    """
    archive    : {synthese, detail, etapes, bureaux} (stockage.charger_archive())
    date_iso   : export à présenter ('2026-11-10')
    flux_jours : flux des 20 derniers jours ouvrés (regles.flux_par_jour), facultatif ;
                 à défaut, le flux archivé (un jour par export) est utilisé
    """
    synthese = archive["synthese"].sort_values("date_export").reset_index(drop=True)
    lignes = synthese[synthese["date_export"] == date_iso]
    if lignes.empty:
        raise ValueError(f"L'export du {_d(date_iso)} n'est pas archivé.")
    ligne = lignes.iloc[0].to_dict()
    precedents = synthese[synthese["date_export"] < date_iso]
    avant = precedents.iloc[-1].to_dict() if not precedents.empty else {}
    d_export = pd.Timestamp(date_iso)
    semaine = d_export.isocalendar().week

    styles = {
        "titre": ParagraphStyle("titre", fontName="Helvetica-Bold", fontSize=17, leading=21,
                                textColor=colors.HexColor(TEXTE)),
        "sous": ParagraphStyle("sous", fontName="Helvetica", fontSize=10.5, leading=14,
                               textColor=colors.HexColor(TEXTE_DOUX)),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, spaceBefore=8,
                             spaceAfter=4, textColor=colors.HexColor(BLEU)),
        "texte": ParagraphStyle("texte", fontName="Helvetica", fontSize=9, leading=12,
                                textColor=colors.HexColor(TEXTE_DOUX), alignment=TA_LEFT),
    }

    def _pied(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor(TEXTE_DOUX))
        canvas.drawString(1.6 * cm, 1.0 * cm, f"Suivi du contrôle des lots — export du {_d(date_iso)}")
        canvas.drawRightString(A4[0] - 1.6 * cm, 1.0 * cm, f"Page {doc.page}")
        canvas.setStrokeColor(colors.HexColor(BLEU))
        canvas.setLineWidth(3)
        canvas.line(1.6 * cm, A4[1] - 1.0 * cm, A4[0] - 1.6 * cm, A4[1] - 1.0 * cm)
        canvas.restoreState()

    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.6 * cm,
                            title=f"Suivi hebdomadaire du contrôle des lots — {_d(date_iso)}",
                            author=auteur)
    largeur = A4[0] - 3.2 * cm
    h = []

    # ---------------- Page 1 ----------------
    h.append(Paragraph("Suivi hebdomadaire du contrôle des lots", styles["titre"]))
    h.append(Paragraph(f"Semaine {semaine} — export ODICEE du {_d(date_iso)}"
                       + (f" — comparaison avec l'export du {_d(avant.get('date_export'))}" if avant else ""),
                       styles["sous"]))
    h.append(Spacer(1, 8))
    h.append(Paragraph("Flux du contrôle", styles["h2"]))
    h.append(_image(schema_flux(ligne), largeur / cm))
    h.append(Paragraph("Cercles : stock de lots dans l'export. Flèches : lots créés, envoyés et restitués "
                       "le jour ouvré précédant l'export. Opé. = nombre d'opérations de ces lots.",
                       styles["texte"]))

    h.append(Paragraph("Indicateurs clés", styles["h2"]))
    lignes_tab = [
        ("Lots en création (constitution + coordonnées)",
         _somme(ligne, "lots_en_constitution", "lots_demande_coordonnees"),
         _somme(avant, "lots_en_constitution", "lots_demande_coordonnees") if avant else None, False),
        ("    dont en cours de constitution", ligne.get("lots_en_constitution"), avant.get("lots_en_constitution"), True),
        ("    dont en demande de coordonnées", ligne.get("lots_demande_coordonnees"), avant.get("lots_demande_coordonnees"), True),
        ("Lots en cours de contrôle", ligne.get("lots_en_controle"), avant.get("lots_en_controle"), False),
        ("    dont envoyés depuis plus de 5 semaines", ligne.get("lots_controle_plus_5_sem"), avant.get("lots_controle_plus_5_sem"), True),
        ("Opérations en cours de contrôle", ligne.get("ops_en_controle"), avant.get("ops_en_controle"), False),
        ("    dont depuis plus de 5 semaines", ligne.get("ops_controle_plus_5_sem"), avant.get("ops_controle_plus_5_sem"), True),
        ("Lots restitués à traiter", ligne.get("lots_restitues"), avant.get("lots_restitues"), False),
        ("Opérations éligibles", ligne.get("ops_eligibles"), avant.get("ops_eligibles"), False),
        ("    dont intégrables dans un lot (délai > 2 mois)", ligne.get("ops_integrables_delai_2_mois"), avant.get("ops_integrables_delai_2_mois"), True),
        ("Opérations à intégrer dans des lots", ligne.get("nb_operations_a_integrer"), avant.get("nb_operations_a_integrer"), False),
        ("Nouveaux lots à créer", ligne.get("nb_lots_a_creer"), avant.get("nb_lots_a_creer"), False),
        ("Opérations ajoutées aux lots existants", ligne.get("nb_operations_ajoutees_lots_existants"), avant.get("nb_operations_ajoutees_lots_existants"), False),
    ]
    entete = ["Indicateur", f"Export\n{_d(date_iso)}",
              f"Export préc.\n{_d(avant.get('date_export'))}" if avant else "Export\nprécédent", "Évolution"]
    donnees = [entete] + [[lib, _n(v), _n(a), _ecart(v, a)] for lib, v, a, _ in lignes_tab]
    tab = Table(donnees, colWidths=[8.6 * cm, 3.0 * cm, 3.0 * cm, 3.2 * cm], repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BLEU)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, (_, _, _, sous) in enumerate(lignes_tab, start=1):
        if sous:
            style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor(TEXTE_DOUX)))
        else:
            style.append(("FONT", (0, i), (1, i), "Helvetica-Bold", 9))
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F3F6FA")))
    tab.setStyle(TableStyle(style))
    h.append(tab)

    # ---------------- Page 2 ----------------
    h.append(PageBreak())
    h.append(Paragraph("Répartition des lots", styles["h2"]))
    etapes = archive["etapes"][archive["etapes"]["date_export"] == date_iso]
    bureaux = archive["bureaux"][archive["bureaux"]["date_export"] == date_iso]
    duo = Table([[_image(camembert_etapes(etapes), largeur / cm / 2 - 0.2),
                  _image(histogramme_bureaux(bureaux), largeur / cm / 2 - 0.2)]],
                colWidths=[largeur / 2, largeur / 2])
    duo.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    h.append(duo)

    if flux_jours is None or flux_jours.empty:
        arch = synthese[synthese["flux_lots_creation"].notna() & (synthese["date_export"] <= date_iso)].tail(20)
        flux_jours = pd.DataFrame({"jour": pd.to_datetime(arch["date_reference_flux"])})
        for cle in ("creation", "envoi", "retour"):
            flux_jours[f"lots_{cle}"] = arch[f"flux_lots_{cle}"].astype(float).values
        source = "un jour par export archivé"
    else:
        source = "20 derniers jours ouvrés, d'après l'export"
    h.append(KeepTogether([Paragraph("Flux d'activité", styles["h2"]),
                           _image(histogramme_flux(flux_jours), largeur / cm),
                           Paragraph(f"Source : {source}.", styles["texte"])]))
    h.append(KeepTogether([Paragraph("Évolution", styles["h2"]),
                           _image(courbes_evolution(synthese[synthese["date_export"] <= date_iso]),
                                  largeur / cm)]))

    doc.build(h, onFirstPage=_pied, onLaterPages=_pied)
    return tampon.getvalue()
