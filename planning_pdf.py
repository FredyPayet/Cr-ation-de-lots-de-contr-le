"""
planning_pdf.py — PDF « Planning de la semaine » (distinct du rapport de suivi)
===============================================================================

Document d'une à quelques pages, joint au mail hebdomadaire en plus du rapport de suivi :
  - les 6 chiffres de la charge de la semaine ;
  - un graphique des lots par priorité ;
  - la liste des lots AU PLANNING de la semaine pour chaque activité (les lots dans les
    délais sont comptés mais pas listés : la liste complète est dans l'export Excel) ;
  - les dossiers à relancer sans AH et les opérations non satisfaisantes les plus urgents.
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import planning

BLEU, TEXTE, TEXTE_DOUX = "#1F5FAD", "#1A202C", "#4A5568"
COULEURS = {planning.PRIORITAIRE: "#B91C1C", planning.DEPASSE: "#C2410C",
            planning.A_PLANIFIER: "#E08A00", planning.DANS_DELAIS: "#8A9BB0"}
NOMS = {planning.PRIORITAIRE: "Prioritaire (échéance proche)", planning.DEPASSE: "Délai dépassé",
        planning.A_PLANIFIER: "À planifier cette semaine", planning.DANS_DELAIS: "Dans les délais"}
MAX_LIGNES = 40   # lignes listées au plus par tableau de relance


def _n(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{int(v):,}".replace(",", " ")


def _d(v) -> str:
    return "—" if v is None or pd.isna(v) else pd.Timestamp(v).strftime("%d/%m/%Y")


def _graphique(charge: dict) -> Image:
    """Barres horizontales empilées : lots de chaque activité par priorité."""
    titres = [a[1] for a in planning.ACTIVITES][::-1]
    fig, ax = plt.subplots(figsize=(10, 2.3))
    gauche = [0] * len(titres)
    for prio in planning.PRIORITES:
        valeurs = [int((charge[a[0]]["priorite"] == prio).sum()) for a in planning.ACTIVITES][::-1]
        ax.barh(titres, valeurs, left=gauche, color=COULEURS[prio], label=NOMS[prio], height=0.62)
        for i, (v, g) in enumerate(zip(valeurs, gauche)):
            if v:
                ax.text(g + v / 2, i, str(v), ha="center", va="center", color="white", fontsize=9,
                        fontweight="bold")
        gauche = [g + v for g, v in zip(gauche, valeurs)]
    for i, total in enumerate(gauche):
        ax.text(total + max(gauche + [1]) * 0.01, i, f"{total} lots", va="center", fontsize=9, color=TEXTE)
    ax.set_xlim(0, max(gauche + [1]) * 1.12)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=9.5)
    ax.tick_params(axis="x", labelsize=8)
    ax.legend(frameon=False, fontsize=8.5, ncol=4, loc="upper center", bbox_to_anchor=(0.45, -0.18))
    tampon = io.BytesIO()
    fig.savefig(tampon, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    tampon.seek(0)
    img = Image(tampon)
    img.drawWidth = 24 * cm
    img.drawHeight = 24 * cm * img.imageHeight / img.imageWidth
    return img


def generer_pdf(charge: dict, resume: dict, auteur: str = "") -> bytes:
    """Construit le PDF du planning (A4 paysage) et renvoie son contenu."""
    styles = {
        "titre": ParagraphStyle("titre", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=colors.HexColor(TEXTE),
                                spaceAfter=4),
        "sous": ParagraphStyle("sous", fontName="Helvetica", fontSize=10, textColor=colors.HexColor(TEXTE_DOUX),
                               spaceAfter=10),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, textColor=colors.HexColor(BLEU),
                             spaceBefore=12, spaceAfter=4),
        "texte": ParagraphStyle("texte", fontName="Helvetica", fontSize=9, textColor=colors.HexColor(TEXTE_DOUX),
                                leading=12, spaceAfter=4),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=7.5, leading=9, textColor=colors.HexColor(TEXTE)),
        "carte_n": ParagraphStyle("carte_n", fontName="Helvetica-Bold", fontSize=20, leading=23, alignment=1,
                                  textColor=colors.HexColor(TEXTE)),
        "carte_t": ParagraphStyle("carte_t", fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, alignment=1,
                                  textColor=colors.HexColor(BLEU)),
        "carte_d": ParagraphStyle("carte_d", fontName="Helvetica", fontSize=7.5, leading=9.5, alignment=1,
                                  textColor=colors.HexColor(TEXTE_DOUX)),
    }
    date_export = resume["date_export"]

    def pied(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor(TEXTE_DOUX))
        canvas.drawString(1.5 * cm, 0.9 * cm, f"Planning de la semaine — export ODICEE du {_d(date_export)}"
                          + (f" — {auteur}" if auteur else ""))
        canvas.drawRightString(landscape(A4)[0] - 1.5 * cm, 0.9 * cm, f"Page {doc.page}")
        canvas.restoreState()

    h = [Paragraph(f"Planning de la semaine {resume['semaine_export']}", styles["titre"]),
         Paragraph(f"Charge de travail d'après l'export ODICEE du {_d(date_export)}. Délais : "
                   f"{resume['delai_coordonnees_sem']} semaines pour passer un lot en demande de coordonnées, "
                   f"{resume['delai_envoi_sem']} semaines pour l'envoyer, {resume['delai_traitement_sem']} semaines "
                   f"pour traiter un lot restitué. Un lot est prioritaire si son échéance (date réelle de "
                   f"réalisation la plus ancienne + 12 mois) tombe dans {resume['priorite_mois']} mois ou moins.",
                   styles["sous"])]

    # ---- Les 6 chiffres ----
    def carte(titre, nombre, detail):
        return [Paragraph(titre, styles["carte_t"]), Paragraph(_n(nombre), styles["carte_n"]),
                Paragraph(detail, styles["carte_d"])]

    cartes = [
        carte("Lots à passer en demande de coordonnées", resume["coordonnees_planning"],
              f"au planning, sur {_n(resume['coordonnees_total'])} lots<br/>{_n(resume['coordonnees_ops_planning'])} opérations"),
        carte("Lots à envoyer", resume["envoi_planning"],
              f"au planning, sur {_n(resume['envoi_total'])} lots<br/>{_n(resume['envoi_ops_planning'])} opérations"),
        carte("Lots à traiter", resume["traitement_planning"],
              f"au planning, sur {_n(resume['traitement_total'])} lots<br/>{_n(resume['traitement_ops_planning'])} opérations"),
        carte("Dossiers à relancer sans AH", resume["ah_dossiers"],
              f"Statut AH « Attente AH »<br/>{_n(resume['ah_ops'])} opérations"),
        carte("Opérations non satisfaisantes à relancer", resume["ko_ops"],
              f"« {planning.STATUT_KO} »<br/>dans {_n(resume['ko_dossiers'])} dossiers"),
        carte("Lots à créer", resume["lots_a_creer"],
              f"nouveaux lots proposés<br/>{_n(resume['ops_a_integrer'])} opérations"),
    ]
    t = Table([cartes], colWidths=[4.45 * cm] * 6)
    t.setStyle(TableStyle([
        ("BOX", (i, 0), (i, 0), 1, colors.HexColor("#CBD5E0")) for i in range(6)] + [
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F6FA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    h += [t, Spacer(1, 8), Paragraph("Lots par priorité", styles["h2"]), _graphique(charge)]

    style_tab = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BLEU)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]

    # ---- Lots au planning, par activité ----
    for cle, titre, etape, _col, libelle_date, _champ in planning.ACTIVITES:
        tout = charge[cle]
        au_planning = tout[tout["au_planning"].astype(bool)] if len(tout) else tout
        h.append(Paragraph(f"{titre} — {_n(len(au_planning))} au planning sur {_n(len(tout))} lots "
                           f"« {etape} »", styles["h2"]))
        if au_planning.empty:
            h.append(Paragraph("Aucun lot à mettre au planning cette semaine pour cette activité.", styles["texte"]))
            continue
        lignes = [["Priorité", "Lot", "Opé.", libelle_date, "Âge\n(jours)", "Jours restants\navant le délai",
                   "Échéance\n(+ 12 mois)", "Jours avant\nl'échéance", "Fiche CEE", "Bureau de contrôle"]]
        style = list(style_tab) + [("ALIGN", (2, 0), (7, -1), "RIGHT")]
        for i, r in enumerate(au_planning.itertuples(), start=1):
            lignes.append([NOMS[r.priorite], r.lot, _n(r.nb_ops), _d(r.date_depart), _n(r.age_jours),
                           _n(r.jours_restants), _d(r.echeance), _n(r.jours_avant_echeance),
                           Paragraph(str(r.fiche), styles["cell"]), Paragraph(str(r.bureau), styles["cell"])])
            style.append(("TEXTCOLOR", (0, i), (0, i), colors.HexColor(COULEURS[r.priorite])))
            style.append(("FONT", (0, i), (0, i), "Helvetica-Bold", 8))
        tab = Table(lignes, colWidths=[4.3 * cm, 2.0 * cm, 1.2 * cm, 2.2 * cm, 1.4 * cm, 2.4 * cm, 2.2 * cm,
                                       2.1 * cm, 5.0 * cm, 3.9 * cm], repeatRows=1)
        tab.setStyle(TableStyle(style))
        h.append(tab)
        reste = len(tout) - len(au_planning)
        if reste:
            h.append(Paragraph(f"+ {_n(reste)} lots dans les délais, non listés ici (liste complète dans "
                               "l'export Excel du planning).", styles["texte"]))

    # ---- Relances ----
    def relances(titre, tableau, entetes, valeurs, largeurs, vide):
        h.append(Paragraph(titre, styles["h2"]))
        if tableau.empty:
            h.append(Paragraph(vide, styles["texte"]))
            return
        lignes = [entetes] + [valeurs(r) for r in tableau.head(MAX_LIGNES).itertuples()]
        tab = Table(lignes, colWidths=[l * cm for l in largeurs], repeatRows=1)
        tab.setStyle(TableStyle(style_tab))
        h.append(tab)
        if len(tableau) > MAX_LIGNES:
            h.append(Paragraph(f"Les {MAX_LIGNES} échéances les plus proches sont listées, sur {_n(len(tableau))} "
                               "(liste complète dans l'export Excel du planning).", styles["texte"]))

    P = lambda v: Paragraph(str(v), styles["cell"])  # noqa: E731
    relances(f"Dossiers à relancer sans AH — {_n(len(charge['ah']))} dossiers", charge["ah"],
             ["N° dossier", "DCTS", "Organisme", "Opé.", "Fiches CEE", "Lots associés",
              "Date réelle la plus\nancienne du lot", "Échéance\n(+ 12 mois)"],
             lambda r: [r.dossier, P(r.dcts), P(r.organisme), _n(r.nb_ops), P(r.fiches), P(r.lots),
                        _d(r.drr_plus_ancienne), _d(r.echeance)],
             [2.4, 2.6, 5.6, 1.2, 4.2, 5.6, 2.8, 2.3], "Aucun dossier au Statut AH « Attente AH ».")
    relances(f"Opérations non satisfaisantes à relancer — {_n(len(charge['ko']))} opérations", charge["ko"],
             ["N° dossier", "DCTS", "Organisme", "Fiche CEE", "Lot", "Étape de contrôle",
              "Date réelle la plus\nancienne du lot", "Échéance\n(+ 12 mois)"],
             lambda r: [r.dossier, P(r.dcts), P(r.organisme), r.fiche, r.lot, P(r.etape),
                        _d(r.drr_plus_ancienne), _d(r.echeance)],
             [2.4, 2.6, 6.4, 2.6, 2.4, 5.2, 2.8, 2.3],
             f"Aucune opération au Statut du contrôle « {planning.STATUT_KO} ».")

    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.3 * cm, bottomMargin=1.5 * cm,
                            title=f"Planning de la semaine {resume['semaine_export']}", author=auteur)
    doc.build(h, onFirstPage=pied, onLaterPages=pied)
    return tampon.getvalue()
