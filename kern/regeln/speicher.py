"""Regel-Speicher (`data/regeln.db`): versioniert wird nicht, gelöscht wird NIE (Status wechselt nur).

Eine Regel ist Eikes Auftrag in seinen Worten plus feste Grenzen im Code: Kanal und Auslöser (eine Person bzw. ein freigegebener Chat),
feste Empfänger, erlaubte Aktionen aus einer Positivliste, Limits. Sie wirkt nur im Status `aktiv` (Eikes Ja). Ein Lauf je (Regel,
Ereignis) — das macht ein UNIQUE-Schlüssel, auch nach einem Neustart."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import timezone

from kern import config
from kern.personen import register

KANAELE = ("whatsapp", "snapchat", "zeit")
AKTIONEN = ("push", "antwort_an_ausloeser", "antwort_an_empfaenger", "kalender_eintragen", "snap_senden")
STATUS = ("entwurf", "aktiv", "pausiert", "widerrufen", "ersetzt", "erledigt")
LAUF_STATUS = ("laeuft", "ok", "fehler", "gedrosselt", "uebersprungen")
MAX_AKTIV, AUFTRAG_MAX, NAME_MAX, KURZ_MAX, MAX_EMPFAENGER = 20, 1000, 60, 120, 10
LIMIT_STUNDE_MAX, LIMIT_TAG_MAX = 60, 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS regeln (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kanal TEXT NOT NULL, ausloeser TEXT NOT NULL,
    auftrag TEXT NOT NULL, empfaenger TEXT NOT NULL, aktionen TEXT NOT NULL,
    limit_stunde INTEGER NOT NULL, limit_tag INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'entwurf',
    erstellt TEXT NOT NULL, bestaetigt TEXT, grund TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS laeufe (
    id INTEGER PRIMARY KEY AUTOINCREMENT, regel_id INTEGER NOT NULL, ereignis_id TEXT NOT NULL, zeit TEXT NOT NULL,
    ausloeser_kurz TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'laeuft', aktionen TEXT NOT NULL DEFAULT '[]',
    gesehen INTEGER NOT NULL DEFAULT 0, UNIQUE (regel_id, ereignis_id)
);
"""
_sperre = threading.RLock()


class RegelFehler(Exception):
    """Mit deutscher Meldung, die Friday Eike sagen darf."""


def _jetzt() -> str:
    return config.jetzt().astimezone(timezone.utc).isoformat(timespec="seconds")


def _verbinden() -> sqlite3.Connection:
    ordner = config.daten_ordner()
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / "regeln.db"
    neu = not pfad.exists()
    if neu:
        os.close(os.open(pfad, os.O_WRONLY | os.O_CREAT, 0o600))
    db = sqlite3.connect(pfad, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _sauber(t, zeilen: bool = False) -> str:
    s = register._sichtbar(str(t or ""), zeilen=zeilen)
    return ("\n".join(" ".join(z.split()) for z in s.splitlines()).strip() if zeilen else " ".join(s.split()))


def _regel(z) -> dict:
    d = dict(z)
    for k in ("ausloeser", "empfaenger", "aktionen"):
        d[k] = json.loads(d[k])
    return d


def _lauf(z) -> dict:
    d = dict(z)
    d["aktionen"] = json.loads(d["aktionen"])
    d["gesehen"] = bool(d["gesehen"])
    return d


def _ausloeser_pruefen(kanal: str, a) -> dict:
    from kern.dienste import whatsapp
    if not isinstance(a, dict):
        raise RegelFehler("Der Auslöser fehlt.")
    name = _sauber(a.get("name"))[:NAME_MAX]
    if kanal == "zeit":
        from kern.regeln import zeit
        return zeit.pruefen(a)
    if kanal == "whatsapp":
        chat = str(a.get("chat_id") or "")
        if not any(c.get("id") == chat for c in whatsapp.frei()):
            raise RegelFehler("Dieser WhatsApp-Chat ist nicht freigegeben — dann kann eine Regel ihn nicht beobachten.")
        return {"chat_id": chat, "name": name or chat.split("@")[0]}
    pid = str(a.get("person_id") or "")
    p = register.holen(pid) if re.fullmatch(r"p[1-9][0-9]{0,5}", pid) else None
    if not p:
        raise RegelFehler("Diese Person gibt es in den Kontakten nicht.")
    return {"person_id": pid, "name": p.get("name") or name}


def _empfaenger_pruefen(roh) -> list[dict]:
    from kern.dienste import whatsapp
    if not isinstance(roh, (list, tuple)) or len(roh) > MAX_EMPFAENGER:
        raise RegelFehler(f"Die Empfänger müssen eine Liste mit höchstens {MAX_EMPFAENGER} Einträgen sein.")
    aus = []
    for e in roh:
        if not isinstance(e, dict):
            raise RegelFehler("Ein Empfänger muss Art, Id und Name haben.")
        art, eid = e.get("art"), str(e.get("id") or "")
        if art == "person":
            p = register.holen(eid) if re.fullmatch(r"p[1-9][0-9]{0,5}", eid) else None
            if not p:
                raise RegelFehler("Ein Empfänger steht nicht in den Kontakten.")
            aus.append({"art": "person", "id": eid, "name": p.get("name") or ""})
        elif art == "chat":
            c = next((c for c in whatsapp.frei() if c.get("id") == eid), None)
            if not c:
                raise RegelFehler("Ein Empfänger-Chat ist nicht freigegeben.")
            aus.append({"art": "chat", "id": eid, "name": c.get("name") or eid.split("@")[0]})
        else:
            raise RegelFehler("Empfänger können nur Kontakte oder freigegebene WhatsApp-Chats sein.")
    return aus


def anlegen(name, kanal, ausloeser, auftrag, empfaenger, aktionen, limit_stunde=5, limit_tag=20) -> dict:
    name, auftrag = _sauber(name)[:NAME_MAX], _sauber(auftrag, zeilen=True)
    if not name:
        raise RegelFehler("Die Regel braucht einen Namen.")
    if not auftrag:
        raise RegelFehler("Die Regel braucht einen Auftrag.")
    if len(auftrag) > AUFTRAG_MAX:
        raise RegelFehler(f"Der Auftrag ist zu lang ({len(auftrag)} Zeichen, erlaubt sind {AUFTRAG_MAX}).")
    if kanal not in KANAELE:
        raise RegelFehler("Der Kanal muss whatsapp oder snapchat sein.")
    if not isinstance(aktionen, (list, tuple)) or not aktionen or any(a not in AKTIONEN for a in aktionen):
        raise RegelFehler("Erlaubte Aktionen: " + ", ".join(AKTIONEN) + ".")
    for w, oben in ((limit_stunde, LIMIT_STUNDE_MAX), (limit_tag, LIMIT_TAG_MAX)):
        if not isinstance(w, int) or isinstance(w, bool) or not 1 <= w <= oben:
            raise RegelFehler(f"Die Limits müssen ganze Zahlen von 1 bis {oben} sein.")
    a = _ausloeser_pruefen(kanal, ausloeser)
    emp = _empfaenger_pruefen(empfaenger)
    if kanal == "zeit" and "antwort_an_ausloeser" in aktionen:
        raise RegelFehler("Eine Zeit-Regel hat keinen Absender: Nimm antwort_an_empfaenger mit festen Empfängern.")
    if "antwort_an_empfaenger" in aktionen and not emp:
        raise RegelFehler("Für antwort_an_empfaenger brauchst du mindestens einen Empfänger.")
    with _sperre, _verbinden() as db:
        cur = db.execute(
            "INSERT INTO regeln (name,kanal,ausloeser,auftrag,empfaenger,aktionen,limit_stunde,limit_tag,erstellt) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, kanal, json.dumps(a, ensure_ascii=False), auftrag, json.dumps(emp, ensure_ascii=False),
             json.dumps(list(dict.fromkeys(aktionen))), limit_stunde, limit_tag, _jetzt()))
        rid = cur.lastrowid
    return holen(rid)


def holen(rid) -> dict | None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT * FROM regeln WHERE id=?", (rid,)).fetchone()
    return _regel(z) if z else None


def liste(status: str | None = None) -> list[dict]:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT * FROM regeln" + (" WHERE status=?" if status else "") + " ORDER BY id",
                       (status,) if status else ()).fetchall()
    return [_regel(x) for x in z]


def aktive(kanal: str | None = None) -> list[dict]:
    return [r for r in liste("aktiv") if kanal is None or r["kanal"] == kanal]


def bestaetigen(rid) -> dict:
    with _sperre, _verbinden() as db:
        r = db.execute("SELECT status FROM regeln WHERE id=?", (rid,)).fetchone()
        if not r:
            raise RegelFehler("Diese Regel gibt es nicht.")
        if r["status"] != "entwurf":
            raise RegelFehler("Nur ein Entwurf lässt sich bestätigen.")
        if db.execute("SELECT COUNT(*) FROM regeln WHERE status='aktiv'").fetchone()[0] >= MAX_AKTIV:
            raise RegelFehler(f"Es laufen schon {MAX_AKTIV} Regeln — widerrufe erst eine.")
        db.execute("UPDATE regeln SET status='aktiv', bestaetigt=? WHERE id=?", (_jetzt(), rid))
    return holen(rid)


def setzen(rid, status: str, grund: str = "") -> dict:
    if status not in STATUS:
        raise RegelFehler("Diesen Status gibt es nicht.")
    with _sperre, _verbinden() as db:
        if not db.execute("SELECT 1 FROM regeln WHERE id=?", (rid,)).fetchone():
            raise RegelFehler("Diese Regel gibt es nicht.")
        db.execute("UPDATE regeln SET status=?, grund=? WHERE id=?", (status, _sauber(grund)[:200], rid))
    return holen(rid)


def lauf_neu(regel_id, ereignis_id, ausloeser_kurz) -> int | None:
    """Reserviert den Lauf; None, wenn dieses Ereignis die Regel schon ausgelöst hat."""
    kurz = _sauber(ausloeser_kurz)[:KURZ_MAX]
    with _sperre, _verbinden() as db:
        try:
            return db.execute("INSERT INTO laeufe (regel_id,ereignis_id,zeit,ausloeser_kurz) VALUES (?,?,?,?)",
                              (regel_id, str(ereignis_id)[:200], _jetzt(), kurz)).lastrowid
        except sqlite3.IntegrityError:
            return None


def lauf_ende(lauf_id, status: str, aktionen: list[dict]) -> None:
    if status not in LAUF_STATUS:
        raise RegelFehler("Diesen Laufstatus gibt es nicht.")
    with _sperre, _verbinden() as db:
        db.execute("UPDATE laeufe SET status=?, aktionen=? WHERE id=?",
                   (status, json.dumps(aktionen or [], ensure_ascii=False), lauf_id))


def laeufe(limit: int = 30) -> list[dict]:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT l.*, r.name AS regel FROM laeufe l JOIN regeln r ON r.id=l.regel_id ORDER BY l.id DESC LIMIT ?",
                       (limit,)).fetchall()
    return [_lauf(x) for x in z]


def laeufe_zaehlen(regel_id, seit_iso: str) -> int:
    """Läufe seit dem Zeitpunkt (ISO UTC) — gedrosselte zählen nicht mit."""
    with _sperre, _verbinden() as db:
        return db.execute("SELECT COUNT(*) FROM laeufe WHERE regel_id=? AND zeit>=? AND status NOT IN ('gedrosselt','uebersprungen')",
                          (regel_id, seit_iso)).fetchone()[0]


def letzte_status(regel_id, n: int) -> list[str]:
    with _sperre, _verbinden() as db:
        return [z[0] for z in db.execute("SELECT status FROM laeufe WHERE regel_id=? AND status!='laeuft' ORDER BY id DESC LIMIT ?",
                                         (regel_id, n)).fetchall()]


def gesehen_setzen(lauf_id) -> bool:
    with _sperre, _verbinden() as db:
        return db.execute("UPDATE laeufe SET gesehen=1 WHERE id=?", (lauf_id,)).rowcount > 0


def lauf_aktion(lauf_id, aktion: dict) -> None:
    """Hängt eine ausgeführte Aktion SOFORT an den Lauf an — ein Absturz mitten im Lauf lässt sie nicht unprotokolliert."""
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT aktionen FROM laeufe WHERE id=?", (lauf_id,)).fetchone()
        if z:
            db.execute("UPDATE laeufe SET aktionen=? WHERE id=?",
                       (json.dumps(json.loads(z["aktionen"]) + [aktion], ensure_ascii=False), lauf_id))


def laufende_abbrechen() -> int:
    """Beim Start: Läufe, die noch auf „laeuft“ stehen, wurden durch einen Neustart unterbrochen — als Fehler festhalten."""
    n = 0
    with _sperre, _verbinden() as db:
        for z in db.execute("SELECT id, aktionen FROM laeufe WHERE status='laeuft'").fetchall():
            akt = json.loads(z["aktionen"]) + [{"werkzeug": "-", "kurz": "abgebrochen (Neustart)", "status": "nicht"}]
            db.execute("UPDATE laeufe SET status='fehler', aktionen=? WHERE id=?", (json.dumps(akt, ensure_ascii=False), z["id"]))
            n += 1
    return n


def loeschen(rid) -> bool:
    """ENDGÜLTIG: die Regel und alle ihre Läufe. Nur über eine ausdrückliche Handlung Eikes (Ja im Gespräch oder Knopf in der Karte)."""
    with _sperre, _verbinden() as db:
        if not db.execute("SELECT 1 FROM regeln WHERE id=?", (rid,)).fetchone():
            return False
        db.execute("DELETE FROM laeufe WHERE regel_id=?", (rid,))
        db.execute("DELETE FROM regeln WHERE id=?", (rid,))
    return True
