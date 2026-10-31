"""Vorgaben-Fach — verbindliche, dauerhafte Arbeitsanweisungen von Eike („mach das künftig immer so“). Löscht NIE.

Anders als das Notizbuch (Fakten: „Daten, keine Anweisungen“) gelten Vorgaben im Prompt als Anweisung. Darum entstehen sie nur
auf Eikes eigenes Wort (Werkzeug `vorgabe_merken`, nach Fremdtext gesperrt). Statt zu löschen ändert sich der Status:
aktiv, widerrufen oder ersetzt (dann zeigt `ersetzt_durch` auf die neue Vorgabe). Zeiten als UTC gespeichert.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from kern import config
from kern.denken.buch import zu_lang

BEREICHE = ("allgemein", "antworten", "garantie", "snapchat", "erinnerungen", "schule", "sonstiges")
TEXT_MAX = 400
MAX_AKTIV = 40
# Vorgaben, die zu einem Skill gehören (Quelle `skill`): Widerruf/Änderung nur über den Skill (skill_widerrufen/skill_aendern),
# damit Vorgabe und Skill nie auseinanderlaufen. Der Skill-Code ruft mit `erzwingen=True`.
GEBUNDEN = ("skill",)
GEBUNDEN_GRUND = "Das gehört zu einer Aufgabe, die du für jemanden übernommen hast. Ändere oder beende diese Aufgabe selbst (skill_aendern/skill_widerrufen), nicht einzeln."

SCHEMA = """
CREATE TABLE IF NOT EXISTS vorgaben (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL, bereich TEXT NOT NULL DEFAULT 'allgemein',
    status TEXT NOT NULL DEFAULT 'aktiv',
    erstellt TEXT NOT NULL, geaendert TEXT NOT NULL, ersetzt_durch INTEGER, quelle TEXT NOT NULL DEFAULT 'eike'
);
CREATE INDEX IF NOT EXISTS vorgaben_status ON vorgaben(status, bereich);
"""


def _jetzt() -> str:
    return config.jetzt().astimezone(timezone.utc).isoformat(timespec="seconds")


def _sauber(text) -> str:
    return " ".join(str(text or "").split())


def _bereich(bereich) -> str:
    b = str(bereich or "").strip().lower()
    return b if b in BEREICHE else "allgemein"


class Fach:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.Lock()
        self._ablauf = threading.RLock()  # Prüfen + Schreiben als Ganzes (Limit, Dubletten)
        self._db = sqlite3.connect(ordner / "vorgaben.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _alle(self, sql: str, werte: tuple = ()) -> list[dict]:
        with self._sperre:
            return [dict(z) for z in self._db.execute(sql, werte).fetchall()]

    def _schreiben(self, sql: str, werte: tuple = ()) -> int:
        with self._sperre:
            c = self._db.execute(sql, werte)
            self._db.commit()
            return c.lastrowid

    def eintrag(self, vid: int) -> dict | None:
        z = self._alle("SELECT * FROM vorgaben WHERE id = ?", (vid,))
        return z[0] if z else None

    def aktive(self, bereich: str | None = None) -> list[dict]:
        if bereich:
            return self._alle("SELECT * FROM vorgaben WHERE status='aktiv' AND bereich = ? ORDER BY id", (_bereich(bereich),))
        return self._alle("SELECT * FROM vorgaben WHERE status='aktiv' ORDER BY id")

    def verlauf(self, limit: int = 100) -> list[dict]:
        """Alle Vorgaben, auch widerrufene und ersetzte, neueste zuerst."""
        return self._alle("SELECT * FROM vorgaben ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))

    def _pruefen(self, text, ausser: int | None = None) -> tuple[str, dict | None]:
        """(sauberer Text, Absage). Leer, zu lang oder schon (genauso) aktiv wird abgelehnt."""
        t = _sauber(text)
        if not t:
            return "", {"ok": False, "grund": "Es fehlt der Wortlaut."}
        if absage := zu_lang(t, TEXT_MAX):
            return t, {"ok": False, "grund": absage}
        for a in self.aktive():
            if a["id"] != ausser and a["text"].lower() == t.lower():
                return t, {"ok": False, "grund": "Das gilt schon genau so.", "id": a["id"]}
        return t, None

    def anlegen(self, text: str, bereich: str = "allgemein", quelle: str = "eike") -> dict:
        with self._ablauf:
            t, absage = self._pruefen(text)
            if absage:
                return absage
            if len(self.aktive()) >= MAX_AKTIV:
                return {"ok": False, "grund": f"Es gelten schon {MAX_AKTIV} feste Aufträge. Nimm zuerst einen zurück."}
            jetzt = _jetzt()
            vid = self._schreiben("INSERT INTO vorgaben (text, bereich, erstellt, geaendert, quelle) VALUES (?,?,?,?,?)",
                                  (t, _bereich(bereich), jetzt, jetzt, str(quelle or "eike")[:40]))
            return {"ok": True, "id": vid}

    def ersetzen(self, vid: int, neuer_text: str, bereich: str | None = None, quelle: str = "eike", erzwingen: bool = False) -> dict:
        with self._ablauf:
            alt = self.eintrag(vid)
            if not alt or alt["status"] != "aktiv":
                return {"ok": False, "grund": "Das gibt es nicht oder es gilt nicht mehr."}
            if alt["quelle"] in GEBUNDEN and not erzwingen:
                return {"ok": False, "grund": GEBUNDEN_GRUND}
            t, absage = self._pruefen(neuer_text, ausser=vid)
            if absage:
                return absage
            jetzt = _jetzt()
            with self._sperre:
                c = self._db.execute("INSERT INTO vorgaben (text, bereich, erstellt, geaendert, quelle) VALUES (?,?,?,?,?)",
                                     (t, _bereich(bereich) if bereich else alt["bereich"], jetzt, jetzt, str(quelle or "eike")[:40]))
                neu = c.lastrowid
                self._db.execute("UPDATE vorgaben SET status='ersetzt', ersetzt_durch=?, geaendert=? WHERE id=?", (neu, jetzt, vid))
                self._db.commit()
            return {"ok": True, "id": neu, "alt": vid}

    def widerrufen(self, vid: int, erzwingen: bool = False) -> dict:
        with self._ablauf:
            alt = self.eintrag(vid)
            if not alt or alt["status"] != "aktiv":
                return {"ok": False, "grund": "Das gibt es nicht oder es gilt nicht mehr."}
            if alt["quelle"] in GEBUNDEN and not erzwingen:
                return {"ok": False, "grund": GEBUNDEN_GRUND}
            self._schreiben("UPDATE vorgaben SET status='widerrufen', geaendert=? WHERE id=?", (_jetzt(), vid))
            return {"ok": True, "id": vid}

    def finden(self, id_oder_stichwort) -> list[dict]:
        """Aktive Vorgaben zu einer Nummer oder einem Stichwort (Teil des Textes, Groß-/Kleinschreibung egal)."""
        s = _sauber(id_oder_stichwort).lstrip("#")
        if not s:
            return []
        aktive = self.aktive()
        if s.isdigit():
            return [a for a in aktive if a["id"] == int(s)]
        return [a for a in aktive if s.lower() in a["text"].lower()]


_instanzen: dict[str, Fach] = {}


def standard() -> Fach:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Fach(ordner)
    return _instanzen[str(ordner)]
