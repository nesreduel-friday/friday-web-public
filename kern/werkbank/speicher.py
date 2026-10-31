"""Vorhaben-Speicher der Werkbank — was Friday an sich verbessern könnte. Löscht nie: Vorhaben wechseln nur den Status.

Zeiten als UTC gespeichert, „heute“ zählt nach dem Berliner Tag. Dieselbe Datei (`werkbank.db`) hält zusätzlich die Tabelle
`stoerungen` (Zähler je Fingerabdruck); sie wird über `verbindung()` benutzt.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from kern import config
from kern.denken.buch import _sauber, gleicher_sinn, zu_lang

ART = ("fehler", "wunsch", "idee", "test")
QUELLE = ("gespraech", "stoerung", "denkrunde")
STATUS = ("vorgeschlagen", "freigegeben", "laeuft", "bereit", "uebernommen", "verworfen", "ruht", "fehler")
OFFEN = ("vorgeschlagen", "freigegeben", "laeuft", "bereit", "fehler")
TITEL_MAX = 80
DENKRUNDE_PRO_TAG = 2                 # neue Vorhaben aus Denkrunden je Berliner Tag (Dubletten zählen nicht)
BELEG_MAX = 1500
FELDER = ("backend", "branch", "diff_pfad", "bericht", "tests", "versuche", "uebernahme", "uebernahme_seit",
          "nachbesserungen", "auto_uebernommen")
UEBERNAHME = ("laeuft", "ok", "zurueckgerollt", "fehler")      # Spalte `uebernahme`; NULL = nie versucht

SCHEMA = """
CREATE TABLE IF NOT EXISTS vorhaben (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titel TEXT NOT NULL, art TEXT NOT NULL, quelle TEXT NOT NULL, beleg TEXT NOT NULL DEFAULT '',
    fingerabdruck TEXT NOT NULL DEFAULT '', fremd INTEGER NOT NULL DEFAULT 0, zaehler INTEGER NOT NULL DEFAULT 1,
    erstmals TEXT NOT NULL, zuletzt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'vorgeschlagen',
    backend TEXT, branch TEXT, diff_pfad TEXT, bericht TEXT, tests TEXT, versuche INTEGER NOT NULL DEFAULT 0,
    gestartet TEXT
);
CREATE INDEX IF NOT EXISTS vorhaben_status ON vorhaben(status);
CREATE INDEX IF NOT EXISTS vorhaben_fingerabdruck ON vorhaben(fingerabdruck);
CREATE TABLE IF NOT EXISTS stoerungen (
    fingerabdruck TEXT PRIMARY KEY, ort TEXT, erstmals TEXT, zuletzt TEXT, anzahl INTEGER,
    tage TEXT, vorhaben_id INTEGER
);
CREATE TABLE IF NOT EXISTS starts (vid INTEGER NOT NULL, zeit TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS starts_zeit ON starts(zeit);
"""
# Spalten, die nach dem ersten Stand dazukamen (ALTER TABLE beim Öffnen, nie Daten weg)
SPALTEN_NEU = {"uebernahme": "TEXT", "uebernahme_seit": "TEXT",
               "nachbesserungen": "INTEGER",
               "auto_uebernommen": "TEXT"}     # 29.10.2026: Zeit der Selbstübernahme (`selbst.py`), NULL = nie   # 28.10.2026: Nachbesserungen des Helfers bei roten Tests
# dasselbe für die Tabelle `starts`: 28.10.2026 `auto` = Start durch die Werkbank-Automatik (Altzeilen = manuell)
SPALTEN_NEU_STARTS = {"auto": "INTEGER NOT NULL DEFAULT 0"}


def _jetzt() -> datetime:
    return config.jetzt().astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _tagesbeginn() -> str:
    return _iso(config.jetzt().replace(hour=0, minute=0, second=0, microsecond=0))


def _schwaerzen(text: str) -> str:
    from kern.werkzeuge import katalog  # spät: der Katalog zieht viele Module nach
    return katalog.geheimes_schwaerzen(text)


class Fach:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        pfad = ordner / "werkbank.db"
        self._sperre = threading.RLock()
        self._db = sqlite3.connect(pfad, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        for tabelle, neu in (("vorhaben", SPALTEN_NEU), ("starts", SPALTEN_NEU_STARTS)):
            da = {z["name"] for z in self._db.execute(f"PRAGMA table_info({tabelle})").fetchall()}
            for spalte, typ in neu.items():
                if spalte not in da:
                    self._db.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")
        self._db.commit()
        for datei in (pfad, Path(f"{pfad}-wal"), Path(f"{pfad}-shm")):
            try:
                os.chmod(datei, 0o600)
            except OSError:
                pass

    @contextmanager
    def verbindung(self):
        """Verbindung (Row-Zeilen) unter der Fach-Sperre; Ende ohne Fehler = commit, mit Fehler = rollback."""
        with self._sperre:
            try:
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    @staticmethod
    def _dict(z) -> dict:
        d = dict(z)
        d["fremd"] = bool(d.get("fremd"))
        return d

    def _alle(self, sql: str, werte: tuple = ()) -> list[dict]:
        with self._sperre:
            return [self._dict(z) for z in self._db.execute(sql, werte).fetchall()]

    def eintrag(self, vid: int) -> dict | None:
        z = self._alle("SELECT * FROM vorhaben WHERE id = ?", (vid,))
        return z[0] if z else None

    def offene(self) -> list[dict]:
        marken = ",".join("?" * len(OFFEN))
        return self._alle(f"SELECT * FROM vorhaben WHERE status IN ({marken}) ORDER BY id", OFFEN)

    def treffer(self, fingerabdruck: str) -> dict | None:
        if not fingerabdruck:
            return None
        marken = ",".join("?" * len(OFFEN))
        z = self._alle(f"SELECT * FROM vorhaben WHERE fingerabdruck = ? AND status IN ({marken}) ORDER BY id LIMIT 1",
                       (fingerabdruck, *OFFEN))
        return z[0] if z else None

    def zaehlen(self, vid: int, jetzt: datetime | None = None) -> dict:
        with self.verbindung() as db:
            db.execute("UPDATE vorhaben SET zaehler = zaehler + 1, zuletzt = ? WHERE id = ?",
                       (_iso(jetzt or _jetzt()), vid))
        return self.eintrag(vid)

    def anlegen(self, titel: str, art: str, quelle: str, beleg: str = "", fingerabdruck: str = "",
                fremd: bool = False) -> dict:
        if art not in ART:
            return {"ok": False, "grund": f"art muss eine von {', '.join(ART)} sein."}
        if quelle not in QUELLE:
            return {"ok": False, "grund": f"quelle muss eine von {', '.join(QUELLE)} sein."}
        if absage := zu_lang(titel, TITEL_MAX):
            return {"ok": False, "grund": absage}
        titel = _sauber(titel, TITEL_MAX)
        if not titel:
            return {"ok": False, "grund": "Ein Vorhaben braucht einen Titel."}
        beleg = _schwaerzen(str(beleg or ""))[:BELEG_MAX]
        fingerabdruck = str(fingerabdruck or "").strip()
        with self._sperre:
            alt = self.treffer(fingerabdruck)
            if not alt:
                alt = next((v for v in self.offene() if gleicher_sinn(v["titel"], titel)), None)
            if alt:
                self.zaehlen(alt["id"])
                return {"ok": True, "id": alt["id"], "neu": False}
            if quelle == "denkrunde" and self._alle(
                    "SELECT COUNT(*) AS n FROM vorhaben WHERE quelle = 'denkrunde' AND erstmals >= ?",
                    (_tagesbeginn(),))[0]["n"] >= DENKRUNDE_PRO_TAG:
                return {"ok": False, "grund": f"Aus Denkrunden sind heute schon {DENKRUNDE_PRO_TAG} Vorhaben entstanden; morgen wieder."}
            jetzt = _iso(_jetzt())
            with self.verbindung() as db:
                c = db.execute(
                    "INSERT INTO vorhaben (titel, art, quelle, beleg, fingerabdruck, fremd, erstmals, zuletzt) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (titel, art, quelle, beleg, fingerabdruck, 1 if fremd else 0, jetzt, jetzt))
                return {"ok": True, "id": c.lastrowid, "neu": True}

    def status_setzen(self, vid: int, status: str, **felder) -> dict:
        if status not in STATUS:
            raise ValueError(f"Unbekannter Status: {status}")
        for f in felder:
            if f not in FELDER:
                raise ValueError(f"Unbekanntes Feld: {f}")
        with self.verbindung() as db:
            if not db.execute("SELECT 1 FROM vorhaben WHERE id = ?", (vid,)).fetchone():
                raise ValueError(f"Kein Vorhaben {vid}")
            teile, werte = ["status = ?"], [status]
            for f, w in felder.items():
                teile.append(f"{f} = ?")
                werte.append(w)
            if status == "laeuft":
                teile.append("gestartet = ?")
                werte.append(_iso(_jetzt()))
            db.execute(f"UPDATE vorhaben SET {', '.join(teile)} WHERE id = ?", (*werte, vid))
        return self.eintrag(vid)

    def uebergang(self, vid: int, von: str, nach: str, **felder) -> bool:
        """Status `von` → `nach` nur, wenn das Vorhaben noch `von` hat (atomar). True = dieser Aufruf hat gewechselt,
        so meldet nur einer (Lauf-Takt oder Hintergrundfaden) das Ergebnis."""
        if nach not in STATUS:
            raise ValueError(f"Unbekannter Status: {nach}")
        for f in felder:
            if f not in FELDER:
                raise ValueError(f"Unbekanntes Feld: {f}")
        teile, werte = ["status = ?"], [nach]
        for f, w in felder.items():
            teile.append(f"{f} = ?")
            werte.append(w)
        with self.verbindung() as db:
            c = db.execute(f"UPDATE vorhaben SET {', '.join(teile)} WHERE id = ? AND status = ?", (*werte, vid, von))
            return c.rowcount == 1

    def felder_setzen(self, vid: int, **felder) -> None:
        """Felder eines Vorhabens setzen, ohne den Status zu ändern."""
        for f in felder:
            if f not in FELDER:
                raise ValueError(f"Unbekanntes Feld: {f}")
        with self.verbindung() as db:
            db.execute(f"UPDATE vorhaben SET {', '.join(f'{f} = ?' for f in felder)} WHERE id = ?", (*felder.values(), vid))

    def uebernahmen_offen(self) -> list[dict]:
        return self._alle("SELECT * FROM vorhaben WHERE status = 'uebernommen' AND uebernahme = 'laeuft' ORDER BY id")

    def uebernahme_abschliessen(self, vid: int, ergebnis: str, bericht: str | None = None) -> bool:
        """Ergebnis einer laufenden Übernahme eintragen (atomar, nur einmal). `ok` bleibt `uebernommen`,
        alles andere geht zurück auf `bereit`. True = dieser Aufruf hat eingetragen (und meldet)."""
        if ergebnis not in UEBERNAHME or ergebnis == "laeuft":
            raise ValueError(f"Unbekanntes Ergebnis: {ergebnis}")
        teile, werte = ["uebernahme = ?"], [ergebnis]
        if ergebnis != "ok":
            teile.append("status = 'bereit'")
        if bericht is not None:
            teile.append("bericht = ?")
            werte.append(bericht)
        with self.verbindung() as db:
            c = db.execute(f"UPDATE vorhaben SET {', '.join(teile)} WHERE id = ? AND status = 'uebernommen' "
                           "AND uebernahme = 'laeuft'", (*werte, vid))
            return c.rowcount == 1

    def laufende(self) -> list[dict]:
        return self._alle("SELECT * FROM vorhaben WHERE status = 'laeuft' ORDER BY id")

    def letzte(self, anzahl: int = 20) -> list[dict]:
        return self._alle("SELECT * FROM vorhaben ORDER BY id DESC LIMIT ?", (int(anzahl),))

    def starten_notieren(self, vid: int, jetzt: datetime | None = None, auto: bool = False) -> None:
        """Jeder Start wird protokolliert — auch wenn dasselbe Vorhaben am Tag mehrmals startet. Nur `auto` zählt fürs
        Tageslimit der Automatik; manuelle Starts haben keins."""
        with self.verbindung() as db:
            db.execute("INSERT INTO starts (vid, zeit, auto) VALUES (?, ?, ?)",
                       (vid, _iso(jetzt or _jetzt()), 1 if auto else 0))

    def heute_gestartet(self) -> int:
        """Alle Starts seit Beginn des Berliner Tags (Tabelle `starts`, nicht die Zahl der Vorhaben)."""
        return self._alle("SELECT COUNT(*) AS n FROM starts WHERE zeit >= ?", (_tagesbeginn(),))[0]["n"]

    def auto_heute(self) -> int:
        """Nur die automatischen Starts seit Beginn des Berliner Tags (Tageslimit der Automatik)."""
        return self._alle("SELECT COUNT(*) AS n FROM starts WHERE zeit >= ? AND auto = 1", (_tagesbeginn(),))[0]["n"]


_instanzen: dict[str, Fach] = {}


def standard() -> Fach:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Fach(ordner)
    return _instanzen[str(ordner)]
