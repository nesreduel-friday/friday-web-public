"""Mail-Ablage — das IServ-Postfach, gespeichert und durchsuchbar.

SQLite im WAL-Modus (`data/mail.db`) mit Volltextsuche (FTS5, Umlaute egal),
Anhänge als Dateien unter `data/mail/<id>/`. Nichts wird gelöscht — auch wenn
eine Mail im Postfach verschwindet, bleibt sie hier.

Status: roh (gespeichert, nicht vorbereitet) · neu (soll vorbereitet werden) ·
verarbeitet · fehler (höchstens MAX_VERSUCHE).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from kern import config

MAX_VERSUCHE = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS mails (
    id          INTEGER PRIMARY KEY,
    ordner      TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    uid         INTEGER NOT NULL,
    datum       TEXT NOT NULL DEFAULT '',     -- UTC, ISO
    von         TEXT NOT NULL DEFAULT '',
    an          TEXT NOT NULL DEFAULT '',
    betreff     TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    anhaenge    TEXT NOT NULL DEFAULT '[]',
    groesse     INTEGER,
    gelesen     INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'roh',
    ergebnis    TEXT,
    modell      TEXT,
    versuche    INTEGER NOT NULL DEFAULT 0,
    fehler      TEXT,
    gespeichert TEXT,
    verarbeitet TEXT,
    UNIQUE (ordner, uidvalidity, uid)
);
CREATE INDEX IF NOT EXISTS mails_datum ON mails(datum);
CREATE VIRTUAL TABLE IF NOT EXISTS mails_suche USING fts5(
    betreff, von, text, zusammenfassung, tokenize = "unicode61 remove_diacritics 2");
"""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def suchausdruck(text: str) -> str:
    """Freie Wörter → FTS5-Ausdruck: jedes Wort muss vorkommen, auch als Wortanfang.
    Sonderzeichen fallen weg — so kann eine Eingabe die Abfrage nicht verbiegen."""
    return " ".join(f'"{w}"*' for w in re.findall(r"\w+", text or "")[:8])


class Ablage:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self.ordner = ordner
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(ordner / "mail.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def bekannte_uids(self, ordner: str, uidvalidity: int) -> set[int]:
        with self._sperre:
            return {z[0] for z in self._db.execute(
                "SELECT uid FROM mails WHERE ordner = ? AND uidvalidity = ?", (ordner, uidvalidity))}

    def hinzufuegen(self, m: dict) -> int:
        with self._sperre, self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO mails (ordner, uidvalidity, uid, datum, von, an, betreff, text, anhaenge, "
                "groesse, gelesen, status, gespeichert) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (m["ordner"], m["uidvalidity"], m["uid"], m.get("datum", ""), m.get("von", ""), m.get("an", ""),
                 m.get("betreff", ""), m.get("text", ""), json.dumps(m.get("anhaenge", []), ensure_ascii=False),
                 m.get("groesse"), int(bool(m.get("gelesen"))), m.get("status", "roh"), _jetzt()))
            if not cur.rowcount:
                return 0
            mid = cur.lastrowid
            self._db.execute("INSERT INTO mails_suche (rowid, betreff, von, text, zusammenfassung) VALUES (?, ?, ?, ?, '')",
                             (mid, m.get("betreff", ""), m.get("von", ""), m.get("text", "")))
            return mid

    def offen(self) -> list[int]:
        with self._sperre:
            return [z[0] for z in self._db.execute(
                "SELECT id FROM mails WHERE status = 'neu' OR (status = 'fehler' AND versuche < ?) "
                "ORDER BY datum DESC", (MAX_VERSUCHE,))]

    def anhaenge_setzen(self, mid: int, anhaenge: list[dict]) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE mails SET anhaenge = ? WHERE id = ?", (json.dumps(anhaenge, ensure_ascii=False), mid))

    def ergebnis_setzen(self, mid: int, ergebnis: dict, modell: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE mails SET ergebnis = ?, modell = ?, status = 'verarbeitet', fehler = NULL, "
                             "verarbeitet = ? WHERE id = ?",
                             (json.dumps(ergebnis, ensure_ascii=False), modell, _jetzt(), mid))
            self._db.execute("UPDATE mails_suche SET zusammenfassung = ? WHERE rowid = ?",
                             (" ".join(str(ergebnis.get(k, "")) for k in ("kurz", "inhalt")), mid))

    def fehler_setzen(self, mid: int, text: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE mails SET status = 'fehler', versuche = versuche + 1, fehler = ? WHERE id = ?",
                             (text, mid))

    @staticmethod
    def _zeile(z: sqlite3.Row) -> dict:
        d = dict(z)
        d["anhaenge"] = json.loads(d["anhaenge"] or "[]")
        try:
            d["ergebnis"] = json.loads(d["ergebnis"]) if d["ergebnis"] else None
        except ValueError:
            d["ergebnis"] = None
        return d

    def eintrag(self, mid: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM mails WHERE id = ?", (mid,)).fetchone()
        return self._zeile(z) if z else None

    def liste(self, anzahl: int = 10, suche: str = "", ordner: str = "", nur_ungelesen: bool = False,
              seit: str = "", nur_verarbeitet: bool = False) -> list[dict]:
        bedingungen, werte = [], []
        if ordner:
            bedingungen.append("m.ordner LIKE ?")
            werte.append(f"%{ordner}%")
        if nur_ungelesen:
            bedingungen.append("m.gelesen = 0")
        if seit:
            bedingungen.append("m.datum >= ?")
            werte.append(seit)
        if nur_verarbeitet:
            bedingungen.append("m.ergebnis IS NOT NULL")
        ausdruck = suchausdruck(suche)
        if ausdruck:
            sql = "SELECT m.* FROM mails_suche s JOIN mails m ON m.id = s.rowid WHERE mails_suche MATCH ?"
            werte.insert(0, ausdruck)
            sql += "".join(f" AND {b}" for b in bedingungen)
        else:
            sql = "SELECT m.* FROM mails m" + (" WHERE " + " AND ".join(bedingungen) if bedingungen else "")
        sql += " ORDER BY m.datum DESC LIMIT ?"
        werte.append(int(anzahl))
        with self._sperre:
            return [self._zeile(z) for z in self._db.execute(sql, werte)]

    def stand(self) -> dict:
        with self._sperre:
            return {s: n for s, n in self._db.execute("SELECT status, COUNT(*) FROM mails GROUP BY status")}

    def anhang_ordner(self, mid: int) -> Path:
        return self.ordner / "mail" / str(int(mid))


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Ablage(ordner)
    return _instanzen[str(ordner)]
