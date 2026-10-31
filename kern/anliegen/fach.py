"""Anliegen-Fach — was Friday im Gespräch erwähnen darf. Löscht nie.

Der Server wählt aus (`auswaehlen`), das Modell formuliert nur das Wie. Pro Gespräch höchstens ein Anliegen, pro Tag höchstens zwei
normale, in der Ruhezeit nur hohe; nach drei Einblendungen ohne Erwähnung oder nach Ablauf verfällt es. Zeiten als UTC gespeichert,
„heute“ zählt nach dem Berliner Tag.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kern import config
from kern.denken.buch import _sauber, gleicher_sinn, zu_lang

EIKE = "eike"
TITEL_MAX = 80
TEXT_MAX = 300
GUELTIG_STUNDEN = 48
MAX_EINBLENDUNGEN = 3
MAX_NORMAL_PRO_TAG = 2
SPAETER_STUNDEN = 4
HINWEIS_REST = " (Mehr im Reiter Gedanken.)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS anliegen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empfaenger TEXT NOT NULL, quelle TEXT NOT NULL, titel TEXT NOT NULL, text TEXT NOT NULL,
    dringlichkeit TEXT NOT NULL DEFAULT 'normal', fremd INTEGER NOT NULL DEFAULT 0,
    erstellt TEXT NOT NULL, gueltig_bis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'offen', einblendungen INTEGER NOT NULL DEFAULT 0,
    eingeblendet_in INTEGER, zuletzt_eingeblendet TEXT, zurueck_bis TEXT, erledigt TEXT
);
CREATE INDEX IF NOT EXISTS anliegen_empfaenger ON anliegen(empfaenger, status);
"""


def _jetzt() -> datetime:
    return config.jetzt().astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _tagesbeginn() -> str:
    return _iso(config.jetzt().replace(hour=0, minute=0, second=0, microsecond=0))


def _ist_ruhezeit() -> bool:
    from kern.dienste import benachrichtigungen
    return benachrichtigungen.still()


def kuerzen_satz(text: str, maximal: int = TEXT_MAX) -> str:
    """Passt der Text nicht, bleiben ganze Sätze plus Verweis auf den Reiter — nie ein Schnitt mitten im Wort."""
    t = " ".join(str(text or "").split())
    if len(t) <= maximal:
        return t
    platz = maximal - len(HINWEIS_REST)
    satz = ""
    for teil in re.split(r"(?<=[.!?]) ", t):
        if len(satz) + len(teil) + (1 if satz else 0) > platz:
            break
        satz = f"{satz} {teil}".strip()
    if not satz:
        satz = t[:platz - 1].rsplit(" ", 1)[0] + "…"
    return satz + HINWEIS_REST


class Fach:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(ordner / "anliegen.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    @staticmethod
    def _dict(z) -> dict:
        d = dict(z)
        if "fremd" in d:
            d["fremd"] = bool(d["fremd"])
        return d

    def _alle(self, sql: str, werte: tuple = ()) -> list[dict]:
        with self._sperre:
            return [self._dict(z) for z in self._db.execute(sql, werte).fetchall()]

    def _schreiben(self, sql: str, werte: tuple = ()) -> int:
        with self._sperre:
            c = self._db.execute(sql, werte)
            self._db.commit()
            return c.lastrowid

    def eintrag(self, aid: int) -> dict | None:
        z = self._alle("SELECT * FROM anliegen WHERE id = ?", (aid,))
        return z[0] if z else None

    def offene(self, empfaenger: str) -> list[dict]:
        return self._alle("SELECT * FROM anliegen WHERE empfaenger = ? AND status IN ('offen', 'zurueckgestellt') "
                          "ORDER BY id", (empfaenger,))

    def anlegen(self, empfaenger: str, quelle: str, titel: str, text: str, dringlichkeit: str = "normal",
                fremd: bool = False, gueltig_stunden: int = GUELTIG_STUNDEN) -> dict:
        empfaenger = (empfaenger or "").strip()
        if not empfaenger:
            return {"ok": False, "grund": "Ein Anliegen braucht einen Empfänger."}
        if absage := zu_lang(titel, TITEL_MAX) or zu_lang(text, TEXT_MAX):
            return {"ok": False, "grund": absage}
        titel, text = _sauber(titel, TITEL_MAX), _sauber(text, TEXT_MAX)
        if not titel or not text:
            return {"ok": False, "grund": "Ein Anliegen braucht Titel und Text."}
        for a in self.offene(empfaenger):
            if gleicher_sinn(a["titel"], titel) or gleicher_sinn(a["text"], text):
                return {"ok": False, "grund": "Dazu gibt es schon ein Anliegen."}
        jetzt = _jetzt()
        aid = self._schreiben(
            "INSERT INTO anliegen (empfaenger, quelle, titel, text, dringlichkeit, fremd, erstellt, gueltig_bis) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (empfaenger, quelle, titel, text, "hoch" if dringlichkeit == "hoch" else "normal", 1 if fremd else 0,
             _iso(jetzt), _iso(jetzt + timedelta(hours=gueltig_stunden))))
        return {"ok": True, "id": aid}

    def auswaehlen(self, empfaenger: str, gespraech: int | None, still: bool | None = None) -> dict | None:
        jetzt = _iso(_jetzt())
        with self._sperre:
            self._db.execute("UPDATE anliegen SET status='verfallen' WHERE status IN ('offen','zurueckgestellt') "
                             "AND gueltig_bis < ?", (jetzt,))
            self._db.execute("UPDATE anliegen SET status='verfallen' WHERE status IN ('offen','zurueckgestellt') "
                             "AND einblendungen >= ? AND (eingeblendet_in IS NULL OR eingeblendet_in IS NOT ?)",
                             (MAX_EINBLENDUNGEN, gespraech))
            self._db.commit()
        if gespraech is None:
            return None
        schon = self._alle("SELECT * FROM anliegen WHERE empfaenger = ? AND eingeblendet_in = ? "
                           "AND status IN ('offen','zurueckgestellt') ORDER BY id LIMIT 1", (empfaenger, gespraech))
        if schon:
            return schon[0]
        if still is None:
            still = _ist_ruhezeit()
        heute_normal = self._alle(
            "SELECT COUNT(*) AS n FROM anliegen WHERE empfaenger = ? AND dringlichkeit = 'normal' AND zuletzt_eingeblendet >= ?",
            (empfaenger, _tagesbeginn()))[0]["n"]
        for a in self._alle(
                "SELECT * FROM anliegen WHERE empfaenger = ? AND einblendungen < ? AND (status = 'offen' OR "
                "(status = 'zurueckgestellt' AND zurueck_bis <= ?)) ORDER BY (dringlichkeit = 'hoch') DESC, id ASC",
                (empfaenger, MAX_EINBLENDUNGEN, jetzt)):
            if a["dringlichkeit"] != "hoch" and (still or heute_normal >= MAX_NORMAL_PRO_TAG):
                continue
            self._schreiben("UPDATE anliegen SET einblendungen = einblendungen + 1, eingeblendet_in = ?, "
                            "zuletzt_eingeblendet = ? WHERE id = ?", (gespraech, jetzt, a["id"]))
            return self.eintrag(a["id"])
        return None

    def abhaken(self, aid: int, empfaenger: str, wie: str) -> dict:
        a = self.eintrag(aid)
        if not a or a["empfaenger"] != empfaenger or a["status"] not in ("offen", "zurueckgestellt"):
            return {"ok": False, "grund": "Dieses Anliegen gibt es nicht oder es ist schon erledigt."}
        jetzt = _jetzt()
        if wie == "erwaehnt":
            self._schreiben("UPDATE anliegen SET status='erwaehnt', erledigt=? WHERE id=?", (_iso(jetzt), aid))
        elif wie == "spaeter":
            self._schreiben("UPDATE anliegen SET status='zurueckgestellt', zurueck_bis=?, eingeblendet_in=NULL WHERE id=?",
                            (_iso(jetzt + timedelta(hours=SPAETER_STUNDEN)), aid))
        elif wie == "nicht_mehr":
            self._schreiben("UPDATE anliegen SET status='verfallen', erledigt=? WHERE id=?", (_iso(jetzt), aid))
        else:
            return {"ok": False, "grund": "wie muss erwähnt, später oder nicht_mehr sein."}
        return {"ok": True}


_instanzen: dict[str, Fach] = {}


def standard() -> Fach:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Fach(ordner)
    return _instanzen[str(ordner)]
