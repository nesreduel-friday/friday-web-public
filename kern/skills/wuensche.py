"""Medienwünsche von Personen (`data/medienwuensche.db`, 600, löscht nie): wer wünschte sich wann was — Grundlage für das Tageslimit
je Person und gegen doppelte Wünsche. Der Wunsch selbst hat nie Befugnis: er ist ein Vorschlag an Eike oder (Policy `selbst`) der
bestehende Download."""

from __future__ import annotations

import sqlite3
import threading
from datetime import timezone

from kern import config
from kern.personen import register

TITEL_MAX = 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS wuensche (
    id INTEGER PRIMARY KEY AUTOINCREMENT, person_id TEXT NOT NULL, art TEXT NOT NULL, titel TEXT NOT NULL, jahr INTEGER,
    status TEXT NOT NULL, zeit TEXT NOT NULL      -- status: wartet (auf Eike) | gestartet (Download angelegt)
);
CREATE INDEX IF NOT EXISTS wuensche_person ON wuensche(person_id, zeit);
"""


def titel_sauber(t) -> str:
    """Wunschtitel sind Fremdtext: unsichtbare Zeichen, Zeilenumbrüche, Anführungszeichen und Klammern/Backticks weg, kurz."""
    s = register._sichtbar(str(t or ""))
    s = "".join(c for c in s if c not in "\"'„“”«»`<>[]{}|\\")
    return " ".join(s.split())[:TITEL_MAX]


class Ablage:
    def __init__(self, ordner):
        ordner.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(ordner / "medienwuensche.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        try:
            (ordner / "medienwuensche.db").chmod(0o600)
        except OSError:
            pass

    def heute(self, pid: str) -> int:
        tag = config.jetzt().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")
        with self._sperre:
            return self._db.execute("SELECT COUNT(*) FROM wuensche WHERE person_id=? AND zeit>=?", (pid, tag)).fetchone()[0]

    def gibt_es(self, pid: str, art: str, titel: str, jahr) -> bool:
        with self._sperre:
            return self._db.execute("SELECT 1 FROM wuensche WHERE person_id=? AND art=? AND titel=? AND jahr IS ?",
                                    (pid, art, titel, jahr)).fetchone() is not None

    def anlegen(self, pid: str, art: str, titel: str, jahr, status: str) -> int:
        with self._sperre, self._db:
            return self._db.execute("INSERT INTO wuensche (person_id, art, titel, jahr, status, zeit) VALUES (?,?,?,?,?,?)",
                                    (pid, art, titel, jahr, status,
                                     config.jetzt().astimezone(timezone.utc).isoformat(timespec="seconds"))).lastrowid

    def status_setzen(self, wid: int, status: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE wuensche SET status=? WHERE id=?", (status, wid))

    def alle(self, pid: str | None = None) -> list[dict]:
        with self._sperre:
            if pid:
                return [dict(z) for z in self._db.execute("SELECT * FROM wuensche WHERE person_id=? ORDER BY id", (pid,))]
            return [dict(z) for z in self._db.execute("SELECT * FROM wuensche ORDER BY id")]


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Ablage(ordner)
    return _instanzen[str(ordner)]
