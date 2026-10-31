"""Rundnachrichten an Personen — generisch, ohne feste Fähigkeit (12.10.2026).

`data/rundnachrichten.db` (600) merkt, wer zu einem Schlüssel schon bekam: pro (Schlüssel, Person) höchstens einmal, auch nach
einem Neustart. Zugestellt wird NIE direkt, sondern als Snapchat-Erinnerung (`erinnerungen.py`): dort gelten Schreibrecht,
Sendeprüfung, Nachtruhe, Limits je Person, Bestätigung und der Rückfall an Eike. Vorschauen (Policy `vorschau_und_ja`) warten hier
auf Eikes Ja (`rundnachricht_freigeben`). Gruppenregeln kommen ausschließlich aus `fach.GRUPPEN` (Code) plus `person:<id>`."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import timezone

from kern import config, erinnerungen
from kern.personen import register
from kern.skills import fach

MAX_EMPFAENGER = 20
TEXT_MAX = erinnerungen.SNAP_TEXT_MAX
SCHLUESSEL_MAX = 60
VORSCHAU_TAGE = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS zugestellt (
    schluessel TEXT NOT NULL, person_id TEXT NOT NULL, erinnerung_id INTEGER, zeit TEXT NOT NULL,
    PRIMARY KEY (schluessel, person_id)
);
CREATE TABLE IF NOT EXISTS vorschauen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, schluessel TEXT NOT NULL, text TEXT NOT NULL, personen TEXT NOT NULL,
    absender TEXT NOT NULL, erstellt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'offen'   -- offen | freigegeben | verfallen
);
"""


def schluessel_sauber(s) -> str:
    return re.sub(r"[^a-z0-9_.\-]+", "_", register._sichtbar(str(s or "")).strip().lower())[:SCHLUESSEL_MAX].strip("_")


def text_sauber(s) -> str:
    return " ".join(register._sichtbar(str(s or "")).split())


def _jetzt() -> str:
    return config.jetzt().astimezone(timezone.utc).isoformat(timespec="seconds")


class Ablage:
    def __init__(self, ordner):
        ordner.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(ordner / "rundnachrichten.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        try:
            (ordner / "rundnachrichten.db").chmod(0o600)
        except OSError:
            pass

    def bekam(self, schluessel: str) -> set[str]:
        with self._sperre:
            return {z[0] for z in self._db.execute("SELECT person_id FROM zugestellt WHERE schluessel=?", (schluessel,))}

    def belegen(self, schluessel: str, pid: str) -> bool:
        """Atomar: True nur für den, der den Platz zuerst bekommt."""
        with self._sperre, self._db:
            return self._db.execute("INSERT OR IGNORE INTO zugestellt (schluessel, person_id, zeit) VALUES (?,?,?)",
                                    (schluessel, pid, _jetzt())).rowcount == 1

    def erinnerung_vermerken(self, schluessel: str, pid: str, eid: int) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE zugestellt SET erinnerung_id=? WHERE schluessel=? AND person_id=?", (eid, schluessel, pid))

    def freigeben_platz(self, schluessel: str, pid: str) -> None:
        with self._sperre, self._db:
            self._db.execute("DELETE FROM zugestellt WHERE schluessel=? AND person_id=? AND erinnerung_id IS NULL", (schluessel, pid))

    def vorschau_anlegen(self, schluessel: str, text: str, pids: list[str], absender: str) -> int:
        with self._sperre, self._db:
            return self._db.execute("INSERT INTO vorschauen (schluessel, text, personen, absender, erstellt) VALUES (?,?,?,?,?)",
                                    (schluessel, text, json.dumps(pids), absender, _jetzt())).lastrowid

    def _vorschau(self, z) -> dict:
        d = dict(z)
        d["personen"] = json.loads(d["personen"])
        return d

    def offene_vorschauen(self, schluessel: str | None = None) -> list[dict]:
        sql, w = "SELECT * FROM vorschauen WHERE status='offen'", ()
        if schluessel:
            sql, w = sql + " AND schluessel=?", (schluessel,)
        with self._sperre:
            return [self._vorschau(z) for z in self._db.execute(sql + " ORDER BY id", w)]

    def vorschau_status(self, vid: int, status: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE vorschauen SET status=? WHERE id=?", (status, vid))


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Ablage(ordner)
    return _instanzen[str(ordner)]


def regel_personen(regel) -> tuple[list[dict], list[list[str]], str]:
    """(Personen, benutzte Begriffe als Alternativen je Teil, Absage). Gruppen nur aus `fach.GRUPPEN`; sonst `person:<id>`, eine Person-Id oder ein exakter
    Name aus dem Register. Pausierte Personen fallen weg."""
    teile = [str(r).strip() for r in (regel if isinstance(regel, (list, tuple)) else [regel]) if str(r or "").strip()]
    if not teile:
        return [], [], "Wen? Nenne eine Gruppenregel oder Personen."
    alle, aus, begriffe = register.liste(), [], []
    for t in teile:
        if t.lower().startswith("gruppe:"):
            t = t[7:].strip()
        k = t.lower()
        if k in fach.GRUPPEN:
            treffer = []
            for p in alle:
                try:
                    if fach.GRUPPEN[k](p):
                        treffer.append(p)
                except Exception:  # noqa: BLE001 — im Zweifel nicht dabei
                    pass
            begriffe.append([k])
        else:
            ident = t[7:].strip() if k.startswith("person:") else t
            treffer = [p for p in alle if ident == p["id"] or register.schluessel(ident) == register.schluessel(p["name"])]
            if len(treffer) != 1:
                return [], [], (f"„{t}“ ist keine Gruppenregel (möglich: {', '.join(fach.GRUPPEN) or 'keine'}) und keine "
                                f"eindeutige Person.")
            begriffe.append([treffer[0]["id"], treffer[0]["name"]])
        aus += [p for p in treffer if p["id"] not in {x["id"] for x in aus}]
    aus = [p for p in aus if not p.get("pausiert")]
    if len(aus) > MAX_EMPFAENGER:
        return [], [], f"Das sind {len(aus)} Personen — höchstens {MAX_EMPFAENGER} je Rundnachricht."
    return aus, begriffe, ""


def offen_fuer(regel, schluessel: str) -> list[dict]:
    """Wer aus der Regel hat zu diesem Schlüssel noch nichts bekommen? (Für Hintergrundlauf und Skills; nur Id und Name.)"""
    personen, _b, _a = regel_personen(regel)
    bekam = standard().bekam(schluessel_sauber(schluessel))
    return [{"id": p["id"], "name": p["name"]} for p in personen if p["id"] not in bekam]


def zustellen(schluessel: str, text: str, personen: list[dict]) -> dict:
    """Legt für jede Person, die noch nichts bekam, eine fällige Snapchat-Erinnerung an — der Erinnerungslauf stellt zu.
    {"gelegt": [Namen], "schon": [Namen], "nicht": [(Name, Grund)]}"""
    a, e = standard(), erinnerungen.standard()
    r = {"gelegt": [], "schon": [], "nicht": []}
    for p in personen:
        pid, name = p["id"], p["name"]
        if not ((p.get("kanaele") or {}).get("snapchat") or "").strip():
            r["nicht"].append((name, "hat keinen Snapchat-Namen"))
            continue
        if e.offene_an(pid) >= erinnerungen.MAX_OFFEN_JE_PERSON or e.offene_an_personen() >= erinnerungen.MAX_OFFEN_PERSONEN:
            r["nicht"].append((name, "es sind schon zu viele Nachrichten unterwegs — später noch einmal"))
            continue
        if not a.belegen(schluessel, pid):
            r["schon"].append(name)
            continue
        try:
            wann, _v = erinnerungen.person_zeit(config.jetzt())
            eid = e.anlegen(f"Rundnachricht {schluessel}"[:80], text, wann, kanal="snapchat", empfaenger=pid)
            a.erinnerung_vermerken(schluessel, pid, eid)
            r["gelegt"].append(name)
        except Exception as exc:  # noqa: BLE001
            a.freigeben_platz(schluessel, pid)
            r["nicht"].append((name, f"nicht angelegt ({type(exc).__name__})"))
    return r
