"""
rapport_pdf.py — Rapport hebdomadaire au format PDF
===================================================

Construit le PDF joint au mail hebdomadaire, à partir de l'archive :
    Page 1 : schéma du flux du contrôle + tableaux des indicateurs
             (avec / sans date réelle de réalisation, export précédent, évolution ;
             la catégorie « Rénovation globale audit » est présentée à part)
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
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402
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
VIOLET = "#7C3AED"                      # catégorie « Rénovation globale audit »
AUDIT = "Rénovation globale audit"
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

def periode_flux(ligne: dict) -> tuple[str, str]:
    """Flux entre deux exports (dates incluses) si connu, sinon jour ouvré précédent."""
    if ligne.get("periode_debut") and not pd.isna(ligne.get("flux_periode_lots_creation")):
        return ("flux_periode_", f"du {_d(ligne['periode_debut'])} au {_d(ligne['periode_fin'])} "
                                 "(dates des deux exports incluses)")
    return "flux_", f"du {_d(ligne.get('date_reference_flux'))} (jour ouvré précédant l'export)"


def schema_flux(ligne: dict):
    """
    Schéma : flèche « créés » → 3 carrés (stocks : lots, opérations, volume MWhc) reliés
    par les flèches « envoyés » et « restitués » (flux de la période).
    """
    avec_audit = not pd.isna(ligne.get("audit_lots_en_controle"))
    bas = -0.55 if avec_audit else 0.3
    fig, ax = plt.subplots(figsize=(10, 3.75 - bas))
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)   # 1 unité = 1 pouce
    ax.set_xlim(0, 10)
    ax.set_ylim(bas, 3.75)
    ax.set_aspect("equal")
    ax.axis("off")
    etapes = [
        (2.6, BLEU, "#E6EEFA", "LOTS EN CRÉATION", "Constitution + coordonnées",
         _somme(ligne, "lots_en_constitution", "lots_demande_coordonnees"), ligne.get("ops_en_creation"),
         ligne.get("vol_en_creation")),
        (5.8, ORANGE, "#FDF0DC", "EN COURS DE CONTRÔLE", "Chez les bureaux de contrôle",
         ligne.get("lots_en_controle"), ligne.get("ops_en_controle"), ligne.get("vol_en_controle")),
        (9.0, VERT, "#E3F2E4", "LOTS À TRAITER", "Étape « Lot restitué »",
         ligne.get("lots_restitues"), ligne.get("ops_restitues"), ligne.get("vol_restitues")),
    ]
    y, cote = 2.15, 1.9            # centre vertical et côté des carrés
    for x, c, fond, nom, detail, lots, ops, vol in etapes:
        x0, y0 = x - cote / 2, y - cote / 2
        ax.add_patch(FancyBboxPatch((x0, y0), cote, cote, boxstyle="round,pad=0,rounding_size=0.18",
                                    facecolor=fond, edgecolor=c, linewidth=2))
        ax.add_patch(FancyBboxPatch((x0, y0 + cote - 0.12), cote, 0.12,
                                    boxstyle="round,pad=0,rounding_size=0.06", facecolor=c, edgecolor=c))
        ax.text(x, y0 + cote - 0.33, nom, ha="center", va="center", fontsize=9, fontweight="bold", color=c)
        ax.text(x, y + 0.22, _n(lots), ha="center", va="center", fontsize=30, fontweight="bold", color=TEXTE)
        ax.text(x, y - 0.17, "lots", ha="center", va="center", fontsize=10.5, color=TEXTE_DOUX)
        ax.plot([x0 + 0.15, x0 + cote - 0.15], [y - 0.38, y - 0.38], color="#CBD5E0", linewidth=0.8)
        ax.text(x, y - 0.56, f"{_n(ops)} opérations", ha="center", va="center", fontsize=10.5,
                fontweight="bold", color=TEXTE)
        ax.text(x, y - 0.78, f"{_n(vol)} MWhc", ha="center", va="center", fontsize=10.5,
                fontweight="bold", color=c)
        ax.text(x, y0 - 0.2, detail, ha="center", va="center", fontsize=9.5, color=TEXTE_DOUX)
    if not pd.isna(ligne.get("lots_controle_plus_5_sem")):
        ax.text(5.8, y - cote / 2 - 0.48,
                f"dont {_n(ligne['lots_controle_plus_5_sem'])} lots > 5 semaines "
                f"({_n(ligne.get('vol_controle_plus_5_sem'))} MWhc)",
                ha="center", va="center", fontsize=9.5, fontweight="bold", color=ROUGE)

    prefixe, periode = periode_flux(ligne)
    m = cote / 2 + 0.06
    fleches = [((0.05, y), (2.6 - m, y), BLEU, "Créés", "creation", 0.82),
               ((2.6 + m, y), (5.8 - m, y), ORANGE, "Envoyés", "envoi", 4.2),
               ((5.8 + m, y), (9.0 - m, y), VERT, "Restitués", "retour", 7.4)]
    for debut, fin, c2, titre, cle, xt in fleches:
        ax.add_patch(FancyArrowPatch(debut, fin, arrowstyle="-|>", mutation_scale=20, linewidth=2.6,
                                     color=c2, linestyle=(0, (4, 3))))
        ax.text(xt, y + 0.92, titre.upper(), ha="center", va="bottom", fontsize=9, color=TEXTE_DOUX,
                fontweight="bold")
        ax.text(xt, y + 0.55, f"{_n(ligne.get(f'{prefixe}lots_{cle}'))} lots", ha="center", va="bottom",
                fontsize=13, fontweight="bold", color=TEXTE)
        ax.text(xt, y + 0.31, f"{_n(ligne.get(f'{prefixe}ops_{cle}'))} opé.", ha="center", va="bottom",
                fontsize=9.5, color=TEXTE_DOUX)
        ax.text(xt, y + 0.10, f"{_n(ligne.get(f'{prefixe}vol_{cle}'))} MWhc", ha="center", va="bottom",
                fontsize=9.5, color=TEXTE_DOUX)
    ax.text(0.02, 3.73, f"Flèches : activité {periode}", ha="left", va="top", fontsize=10,
            color=TEXTE_DOUX, style="italic")

    # Bandeau de la catégorie « Rénovation globale audit » (suivie à part)
    if avec_audit:
        ax.add_patch(FancyBboxPatch((0.08, -0.47), 9.84, 0.82, boxstyle="round,pad=0,rounding_size=0.12",
                                    facecolor="#F3EEFE", edgecolor=VIOLET, linewidth=1.6))
        ax.text(0.25, 0.14, "RÉNOVATION GLOBALE AUDIT", ha="left", va="center", fontsize=9,
                fontweight="bold", color=VIOLET)
        ax.text(0.25, -0.08, "BAR-TH-145 / 177 sans date réelle", ha="left", va="center",
                fontsize=8, color=TEXTE_DOUX)
        ax.text(0.25, -0.28, "suivie à part (non comptée ci-dessus)", ha="left", va="center",
                fontsize=8, color=TEXTE_DOUX)
        blocs = [(3.55, "En création", _somme(ligne, "audit_lots_en_constitution", "audit_lots_demande_coordonnees"),
                  ligne.get("audit_ops_en_creation"), "creation", "créés"),
                 (5.8, "En contrôle", ligne.get("audit_lots_en_controle"), ligne.get("audit_ops_en_controle"),
                  "envoi", "envoyés"),
                 (8.05, "À traiter", ligne.get("audit_lots_restitues"), ligne.get("audit_ops_restitues"),
                  "retour", "restitués")]
        for x, nom, lots, ops, cle, verbe in blocs:
            ax.text(x, 0.16, f"{nom} : {_n(lots)} lots", ha="center", va="center", fontsize=10.5,
                    fontweight="bold", color=TEXTE)
            ax.text(x, -0.07, f"{_n(ops)} opérations", ha="center", va="center", fontsize=9, color=TEXTE_DOUX)
            ax.text(x, -0.29, f"{_n(ligne.get(f'{prefixe}audit_lots_{cle}'))} lots {verbe} sur la période",
                    ha="center", va="center", fontsize=8.5, color=VIOLET)
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
    autres = iter([c for c in palette if c != VIOLET] * 3)
    couleurs = [VIOLET if e == AUDIT else next(autres) for e in etapes["etape"]]   # audit : toujours violet
    _, _, textes = ax.pie(valeurs, colors=couleurs, startangle=90, counterclock=False,
                          wedgeprops=dict(width=0.45, edgecolor="white", linewidth=2),
                          autopct=lambda p: f"{p:.0f} %\n({round(p * total / 100)})" if p >= 4 else "",
                          pctdistance=0.78, textprops=dict(fontsize=8.5, color="white", fontweight="bold"))
    ax.text(0, 0, f"{total}\nlots", ha="center", va="center", fontsize=12, fontweight="bold", color=TEXTE)
    ax.legend([f"{e} ({v})" for e, v in zip(etapes["etape"], valeurs)], loc="center left",
              bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=8.5)
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
    audit = (bureaux["lots_audit"].fillna(0).astype(int).values if "lots_audit" in bureaux
             else ok * 0)
    ax.bar(noms, ok, color=BLEU, label="5 semaines ou moins")
    ax.bar(noms, retard, bottom=ok, color=ROUGE, label="Plus de 5 semaines")
    ax.bar(noms, audit, bottom=ok + retard, color=VIOLET, hatch="///", edgecolor="white", linewidth=0,
           label=AUDIT)
    for i, (a, b, c) in enumerate(zip(ok, retard, audit)):
        if a:
            ax.text(i, a / 2, str(a), ha="center", va="center", color="white", fontsize=8.5, fontweight="bold")
        if b:
            ax.text(i, a + b / 2, str(b), ha="center", va="center", color="white", fontsize=8.5, fontweight="bold")
        if c:
            ax.text(i, a + b + c / 2, str(c), ha="center", va="center", color="white", fontsize=8.5,
                    fontweight="bold")
        ax.text(i, a + b + c + 0.2, str(a + b + c), ha="center", va="bottom", fontsize=9, fontweight="bold",
                color=TEXTE)
    ax.set_ylim(0, max((ok + retard + audit).max(), 1) * 1.2)
    ax.tick_params(axis="x", labelsize=7.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylabel("Lots en cours de contrôle")
    ax.legend(frameon=False, fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=3,
              columnspacing=1.0, handlelength=1.4)
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
        positions = [i + (k - 1) * l for i in x]
        base = flux[f"lots_{cle}"].fillna(0).astype(float).values
        ax.bar(positions, base, width=l, color=c, label=nom)
        if f"audit_lots_{cle}" in flux:   # catégorie audit : empilée au-dessus, hachurée
            ax.bar(positions, flux[f"audit_lots_{cle}"].fillna(0).astype(float).values, bottom=base,
                   width=l, color=VIOLET, hatch="///", edgecolor="white", linewidth=0,
                   label=f"dont en plus : {AUDIT}" if k == 0 else None)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{JOURS[j.weekday()]}\n{j:%d/%m}" for j in pd.to_datetime(flux["jour"])], fontsize=7.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylabel("Nombre de lots")
    ax.yaxis.grid(True, color="#E5E9F0")
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8.5, ncol=4, loc="upper left")
    ax.set_ylim(0, max(ax.get_ylim()[1], 1) * 1.18)   # place pour la légende
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
    # Catégorie audit : tous ses lots (création + contrôle + à traiter) ; absente des anciens exports
    audit = (s["audit_lots_en_constitution"].astype(float) + s["audit_lots_demande_coordonnees"].astype(float)
             + s["audit_lots_en_controle"].astype(float) + s["audit_lots_restitues"].astype(float))
    for valeurs, nom, c, style in [(creation, "Lots en création", BLEU, "-"),
                                   (s["lots_en_controle"].astype(float), "Lots en contrôle", ORANGE, "-"),
                                   (s["lots_controle_plus_5_sem"].astype(float), "dont > 5 semaines", ROUGE, "--"),
                                   (s["lots_restitues"].astype(float), "Lots à traiter", VERT, "-"),
                                   (audit, f"{AUDIT} (tous ses lots)", VIOLET, ":")]:
        ax.plot(etiquettes, valeurs, style, marker="s" if c == VIOLET else "o", color=c, linewidth=2, label=nom)
        if not pd.isna(valeurs.iloc[-1]):
            ax.annotate(f"{int(valeurs.iloc[-1])}", (len(etiquettes) - 1, valeurs.iloc[-1]),
                        textcoords="offset points", xytext=(6, 0), va="center", fontsize=8.5, color=c,
                        fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.yaxis.grid(True, color="#E5E9F0")
    ax.set_axisbelow(True)
    ax.set_ylabel("Nombre de lots")
    ax.legend(frameon=False, fontsize=8, ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.12),
              columnspacing=1.2)
    ax.set_title("Évolution des stocks de lots (exports archivés)", fontsize=11, fontweight="bold",
                 color=TEXTE, loc="left")
    return fig


# ============================================================================
# Document PDF
# ============================================================================

def generer_pdf(archive: dict, date_iso: str, flux_jours: pd.DataFrame | None = None,
                auteur: str = "", ligne_maj: dict | None = None,
                date_comparaison: str | None = None) -> bytes:
    """
    archive          : {synthese, detail, etapes, bureaux} (stockage.charger_archive())
    date_iso         : export à présenter ('2026-11-10')
    flux_jours       : flux jour par jour (regles.flux_jours_periode ou flux_par_jour), facultatif ;
                       à défaut, le flux archivé (un jour par export) est utilisé
    ligne_maj        : chiffres de l'export présenté, si recalculés dans l'application
                       (par exemple le flux entre deux dates choisies)
    date_comparaison : export de comparaison (début de période) ; par défaut le précédent archivé
    """
    synthese = archive["synthese"].sort_values("date_export").reset_index(drop=True)
    lignes = synthese[synthese["date_export"] == date_iso]
    if lignes.empty:
        raise ValueError(f"L'export du {_d(date_iso)} n'est pas archivé.")
    ligne = ligne_maj if ligne_maj is not None else lignes.iloc[0].to_dict()
    if date_comparaison:
        precedents = synthese[synthese["date_export"] == date_comparaison]
    else:
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
    h.append(Paragraph("Carrés : stock de lots dans l'export. Flèches : lots créés, envoyés et restitués "
                       f"{periode_flux(ligne)[1]}. Opé. = nombre d'opérations de ces lots.",
                       styles["texte"]))

    # ---- Tableau 1 : lots et opérations, avec / sans date réelle (hors audit) ----
    h.append(Paragraph("Indicateurs clés — lots et opérations", styles["h2"]))
    h.append(Paragraph("Hors catégorie « Rénovation globale audit ». Un lot qui contient à la fois des "
                       "lignes avec et sans date réelle de réalisation est compté dans les deux colonnes.",
                       styles["texte"]))
    h.append(Spacer(1, 4))

    def cat(lib, *cols, sous=False):
        """Ligne : avec date réelle, sans date réelle, total, total de l'export précédent."""
        total = _somme(ligne, *cols)
        return (lib, _somme(ligne, *["ad_" + c for c in cols]), _somme(ligne, *["sd_" + c for c in cols]),
                total, _somme(avant, *cols) if avant else None, sous)

    lignes_cat = [
        cat("Lots en création (constitution + coordonnées)", "lots_en_constitution", "lots_demande_coordonnees"),
        cat("    dont en cours de constitution", "lots_en_constitution", sous=True),
        cat("    dont en demande de coordonnées", "lots_demande_coordonnees", sous=True),
        cat("Opérations en création", "ops_en_creation"),
        cat("Lots en cours de contrôle", "lots_en_controle"),
        cat("    dont envoyés depuis plus de 5 semaines", "lots_controle_plus_5_sem", sous=True),
        cat("Opérations en cours de contrôle", "ops_en_controle"),
        cat("    dont depuis plus de 5 semaines", "ops_controle_plus_5_sem", sous=True),
        cat("Lots restitués à traiter", "lots_restitues"),
        cat("Opérations restituées à traiter", "ops_restitues"),
        cat("Opérations éligibles", "ops_eligibles"),
        cat("    dont intégrables dans un lot (délai > 2 mois)", "ops_integrables_delai_2_mois", sous=True),
    ]
    date_avant = _d(avant.get("date_export")) if avant else "précédent"
    donnees = [["Indicateur", "Avec date\nréelle", "Sans date\nréelle", f"Total\n{_d(date_iso)}",
                f"Total\n{date_avant}", "Évolution"]] + [
        [lib, _n(ad), _n(sd), _n(t), _n(a), _ecart(t, a)] for lib, ad, sd, t, a, _ in lignes_cat]
    tab = Table(donnees, colWidths=[7.0 * cm, 2.1 * cm, 2.1 * cm, 2.3 * cm, 2.3 * cm, 2.0 * cm], repeatRows=1)
    style_base = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BLEU)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    style = list(style_base) + [("LINEAFTER", (2, 0), (2, -1), 0.6, colors.HexColor("#CBD5E0"))]
    for i, (*_, sous) in enumerate(lignes_cat, start=1):
        if sous:
            style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor(TEXTE_DOUX)))
        else:
            style.append(("FONT", (0, i), (0, i), "Helvetica-Bold", 8.5))
            style.append(("FONT", (3, i), (3, i), "Helvetica-Bold", 8.5))
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F3F6FA")))
    tab.setStyle(TableStyle(style))
    h.append(tab)

    # ---- Tableau 2 : audit, volumes, constitution des lots ----
    def ind(lib, col, sous=False):
        return (lib, ligne.get(col), avant.get(col), sous)

    # Tableau en sections : None = ligne de titre de section
    lignes_tab = [
        ("RÉNOVATION GLOBALE AUDIT — fiches BAR-TH-145 et BAR-TH-177 sans date réelle de réalisation "
         "(non comptées ailleurs)", None, None, None),
        ("Lots en création (constitution + coordonnées)",
         _somme(ligne, "audit_lots_en_constitution", "audit_lots_demande_coordonnees"),
         _somme(avant, "audit_lots_en_constitution", "audit_lots_demande_coordonnees") if avant else None, False),
        ind("    opérations de ces lots", "audit_ops_en_creation", True),
        ind("Lots en cours de contrôle", "audit_lots_en_controle"),
        ind("    opérations de ces lots", "audit_ops_en_controle", True),
        ind("    dont lots envoyés depuis plus de 5 semaines", "audit_lots_controle_plus_5_sem", True),
        ind("Lots restitués à traiter", "audit_lots_restitues"),
        ind("    opérations de ces lots", "audit_ops_restitues", True),
        ind("Opérations éligibles", "audit_ops_eligibles"),
        ("Volume de la catégorie, toutes étapes (MWhc, à titre indicatif)",
         _somme(ligne, "audit_vol_en_creation", "audit_vol_en_controle", "audit_vol_restitues", "audit_vol_eligibles"),
         _somme(avant, "audit_vol_en_creation", "audit_vol_en_controle", "audit_vol_restitues",
                "audit_vol_eligibles") if avant else None, True),
        ("VOLUME CUMAC (MWhc) — hors lignes sans date réelle de réalisation", None, None, None),
        ind("Volume en création (constitution + coordonnées)", "vol_en_creation"),
        ind("    dont en cours de constitution", "vol_en_constitution", True),
        ind("    dont en demande de coordonnées", "vol_demande_coordonnees", True),
        ind("Volume en cours de contrôle", "vol_en_controle"),
        ind("    dont depuis plus de 5 semaines", "vol_controle_plus_5_sem", True),
        ind("Volume restitué à traiter", "vol_restitues"),
        ind("Volume éligible", "vol_eligibles"),
        ind("    dont intégrable dans un lot (délai > 2 mois)", "vol_integrables_delai_2_mois", True),
        ("CONSTITUTION DES LOTS", None, None, None),
        ind("Opérations à intégrer dans des lots", "nb_operations_a_integrer"),
        ind("Nouveaux lots à créer", "nb_lots_a_creer"),
        ind("Opérations ajoutées aux lots existants", "nb_operations_ajoutees_lots_existants"),
    ]
    entete = ["Indicateur", f"Export\n{_d(date_iso)}",
              f"Export préc.\n{_d(avant.get('date_export'))}" if avant else "Export\nprécédent", "Évolution"]
    donnees = [entete] + [[lib, "", "", ""] if sous is None else [lib, _n(v), _n(a), _ecart(v, a)]
                          for lib, v, a, sous in lignes_tab]
    tab = Table(donnees, colWidths=[8.8 * cm, 3.0 * cm, 3.0 * cm, 3.0 * cm], repeatRows=1)
    style = list(style_base)
    for i, (lib, _, _, sous) in enumerate(lignes_tab, start=1):
        if sous is None:   # titre de section
            couleur = VIOLET if lib.startswith("RÉNOVATION") else BLEU
            style += [("SPAN", (0, i), (-1, i)), ("FONT", (0, i), (-1, i), "Helvetica-Bold", 7.5),
                      ("TEXTCOLOR", (0, i), (-1, i), colors.HexColor(couleur)),
                      ("TOPPADDING", (0, i), (-1, i), 7), ("LINEBELOW", (0, i), (-1, i), 0.8, colors.HexColor(couleur))]
        elif sous:
            style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor(TEXTE_DOUX)))
        else:
            style.append(("FONT", (0, i), (1, i), "Helvetica-Bold", 8.5))
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F3F6FA")))
    tab.setStyle(TableStyle(style))
    h.append(PageBreak())   # le second tableau commence en haut de la page 2
    h.append(Paragraph("Indicateurs clés — audit, volumes et constitution des lots", styles["h2"]))
    h.append(tab)

    # ---------------- Page 2 ----------------
    prefixe, periode = periode_flux(ligne)
    titre_flux = [Paragraph("Flux de la période", styles["h2"]),
                  Paragraph(f"Activité {periode}.", styles["texte"]), Spacer(1, 4)]
    flux_tab = [["", "Lots", "Opérations", "Volume (MWhc)", "Audit réno.\nglobale : lots",
                 "Audit réno.\nglobale : opé."]] + [
        [nom, _n(ligne.get(f"{prefixe}lots_{cle}")), _n(ligne.get(f"{prefixe}ops_{cle}")),
         _n(ligne.get(f"{prefixe}vol_{cle}")), _n(ligne.get(f"{prefixe}audit_lots_{cle}")),
         _n(ligne.get(f"{prefixe}audit_ops_{cle}"))]
        for nom, cle in (("Lots créés", "creation"), ("Lots envoyés aux bureaux de contrôle", "envoi"),
                         ("Lots restitués", "retour"))]
    tf = Table(flux_tab, colWidths=[6.2 * cm, 1.9 * cm, 2.2 * cm, 2.7 * cm, 2.4 * cm, 2.4 * cm])
    tf.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("FONT", (1, 1), (1, -1), "Helvetica-Bold", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BLEU)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("TEXTCOLOR", (4, 1), (5, -1), colors.HexColor(VIOLET)),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    h.append(KeepTogether(titre_flux + [tf, Paragraph(
        "Les colonnes « Audit réno. globale » s'ajoutent aux autres : cette catégorie n'est pas comptée "
        "dans les lots, opérations et volumes de gauche.", styles["texte"])]))
    etapes = archive["etapes"][archive["etapes"]["date_export"] == date_iso]
    bureaux = archive["bureaux"][archive["bureaux"]["date_export"] == date_iso]
    duo = Table([[_image(camembert_etapes(etapes), largeur / cm / 2 - 0.2),
                  _image(histogramme_bureaux(bureaux), largeur / cm / 2 - 0.2)]],
                colWidths=[largeur / 2, largeur / 2])
    duo.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    h.append(KeepTogether([Paragraph("Répartition des lots", styles["h2"]), duo]))

    if flux_jours is None or flux_jours.empty:
        arch = synthese[synthese["flux_lots_creation"].notna() & (synthese["date_export"] <= date_iso)].tail(20)
        flux_jours = pd.DataFrame({"jour": pd.to_datetime(arch["date_reference_flux"])})
        for cle in ("creation", "envoi", "retour"):
            flux_jours[f"lots_{cle}"] = arch[f"flux_lots_{cle}"].astype(float).values
            flux_jours[f"audit_lots_{cle}"] = arch[f"flux_audit_lots_{cle}"].astype(float).fillna(0).values
        source = "un jour par export archivé"
    elif prefixe == "flux_periode_":
        source = f"jour par jour {periode}"
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
