"""Werkzeug: aus dem Gespräch ein fertiges PDF machen (30.10.2026) — „mach mir daraus ein PDF mit einer Tabelle“.

Eike diktiert oder kopiert eine Liste, einen Plan oder eine abfotografierte Tafel-Aufgabe; Friday setzt daraus ein A4-Dokument mit
Titel, Abschnitten und optional einer Tabelle. Gesetzt wird mit `fpdf2` (reines Python, Tabellen eingebaut): keine Systempakete,
kein Chromium, keine Netzverbindung. Das passt in das schlanke Image; die feste Version steht in `requirements.txt` (2.8.9, am
30.10.2026 im Container geprüft). Die Datei liegt nur in Fridays eigenem Datenordner (`data/dokumente/<uuid>.pdf`) und wird über
`GET /api/dokumente/<id>` gelesen.

Das Werkzeug ist im Sinne der Freigaben LESEND: es schreibt nichts nach außen, braucht keinen Konnektor und keinen Schreibschalter.
Fremder Text ist erlaubt, weil Eike das PDF selbst sieht — `NICHT_NACH_FREMDTEXT` bleibt deshalb leer. Grenzen stehen im Code, damit
ein Modell weder riesige Tabellen noch seitenlange Abschnitte in den Speicher schreibt.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

from kern import config
from kern.ehrlichkeit.wirkung import wirkt

log = logging.getLogger(__name__)

# Grenzen (Zeichen bzw. Zeilen/Spalten). Sie gelten VOR dem Setzen, damit ein zu großer Auftrag freundlich abgelehnt wird.
MAX_ZEILEN, MAX_SPALTEN = 200, 20
MAX_ABSCHNITTE, MAX_ABSCHNITTS_TEXT = 50, 4000
MAX_TITEL, MAX_UEBERSCHRIFT, MAX_ZELLE = 200, 200, 300
MAX_GESAMT = 50_000

# PDF-Standardschriften können nur Windows-1252; alles andere wird beim Kodieren zu „?“. Umlaute, „ “ – — und € bleiben.


class DokumentFehler(Exception):
    """Ein Auftrag, den das Werkzeug nicht setzen kann — der Text ist für Eike bestimmt."""


def ordner() -> Path:
    return config.daten_ordner() / "dokumente"


def _text(wert) -> str:
    """Zahlen und Text für das PDF lesbar machen; `None` bleibt leer."""
    if wert is None:
        return ""
    if isinstance(wert, bool):
        return "ja" if wert else "nein"
    if isinstance(wert, float):
        return f"{wert:g}".replace(".", ",")          # deutsche Schreibweise: 1,5 statt 1.5
    return str(wert)


def _sauber(wert, grenze: int) -> str:
    """Kontrollzeichen raus, Zeilenumbrüche behalten, auf Windows-1252 begrenzen (Umlaute und € bleiben)."""
    text = _text(wert).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c == "\n" or (ord(c) >= 32 and ord(c) != 127))
    text = text.replace("\t", "    ").replace("\u00a0", " ")
    return text.encode("cp1252", "replace").decode("cp1252")[:grenze]


def _kurz(wert, grenze: int) -> str:
    """Tabellenzelle: kürzen statt den ganzen Auftrag abzulehnen."""
    text = _sauber(wert, grenze + 1)
    return text if len(text) <= grenze else text[: grenze - 1].rstrip() + "…"


def _dateiname(titel: str, wunsch: str) -> str:
    """Sauberer Dateiname ohne Pfad; Umlaute bleiben, Pfadtrenner und Steuerzeichen werden zu „-“."""
    roh = (wunsch or titel or "Dokument").strip()
    name = re.sub(r'[\x00-\x1f\\/:*?"<>|]', "-", _sauber(roh, 160))
    name = re.sub(r"\s+", " ", name).strip(" .-")
    if name.lower().endswith(".pdf"):
        name = name[:-4].rstrip(" .-")
    return (name[:80].rstrip(" .-") or "Dokument") + ".pdf"


def _pruefen(titel, abschnitte, tabelle, dateiname) -> dict:
    """Angaben prüfen und für das Setzen vorbereiten. Jeder Fehler nennt die Grenze auf Deutsch."""
    if not isinstance(titel, str):
        titel = _text(titel)
    titel = titel.strip()
    if not titel:
        raise DokumentFehler("Der Titel fehlt. Sag Eike, wie das Dokument heißen soll.")
    if len(titel) > MAX_TITEL:
        raise DokumentFehler(f"Der Titel ist zu lang (höchstens {MAX_TITEL} Zeichen).")
    titel = _sauber(titel, MAX_TITEL)

    if abschnitte is not None and not isinstance(abschnitte, (list, tuple)):
        raise DokumentFehler("Die Abschnitte müssen als Liste kommen.")
    if abschnitte and len(abschnitte) > MAX_ABSCHNITTE:
        raise DokumentFehler(f"Das sind zu viele Abschnitte (höchstens {MAX_ABSCHNITTE}).")

    sauber = []
    for nummer, roh in enumerate(abschnitte or [], 1):
        if not isinstance(roh, dict):
            raise DokumentFehler(f"Abschnitt {nummer} ist keine Angabe mit Überschrift und Text.")
        ueberschrift = _text(roh.get("ueberschrift")).strip()
        text = _text(roh.get("text")).strip()
        if len(ueberschrift) > MAX_UEBERSCHRIFT:
            raise DokumentFehler(f"Die Überschrift von Abschnitt {nummer} ist zu lang "
                                 f"(höchstens {MAX_UEBERSCHRIFT} Zeichen).")
        if len(text) > MAX_ABSCHNITTS_TEXT:
            raise DokumentFehler(f"Der Text von Abschnitt {nummer} ist zu lang "
                                 f"(höchstens {MAX_ABSCHNITTS_TEXT} Zeichen).")
        if not ueberschrift and not text:
            raise DokumentFehler(f"Abschnitt {nummer} ist leer. Lass ihn weg oder gib ihm Text.")
        sauber.append({"ueberschrift": _sauber(ueberschrift, MAX_UEBERSCHRIFT),
                       "text": _sauber(text, MAX_ABSCHNITTS_TEXT)})

    gedeckt = None
    if tabelle is not None:
        if not isinstance(tabelle, dict):
            raise DokumentFehler("Die Tabelle muss ein Objekt mit „kopf“ und „zeilen“ sein.")
        kopf, zeilen = tabelle.get("kopf"), tabelle.get("zeilen") or []
        if not isinstance(kopf, (list, tuple)) or not kopf:
            raise DokumentFehler("Die Tabelle braucht eine Kopfzeile mit Spaltennamen.")
        if not isinstance(zeilen, (list, tuple)):
            raise DokumentFehler("Die Zeilen der Tabelle müssen eine Liste sein.")
        if len(kopf) > MAX_SPALTEN:
            raise DokumentFehler(f"Die Tabelle hat zu viele Spalten (höchstens {MAX_SPALTEN}).")
        if len(zeilen) > MAX_ZEILEN:
            raise DokumentFehler(f"Die Tabelle hat zu viele Zeilen (höchstens {MAX_ZEILEN}).")
        kopf = [_kurz(k, MAX_ZELLE) for k in kopf]
        breite = len(kopf)
        fertige = []
        for nummer, zeile in enumerate(zeilen, 1):
            if not isinstance(zeile, (list, tuple)):
                raise DokumentFehler(f"Zeile {nummer} der Tabelle ist keine Liste von Zellen.")
            if len(zeile) > breite:
                raise DokumentFehler(f"Zeile {nummer} hat mehr Zellen als die Kopfzeile ({breite} Spalten).")
            fertige.append([_kurz(z, MAX_ZELLE) for z in list(zeile) + [""] * (breite - len(zeile))])
        gedeckt = {"kopf": kopf, "zeilen": fertige}

    if not sauber and gedeckt is None:
        raise DokumentFehler("Es fehlt der Inhalt: Gib mindestens einen Abschnitt oder eine Tabelle an.")

    gesamt = len(titel) + sum(len(a["ueberschrift"]) + len(a["text"]) for a in sauber)
    if gedeckt is not None:
        gesamt += sum(len(z) for z in gedeckt["kopf"]) + sum(len(z) for zeile in gedeckt["zeilen"] for z in zeile)
    if gesamt > MAX_GESAMT:
        raise DokumentFehler(f"Das Dokument ist zu groß (höchstens {MAX_GESAMT} Zeichen insgesamt).")

    if dateiname and not isinstance(dateiname, str):
        raise DokumentFehler("Der Dateiname muss Text sein.")
    return {"titel": titel, "abschnitte": sauber, "tabelle": gedeckt,
            "dateiname": _dateiname(titel, dateiname or "")}


def _bauen(titel: str, abschnitte: list[dict], tabelle: dict | None, dateiname: str) -> bytes:
    """A4-Seiten setzen: Titelband, Abschnitte, optionale Tabelle. Gibt das fertige PDF als Bytes zurück."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.core_fonts_encoding = "windows-1252"      # Umlaute und € in den Standardschriften
    pdf.set_auto_page_break(True, margin=20)
    pdf.set_margins(20, 20, 20)
    pdf.set_title(titel)
    pdf.set_author("Friday")
    pdf.set_creator("Friday")
    pdf.add_page()

    pdf.set_font("helvetica", "B", 18)
    pdf.set_text_color(31, 58, 95)
    pdf.multi_cell(0, 9.5, titel, align="L", new_x="LMARGIN", new_y="NEXT", wrapmode="CHAR")
    pdf.ln(2)
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(110, 118, 132)
    pdf.multi_cell(0, 5, f"Erstellt am {config.jetzt():%d.%m.%Y} von Friday", align="L",
                   new_x="LMARGIN", new_y="NEXT", wrapmode="CHAR")
    y = pdf.get_y() + 2
    pdf.set_draw_color(200, 207, 218)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_y(y + 6)

    for abschnitt in abschnitte:
        if abschnitt["ueberschrift"]:
            pdf.set_font("helvetica", "B", 13)
            pdf.set_text_color(31, 58, 95)
            pdf.multi_cell(0, 7, abschnitt["ueberschrift"], align="L",
                           new_x="LMARGIN", new_y="NEXT", wrapmode="CHAR")
            pdf.ln(1.5)
        if abschnitt["text"]:
            pdf.set_font("helvetica", "", 11)
            pdf.set_text_color(26, 32, 44)
            for absatz in abschnitt["text"].split("\n"):
                if absatz.strip():
                    pdf.multi_cell(0, 6, absatz, align="L", new_x="LMARGIN", new_y="NEXT", wrapmode="CHAR")
                else:
                    pdf.ln(2.5)
            pdf.ln(3)

    if tabelle is not None:
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(26, 32, 44)
        with pdf.table(text_align="LEFT", line_height=6,
                       headings_style=FontFace(emphasis="BOLD", fill_color=(31, 58, 95),
                                               color=(255, 255, 255))) as t:
            kopf = t.row()
            for spalte in tabelle["kopf"]:
                kopf.cell(spalte)
            for zeile in tabelle["zeilen"]:
                reihe = t.row()
                for zelle in zeile:
                    reihe.cell(zelle)
        pdf.ln(3)

    return bytes(pdf.output())


def _schreiben(pfad: Path, daten: bytes) -> None:
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(daten)


def _ablegen(dok_id: str, daten: bytes, titel: str, dateiname: str) -> Path:
    """PDF und Namenszettel ablegen — nur in `data/dokumente/`, Rechte 600."""
    ordner().mkdir(parents=True, exist_ok=True)
    pfad = ordner() / f"{dok_id}.pdf"
    _schreiben(pfad, daten)
    try:
        zettel = json.dumps({"titel": titel, "dateiname": dateiname}, ensure_ascii=False).encode("utf-8")
        _schreiben(pfad.with_suffix(".json"), zettel)
    except OSError:
        log.warning("Namenszettel zum Dokument %s fehlt", dok_id)   # PDF bleibt nutzbar (Standardname)
    return pfad


def _pfad(dok_id: str) -> Path:
    if not isinstance(dok_id, str) or not re.fullmatch(r"[0-9a-f]{32}", dok_id):
        raise DokumentFehler("Dieses Dokument gibt es nicht.")
    pfad = ordner() / f"{dok_id}.pdf"
    if not pfad.is_file():
        raise DokumentFehler("Dieses Dokument gibt es nicht.")
    return pfad


def pfad_und_name(dok_id: str) -> tuple[Path, str]:
    """Für die Route: Datei und sauberer Download-Name (aus dem Namenszettel, sonst „Dokument.pdf“)."""
    pfad = _pfad(dok_id)
    name = "Dokument.pdf"
    try:
        zettel = json.loads(pfad.with_suffix(".json").read_text(encoding="utf-8"))
        if isinstance(zettel, dict) and zettel.get("dateiname"):
            name = _dateiname("Dokument", str(zettel["dateiname"]))
    except (OSError, ValueError):
        pass
    return pfad, name


ERKLAERUNGEN = [{
    "name": "dokument_erstellen",
    "description": ("Erstellt aus Eikes Angaben ein fertiges PDF (A4) mit Titel, Abschnitten und optional einer Tabelle und legt es in der App ab. "
                    "Benutzen, wenn Eike „mach mir daraus ein PDF“, „als Dokument“ oder „mit einer Tabelle“ sagt — z. B. aus einer Tafel-Aufgabe, "
                    "einer Liste oder einem Plan. Gib den Inhalt vollständig und wörtlich weiter; erfinde nichts dazu."),
    "parameters": {"type": "OBJECT", "properties": {
        "titel": {"type": "STRING", "description": "Überschrift des Dokuments, kurz und konkret."},
        "abschnitte": {"type": "ARRAY", "description": "Abschnitte in der gewünschten Reihenfolge (höchstens 50).",
                       "items": {"type": "OBJECT", "properties": {
                           "ueberschrift": {"type": "STRING", "description": "Zwischenüberschrift; leer lassen, wenn keine nötig ist."},
                           "text": {"type": "STRING", "description": "Fließtext des Abschnitts; Zeilenumbrüche bleiben erhalten (höchstens 4000 Zeichen)."}}}},
        "tabelle": {"type": "OBJECT", "description": "Optionale Tabelle (höchstens 200 Zeilen und 20 Spalten).",
                    "properties": {
                        "kopf": {"type": "ARRAY", "description": "Spaltennamen.", "items": {"type": "STRING"}},
                        "zeilen": {"type": "ARRAY", "description": "Zeilen; jede Zeile hat so viele Zellen wie der Kopf.",
                                   "items": {"type": "ARRAY", "items": {"type": "STRING"}}}}},
        "dateiname": {"type": "STRING", "description": "Optional: Wunsch-Dateiname ohne Pfad, z. B. „Mathe-Hausaufgabe.pdf“. Sonst der Titel."}},
        "required": ["titel"]}}]


@wirkt("sonstiges", ok=("Dokument erstellt:",), kopf_ok="Dokument erstellt", nicht_praefix="Nichts erstellt. ",
       detail=lambda a, t: f"„{a.get('titel', '')}“")
def dokument_erstellen(titel: str, abschnitte: list | None = None, tabelle: dict | None = None,
                       dateiname: str = "", aktionen: list | None = None) -> str:
    """Eikes Inhalt als PDF ablegen und den App-Verweis zurückgeben (Details im Modulkopf)."""
    try:
        angaben = _pruefen(titel, abschnitte, tabelle, dateiname)
    except DokumentFehler as fehler:
        return str(fehler)
    dok_id = uuid.uuid4().hex
    try:
        daten = _bauen(**angaben)
        _ablegen(dok_id, daten, angaben["titel"], angaben["dateiname"])
    except Exception as exc:  # noqa: BLE001 — das Modell bekommt nur den freundlichen Satz; die Klasse steht im Log
        log.warning("Dokument %s ließ sich nicht erstellen (%s)", dok_id, type(exc).__name__)
        return "Das PDF ließ sich nicht erstellen. Sag Eike, dass es nicht geklappt hat."

    url = f"/api/dokumente/{dok_id}"
    if aktionen is not None:
        aktionen.append({"typ": "datei", "url": url, "titel": f"{angaben['titel']} (PDF)"[:80]})
    return (f"Dokument erstellt: „{angaben['titel']}“ liegt als PDF bereit ({url}). "
            "Sag Eike, dass er es in der App antippen kann.")


dokument_erstellen.kennt_aktionen = True

# Kein Konnektor, keine Sperre nach Fremdtext: Das PDF bleibt in Fridays eigenem Ordner, Eike sieht es selbst.
FREMDE_WERKZEUGE: set = set()
NICHT_NACH_FREMDTEXT: set = set()
WERKZEUGE = {"dokument_erstellen": dokument_erstellen}
