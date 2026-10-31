"""Noten (Schule → Noten): alles eintragen, was zählt — und sehen, wo man steht.

Aufgebaut wie die Oberstufe in Niedersachsen: Qualifikationsphase mit vier
Semestern (12/1, 12/2, 13/1, 13/2). Jedes Fach ist in einem Teil davon belegt
(das Seminarfach etwa nur in drei), hat eine Rolle (Prüfungsfach P1–P3 auf
erhöhtem Niveau, P4/P5 oder ein weiteres Fach) und Wochenstunden. Die Noten
gehören immer zu einem Semester; jedes Semester ergibt so seine Kursnote.

Bewusst ohne feste Vorgaben bei der Zusammensetzung. Jedes Fach hat in jedem Semester frei benannte *Bereiche* (etwa
„Klausuren“, „Mündlich“, „Referate“) mit eigener Gewichtung; jede Note gehört
zu einem Bereich (oder keinem) und kann selbst sagen, wie viel sie im Bereich
zählt („höchstens 20 %“).

Gerechnet wird in Notenpunkten (0–15, Oberstufe):
- Punkte:  wert von max → wert / max · 15   (13 von 15 → 13; 27 von 40 → 10,1)
- Note:    1–6, auch 1,7 („2-“) → 17 − 3 · Note, begrenzt auf 0–15

Bereich: Noten mit Anteil zählen genau so viel; der Rest des Bereichs (100 % −
Summe der Anteile) gehört den Noten ohne Anteil, verteilt nach Faktor. Gibt es
nur eine Note mit 20 %, ist der Bereich damit vorläufig ihr Wert und zu 20 %
„abgedeckt“ — der Rest kommt noch.
Fach: Die Bereiche zählen nach ihrer Gewichtung, aber nur die, die schon eine
Note haben (Klausur noch nicht geschrieben → vorläufig zählt nur Mündlich).
Noten ohne Bereich bekommen, was an 100 % fehlt.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from kern import config

MAX_TEXT = 200
_DATUM = re.compile(r"\d{4}-\d{2}-\d{2}")

HALBJAHRE = ("12/1", "12/2", "13/1", "13/2")
JAHRGANG_12_BEGINN = 2026        # Schuljahr 2026/27 = Jahrgang 12; ein Semester wechselt zum 1. Februar bzw. 1. August
# Fächer, in denen es meist keine Klausuren gibt: ein einziger Bereich.
OHNE_KLAUSUR = {"sport", "kunst", "musik", "seminarfach", "darstellendes spiel"}

TABELLE = """
CREATE TABLE IF NOT EXISTS faecher (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    pruefung  INTEGER NOT NULL DEFAULT 0,   -- 1–3 erhöhtes Niveau, 4–5 grundlegend, 0 kein Prüfungsfach
    stunden   INTEGER,                      -- Wochenstunden
    halbjahre TEXT NOT NULL DEFAULT '',     -- belegte Semester, kommagetrennt
    ziel      REAL,                         -- Wunsch in Notenpunkten
    notiz     TEXT NOT NULL DEFAULT '',
    erstellt  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bereiche (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    fach_id   INTEGER NOT NULL REFERENCES faecher(id) ON DELETE CASCADE,
    halbjahr  TEXT NOT NULL DEFAULT '',   -- jedes Semester hat seine eigene Zusammensetzung
    name      TEXT NOT NULL,
    gewicht   REAL NOT NULL DEFAULT 50,   -- Prozent, relativ zu den anderen Bereichen
    reihe     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS noten (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    fach_id   INTEGER NOT NULL REFERENCES faecher(id) ON DELETE CASCADE,
    halbjahr  TEXT NOT NULL,
    bereich_id INTEGER REFERENCES bereiche(id) ON DELETE SET NULL,
    titel     TEXT NOT NULL,
    datum     TEXT NOT NULL,
    skala     TEXT NOT NULL DEFAULT 'punkte',   -- punkte | note
    wert      REAL NOT NULL,
    max       REAL NOT NULL DEFAULT 15,
    anteil    REAL,                             -- Prozent im Bereich; leer = gleichmäßig
    faktor    REAL NOT NULL DEFAULT 1,
    notiz     TEXT NOT NULL DEFAULT '',
    erstellt  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS noten_fach ON noten(fach_id);
CREATE TABLE IF NOT EXISTS einstellungen (
    name      TEXT PRIMARY KEY,
    wert      TEXT NOT NULL
);
"""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- Rechnen (rein, ohne Datenbank) ----------

def punkte(n: dict) -> float:
    """Eine Note in Notenpunkte (0–15) umrechnen."""
    if n["skala"] == "note":
        return min(15.0, max(0.0, 17 - 3 * n["wert"]))
    return n["wert"] / n["max"] * 15


def note_aus_punkten(p: float) -> float:
    return round(min(6.0, max(1.0, (17 - p) / 3)), 1)


def gruppe(eintraege: list[dict]) -> dict:
    """Ein Bereich: Wert, wie viel der 100 % schon belegt sind, und Hinweise."""
    fest = [e for e in eintraege if e.get("anteil") is not None]
    rest = [e for e in eintraege if e.get("anteil") is None]
    fest_summe = sum(e["anteil"] for e in fest)
    gewichte = [(e["anteil"], punkte(e)) for e in fest]
    hinweise = []
    if rest:
        rest_anteil = max(0.0, 100 - fest_summe)
        faktoren = sum(e["faktor"] for e in rest) or 1
        gewichte += [(rest_anteil * e["faktor"] / faktoren, punkte(e)) for e in rest]
        if rest_anteil == 0:
            hinweise.append("Die Anteile ergeben schon 100 % — Noten ohne Anteil zählen hier nicht.")
    elif fest_summe < 100:
        hinweise.append(f"Erst {fest_summe:g} % belegt — der Rest kommt noch.")
    if fest_summe > 100:
        hinweise.append(f"Die Anteile ergeben {fest_summe:g} % — sie werden anteilig gekürzt.")
    gesamt = sum(w for w, _ in gewichte)
    wert = sum(w * p for w, p in gewichte) / gesamt if gesamt > 0 else None
    return {"wert": wert, "abgedeckt": min(100.0, gesamt), "hinweise": hinweise}


def fach_rechnen(bereiche: list[dict], eintraege: list[dict]) -> dict:
    """Bereiche, Wert und Noten eines Fachs → mit ausgerechneten Werten."""
    for e in eintraege:
        e["punkte"] = punkte(e)
    aus_bereichen = []
    for b in bereiche:
        eigene = [e for e in eintraege if e["bereich_id"] == b["id"]]
        g = gruppe(eigene) if eigene else {"wert": None, "abgedeckt": 0.0, "hinweise": []}
        aus_bereichen.append({**b, "anzahl": len(eigene), **g})
    lose = [e for e in eintraege if e["bereich_id"] is None]
    lose_gewicht = max(0.0, 100 - sum(b["gewicht"] for b in bereiche))
    ohne = None
    if lose:
        ohne = {"gewicht": lose_gewicht, "anzahl": len(lose), **gruppe(lose)}
        if lose_gewicht == 0:
            ohne["hinweise"] = ["Ohne Bereich zählt nicht mit: Die Gewichtungen der Bereiche ergeben schon 100 %."] \
                + ohne["hinweise"]
    teile = [(b["gewicht"], b["wert"]) for b in aus_bereichen if b["wert"] is not None]
    if ohne and ohne["wert"] is not None:
        teile.append((ohne["gewicht"], ohne["wert"]))
    summe = sum(w for w, _ in teile)
    wert = sum(w * v for w, v in teile) / summe if summe > 0 else None
    return {"bereiche": aus_bereichen, "ohne_bereich": ohne, "wert": wert,
            "note": note_aus_punkten(wert) if wert is not None else None,
            "eintraege": sorted(eintraege, key=lambda e: (e["datum"], e["id"]), reverse=True)}


# ---------- Abitur-Schätzung ----------
# Nach den Regeln der Kultusministerkonferenz, wie sie in Niedersachsen gelten. Eine Schätzung, keine amtliche
# Berechnung: Welche Kurse deine Schule wirklich einbringen lässt oder verlangt, kann abweichen — darum kannst du
# einzelne Kurse selbst abwählen.
#   Block I  (max. 600): Kursnoten der vier Semester in ganzen Punkten, erhöhtes Niveau doppelt gewichtet,
#                        gewichteter Schnitt × 40.
#   Block II (max. 300): fünf Prüfungen (P1–P5), jede × 4.
#   Gesamt   (max. 900): bestanden ab 300 (Block I mindestens 200, Block II mindestens 100).
#   Note:    17/3 − Punkte/180, auf eine Stelle abgerundet, mindestens 1,0.
BLOCK1_MIN, BLOCK2_MIN, GESAMT_MIN = 200, 100, 300


def halbjahr_aus_datum(datum: str) -> str | None:
    """„2026-09-18“ → „12/1“. Außerhalb der vier Semester (oder bei Unsinn): None."""
    try:
        d = date.fromisoformat(str(datum))
    except ValueError:
        return None
    schuljahr = d.year if d.month >= 8 else d.year - 1              # Aug–Dez: dieses, Jan–Jul: das vorige Schuljahr beginnt
    nummer = (schuljahr - JAHRGANG_12_BEGINN) * 2 + (0 if d.month in (8, 9, 10, 11, 12, 1) else 1)
    return HALBJAHRE[nummer] if 0 <= nummer < len(HALBJAHRE) else None


def runden(x: float) -> int:
    """Kaufmännisch: 10,5 → 11 (Python rundet .5 sonst zur geraden Zahl)."""
    return int(math.floor(x + 0.5))


def abiturnote(punkte: int) -> float | None:
    if punkte < GESAMT_MIN:
        return None
    return max(10, (1020 - punkte) // 18) / 10       # (17/3 − P/180) · 10, abgerundet, ganzzahlig gerechnet


def abitur_rechnen(faecher: list[dict], einst: dict) -> dict:
    pruef = {int(k): v for k, v in (einst.get("pruefungen") or {}).items() if v is not None}
    weg = set(einst.get("ausgeschlossen") or [])
    ausgabe, summe, gewichte, mit_note, kurse_gesamt = [], 0.0, 0.0, 0, 0
    for f in faecher:
        gewicht = 2 if 1 <= f["pruefung"] <= 3 else 1
        kurse = {}
        for h in f["halbjahre"]:
            w = f["semester"][h]["wert"]
            punkte = None if w is None else runden(w)
            zaehlt = f"{f['id']}/{h}" not in weg
            kurse[h] = {"punkte": punkte, "zaehlt": zaehlt}
            kurse_gesamt += 1
            if punkte is not None and zaehlt:
                summe += gewicht * punkte
                gewichte += gewicht
                mit_note += 1
        ausgabe.append({"id": f["id"], "name": f["name"], "pruefung": f["pruefung"], "gewicht": gewicht, "kurse": kurse})
    schnitt = summe / gewichte if gewichte else None
    block1 = None if schnitt is None else runden(schnitt * 40)

    pruefungen = []
    for nr in range(1, 6):
        fach = next((f for f in ausgabe if f["pruefung"] == nr), None)
        eigene = [k["punkte"] for k in (fach["kurse"].values() if fach else []) if k["punkte"] is not None]
        if nr in pruef:
            wert, quelle = pruef[nr], "eingabe"
        elif eigene:
            wert, quelle = runden(sum(eigene) / len(eigene)), "schnitt"
        else:
            wert, quelle = None, None
        pruefungen.append({"nr": nr, "fach": fach["name"] if fach else None, "eingabe": pruef.get(nr),
                           "wert": wert, "quelle": quelle})
    bekannt = [p["wert"] for p in pruefungen if p["wert"] is not None]
    block2 = runden(sum(bekannt) / len(bekannt) * 20) if bekannt else None      # fehlende Prüfungen: Schnitt der bekannten

    hinweise = []
    if 0 < mit_note <= kurse_gesamt / 4:
        hinweise.append(f"Erst {mit_note} von {kurse_gesamt} Kursen haben eine Note — die Schätzung ist noch sehr grob.")
    gesamt = note = bestanden = None
    if block1 is not None and block2 is not None:
        gesamt = block1 + block2
        note = abiturnote(gesamt)
        bestanden = gesamt >= GESAMT_MIN and block1 >= BLOCK1_MIN and block2 >= BLOCK2_MIN
        if block1 < BLOCK1_MIN:
            hinweise.append(f"Block I liegt unter {BLOCK1_MIN} Punkten.")
        if block2 < BLOCK2_MIN:
            hinweise.append(f"Block II liegt unter {BLOCK2_MIN} Punkten.")
        if gesamt < GESAMT_MIN:
            hinweise.append(f"Gesamt unter {GESAMT_MIN} Punkten: so nicht bestanden.")
    if any(p["quelle"] == "schnitt" for p in pruefungen):
        hinweise.append("Prüfungen ohne eigenes Ergebnis sind mit dem Schnitt der Kursnoten angenommen.")
    if len(bekannt) < 5:
        hinweise.append(f"Für {5 - len(bekannt)} Prüfung(en) gibt es noch keine Grundlage — Block II ist hochgerechnet.")
    return {"faecher": ausgabe, "pruefungen": pruefungen, "einstellung": {"pruefungen": {str(k): v for k, v in pruef.items()},
            "ausgeschlossen": sorted(weg)}, "kurse_mit_note": mit_note, "kurse_gesamt": kurse_gesamt,
            "schnitt": schnitt, "block1": block1, "block2": block2, "gesamt": gesamt, "note": note,
            "bestanden": bestanden, "hinweise": hinweise}


def abitur_pruefen(d: dict) -> dict:
    pruef = {}
    for k, v in (d.get("pruefungen") or {}).items():
        if str(k) not in {"1", "2", "3", "4", "5"}:
            raise ValueError("Eine Prüfung ist P1 bis P5.")
        if v not in (None, ""):
            pruef[str(k)] = runden(_zahl(v, f"Das Ergebnis von P{k}", 0, 15))
    weg = d.get("ausgeschlossen") or []
    if len(weg) > 200 or not all(isinstance(x, str) and re.fullmatch(r"\d+/1[23]/[12]", x) for x in weg):
        raise ValueError("Die Kursauswahl ist ungültig.")
    return {"pruefungen": pruef, "ausgeschlossen": sorted(set(weg))}


# ---------- Prüfen ----------

def _text(wert, name: str, pflicht: bool = True, laenge: int = MAX_TEXT) -> str:
    t = " ".join(str(wert or "").split()) if laenge <= MAX_TEXT else str(wert or "").strip()
    if pflicht and not t:
        raise ValueError(f"{name} fehlt.")
    return t[:laenge]


def _zahl(wert, name: str, klein: float, gross: float) -> float:
    try:
        z = float(wert)
    except (TypeError, ValueError):
        raise ValueError(f"{name} ist keine Zahl.") from None
    if not klein <= z <= gross or z != z:
        raise ValueError(f"{name} muss zwischen {klein:g} und {gross:g} liegen.")
    return z


def eintrag_pruefen(d: dict) -> dict:
    skala = d.get("skala") or "punkte"
    if skala not in ("punkte", "note"):
        raise ValueError("Unbekannte Art der Note.")
    datum = str(d.get("datum") or "")
    try:
        if not _DATUM.fullmatch(datum):
            raise ValueError
        date.fromisoformat(datum)
    except ValueError:
        raise ValueError("Das Datum ist ungültig.") from None
    if skala == "note":
        wert, maximum = _zahl(d.get("wert"), "Die Note", 0.5, 6), 15.0
    else:
        maximum = _zahl(d.get("max", 15), "Das Maximum", 0.01, 1000)
        wert = _zahl(d.get("wert"), "Der Wert", 0, maximum)
    anteil = d.get("anteil")
    anteil = None if anteil in (None, "") else _zahl(anteil, "Der Anteil", 0.1, 100)
    halbjahr = str(d.get("halbjahr") or "")
    if halbjahr not in HALBJAHRE:
        raise ValueError("Zu welchem Semester gehört die Note?")
    return {"titel": _text(d.get("titel"), "Der Titel"), "datum": datum, "skala": skala, "halbjahr": halbjahr,
            "wert": wert, "max": maximum, "anteil": anteil,
            "faktor": _zahl(d.get("faktor", 1), "Der Faktor", 0.1, 10),
            "notiz": _text(d.get("notiz"), "", pflicht=False, laenge=1000)}


def bereiche_pruefen(liste) -> list[dict]:
    aus = []
    for b in liste or []:
        name = _text(b.get("name"), "Der Name des Bereichs")
        aus.append({"id": b.get("id"), "name": name, "gewicht": _zahl(b.get("gewicht", 50), "Die Gewichtung", 0, 100)})
    if len({b["name"].lower() for b in aus}) != len(aus):
        raise ValueError("Zwei Bereiche heißen gleich.")
    return aus[:20]


def fach_pruefen(d: dict) -> dict:
    """Ziel und Notiz eines Fachs (Name, Rolle und Semester kommen über `fachwahl_pruefen`)."""
    ziel = d.get("ziel")
    return {"ziel": None if ziel in (None, "") else _zahl(ziel, "Das Ziel", 0, 15),
            "notiz": _text(d.get("notiz"), "", pflicht=False, laenge=1000)}


def halbjahre_pruefen(liste) -> list[str]:
    raus = sorted({str(h) for h in liste or []}, key=lambda h: HALBJAHRE.index(h) if h in HALBJAHRE else 99)
    falsch = [h for h in raus if h not in HALBJAHRE]
    if falsch:
        raise ValueError(f"Unbekanntes Semester: {falsch[0]}.")
    return raus


def fachwahl_pruefen(liste) -> list[dict]:
    """Die Fächerwahl als Ganzes: Namen eindeutig, jedes Prüfungsfach (P1–P5) nur einmal."""
    aus = []
    for f in liste or []:
        stunden = f.get("stunden")
        pruefung = int(f.get("pruefung") or 0)
        if not 0 <= pruefung <= 5:
            raise ValueError("Ein Prüfungsfach ist P1 bis P5.")
        aus.append({"id": f.get("id"), "name": _text(f.get("name"), "Der Name des Fachs"), "pruefung": pruefung,
                    "stunden": None if stunden in (None, "") else int(_zahl(stunden, "Die Stundenzahl", 0, 20)),
                    "halbjahre": halbjahre_pruefen(f.get("halbjahre"))})
    if len({f["name"].lower() for f in aus}) != len(aus):
        raise ValueError("Ein Fach steht doppelt in der Liste.")
    nummern = [f["pruefung"] for f in aus if f["pruefung"]]
    if len(set(nummern)) != len(nummern):
        raise ValueError("Ein Prüfungsfach (P1–P5) ist doppelt vergeben.")
    return aus


def standard_bereiche(name: str) -> list[dict]:
    """Vorschlag, den man jederzeit ändert: Klausuren und Mündlich je zur Hälfte."""
    if name.strip().lower() in OHNE_KLAUSUR:
        return [{"id": None, "name": "Mündlich / Sonstige", "gewicht": 100.0}]
    return [{"id": None, "name": "Klausuren", "gewicht": 50.0}, {"id": None, "name": "Mündlich", "gewicht": 50.0}]


# ---------- Ablage ----------

class Ablage:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(ordner / "noten.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._alt_verwerfen()
        self._db.executescript(TABELLE)
        self._bereiche_je_semester()

    def _alt_verwerfen(self) -> None:
        """Die erste Fassung (ein Fach je Halbjahr) war leer und wird ersetzt. Enthielte sie
        Daten, bliebe sie stehen und der Start bricht laut ab — nichts geht still verloren."""
        spalten = {z[1] for z in self._db.execute("PRAGMA table_info(faecher)")}
        if not spalten or "halbjahre" in spalten:
            return
        if self._db.execute("SELECT (SELECT COUNT(*) FROM faecher) + (SELECT COUNT(*) FROM noten)").fetchone()[0]:
            raise RuntimeError("noten.db hat das alte Format und enthält Daten — bitte erst umziehen.")
        for tabelle in ("noten", "bereiche", "faecher"):
            self._db.execute(f"DROP TABLE {tabelle}")

    def _bereiche_je_semester(self) -> None:
        """Zweite Fassung: Bereiche gab es je Fach, jetzt je Fach und Semester. Jeder alte Bereich wird
        für jedes belegte Semester des Fachs kopiert, seine Noten wandern zur Kopie ihres Semesters."""
        if "halbjahr" in {z[1] for z in self._db.execute("PRAGMA table_info(bereiche)")}:
            return
        with self._db:
            self._db.execute("ALTER TABLE bereiche ADD COLUMN halbjahr TEXT NOT NULL DEFAULT ''")
            for b in self._zeilen("SELECT * FROM bereiche"):
                f = self._db.execute("SELECT halbjahre FROM faecher WHERE id=?", (b["fach_id"],)).fetchone()
                for h in [x for x in (f["halbjahre"] if f else "").split(",") if x]:
                    neu = self._db.execute("INSERT INTO bereiche (fach_id, halbjahr, name, gewicht, reihe) VALUES (?,?,?,?,?)",
                                           (b["fach_id"], h, b["name"], b["gewicht"], b["reihe"])).lastrowid
                    self._db.execute("UPDATE noten SET bereich_id=? WHERE bereich_id=? AND halbjahr=?", (neu, b["id"], h))
                self._db.execute("DELETE FROM bereiche WHERE id=?", (b["id"],))

    def _zeilen(self, sql: str, *args) -> list[dict]:
        return [dict(z) for z in self._db.execute(sql, args)]

    def uebersicht(self) -> dict:
        with self._sperre:
            faecher = self._zeilen("SELECT * FROM faecher")
            bereiche = self._zeilen("SELECT * FROM bereiche ORDER BY reihe, id")
            noten = self._zeilen("SELECT * FROM noten")
        faecher.sort(key=lambda f: (f["pruefung"] or 9, f["name"].lower()))
        aus, stunden = [], {h: 0 for h in HALBJAHRE}
        for f in faecher:
            f["halbjahre"] = [h for h in f["halbjahre"].split(",") if h]
            semester = {h: fach_rechnen([x for x in bereiche if x["fach_id"] == f["id"] and x["halbjahr"] == h],
                                        [dict(n) for n in noten if n["fach_id"] == f["id"] and n["halbjahr"] == h])
                        for h in f["halbjahre"]}
            for h in f["halbjahre"]:
                stunden[h] += f["stunden"] or 0
            aus.append({**f, "semester": semester})
        return {"faecher": aus, "halbjahre": list(HALBJAHRE), "stunden": stunden,
                "abitur": abitur_rechnen(aus, self._abitur_einstellung())}

    def _abitur_einstellung(self) -> dict:
        with self._sperre:
            z = self._db.execute("SELECT wert FROM einstellungen WHERE name='abitur'").fetchone()
        try:
            return json.loads(z["wert"]) if z else {}
        except ValueError:
            return {}

    def abitur_setzen(self, d: dict) -> None:
        with self._sperre, self._db:
            self._db.execute("INSERT INTO einstellungen (name, wert) VALUES ('abitur', ?) "
                             "ON CONFLICT(name) DO UPDATE SET wert=excluded.wert", (json.dumps(d),))

    def fach(self, fid: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM faecher WHERE id=?", (fid,)).fetchone()
        return dict(z) if z else None

    def eintrag(self, nid: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM noten WHERE id=?", (nid,)).fetchone()
        return dict(z) if z else None

    def _bereiche_setzen(self, fid: int, halbjahr: str, bereiche: list[dict]) -> None:
        """Bereiche eines Fachs in einem Semester abgleichen: bekannte ändern, neue anlegen, fehlende
        löschen (ihre Noten bleiben, nur ohne Bereich)."""
        vorhanden = {z["id"] for z in self._db.execute(
            "SELECT id FROM bereiche WHERE fach_id=? AND halbjahr=?", (fid, halbjahr))}
        behalten = set()
        for reihe, b in enumerate(bereiche):
            if b["id"] in vorhanden:
                self._db.execute("UPDATE bereiche SET name=?, gewicht=?, reihe=? WHERE id=?",
                                 (b["name"], b["gewicht"], reihe, b["id"]))
                behalten.add(b["id"])
            else:
                self._db.execute("INSERT INTO bereiche (fach_id, halbjahr, name, gewicht, reihe) VALUES (?,?,?,?,?)",
                                 (fid, halbjahr, b["name"], b["gewicht"], reihe))
        for weg in vorhanden - behalten:
            self._db.execute("DELETE FROM bereiche WHERE id=?", (weg,))

    def bereiche(self, fid: int, halbjahr: str) -> list[dict]:
        with self._sperre:
            return self._bereiche_von(fid, halbjahr)

    def _bereiche_von(self, fid: int, halbjahr: str) -> list[dict]:
        return self._zeilen("SELECT * FROM bereiche WHERE fach_id=? AND halbjahr=? ORDER BY reihe, id", fid, halbjahr)

    def fachwahl_setzen(self, wahl: list[dict]) -> None:
        """Die Fächerwahl übernehmen: bekannte Fächer ändern, neue anlegen (mit den vorgeschlagenen
        Bereichen), fehlende samt Noten löschen. Ein Semester, in dem schon Noten stehen, lässt
        sich nicht abwählen — sonst gingen sie unsichtbar verloren."""
        with self._sperre, self._db:
            vorhanden = {z["id"]: dict(z) for z in self._db.execute("SELECT * FROM faecher")}
            for f in wahl:
                if f["id"] in vorhanden:
                    weg = set(vorhanden[f["id"]]["halbjahre"].split(",")) - set(f["halbjahre"]) - {""}
                    for h in sorted(weg):
                        n = self._db.execute("SELECT COUNT(*) FROM noten WHERE fach_id=? AND halbjahr=?",
                                             (f["id"], h)).fetchone()[0]
                        if n:
                            raise ValueError(f"In {f['name']} stehen für {h} noch {n} Noten — erst die löschen.")
            behalten = set()
            for f in wahl:
                if f["id"] in vorhanden:
                    fid = f["id"]
                    self._db.execute("UPDATE faecher SET name=?, pruefung=?, stunden=?, halbjahre=? WHERE id=?",
                                     (f["name"], f["pruefung"], f["stunden"], ",".join(f["halbjahre"]), fid))
                    behalten.add(fid)
                else:
                    fid = self._db.execute(
                        "INSERT INTO faecher (name, pruefung, stunden, halbjahre, notiz, erstellt) VALUES (?,?,?,?,?,?)",
                        (f["name"], f["pruefung"], f["stunden"], ",".join(f["halbjahre"]), "", _jetzt())).lastrowid
                # Semester ohne Bereiche bekommen die eines anderen Semesters desselben Fachs, sonst den Vorschlag.
                for h in f["halbjahre"]:
                    if self._bereiche_von(fid, h):
                        continue
                    vorlage = next((self._bereiche_von(fid, x) for x in HALBJAHRE if x != h and self._bereiche_von(fid, x)), None)
                    self._bereiche_setzen(fid, h, [{**b, "id": None} for b in vorlage] if vorlage else standard_bereiche(f["name"]))
                platz = ",".join("?" * len(f["halbjahre"]))
                self._db.execute(f"DELETE FROM bereiche WHERE fach_id=? AND halbjahr NOT IN ({platz})",
                                 (fid, *f["halbjahre"]))
            for fid in set(vorhanden) - behalten:
                self._db.execute("DELETE FROM noten WHERE fach_id=?", (fid,))
                self._db.execute("DELETE FROM bereiche WHERE fach_id=?", (fid,))
                self._db.execute("DELETE FROM faecher WHERE id=?", (fid,))

    def fach_aendern(self, fid: int, d: dict, halbjahr: str | None, bereiche: list[dict] | None,
                     fuer_alle: bool = False) -> None:
        """Ziel und Notiz gelten für das Fach; die Bereiche für ein Semester — oder, mit `fuer_alle`,
        gleich für alle belegten Semester (Bereiche gleichen Namens behalten dort ihre Noten)."""
        with self._sperre, self._db:
            self._db.execute("UPDATE faecher SET ziel=?, notiz=? WHERE id=?", (d["ziel"], d["notiz"], fid))
            if bereiche is None:
                return
            belegt = self._db.execute("SELECT halbjahre FROM faecher WHERE id=?", (fid,)).fetchone()["halbjahre"].split(",")
            if halbjahr not in belegt:
                raise ValueError("In welchem Semester sollen die Bereiche gelten?")
            self._bereiche_setzen(fid, halbjahr, bereiche)
            if fuer_alle:
                for h in [x for x in belegt if x and x != halbjahr]:
                    bekannt = {b["name"].lower(): b["id"] for b in self._bereiche_von(fid, h)}
                    self._bereiche_setzen(fid, h, [{**b, "id": bekannt.get(b["name"].lower())} for b in bereiche])

    def _pruefen_zugehoerigkeit(self, fid: int, halbjahr: str, bereich_id) -> None:
        with self._sperre:
            f = self._db.execute("SELECT name, halbjahre FROM faecher WHERE id=?", (fid,)).fetchone()
            if halbjahr not in f["halbjahre"].split(","):
                raise ValueError(f"{f['name']} ist in {halbjahr} nicht belegt (Fächer ändern).")
            if bereich_id is not None and not self._db.execute(
                    "SELECT 1 FROM bereiche WHERE id=? AND fach_id=? AND halbjahr=?", (bereich_id, fid, halbjahr)).fetchone():
                raise ValueError("Dieser Bereich gehört nicht zu diesem Fach und Semester.")

    def eintrag_neu(self, fid: int, bereich_id, d: dict) -> int:
        self._pruefen_zugehoerigkeit(fid, d["halbjahr"], bereich_id)
        with self._sperre, self._db:
            return self._db.execute(
                "INSERT INTO noten (fach_id, halbjahr, bereich_id, titel, datum, skala, wert, max, anteil, faktor, notiz, erstellt)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, d["halbjahr"], bereich_id, d["titel"], d["datum"], d["skala"], d["wert"], d["max"], d["anteil"],
                 d["faktor"], d["notiz"], _jetzt())).lastrowid

    def eintrag_aendern(self, nid: int, fid: int, bereich_id, d: dict) -> None:
        self._pruefen_zugehoerigkeit(fid, d["halbjahr"], bereich_id)
        with self._sperre, self._db:
            self._db.execute(
                "UPDATE noten SET halbjahr=?, bereich_id=?, titel=?, datum=?, skala=?, wert=?, max=?, anteil=?, faktor=?, notiz=?"
                " WHERE id=?",
                (d["halbjahr"], bereich_id, d["titel"], d["datum"], d["skala"], d["wert"], d["max"], d["anteil"],
                 d["faktor"], d["notiz"], nid))

    def eintrag_loeschen(self, nid: int) -> None:
        with self._sperre, self._db:
            self._db.execute("DELETE FROM noten WHERE id=?", (nid,))


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Ablage(ordner)
    return _instanzen[str(ordner)]
