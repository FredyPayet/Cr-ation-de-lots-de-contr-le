"""
messagerie.py — Mail hebdomadaire aux managers (brouillon Gmail)
================================================================

Le mail est déposé dans les BROUILLONS de la boîte Gmail configurée : rien n'est
envoyé automatiquement. Vous l'ouvrez dans Gmail, le relisez, puis cliquez sur Envoyer.

Connexion : IMAP de Gmail avec un « mot de passe d'application » (16 lettres), indiqué
dans les secrets Streamlit (section [gmail], voir README.md).

Organisation : 1. Adresses  2. Texte du mail (ton formel)  3. Création du brouillon
"""
from __future__ import annotations

import html
import imaplib
import re
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import pandas as pd

SERVEUR_IMAP = "imap.gmail.com"


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
    def detail(cle):
        return f"{_n(ligne.get(p + 'ops_' + cle))} opérations, {_n(ligne.get(p + 'vol_' + cle))} MWhc cumac"
    return (f"{debut}, {_n(ligne.get(p + 'lots_creation'))} lots ont été créés ({detail('creation')}), "
            f"{_n(ligne.get(p + 'lots_envoi'))} lots ont été envoyés aux bureaux de contrôle "
            f"({detail('envoi')}) et {_n(ligne.get(p + 'lots_retour'))} lots ont été restitués "
            f"({detail('retour')}).")


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

Les principaux éléments à retenir sont les suivants (volumes exprimés en MWh cumac, hors opérations sans date réelle de réalisation) :

• Lots en création : {_n(creation)} lots ({_n(ligne.get('ops_en_creation'))} opérations, {_n(ligne.get('vol_en_creation'))} MWhc cumac), dont {_n(ligne.get('lots_en_constitution'))} en cours de constitution et {_n(ligne.get('lots_demande_coordonnees'))} en demande de coordonnées{_evolution(creation, creation_avant)} ;
• Lots en cours de contrôle : {_n(ligne.get('lots_en_controle'))} lots ({_n(ligne.get('ops_en_controle'))} opérations, {_n(ligne.get('vol_en_controle'))} MWhc cumac){_evolution(ligne.get('lots_en_controle'), avant.get('lots_en_controle'))}, dont {_n(ligne.get('lots_controle_plus_5_sem'))} lots ({_n(ligne.get('ops_controle_plus_5_sem'))} opérations, {_n(ligne.get('vol_controle_plus_5_sem'))} MWhc cumac) envoyés depuis plus de cinq semaines ;
• Lots restitués restant à traiter : {_n(ligne.get('lots_restitues'))} lots ({_n(ligne.get('ops_restitues'))} opérations, {_n(ligne.get('vol_restitues'))} MWhc cumac){_evolution(ligne.get('lots_restitues'), avant.get('lots_restitues'))} ;
• Opérations éligibles : {_n(ligne.get('ops_eligibles'))} ({_n(ligne.get('vol_eligibles'))} MWhc cumac), dont {_n(ligne.get('ops_integrables_delai_2_mois'))} intégrables dans un lot ({_n(ligne.get('vol_integrables_delai_2_mois'))} MWhc cumac ; délai de contrôle supérieur à deux mois) ;
• Constitution des lots : {_n(ligne.get('nb_lots_a_creer'))} nouveaux lots proposés pour {_n(ligne.get('nb_operations_a_integrer'))} opérations, dont {_n(ligne.get('nb_operations_ajoutees_lots_existants'))} ajoutées à des lots existants.

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
# 3. CRÉATION DU BROUILLON DANS GMAIL
# ============================================================================

class ErreurMessagerie(Exception):
    """Erreur lisible par l'utilisateur."""


def construire_message(expediteur: str, a: list[str], cc: list[str], objet: str, corps: str,
                       pdf: bytes | None, nom_pdf: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = expediteur
    msg["To"] = ", ".join(a)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = objet
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=expediteur.split("@")[-1])
    msg.set_content(corps)
    msg.add_alternative(texte_vers_html(corps), subtype="html")
    if pdf:
        msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=nom_pdf)
    return msg


def _dossier_brouillons(imap) -> str:
    """Nom du dossier Brouillons (« [Gmail]/Drafts » ou « [Gmail]/Brouillons » selon la langue)."""
    typ, dossiers = imap.list()
    if typ == "OK":
        for brut in dossiers or []:
            ligne = brut.decode(errors="replace") if isinstance(brut, bytes) else str(brut)
            if "\\Drafts" in ligne:
                m = re.match(r'^\((?P<attributs>[^)]*)\) (?P<sep>"[^"]*"|NIL) (?P<nom>.+)$', ligne)
                if m:
                    nom = m.group("nom").strip()
                    return nom if nom.startswith('"') else f'"{nom}"'
    return '"[Gmail]/Drafts"'


def creer_brouillon(adresse: str, mot_de_passe: str, message: EmailMessage) -> str:
    """Dépose le message dans les Brouillons de la boîte Gmail. Renvoie le nom du dossier."""
    try:
        imap = imaplib.IMAP4_SSL(SERVEUR_IMAP, 993, timeout=30)
    except OSError as e:
        raise ErreurMessagerie(f"Impossible de joindre Gmail ({e}). Réessayez dans un instant.") from e
    try:
        try:
            imap.login(adresse, mot_de_passe.replace(" ", ""))
        except imaplib.IMAP4.error as e:
            raise ErreurMessagerie(
                f"Gmail refuse la connexion de « {adresse} ». Vérifiez le mot de passe "
                "d'application (16 lettres, voir README.md) : il faut que la validation en deux "
                "étapes soit activée sur ce compte et que le mot de passe ait été créé pour ce "
                f"compte. (Détail : {e})") from e
        dossier = _dossier_brouillons(imap)
        typ, retour = imap.append(dossier, r"(\Draft)", imaplib.Time2Internaldate(time.time()),
                                  message.as_bytes())
        if typ != "OK":
            raise ErreurMessagerie(f"Gmail n'a pas accepté le brouillon ({retour}).")
        return dossier.strip('"')
    finally:
        try:
            imap.logout()
        except Exception:
            pass
