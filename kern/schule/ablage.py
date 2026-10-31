"""Elternbrief-Ablage — was aus dem Schulmanager kam, schon verarbeitet.

SQLite im WAL-Modus (`data/schule.db`), Anhänge als Dateien unter
`data/schule/briefe/<id>/`. Verarbeitet wird in `briefe.py`, wenn ein Brief
ankommt — Friday liest hier beim Fragen nur noch nach.

Status eines Briefs: neu → verarbeitet, oder fehler (höchstens
MAX_VERSUCHE Versuche, dann bleibt er mit Rohtext stehen).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from kern import config

MAX_VERSUCHE = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefe (
    id          INTEGER PRIMARY KEY,     -- die Nummer aus dem Schulmanager
    titel       TEXT NOT NULL DEFAULT '',
    gesendet    TEXT NOT NULL DEFAULT '',
    gelesen     INTEGER NOT NULL DEFAULT 0,
    frist       TEXT,                    -- Antwortfrist laut Schulmanager
    text        TEXT,                    -- Brieftext als Klartext
    anhaenge    TEXT NOT NULL DEFAULT '[]',
    ergebnis    TEXT,                    -- JSON: kurz, inhalt, termine, fristen, zu_tun, kosten
    modell      TEXT,
    status      TEXT NOT NULL DEFAULT 'neu',
    versuche    INTEGER NOT NULL DEFAULT 0,
    fehler      TEXT,
    gesehen     TEXT,
    verarbeitet TEXT
);
"""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Gemeinsame Sperre für alle Zugriffe auf schule.db (ablage, hausaufgaben, buecher)
DB_SPERRE = threading.Lock()


class Ablage:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self.ordner = ordner
        self._sperre = DB_SPERRE
        self._db = sqlite3.connect(ordner / "schule.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _schreiben(self, sql: str, werte: tuple) -> None:
        with self._sperre, self._db:
            self._db.execute(sql, werte)

    def abgleichen(self, liste: list[dict]) -> list[int]:
        """Übersicht aus dem Schulmanager eintragen. Gibt die neuen Nummern zurück."""
        neu = []
        with self._sperre, self._db:
            for b in liste:
                if not isinstance(b, dict) or not isinstance(b.get("id"), int):
                    continue
                gelesen = int(any((s or {}).get("readTimestamp") for s in b.get("studentStatuses") or []))
                werte = (b.get("title") or "", b.get("sentDate") or b.get("createdAt") or "", gelesen)
                if self._db.execute("SELECT 1 FROM briefe WHERE id = ?", (b["id"],)).fetchone():
                    self._db.execute("UPDATE briefe SET titel = ?, gesendet = ?, gelesen = ? WHERE id = ?",
                                     (*werte, b["id"]))
                else:
                    self._db.execute("INSERT INTO briefe (id, titel, gesendet, gelesen, gesehen) VALUES (?, ?, ?, ?, ?)",
                                     (b["id"], *werte, _jetzt()))
                    neu.append(b["id"])
        return neu

    def offen(self) -> list[int]:
        """Noch zu verarbeiten, neueste zuerst."""
        with self._sperre:
            return [z[0] for z in self._db.execute(
                "SELECT id FROM briefe WHERE status = 'neu' OR (status = 'fehler' AND versuche < ?) "
                "ORDER BY gesendet DESC", (MAX_VERSUCHE,))]

    def inhalt_setzen(self, bid: int, text: str, frist: str | None, anhaenge: list[dict]) -> None:
        self._schreiben("UPDATE briefe SET text = ?, frist = ?, anhaenge = ? WHERE id = ?",
                        (text, frist, json.dumps(anhaenge, ensure_ascii=False), bid))

    def ergebnis_setzen(self, bid: int, ergebnis: dict, modell: str) -> None:
        self._schreiben("UPDATE briefe SET ergebnis = ?, modell = ?, status = 'verarbeitet', fehler = NULL, "
                        "verarbeitet = ? WHERE id = ?",
                        (json.dumps(ergebnis, ensure_ascii=False), modell, _jetzt(), bid))

    def fehler_setzen(self, bid: int, text: str) -> None:
        self._schreiben("UPDATE briefe SET status = 'fehler', versuche = versuche + 1, fehler = ? WHERE id = ?",
                        (text, bid))

    @staticmethod
    def _zeile(z: sqlite3.Row) -> dict:
        d = dict(z)
        d["anhaenge"] = json.loads(d["anhaenge"] or "[]")
        try:
            d["ergebnis"] = json.loads(d["ergebnis"]) if d["ergebnis"] else None
        except ValueError:
            d["ergebnis"] = None
        return d

    def liste(self) -> list[dict]:
        with self._sperre:
            return [self._zeile(z) for z in self._db.execute("SELECT * FROM briefe ORDER BY gesendet DESC, id DESC")]

    def eintrag(self, bid: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM briefe WHERE id = ?", (bid,)).fetchone()
        return self._zeile(z) if z else None

    def stand(self) -> dict:
        with self._sperre:
            return {s: n for s, n in self._db.execute("SELECT status, COUNT(*) FROM briefe GROUP BY status")}

    def anhang_ordner(self, bid: int) -> Path:
        return self.ordner / "schule" / "briefe" / str(int(bid))


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    schluessel = str(ordner)
    if schluessel not in _instanzen:
        _instanzen[schluessel] = Ablage(ordner)
    return _instanzen[schluessel]
