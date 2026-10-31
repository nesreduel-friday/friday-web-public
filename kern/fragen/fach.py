"""Fragen an Eike — wenn Friday selbstständig etwas tut und es von Eikes Vorliebe abhängt. Löscht nie.

Eine Frage hat bis zu vier kurze Optionen; Eike tippt eine oder schreibt frei. Grenzen im Code: höchstens 3 offene Fragen, 3 neue je
Berliner Tag, keine Dubletten gegen offene und in den letzten 48 h beantwortete. Zeiten als UTC gespeichert. Optionen werden 1-basiert
gezählt („Option 2“).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kern import config
from kern.denken.buch import _sauber, gleicher_sinn, zu_lang

QUELLEN = ("denkrunde", "chat")
TITEL_MAX = 80
TEXT_MAX = 300
OPTION_MAX = 40
OPTIONEN_MAX = 4
OFFEN_MAX = 3
NEU_PRO_TAG = 3
GUELTIG_STUNDEN = 72
DUBLETTE_STUNDEN = 48

SCHEMA = """
CREATE TABLE IF NOT EXISTS fragen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quelle TEXT NOT NULL, titel TEXT NOT NULL, text TEXT NOT NULL, optionen TEXT NOT NULL DEFAULT '[]',
    bezug TEXT NOT NULL DEFAULT '', fremd INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'offen', antwort TEXT, antwort_art TEXT,
    erstellt TEXT NOT NULL, gueltig_bis TEXT NOT NULL, beantwortet TEXT
);
"""


def _jetzt() -> datetime:
    return config.jetzt().astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _tagesbeginn() -> str:
    return _iso(config.jetzt().replace(hour=0, minute=0, second=0, microsecond=0))


def _bezug_ok(bezug: str) -> bool:
    if not bezug:
        return True
    art, _, nummer = bezug.partition(":")
    return art in ("faden", "vorhaben") and nummer.isdigit()


class Fach:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.RLock()
        self._db = sqlite3.connect(ordner / "fragen.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    @staticmethod
    def _dict(z) -> dict:
        d = dict(z)
        if "fremd" in d:
            d["fremd"] = bool(d["fremd"])
            d["optionen"] = json.loads(d["optionen"] or "[]")
        return d

    def _alle(self, sql: str, werte: tuple = ()) -> list[dict]:
        with self._sperre:
            return [self._dict(z) for z in self._db.execute(sql, werte).fetchall()]

    def eintrag(self, fid: int) -> dict | None:
        z = self._alle("SELECT * FROM fragen WHERE id = ?", (fid,))
        return z[0] if z else None

    def verfallen_markieren(self) -> int:
        with self._sperre:
            c = self._db.execute("UPDATE fragen SET status = 'verfallen' WHERE status = 'offen' AND gueltig_bis <= ?",
                                 (_iso(_jetzt()),))
            self._db.commit()
            return c.rowcount

    def offene(self) -> list[dict]:
        self.verfallen_markieren()
        return self._alle("SELECT * FROM fragen WHERE status = 'offen' ORDER BY id")

    def beantwortet_seit(self, stunden: int) -> list[dict]:
        grenze = _iso(_jetzt() - timedelta(hours=stunden))
        return self._alle("SELECT * FROM fragen WHERE status = 'beantwortet' AND beantwortet >= ? ORDER BY id", (grenze,))

    def verfallene_seit(self, stunden: int) -> list[dict]:
        self.verfallen_markieren()
        grenze = _iso(_jetzt() - timedelta(hours=stunden))
        return self._alle("SELECT * FROM fragen WHERE status = 'verfallen' AND gueltig_bis >= ? ORDER BY id", (grenze,))

    def stellen(self, quelle: str, titel: str, text: str, optionen: list[str] | None = None, bezug: str = "",
                fremd: bool = False, gueltig_stunden: int = GUELTIG_STUNDEN) -> dict:
        if quelle not in QUELLEN:
            return {"ok": False, "grund": f"quelle muss eine von {', '.join(QUELLEN)} sein."}
        if absage := zu_lang(titel, TITEL_MAX) or zu_lang(text, TEXT_MAX):
            return {"ok": False, "grund": absage}
        titel, text = _sauber(titel, TITEL_MAX), _sauber(text, TEXT_MAX)
        if not titel or not text:
            return {"ok": False, "grund": "Eine Frage braucht Titel und Text."}
        optionen = [o for o in (optionen or []) if str(o or "").strip()]
        if len(optionen) > OPTIONEN_MAX:
            return {"ok": False, "grund": f"Höchstens {OPTIONEN_MAX} Antwortoptionen."}
        if any(zu_lang(o, OPTION_MAX) for o in optionen):
            return {"ok": False, "grund": f"Eine Option darf höchstens {OPTION_MAX} Zeichen haben."}
        optionen = [_sauber(o, OPTION_MAX) for o in optionen]
        bezug = str(bezug or "").strip()
        if not _bezug_ok(bezug):
            return {"ok": False, "grund": "bezug ist leer oder faden:<Nummer> bzw. vorhaben:<Nummer>."}
        with self._sperre:                          # Prüfen und Einfügen zusammen: parallele Aufrufe halten die Limits
            offen = self.offene()
            if len(offen) >= OFFEN_MAX:
                return {"ok": False, "grund": f"Es sind schon {OFFEN_MAX} Fragen offen; warte auf Eikes Antwort."}
            if self._alle("SELECT COUNT(*) AS n FROM fragen WHERE erstellt >= ?", (_tagesbeginn(),))[0]["n"] >= NEU_PRO_TAG:
                return {"ok": False, "grund": f"Heute sind schon {NEU_PRO_TAG} Fragen gestellt; morgen wieder."}
            kurz = _iso(_jetzt() - timedelta(hours=DUBLETTE_STUNDEN))
            for a in offen + self._alle("SELECT * FROM fragen WHERE (status = 'beantwortet' AND beantwortet >= ?) "
                                        "OR (status = 'verfallen' AND gueltig_bis >= ?)", (kurz, kurz)):
                if gleicher_sinn(a["titel"], titel):
                    return {"ok": False, "grund": "Das habe ich Eike schon gefragt."}
            jetzt = _jetzt()
            c = self._db.execute(
                "INSERT INTO fragen (quelle, titel, text, optionen, bezug, fremd, erstellt, gueltig_bis) VALUES (?,?,?,?,?,?,?,?)",
                (quelle, titel, text, json.dumps(optionen, ensure_ascii=False), bezug, 1 if fremd else 0, _iso(jetzt),
                 _iso(jetzt + timedelta(hours=max(1, int(gueltig_stunden))))))
            self._db.commit()
            return {"ok": True, "id": c.lastrowid}

    def beantworten(self, fid: int, option: int | None = None, text: str = "") -> dict:
        text = str(text or "").strip()
        if (option is None) == (not text):
            return {"ok": False, "grund": "Entweder eine Option oder eine eigene Antwort, nicht beides und nicht nichts."}
        self.verfallen_markieren()
        f = self.eintrag(fid)
        if not f:
            return {"ok": False, "grund": "Diese Frage kenne ich nicht."}
        if f["status"] != "offen":
            return {"ok": False, "grund": "Diese Frage ist schon beantwortet oder abgelaufen."}
        if option is not None:
            try:
                nr = int(option)
            except (TypeError, ValueError):
                return {"ok": False, "grund": "Die Option muss eine Zahl sein."}
            if not 1 <= nr <= len(f["optionen"]):
                return {"ok": False, "grund": f"Es gibt nur {len(f['optionen'])} Optionen."}
            antwort, art = f["optionen"][nr - 1], "option"
        else:
            if absage := zu_lang(text, TEXT_MAX):
                return {"ok": False, "grund": absage}
            antwort, art = _sauber(text, TEXT_MAX), "frei"
        with self._sperre:
            c = self._db.execute(
                "UPDATE fragen SET status = 'beantwortet', antwort = ?, antwort_art = ?, beantwortet = ? "
                "WHERE id = ? AND status = 'offen'", (antwort, art, _iso(_jetzt()), fid))
            self._db.commit()
            if not c.rowcount:
                return {"ok": False, "grund": "Diese Frage ist schon beantwortet oder abgelaufen."}
        return {"ok": True, "id": fid, "antwort": antwort, "art": art}


_instanzen: dict[str, Fach] = {}


def standard() -> Fach:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Fach(ordner)
    return _instanzen[str(ordner)]
