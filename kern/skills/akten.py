"""Akten (`data/akten.db`, 600): eine strukturierte Sammlung von Angaben je (Skill, Person, Vorgang) — das Gedächtnis einer
Skill-Instanz, die etwas von einer Person aufnimmt (12.10.2026, Spec `2026-10-12-skills-design.md`).

Der SKILL deklariert die Felder (`fach`: `felder` mit Typ text|datum|zahl|auswahl und Pflicht); dieses Modul kennt keinen
Anwendungsfall. Grenzen im Code:
- Identität kommt vom Kern: Jede Funktion bekommt die Person-Id, nie vom Modell; eine Akte ist nur für ihre Person les-/änderbar.
- Werte sind Fremdtext: bereinigt (keine Steuer-/Formatzeichen, ein Zeile), gekürzt-abgelehnt über `WERT_MAX`, nach Typ geprüft.
  Beim Wiedergeben stehen sie in Anführungszeichen als Daten, nie als Anweisung.
- Es wird nie gelöscht: jede Angabe ist eine Zeile; eine Korrektur fügt eine neue Zeile an, die gültige ist die letzte je Feld.
- Eine Akte gehört einem Vorgang: `offen` bis sie übergeben wird (`uebergeben`) oder `verworfen`; danach beginnt der nächste Vorgang."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import date, timedelta, timezone
from pathlib import Path

from kern import config
from kern.personen import register

TEXT_MAX = 300
WERTE_JE_AKTE = 80
OFFENE_JE_PERSON_UND_SKILL = 5
LIEGENGEBLIEBEN_MIN = 10
_VORGANG = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,29}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS akten (
    id INTEGER PRIMARY KEY AUTOINCREMENT, skill TEXT NOT NULL, person TEXT NOT NULL, vorgang TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'offen', ergebnis TEXT, erstellt TEXT NOT NULL, geaendert TEXT NOT NULL,
    UNIQUE (skill, person, vorgang)
);
CREATE TABLE IF NOT EXISTS akten_werte (
    id INTEGER PRIMARY KEY AUTOINCREMENT, akte INTEGER NOT NULL, feld TEXT NOT NULL, wert TEXT NOT NULL, zeit TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS akten_werte_akte ON akten_werte(akte, feld, id);
CREATE TABLE IF NOT EXISTS akten_ereignis (
    id INTEGER PRIMARY KEY AUTOINCREMENT, akte INTEGER NOT NULL, zeit TEXT NOT NULL, text TEXT NOT NULL
);
"""


def _jetzt() -> str:
    return config.jetzt().astimezone(timezone.utc).isoformat(timespec="seconds")


def sauber(t) -> str:
    """Eine Zeile Klartext: ohne unsichtbare/Format-/Steuerzeichen, Leerraum zusammengezogen."""
    return " ".join(register._sichtbar(str(t if t is not None else "")).split())


def wert_pruefen(feld: dict, roh) -> tuple[str | None, str]:
    """(gespeicherter Wert, Absage) für ein Feld nach seinem Typ."""
    name = feld["name"]
    w = sauber(roh)
    if not w:
        return None, f"{name}: leer."
    typ = feld["typ"]
    if typ == "text":
        if len(w) > TEXT_MAX:
            return None, f"{name}: zu lang (höchstens {TEXT_MAX} Zeichen)."
        return w, ""
    if typ == "zahl":
        m = re.fullmatch(r"(-?\d{1,9}(?:[.,]\d{1,3})?)\s*[A-Za-zÄÖÜäöü.]{0,12}", w)
        if not m:
            return None, f"{name}: keine Zahl."
        z = m.group(1).replace(",", ".")
        return (z[:-2] if z.endswith(".0") else z), ""
    if typ == "datum":
        heute = config.jetzt().date()
        try:
            if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", w):
                d = date(int(m[1]), int(m[2]), int(m[3]))
            elif m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})?", w):
                if m[3]:
                    jahr = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
                    d = date(jahr, int(m[2]), int(m[1]))
                else:                                        # „12.10.“ meint den letzten solchen Tag, nie einen künftigen
                    d = date(heute.year, int(m[2]), int(m[1]))
                    if d > heute:
                        d = date(heute.year - 1, int(m[2]), int(m[1]))
            else:
                return None, f"{name}: kein Datum (TT.MM.JJJJ)."
        except ValueError:
            return None, f"{name}: kein gültiges Datum."
        if not date(2000, 1, 1) <= d <= date(heute.year + 1, 12, 31):
            return None, f"{name}: Datum außerhalb des sinnvollen Bereichs."
        return d.isoformat(), ""
    if typ == "auswahl":
        treffer = [x for x in feld["werte"] if x.casefold() == w.casefold()]
        return (treffer[0], "") if treffer else (None, f"{name}: nur {' / '.join(feld['werte'])}.")
    return None, f"{name}: unbekannter Typ."


class Akten:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.RLock()
        self._db = sqlite3.connect(ordner / "akten.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        try:
            (ordner / "akten.db").chmod(0o600)
        except OSError:
            pass

    # ---------- Lesen ----------

    def akte(self, aid: int, person: str) -> dict | None:
        """Eine Akte — nur, wenn sie dieser Person gehört."""
        with self._sperre:
            z = self._db.execute("SELECT * FROM akten WHERE id=? AND person=?", (int(aid), person)).fetchone()
        return dict(z) if z else None

    def finden(self, skill: str, person: str, vorgang: str = "") -> dict | None:
        """Die Akte zu (Skill, Person, Vorgang); ohne Vorgang die neueste offene."""
        with self._sperre:
            if vorgang:
                z = self._db.execute("SELECT * FROM akten WHERE skill=? AND person=? AND vorgang=?", (skill, person, vorgang)).fetchone()
            else:
                z = self._db.execute("SELECT * FROM akten WHERE skill=? AND person=? AND status='offen' ORDER BY id DESC LIMIT 1",
                                     (skill, person)).fetchone()
        return dict(z) if z else None

    def letzte(self, skill: str, person: str) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM akten WHERE skill=? AND person=? ORDER BY id DESC LIMIT 1", (skill, person)).fetchone()
        return dict(z) if z else None

    def werte(self, aid: int) -> dict[str, str]:
        """Die gültigen Werte: je Feld die letzte Zeile."""
        with self._sperre:
            return {z["feld"]: z["wert"] for z in self._db.execute("SELECT feld, wert FROM akten_werte WHERE akte=? ORDER BY id", (int(aid),))}

    def verlauf(self, aid: int) -> list[dict]:
        with self._sperre:
            return [dict(z) for z in self._db.execute("SELECT * FROM akten_werte WHERE akte=? ORDER BY id", (int(aid),))]

    def ereignisse(self, aid: int) -> list[dict]:
        with self._sperre:
            return [dict(z) for z in self._db.execute("SELECT * FROM akten_ereignis WHERE akte=? ORDER BY id", (int(aid),))]

    # ---------- Schreiben (nichts wird gelöscht) ----------

    def ereignis(self, aid: int, text: str) -> None:
        with self._sperre, self._db:
            self._db.execute("INSERT INTO akten_ereignis (akte, zeit, text) VALUES (?,?,?)", (int(aid), _jetzt(), str(text)[:300]))

    def zusammenfassung_merken(self, aid: int, stand: str, zug: str) -> None:
        """Vermerkt in der Akte, dass die Zusammenfassung zu diesem Stand in diesem Zug gezeigt wurde (Eike sieht es im Verlauf)."""
        self.ereignis(aid, f"Zusammenfassung gezeigt (Stand {str(stand)[:16]}, Zug {(str(zug)[:16] or "0")})")

    def zusammenfassung(self, aid: int) -> tuple[str, str] | None:
        """(Stand, Zug) der zuletzt gezeigten Zusammenfassung dieser Akte — oder None (auch bei einem Vermerk, der nicht genau
        so aussieht: im Zweifel zu)."""
        with self._sperre:
            z = self._db.execute("SELECT text FROM akten_ereignis WHERE akte=? AND text LIKE 'Zusammenfassung gezeigt (%' "
                                 "ORDER BY id DESC LIMIT 1", (int(aid),)).fetchone()
        m = re.fullmatch(r"Zusammenfassung gezeigt \(Stand ([0-9a-f]{1,16}), Zug ([0-9a-f]{1,16})\)", z["text"]) if z else None
        return (m[1], m[2]) if m else None

    def oeffnen(self, skill: str, person: str, vorgang: str = "") -> tuple[dict | None, str]:
        """Die offene Akte (neu angelegt, wenn es keine gibt). Ein genannter Vorgang, der schon übergeben ist, bleibt zu."""
        with self._sperre, self._db:
            if vorgang:
                if not _VORGANG.match(vorgang):
                    return None, "Der Vorgang darf nur aus a-z, Ziffern, _ und - bestehen (höchstens 30 Zeichen)."
                a = self.finden(skill, person, vorgang)
                if a and a["status"] != "offen":
                    return None, f"Der Vorgang {vorgang} ist schon abgeschlossen."
                if a:
                    return a, ""
            elif a := self.finden(skill, person):
                return a, ""
            offene = self._db.execute("SELECT COUNT(*) FROM akten WHERE skill=? AND person=? AND status='offen'", (skill, person)).fetchone()[0]
            if offene >= OFFENE_JE_PERSON_UND_SKILL:
                return None, f"Es sind schon {OFFENE_JE_PERSON_UND_SKILL} Akten offen — schließ erst eine ab."
            if not vorgang:
                n = self._db.execute("SELECT COUNT(*) FROM akten WHERE skill=? AND person=?", (skill, person)).fetchone()[0]
                vorgang = f"v{n + 1}"
                while self.finden(skill, person, vorgang):
                    n += 1
                    vorgang = f"v{n + 1}"
            jetzt = _jetzt()
            aid = self._db.execute("INSERT INTO akten (skill, person, vorgang, erstellt, geaendert) VALUES (?,?,?,?,?)",
                                   (skill, person, vorgang, jetzt, jetzt)).lastrowid
        return self.akte(aid, person), ""

    def schreiben(self, akte: dict, felder: list[dict], angaben: dict) -> tuple[dict, list[str]]:
        """Prüft und speichert Angaben nach den Feldern des Skills. Gibt (gespeichert, Absagen) zurück; was nicht passt, bleibt draußen."""
        if akte["status"] != "offen":
            return {}, ["Das ist schon abgeschlossen."]
        bekannt = {f["name"]: f for f in felder}
        ok, absagen = {}, []
        if not isinstance(angaben, dict) or not angaben:
            return {}, ["Keine Angaben übergeben."]
        for name, roh in list(angaben.items())[:len(bekannt) + 3]:
            f = bekannt.get(sauber(name).lower())
            if f is None:
                absagen.append(f"„{sauber(name)[:30]}“ kann ich hier nicht festhalten (möglich: {', '.join(bekannt) or 'keine'}).")
                continue
            wert, grund = wert_pruefen(f, roh)
            if grund:
                absagen.append(grund)
            else:
                ok[f["name"]] = wert
        with self._sperre, self._db:
            frisch = self.akte(akte["id"], akte["person"])
            if not frisch or frisch["status"] != "offen":
                return {}, ["Das ist schon abgeschlossen."]
            gelten = self.werte(akte["id"])
            neu = {k: v for k, v in ok.items() if gelten.get(k) != v}
            n = self._db.execute("SELECT COUNT(*) FROM akten_werte WHERE akte=?", (akte["id"],)).fetchone()[0]
            if n + len(neu) > WERTE_JE_AKTE:
                return {}, [f"Mehr als {WERTE_JE_AKTE} Angaben passen hier nicht hinein."]
            jetzt = _jetzt()
            for k, v in neu.items():
                self._db.execute("INSERT INTO akten_werte (akte, feld, wert, zeit) VALUES (?,?,?,?)", (akte["id"], k, v, jetzt))
            if neu:
                self._db.execute("UPDATE akten SET geaendert=? WHERE id=?", (jetzt, akte["id"]))
        return ok, absagen

    def uebernehmen(self, aid: int) -> bool:
        """Anspruch auf die Übergabe: offen -> in_arbeit, genau einmal (Doppelaufruf, Wettlauf). Danach `abschliessen` oder `freigeben`."""
        alt = (config.jetzt() - timedelta(minutes=LIEGENGEBLIEBEN_MIN)).astimezone(timezone.utc).isoformat(timespec="seconds")
        with self._sperre, self._db:      # eine längst liegengebliebene Übernahme (Absturz) zählt nicht mehr
            return self._db.execute("UPDATE akten SET status='in_arbeit', geaendert=? WHERE id=? AND "
                                    "(status='offen' OR (status='in_arbeit' AND geaendert < ?))",
                                    (_jetzt(), int(aid), alt)).rowcount == 1

    def abschliessen(self, aid: int, ergebnis: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE akten SET status='uebergeben', ergebnis=?, geaendert=? WHERE id=?", (str(ergebnis)[:200], _jetzt(), int(aid)))

    def freigeben(self, aid: int) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE akten SET status='offen', geaendert=? WHERE id=? AND status='in_arbeit'", (_jetzt(), int(aid)))


def stand_hash(werte: dict, ticket: dict | None) -> str:
    """Kurzer Fingerabdruck von „was eingereicht würde“: die Werte der Akte plus das Ticket (Kennung, Nummern, Prüfstand)."""
    t = ticket or {}
    roh = json.dumps({"w": {k: str(v) for k, v in sorted((werte or {}).items())},
                      "t": [t.get("id") or "", t.get("kundenkarte") or "", t.get("ticketnummer") or "", bool(t.get("nummern_geprueft"))]},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]


def fehlt(felder: list[dict], werte: dict) -> tuple[list[dict], list[dict]]:
    """(fehlende Pflichtfelder, offene optionale Felder)."""
    return ([f for f in felder if f["pflicht"] and f["name"] not in werte],
            [f for f in felder if not f["pflicht"] and f["name"] not in werte])


def feld_text(f: dict) -> str:
    art = {"text": "Text", "datum": "Datum", "zahl": "Zahl", "auswahl": "eines von " + " / ".join(f.get("werte") or [])}[f["typ"]]
    return f"{f['name']} ({art})" + (f" — {f['hinweis']}" if f.get("hinweis") else "")


_instanzen: dict[str, Akten] = {}


def standard() -> Akten:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Akten(ordner)
    return _instanzen[str(ordner)]
