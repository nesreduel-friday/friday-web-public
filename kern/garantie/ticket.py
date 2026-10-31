"""Das Ticket lesen — Eike lädt es hoch, Friday übernimmt Art und Gültigkeit.

Eikes Wunsch (15.09.2026): „Ticket jeden Monat neu hochladen, die Daten werden
daraus übernommen.“ Gelesen wird mit Gemini (Hintergrund-Schlüssel, wie bei den
Elternbriefen), als Bild oder PDF, mit festem Antwortschema.

Was davon abhängt, steht in den Bedingungen (Stand 01.06.2026): Die Garantie gilt
nur mit ÜSTRA-Fahrausweis — ausgenommen u. a. Deutschlandtickets, die nicht von der
ÜSTRA ausgegeben wurden, das Job-100-Ticket, die Schulfahrkarte und ÜSTRA easy.
Deshalb wird auch der Herausgeber gelesen.

Nur was auf dem Ticket STEHT: Ist ein Datum nicht lesbar, bleibt es leer, statt
geraten zu werden. Eike kann jede erkannte Angabe in der App korrigieren.

Papier (21.09.2026): Manche Freunde haben die Jugendnetzkarte nicht in der App, sondern als
Monatswertmarke mit Kundenkarte. Auf der Wertmarke steht die Kundenkartennummer von Hand,
die Ticketnummer gedruckt mit Trennzeichen. Friday merkt sich die Form (`form`), die Nummer
wie gedruckt (`nummer_gedruckt`) und ob nur die Kundenkarte zu sehen war; abgeschickt wird
ein Papierticket erst, wenn Eike die Nummern mit dem Original verglichen hat
(`muss_verglichen_werden`, geprüft in `abschicken.pruefen`).
"""

from __future__ import annotations

import base64
import re
from datetime import date

from kern.dienste.garantie import FAHRKARTEN
from kern.schule import briefe

SCHEMA = {"type": "OBJECT", "properties": {
    "ist_fahrkarte": {"type": "BOOLEAN"},
    "art": {"type": "STRING", "enum": FAHRKARTEN},
    "bezeichnung": {"type": "STRING"},
    "herausgeber": {"type": "STRING"},
    "gueltig_von": {"type": "STRING"},
    "gueltig_bis": {"type": "STRING"},
    "inhaber": {"type": "STRING"},
    "abo_nummer": {"type": "STRING"},
    "kundenkarte": {"type": "STRING"},
    "ticketnummer": {"type": "STRING"},
    "preis_euro": {"type": "NUMBER"},
    "form": {"type": "STRING", "enum": ["digital", "papier", "unklar"]},
    "ticketnummer_gedruckt": {"type": "STRING"},
    "ist_kundenkarte": {"type": "BOOLEAN"},
    "kundenkarte_handschrift": {"type": "BOOLEAN"},
    "zweifel": {"type": "STRING"},
}, "required": ["ist_fahrkarte", "art", "gueltig_von", "gueltig_bis"]}

ANWEISUNG = (
    "Du liest eine Fahrkarte des Nahverkehrs (Foto, Screenshot oder PDF) und gibst NUR zurück, was darauf "
    "steht. art: eine der erlaubten Arten; ein Deutschlandticket ist immer „Deutschlandticket“, eine "
    "Monats- oder Jahreskarte „Monatskarte“. bezeichnung: der genaue Name des Tickets. herausgeber: das "
    "ausgebende Unternehmen, so wie es auf dem Ticket steht (z.B. ÜSTRA, GVH, Deutsche Bahn); leer, wenn es "
    "nicht draufsteht. gueltig_von und gueltig_bis als JJJJ-MM-TT. Steht nur ein Monat da (z.B. "
    "„Gültig im September 2026“), ist das der erste bis letzte Tag dieses Monats. Ist ein Datum nicht "
    "lesbar, lass es LEER — nicht raten. inhaber: der aufgedruckte Name oder leer. abo_nummer: die Abo- oder Kundennummer "
    "(nur Ziffern), leer, wenn keine draufsteht. kundenkarte: die Kundenkartennummer, genau 6 Ziffern (bei "
    "Schüler- und Azubi-Monatskarten wie der Jugendnetzkarte), sonst leer. ticketnummer: die Nummer des "
    "Monatstickets — in der ÜSTRA-App steht sie oben in der Kopfzeile hinter dem Datum (z. B. 2608127024). "
    "form: „digital“ (Screenshot aus einer App, Handy-Ticket, PDF-Ticket mit QR-Code), „papier“ (Foto einer "
    "gedruckten Karte oder Wertmarke), sonst „unklar“. PAPIER: Die Jugendnetzkarte auf Papier ist ein Heftchen — links die "
    "Kundenkarte mit Lichtbild und gedruckter „Kundenkarte Nr.“, rechts die Monatswertmarke („Jugendnetzkarte“, Preis, "
    "„Gültig: TT.MM.JJJJ - TT.MM.JJJJ“, Geltungsbereich). Auf der Wertmarke steht hinter „Kundenkarten-Nr.:“ die Nummer "
    "HANDSCHRIFTLICH (dann kundenkarte_handschrift = true) — das ist NIE die Ticketnummer; lies die Kundenkartennummer "
    "lieber von der gedruckten Kundenkarte. Ganz unten auf der Wertmarke steht eine gedruckte Zeile aus mehreren "
    "Zahlengruppen, danach Verkaufsdatum und Uhrzeit (z. B. „63256   713801   15.09.26   07:46“). ticketnummer_gedruckt: "
    "genau diese Zahlengruppen OHNE Datum und Uhrzeit, mit den Abständen, wie sie dastehen (z. B. „63256 713801“); "
    "ticketnummer: dieselben Ziffern ohne Abstände in derselben Reihenfolge (z. B. „63256713801“). Nichts weglassen, "
    "nichts dazuerfinden. Ist auf dem Bild nur die "
    "Kundenkarte (Lichtbild, Name, Kundenkartennummer, keine Monatsmarke), dann ist_kundenkarte = true, art „Sonstige“, "
    "gueltig_von und gueltig_bis LEER (ein Ablaufdatum der Kundenkarte ist keine Gültigkeit eines Tickets) und "
    "ticketnummer leer. Ist eine Ziffer nicht sicher lesbar (Handschrift, Knick, Unschärfe), lass die Nummer LEER und "
    "schreib es in zweifel. preis_euro: der Preis der Karte in Euro (bei Monatskarten der Gesamtpreis), -1, wenn keiner "
    "draufsteht. zweifel: kurz, was unsicher war, sonst leer. Ist es gar keine Fahrkarte, ist_fahrkarte = false."
)

_DATUM = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# So prüft es das Formular (elma.gvh.de, `config/settings`, gemessen am 20.09.2026): Kundenkarte genau 6 Ziffern,
# Ticketnummer `^[A-Za-z0-9]{1,255}$` — irgendeine Folge aus Buchstaben und Ziffern (die App-Nummer 2608127024 gilt).
# Ein zuerst vermutetes Muster „36-NN.NNN.NNN“ stammte aus einer Beispielstruktur im Skript und war falsch.
KUNDENKARTE = re.compile(r"^\d{6}$")
TICKETNUMMER = re.compile(r"^[A-Za-z0-9]{1,255}$")
FORMEN = ("digital", "papier")        # „unklar“ bleibt leer

# Fahrkarten, deren Abgabe mehr verlangt als das Deutschlandticket (gemessen am 20.09.2026 im Formular:
# Monatskarte → „SchülerInnen & Azubis“, Kundenkarten- UND Ticketnummer Pflicht, Erstattung höchstens der Kartenpreis
# je Monat). Am 20.09.2026 bis zum Abgabepunkt geprüft (mit Sperre, nichts gesendet): Jedes Feld, das das Formular
# verlangt, kann Friday liefern — darum `automatisch: True`. Das Abschicken selbst hängt weiter an Freigabe,
# Knopfdruck und der vollständigen Prüfung in `abschicken.pruefen`.
SORTEN = (
    (re.compile(r"jugendnetz", re.I), {"name": "Jugendnetzkarte", "art": "Monatskarte",
                                       "typ": "Monatskarte SchülerInnen & Azubis", "detail": "Jugendnetzkarte"}),
    (re.compile(r"ausbildungsnetz", re.I), {"name": "Ausbildungsnetzkarte", "art": "Monatskarte",
                                            "typ": "Monatskarte SchülerInnen & Azubis", "detail": "Ausbildungsnetzkarte"}),
)
FELDNAMEN = {"kundenkarte": "Kundenkartennummer", "ticketnummer": "Ticketnummer"}


def sorte(ticket: dict | None) -> dict | None:
    """Eine Fahrkarte mit eigenen Pflichtangaben (Kundenkarten- und Ticketnummer) — oder None."""
    if not ticket:
        return None
    text = " ".join(str(ticket.get(k) or "") for k in ("bezeichnung", "art"))
    for muster, s in SORTEN:
        if muster.search(text):
            return {**s, "automatisch": True, "braucht": ("kundenkarte", "ticketnummer")}
    return None


def fehlende_felder(ticket: dict | None, angaben: dict | None = None) -> list[str]:
    """Was diese Fahrkartenart im Formular verlangt und noch fehlt (Klartext). Die Kundenkartennummer gehört zur Person und
    steht dann in ihren Angaben; die Ticketnummer gehört zum Monatsticket."""
    s = sorte(ticket)
    hat = lambda f: (ticket or {}).get(f) or (angaben or {}).get(f)
    return [FELDNAMEN[f] for f in (s["braucht"] if s else ()) if not hat(f)]


def _datum(text: str | None) -> str:
    text = (text or "").strip()
    if not _DATUM.match(text):
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def ist_uestra(herausgeber: str) -> bool | None:
    """Vom ÜSTRA-Verbund ausgegeben? None heißt: steht nicht drauf, keine Aussage."""
    h = (herausgeber or "").casefold()
    if not h:
        return None
    return any(w in h for w in ("üstra", "uestra", "ustra", "gvh", "regiobus"))


async def auslesen(mime: str, inhalt: bytes) -> dict:
    teile = [{"inlineData": {"mimeType": mime, "data": base64.b64encode(inhalt).decode()}},
             {"text": "Lies diese Fahrkarte."}]
    roh, _modell = await briefe._gemini(teile, anweisung=ANWEISUNG, schema=SCHEMA)
    von, bis = _datum(roh.get("gueltig_von")), _datum(roh.get("gueltig_bis"))
    if von and bis and bis < von:
        von, bis = "", ""                                  # widersprüchlich: lieber leer als falsch
    kundenkarte_allein = bool(roh.get("ist_kundenkarte"))
    if kundenkarte_allein:
        # Nur die Kundenkarte: Ihr Ablaufdatum darf nie als Gültigkeit eines Monatstickets durchgehen (sonst gälte sie
        # still als Ticket für jeden Tag bis dahin), und eine Ticketnummer hat sie nicht.
        von = bis = ""
        roh = {**roh, "ticketnummer": "", "ticketnummer_gedruckt": ""}
    form = roh.get("form") if roh.get("form") in FORMEN else ""
    zweifel = str(roh.get("zweifel") or "")[:200]
    gelesen, gedruckt = nummer(roh.get("ticketnummer")), nummer(roh.get("ticketnummer_gedruckt"))
    if gelesen and gedruckt and gelesen != gedruckt:
        # Zwei Lesarten derselben Nummer, die nicht zusammenpassen: keine davon nehmen.
        roh = {**roh, "ticketnummer": ""}
        zweifel = ("Die Ticketnummer war nicht eindeutig lesbar. " + zweifel)[:200]
    return {"ist_fahrkarte": bool(roh.get("ist_fahrkarte")),
            "art": roh.get("art") if roh.get("art") in FAHRKARTEN else "Sonstige",
            "bezeichnung": str(roh.get("bezeichnung") or "")[:120],
            "herausgeber": str(roh.get("herausgeber") or "")[:80],
            "gueltig_von": von, "gueltig_bis": bis,
            "inhaber": str(roh.get("inhaber") or "")[:80],
            "abo_nummer": abo if re.fullmatch(r"\d{4,12}", abo := re.sub(r"\s", "", str(roh.get("abo_nummer") or ""))) else "",
            "kundenkarte": karte if KUNDENKARTE.match(karte := re.sub(r"\s", "", str(roh.get("kundenkarte") or ""))) else "",
            "ticketnummer": n if TICKETNUMMER.match(n := nummer(roh.get("ticketnummer"))) else "",
            "preis": preis if isinstance(preis := roh.get("preis_euro"), (int, float)) and 0 < preis <= 500 else None,
            "form": form, "nummer_gedruckt": str(roh.get("ticketnummer_gedruckt") or "").strip()[:60],
            "ist_kundenkarte": kundenkarte_allein, "kundenkarte_handschrift": bool(roh.get("kundenkarte_handschrift")),
            "zweifel": zweifel}


def nummer(text: str | None) -> str:
    """Eine Ticketnummer so, wie das Formular sie nimmt: nur Buchstaben und Ziffern, in derselben Reihenfolge. Auf Papier
    steht sie mit Trennzeichen („36-12.345.678“), in der App ohne — gemeint ist dieselbe Folge."""
    return re.sub(r"[\s\-./]", "", str(text or ""))


def muss_verglichen_werden(t: dict | None) -> str:
    """Warum ein Mensch die Nummern dieses Tickets mit dem Original vergleichen muss, bevor Friday es abschickt — oder "".
    Papier (Handschrift, Knicke, Fotos) liest sich schlechter als ein App-Screenshot; bei Karten, deren Nummern ins Formular
    gehen, lieber einmal hinsehen als eine falsche Nummer einreichen."""
    if not t or not sorte(t) or t.get("nummern_geprueft"):
        return ""
    if t.get("form") == "papier":
        return "Papierticket"
    if t.get("kundenkarte_handschrift"):
        return "handschriftliche Kundenkartennummer"
    if t.get("form") != "digital":
        # Unklar, ob App oder Papier — auch jedes Ticket, das vor dem 21.09.2026 gelesen wurde: lieber einmal hinsehen.
        return "Form des Tickets unklar (Papier oder App?)"
    return ""
