"""
messagerie.py — Mail hebdomadaire aux managers (envoi depuis Outlook)
=====================================================================

L'application n'envoie rien elle-même et ne se connecte à aucune boîte mail : elle
prépare un fichier « .eml » (destinataires, objet, texte, PDF joint). Un double-clic
l'ouvre dans l'application Outlook de l'ordinateur comme un nouveau message : vous le
relisez, puis vous cliquez sur Envoyer. Aucun mot de passe n'est nécessaire.

Organisation : 1. Adresses  2. Texte du mail (ton formel)  3. Mail prêt pour Outlook
"""
from __future__ import annotations

import html
import re
from email.message import EmailMessage
from email.policy import SMTP
from urllib.parse import quote

import pandas as pd

# ============================================================================
# 1. ADRESSES
# ============================================================================

MOTIF_ADRESSE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def lire_adresses(texte: str) -> tuple[list[str], list[str]]:
    """
    Découpe une liste d'adresses (une par ligne, ou séparées par , ou ;).
    Renvoie (adresses valides sans doublon, adresses invalides).
    """
    valides, invalides = [], []
    for morceau in re.split(r"[\n,;]+", texte or ""):
        a = morceau.strip().strip("<>").strip()
        if not a:
            continue
        if MOTIF_ADRESSE.match(a):
            if a.lower() not in [v.lower() for v in valides]:
                valides.append(a)
        else:
            invalides.append(a)
    return valides, invalides


# ============================================================================
# 2. TEXTE DU MAIL (ton formel)
# ============================================================================

def _n(v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "—"
    return f"{int(v):,}".replace(",", " ")


def _d(iso) -> str:
    return pd.Timestamp(iso).strftime("%d/%m/%Y") if iso else "—"


def _evolution(v, avant, unite="lots") -> str:
    if any(x is None or (not isinstance(x, str) and pd.isna(x)) for x in (v, avant)):
        return ""
    e = int(v) - int(avant)
    if e == 0:
        return " (stable par rapport à l'export précédent)"
    return f" ({'+' if e > 0 else '−'}{abs(e)} {unite} par rapport à l'export précédent)"


def objet_par_defaut(date_iso: str) -> str:
    d = pd.Timestamp(date_iso)
    return (f"Suivi hebdomadaire du contrôle des lots – semaine {d.isocalendar().week} "
            f"(export du {_d(date_iso)})")


def phrase_flux(ligne: dict) -> str:
    """Activité de la période entre deux exports (dates incluses), sinon de la veille ouvrée."""
    if ligne.get("periode_debut") and not pd.isna(ligne.get("flux_periode_lots_creation")):
        p, debut = "flux_periode_", (f"Entre le {_d(ligne['periode_debut'])} et le "
                                     f"{_d(ligne['periode_fin'])} inclus")
    else:
        p, debut = "flux_", f"Au cours de la journée du {_d(ligne.get('date_reference_flux'))}"
    audit = ""
    if not pd.isna(ligne.get(p + "audit_lots_creation")):
        audit = (f" S'y ajoutent, pour la catégorie Rénovation globale audit, "
                 f"{_n(ligne.get(p + 'audit_lots_creation'))} lots créés, "
                 f"{_n(ligne.get(p + 'audit_lots_envoi'))} lots envoyés et "
                 f"{_n(ligne.get(p + 'audit_lots_retour'))} lots restitués.")

    def detail(cle):
        return f"{_n(ligne.get(p + 'ops_' + cle))} opérations, {_n(ligne.get(p + 'vol_' + cle))} MWhc cumac"
    return (f"{debut}, {_n(ligne.get(p + 'lots_creation'))} lots ont été créés ({detail('creation')}), "
            f"{_n(ligne.get(p + 'lots_envoi'))} lots ont été envoyés aux bureaux de contrôle "
            f"({detail('envoi')}) et {_n(ligne.get(p + 'lots_retour'))} lots ont été restitués "
            f"({detail('retour')})." + audit)


def _puce_audit(ligne: dict) -> str:
    """Ligne du mail sur la catégorie « Rénovation globale audit » (vide si non archivée)."""
    v = ligne.get("audit_lots_en_controle")
    if v is None or pd.isna(v):
        return ""
    creation = int(ligne["audit_lots_en_constitution"]) + int(ligne["audit_lots_demande_coordonnees"])
    return (f"• Rénovation globale audit (fiches BAR-TH-145 et BAR-TH-177 sans date réelle de "
            f"réalisation, non comptées ci-dessus) : {_n(creation)} lots en création "
            f"({_n(ligne.get('audit_ops_en_creation'))} opérations), "
            f"{_n(ligne.get('audit_lots_en_controle'))} lots en cours de contrôle "
            f"({_n(ligne.get('audit_ops_en_controle'))} opérations), "
            f"{_n(ligne.get('audit_lots_restitues'))} lots restitués "
            f"({_n(ligne.get('audit_ops_restitues'))} opérations) et "
            f"{_n(ligne.get('audit_ops_eligibles'))} opérations éligibles ;\n")


def corps_par_defaut(ligne: dict, avant: dict | None, signature: str) -> str:
    """Mail formel reprenant les chiffres clés de l'export ; le détail est dans le PDF joint."""
    avant = avant or {}
    creation = None
    if not any(pd.isna(ligne.get(c)) for c in ("lots_en_constitution", "lots_demande_coordonnees")):
        creation = int(ligne["lots_en_constitution"]) + int(ligne["lots_demande_coordonnees"])
    creation_avant = None
    if avant and not any(pd.isna(avant.get(c)) for c in ("lots_en_constitution", "lots_demande_coordonnees")):
        creation_avant = int(avant["lots_en_constitution"]) + int(avant["lots_demande_coordonnees"])

    return f"""Madame, Monsieur,

Veuillez trouver ci-joint le point hebdomadaire relatif au suivi du contrôle des lots, établi à partir de l'export ODICEE du {_d(ligne.get('date_export'))}.

Les principaux éléments à retenir sont les suivants (volumes exprimés en MWh cumac, hors opérations sans date réelle de réalisation ; les dossiers « Rénovation globale audit » sont présentés à part) :

• Lots en création : {_n(creation)} lots ({_n(ligne.get('ops_en_creation'))} opérations, {_n(ligne.get('vol_en_creation'))} MWhc cumac), dont {_n(ligne.get('lots_en_constitution'))} en cours de constitution et {_n(ligne.get('lots_demande_coordonnees'))} en demande de coordonnées{_evolution(creation, creation_avant)} ;
• Lots en cours de contrôle : {_n(ligne.get('lots_en_controle'))} lots ({_n(ligne.get('ops_en_controle'))} opérations, {_n(ligne.get('vol_en_controle'))} MWhc cumac){_evolution(ligne.get('lots_en_controle'), avant.get('lots_en_controle'))}, dont {_n(ligne.get('lots_controle_plus_5_sem'))} lots ({_n(ligne.get('ops_controle_plus_5_sem'))} opérations, {_n(ligne.get('vol_controle_plus_5_sem'))} MWhc cumac) envoyés depuis plus de cinq semaines ;
• Lots restitués restant à traiter : {_n(ligne.get('lots_restitues'))} lots ({_n(ligne.get('ops_restitues'))} opérations, {_n(ligne.get('vol_restitues'))} MWhc cumac){_evolution(ligne.get('lots_restitues'), avant.get('lots_restitues'))} ;
• Opérations éligibles : {_n(ligne.get('ops_eligibles'))} ({_n(ligne.get('vol_eligibles'))} MWhc cumac), dont {_n(ligne.get('ops_integrables_delai_2_mois'))} intégrables dans un lot ({_n(ligne.get('vol_integrables_delai_2_mois'))} MWhc cumac ; délai de contrôle supérieur à deux mois) ;
{_puce_audit(ligne)}• Constitution des lots : {_n(ligne.get('nb_lots_a_creer'))} nouveaux lots proposés pour {_n(ligne.get('nb_operations_a_integrer'))} opérations, dont {_n(ligne.get('nb_operations_ajoutees_lots_existants'))} ajoutées à des lots existants.

{phrase_flux(ligne)}

Le détail des indicateurs, leur évolution ainsi que les graphiques de répartition figurent dans le document PDF joint.

Je reste à votre disposition pour tout complément d'information.

Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.

{signature}""".replace("l'export précédent",
                     f"l'export du {_d(avant.get('date_export'))}" if avant else "l'export précédent")


def texte_vers_html(texte: str) -> str:
    """Version HTML simple du mail (paragraphes et liste à puces)."""
    morceaux, liste = [], []

    def vider_liste():
        if liste:
            morceaux.append("<ul style='margin:0 0 12px 0;padding-left:22px'>"
                            + "".join(f"<li style='margin-bottom:4px'>{l}</li>" for l in liste) + "</ul>")
            liste.clear()

    for bloc in texte.split("\n"):
        ligne = bloc.strip()
        if ligne.startswith(("•", "-", "*")) and len(ligne) > 1:
            liste.append(html.escape(ligne[1:].strip()))
        else:
            vider_liste()
            if ligne:
                morceaux.append(f"<p style='margin:0 0 12px 0'>{html.escape(ligne)}</p>")
    vider_liste()
    return ("<div style='font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;"
            "color:#1A202C'>" + "".join(morceaux) + "</div>")


# ============================================================================
# 3. MAIL PRÊT À OUVRIR DANS OUTLOOK (application de bureau)
# ============================================================================

def construire_message(a: list[str], cc: list[str], objet: str, corps: str,
                       pdf: bytes | None, nom_pdf: str) -> EmailMessage:
    """
    Mail « non envoyé » : l'en-tête « X-Unsent: 1 » demande à Outlook de l'ouvrir comme un
    nouveau message à relire (et non comme un message reçu). Pas d'expéditeur ni de date :
    Outlook utilise votre compte et la date du jour au moment de l'envoi.
    """
    msg = EmailMessage()
    msg["To"] = ", ".join(a)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = objet
    msg["X-Unsent"] = "1"
    msg.set_content(corps)
    msg.add_alternative(texte_vers_html(corps), subtype="html")
    if pdf:
        msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=nom_pdf)
    return msg


def fichier_outlook(a: list[str], cc: list[str], objet: str, corps: str,
                    pdf: bytes | None, nom_pdf: str) -> bytes:
    """Contenu du fichier .eml à télécharger puis à ouvrir (double-clic) dans Outlook."""
    return construire_message(a, cc, objet, corps, pdf, nom_pdf).as_bytes(policy=SMTP)


LONGUEUR_MAX_LIEN = 1900   # au-delà, certains postes Windows coupent le lien


def lien_mailto(a: list[str], cc: list[str], objet: str, corps: str) -> tuple[str, bool]:
    """
    Lien « mailto: » qui ouvre un nouveau message dans la messagerie de l'ordinateur
    (solution de secours : un lien ne peut pas porter de pièce jointe).
    Renvoie (lien, texte inclus ?) : si le texte est trop long pour un lien, seuls les
    destinataires et l'objet sont pré-remplis.
    """
    base = "mailto:" + ";".join(a) + "?subject=" + quote(objet, safe="")
    if cc:
        base += "&cc=" + quote(";".join(cc), safe="@;")
    complet = base + "&body=" + quote(corps.replace("\n", "\r\n"), safe="")
    return (complet, True) if len(complet) <= LONGUEUR_MAX_LIEN else (base, False)
