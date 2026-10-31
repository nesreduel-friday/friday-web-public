"""Die Ausarbeitung eines fertigen Aufgabenblatt-Auftrags als PDF (15.10.2026, Etappe B).

Aufbau: farbiges Kopfband (Titel, Blattname, Datum), Chips mit der Übersicht (Aufgaben · sicher · bitte prüfen), dann je Aufgabe eine Karte mit
Farbbalken (grün = sicher, gelb = bitte prüfen): Aufgabe, **Lösung** als hervorgehobenes Feld, Rechenweg, Quelle. Karten bleiben zusammen und brechen
nur um, wenn eine allein länger als eine Seite ist. Jede Seite trägt eine Fußzeile. Am Ende hängt jede Seite des Original-Blatts, auf die sich eine
Lösung bezieht, genau EINMAL als Bild an.
Gesetzt wird mit `satz.py` (PDF-Standardschriften Helvetica und Symbol: Mathe-Zeichen, Hoch-/Tiefstellung) — keine neuen Abhängigkeiten.
Grenze: Ausschnitte per Koordinaten und eigene Skizzen gibt es noch nicht — die ganze Blattseite ist der Beleg."""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter

from kern import config
from kern.aufgabenblatt import satz, speicher

# Seite und Satzspiegel (pt)
H = satz.A4[1]
L, R = 48.0, satz.A4[0] - 48.0
BAND, OBEN_FOLGE, UNTEN = 96.0, 48.0, 54.0
BALKEN, INNEN, ABSTAND = 5.0, 16.0, 10.0
TEXT_X = L + BALKEN + INNEN
TEXT_B = R - INNEN - TEXT_X                  # Breite des Textes in einer Karte
FELD_X0, FELD_X1 = L + BALKEN + 10.0, R - 10.0   # Lösungsfeld (weiß, etwas schmaler als die Karte)
RENDER_SKALA = 1.6

# Farben
BLAU, KARTE, WEISS = "#1F3A5F", "#F2F5FA", "#FFFFFF"
GRUEN, GELB, ROT = "#2E8B57", "#E0A100", "#C0392B"
TEXT, GRAU, HELLBLAU = "#1A202C", "#5A6578", "#BFD0EA"


def pfad(aid: int) -> Path:
    return config.daten_ordner() / "aufgabenblatt" / "ausarbeitung" / f"ausarbeitung-{int(aid)}.pdf"


def _nummer(l: dict) -> str:
    return re.sub(r"^aufgabe\s*:?\s*", "", str(l["nummer"]), flags=re.I) or "–"          # ältere Aufträge tragen „Aufgabe 1“ noch als Nummer


# ---------- Karten aus Zeilen-Bausteinen ----------
def _zeilen(text: str, groesse: float, farbe: str, fett=False, kursiv=False, bg=None, einzug: float = 0.0) -> list[dict]:
    return [{"typ": "zeile", "runs": z, "size": groesse, "farbe": farbe, "bg": bg, "x": TEXT_X + einzug,
             "h": groesse * (0.9 if not z else 1.5)}
            for z in satz.umbrechen(text, groesse, TEXT_B - 2 * einzug, fett, kursiv)]


def _luecke(h: float, bg=None) -> dict:
    return {"typ": "luecke", "h": h, "bg": bg}


def _karte_aufgabe(l: dict) -> tuple[str, list[dict]]:
    sicher = bool(l["sicher"])
    items = [_luecke(10), {"typ": "kopf", "h": 30, "nummer": _nummer(l), "seite": l["seite"], "sicher": sicher}]
    if l["aufgabe"]:
        items += _zeilen(l["aufgabe"], 10.5, GRAU, kursiv=True) + [_luecke(8)]
    items += [{"typ": "zeile", "runs": satz.laeufe("LÖSUNG", fett=True), "size": 8, "farbe": GRAU, "bg": None, "x": TEXT_X, "h": 14}]
    items += [_luecke(6, WEISS)] + _zeilen(l["loesung"], 13, TEXT, fett=True, bg=WEISS, einzug=10) + [_luecke(6, WEISS)]
    if l["weg"]:
        items += [_luecke(10), {"typ": "zeile", "runs": satz.laeufe("RECHENWEG", fett=True), "size": 8, "farbe": GRAU, "bg": None, "x": TEXT_X, "h": 14}]
        items += _zeilen(satz.schritte_trennen(l["weg"]), 10.5, "#1F2937")
    if l["quelle"]:
        items += [_luecke(6)] + _zeilen("Quelle: " + l["quelle"], 8.5, GRAU)
    return (GRUEN if sicher else GELB), items + [_luecke(12)]


def _karte_offen(text: str) -> tuple[str, list[dict]]:
    return ROT, ([_luecke(10), {"typ": "zeile", "runs": satz.laeufe("NOCH OFFEN", fett=True), "size": 8, "farbe": ROT, "bg": None, "x": TEXT_X, "h": 14}]
                 + _zeilen(text, 10.5, TEXT) + [_luecke(12)])


# ---------- Seiten verteilen ----------
def _verteilen(karten: list[tuple[str, list[dict]]], erste: float, voll: float) -> list[list[tuple[str, list[dict]]]]:
    """Karten → Seiten (je Seite eine Liste von Stücken `(farbe, zeilen)`). Eine Karte bleibt zusammen; passt sie nicht mehr, beginnt sie die nächste
    Seite. Ist sie allein länger als eine Seite, läuft sie zeilenweise über (die Überschrift bleibt nie allein am Seitenende)."""
    seiten: list[list] = [[]]
    frei = erste
    for farbe, items in karten:
        gesamt = sum(i["h"] for i in items)
        if seiten[-1] and gesamt > frei and gesamt <= voll:
            seiten.append([])
            frei = voll
        rest, anfang = items, True
        while rest:
            n, h = 0, 0.0
            while n < len(rest) and h + rest[n]["h"] <= frei:
                h += rest[n]["h"]
                n += 1
            if seiten[-1] and (n == 0 or (anfang and n <= 3 and n < len(rest))):
                seiten.append([])
                frei = voll
                continue
            if n == 0:
                n, h = 1, rest[0]["h"]
            seiten[-1].append((farbe, rest[:n]))
            frei -= h + ABSTAND
            rest, anfang = rest[n:], False
            if rest:
                seiten.append([])
                frei = voll
    return seiten


# ---------- Zeichnen ----------
def _grundlinie(y_oben: float, h: float, groesse: float) -> float:
    return y_oben - (h + groesse * 0.7) / 2


def _chip(s: satz.Seite, x: float, y: float, text: str, bg: str, fg: str, groesse: float = 9) -> float:
    """Chip mit linker unterer Ecke (x, y); gibt die rechte Kante zurück."""
    runs = satz.laeufe(text, fett=True)
    b = satz.breite(runs, groesse) + 16
    s.rechteck(x, y, b, groesse + 9, bg, radius=(groesse + 9) / 2)
    s.text(x + 8, y + 4.5, runs, groesse, fg)
    return x + b


def _stueck(s: satz.Seite, y_oben: float, farbe: str, items: list[dict]) -> float:
    gesamt = sum(i["h"] for i in items)
    s.rechteck(L, y_oben - gesamt, R - L, gesamt, KARTE, radius=7)
    s.rechteck(L, y_oben - gesamt + 7, BALKEN, max(gesamt - 14, 1), farbe)
    y = y_oben
    for it in items:
        h = it["h"]
        if it.get("bg"):
            s.rechteck(FELD_X0, y - h - 0.3, FELD_X1 - FELD_X0, h + 0.6, it["bg"])
        if it["typ"] == "kopf":
            runs = satz.laeufe(f"Aufgabe {it['nummer']}", fett=True)
            ende = s.text(TEXT_X, y - 21, runs, 14, BLAU)
            if it["seite"]:
                s.text(ende + 8, y - 21, satz.laeufe(f"Seite {it['seite']}"), 9, GRAU)
            text, bg, fg = ("sicher", GRUEN, WEISS) if it["sicher"] else ("bitte prüfen", GELB, TEXT)
            b = satz.breite(satz.laeufe(text, fett=True), 9) + 16
            _chip(s, R - INNEN - b, y - 25, text, bg, fg)
        elif it["typ"] == "zeile" and it["runs"]:
            s.text(it["x"], _grundlinie(y, h, it["size"]), it["runs"], it["size"], it["farbe"])
        y -= h
    return y_oben - gesamt


def _kopfband(s: satz.Seite, blatt: str, a: dict, anzahl: int) -> None:
    """Kopfband und Chips der ersten Seite."""
    s.rechteck(0, H - BAND, satz.A4[0], BAND, BLAU)
    s.text(L, H - 44, satz.laeufe("Ausarbeitung", fett=True), 24, WEISS)
    name = satz.umbrechen(blatt, 12.5, R - L)[0]
    s.text(L, H - 66, name, 12.5, WEISS)
    s.text(L, H - 83, satz.laeufe(f"Erstellt am {config.jetzt():%d.%m.%Y}  ·  von Friday"), 9, HELLBLAU)
    sicher = sum(1 for l in a["loesungen"] if l["sicher"])
    y = H - BAND - 28
    x = _chip(s, L, y, f"{anzahl} Aufgabe{'n' if anzahl != 1 else ''}", "#E2E8F0", TEXT) + 8
    if sicher:
        x = _chip(s, x, y, f"{sicher} sicher", GRUEN, WEISS) + 8
    if anzahl - sicher:
        _chip(s, x, y, f"{anzahl - sicher} bitte prüfen", GELB, TEXT)


def _blattseite_als_pdfseite(doc: "pdfium.PdfDocument", nummer: int) -> PdfReader | None:
    """Seite `nummer` (1-basiert) des Blatts als einseitiges PDF aus einem Bild; None, wenn es die Seite nicht gibt."""
    if not 1 <= nummer <= len(doc):
        return None
    bild = doc[nummer - 1].render(scale=RENDER_SKALA).to_pil().convert("RGB")
    puffer = io.BytesIO()
    bild.save(puffer, format="PDF", resolution=72 * RENDER_SKALA)
    puffer.seek(0)
    return PdfReader(puffer)


def bauen(aid: int) -> bytes:
    a = speicher.holen(aid)
    if a is None:
        raise speicher.AufgabenFehler("Diesen Auftrag gibt es nicht.")
    if a["status"] != "fertig":
        raise speicher.AufgabenFehler("Der Auftrag ist noch nicht fertig — eine Ausarbeitung gibt es erst danach.")
    blatt = (speicher.blatt_holen(a["blatt"]) or {}).get("name", "Aufgabenblatt")
    original = pdfium.PdfDocument(speicher.blatt_bytes(a["blatt"]))
    try:
        anhang = [n for n in sorted({l["seite"] for l in a["loesungen"] if l["seite"]}) if 1 <= n <= len(original)]
        karten = ([_karte_offen(a["offen"])] if a["offen"] else []) + [_karte_aufgabe(l) for l in a["loesungen"]]
        start = H - BAND - 28 - 14 - 20                       # unterhalb von Band und Chips
        seiten = _verteilen(karten, start - UNTEN, H - OBEN_FOLGE - UNTEN)
        gesamt = len(seiten) + len(anhang)
        satzseiten = []
        for i, stuecke in enumerate(seiten):
            s = satz.Seite()
            if i == 0:
                _kopfband(s, blatt, a, len(a["loesungen"]))
                y = start
            else:
                y = H - OBEN_FOLGE
            for farbe, items in stuecke:
                y = _stueck(s, y, farbe, items) - ABSTAND
            s.linie(L, 42, R, 42, "#D5DBE5")
            s.text(L, 28, satz.laeufe(f"Friday · Ausarbeitung · {blatt[:70]}"), 8, GRAU)
            nr = satz.laeufe(f"Seite {i + 1} von {gesamt}")
            s.text(R - satz.breite(nr, 8), 28, nr, 8, GRAU)
            satzseiten.append(s)
        w = PdfWriter()
        satz.schreibe_seiten(w, satzseiten)
        for nummer in anhang:
            teil = _blattseite_als_pdfseite(original, nummer)
            if teil is not None:
                w.add_page(teil.pages[0])
    finally:
        original.close()
    puffer = io.BytesIO()
    w.write(puffer)
    return puffer.getvalue()


def ablegen(aid: int) -> Path:
    """Baut die Ausarbeitung und legt sie ab (Rechte 600); gibt den Pfad zurück."""
    daten = bauen(aid)
    p = pfad(aid)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(daten)
    return p
