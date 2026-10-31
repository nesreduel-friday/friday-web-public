"""Recherche-Speicher (`data/recherchen.db`): Rechercheaufträge, Notizen und Berichte. Gelöscht wird NIE (der Status wechselt nur).

Ein Auftrag ist Eikes Frage; ein Hintergrundlauf (`planer`, `lauf`) arbeitet ihn ab. Limits im Code: 3 Aufträge am Tag, höchstens 5 offene,
höchstens 2 Versuche (ein Neustart mitten im Lauf zählt als Versuch)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import timezone

from kern import config
from kern.personen import register

STATUS = ("wartend", "laeuft", "fertig", "fehler", "abgebrochen")
TAGESLIMIT, MAX_OFFEN, MAX_VERSUCHE = 3, 5, 2
FRAGE_MAX, HINWEISE_MAX, MAX_NOTIZEN, NOTIZ_MAX, BERICHT_MAX = 600, 600, 40, 800, 8000
SICHERHEIT = ("belegt", "teilweise", "unsicher")

SCHEMA = """
CREATE TABLE IF NOT EXISTS recherchen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, frage TEXT NOT NULL, hinweise TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'wartend', erstellt TEXT NOT NULL, beginn TEXT, ende TEXT, versuche INTEGER NOT NULL DEFAULT 0,
    notizen TEXT NOT NULL DEFAULT '[]', schritte TEXT NOT NULL DEFAULT '{}', bericht TEXT NOT NULL DEFAULT '',
    quellen TEXT NOT NULL DEFAULT '[]', sicherheit TEXT NOT NULL DEFAULT '', offen TEXT NOT NULL DEFAULT '',
    fehler TEXT NOT NULL DEFAULT '', gedanke_id INTEGER
);
"""
_sperre = threading.RLock()


class RechercheFehler(Exception):
    """Mit deutscher Meldung, die Friday Eike sagen darf."""


def _jetzt() -> str:
    return config.jetzt().astimezone(timezone.utc).isoformat(timespec="seconds")


def _verbinden() -> sqlite3.Connection:
    ordner = config.daten_ordner()
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / "recherchen.db"
    if not pfad.exists():
        os.close(os.open(pfad, os.O_WRONLY | os.O_CREAT, 0o600))
    db = sqlite3.connect(pfad, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _sauber(t, laenge: int, zeilen: bool = False) -> str:
    s = register._sichtbar(str(t or ""), zeilen=zeilen)
    s = "\n".join(" ".join(z.split()) for z in s.splitlines()).strip() if zeilen else " ".join(s.split())
    return s[:laenge]


def _zeile(z) -> dict:
    d = dict(z)
    for k in ("notizen", "schritte", "quellen"):
        d[k] = json.loads(d[k])
    return d


def anlegen(frage, hinweise="") -> dict:
    f, h = _sauber(frage, 10_000), _sauber(hinweise, 10_000)
    if not f:
        raise RechercheFehler("Die Frage fehlt.")
    if len(f) > FRAGE_MAX or len(h) > HINWEISE_MAX:
        raise RechercheFehler(f"Frage und Hinweise dürfen höchstens {FRAGE_MAX} Zeichen lang sein.")
    jetzt = config.jetzt()
    tag_ab = jetzt.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")      # deutscher Tag
    with _sperre, _verbinden() as db:
        if db.execute("SELECT COUNT(*) FROM recherchen WHERE erstellt>=?", (tag_ab,)).fetchone()[0] >= TAGESLIMIT:
            raise RechercheFehler(f"Heute habe ich schon {TAGESLIMIT} Recherchen angelegt — das reicht für einen Tag.")
        if db.execute("SELECT COUNT(*) FROM recherchen WHERE status IN ('wartend','laeuft')").fetchone()[0] >= MAX_OFFEN:
            raise RechercheFehler(f"Es warten schon {MAX_OFFEN} Recherchen. Erst wenn eine fertig ist, nehme ich eine neue an.")
        rid = db.execute("INSERT INTO recherchen (frage, hinweise, erstellt) VALUES (?,?,?)", (f, h, _jetzt())).lastrowid
    return holen(rid)


def holen(rid) -> dict | None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT * FROM recherchen WHERE id=?", (rid,)).fetchone()
    return _zeile(z) if z else None


def liste(limit: int = 20) -> list[dict]:
    with _sperre, _verbinden() as db:
        return [_zeile(z) for z in db.execute("SELECT * FROM recherchen ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def naechster_wartender() -> dict | None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT * FROM recherchen WHERE status='wartend' ORDER BY id LIMIT 1").fetchone()
    return _zeile(z) if z else None


def beginnen(rid) -> dict:
    with _sperre, _verbinden() as db:
        db.execute("UPDATE recherchen SET status='laeuft', beginn=? WHERE id=? AND status='wartend'", (_jetzt(), rid))
    return holen(rid)


def notiz(rid, text, quelle="") -> None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT notizen FROM recherchen WHERE id=?", (rid,)).fetchone()
        if not z:
            return
        n = json.loads(z["notizen"])
        if len(n) < MAX_NOTIZEN and _sauber(text, NOTIZ_MAX):
            n.append({"text": _sauber(text, NOTIZ_MAX), "quelle": _sauber(quelle, 300)})
            db.execute("UPDATE recherchen SET notizen=? WHERE id=?", (json.dumps(n, ensure_ascii=False), rid))


def zaehlen(rid, art: str) -> None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT schritte FROM recherchen WHERE id=?", (rid,)).fetchone()
        if z:
            s = json.loads(z["schritte"])
            s[art] = s.get(art, 0) + 1
            db.execute("UPDATE recherchen SET schritte=? WHERE id=?", (json.dumps(s), rid))


def abschliessen(rid, bericht, quellen, sicherheit, offen) -> bool:
    """Nur ein laufender Auftrag wird fertig — ein inzwischen abgebrochener bleibt abgebrochen."""
    sicherheit = sicherheit if sicherheit in SICHERHEIT else "unsicher"
    with _sperre, _verbinden() as db:
        return db.execute("UPDATE recherchen SET status='fertig', ende=?, bericht=?, quellen=?, sicherheit=?, offen=? WHERE id=? AND status IN ('laeuft','wartend')",
                          (_jetzt(), _sauber(bericht, BERICHT_MAX, zeilen=True), json.dumps(list(quellen or []), ensure_ascii=False), sicherheit,
                           _sauber(offen, 1000), rid)).rowcount > 0


def scheitern(rid, fehler) -> bool:
    with _sperre, _verbinden() as db:
        return db.execute("UPDATE recherchen SET status='fehler', ende=?, fehler=? WHERE id=? AND status IN ('laeuft','wartend')",
                          (_jetzt(), _sauber(fehler, 300), rid)).rowcount > 0


def abbrechen(rid) -> bool:
    with _sperre, _verbinden() as db:
        return db.execute("UPDATE recherchen SET status='abgebrochen', ende=? WHERE id=? AND status IN ('wartend','laeuft')",
                          (_jetzt(), rid)).rowcount > 0


def laufende_zuruecksetzen() -> int:
    """Beim Start: ein Lauf, der noch auf „laeuft“ steht, wurde unterbrochen — zurück in die Warteschlange, nach 2 Versuchen Fehler."""
    n = 0
    with _sperre, _verbinden() as db:
        for z in db.execute("SELECT id, versuche FROM recherchen WHERE status='laeuft'").fetchall():
            v = z["versuche"] + 1
            if v >= MAX_VERSUCHE:
                db.execute("UPDATE recherchen SET status='fehler', versuche=?, ende=?, fehler=? WHERE id=?",
                           (v, _jetzt(), "Unterbrochen (Neustart), zu viele Versuche", z["id"]))
            else:
                db.execute("UPDATE recherchen SET status='wartend', versuche=? WHERE id=?", (v, z["id"]))
            n += 1
    return n


def gedanke_setzen(rid, gedanke_id) -> None:
    with _sperre, _verbinden() as db:
        db.execute("UPDATE recherchen SET gedanke_id=? WHERE id=?", (gedanke_id, rid))
