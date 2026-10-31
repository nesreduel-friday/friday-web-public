"""Aufgabenblatt-Speicher (`data/aufgabenblatt.db`, PDFs unter `data/aufgabenblatt/blatt-<id>.pdf`): Blätter und Aufträge. Gelöscht wird NIE.

Ein Blatt ist ein von Eike im Chat angehängtes Aufgaben-PDF (geprüft: echtes, unverschlüsseltes PDF, ≤ 10 MB, ≤ 40 Seiten; dasselbe PDF = dasselbe Blatt).
Ein Auftrag ist die Bearbeitung eines Blattes im Hintergrund (`planer`, `lauf`). Limits im Code: 5 Aufträge am Tag, höchstens 3 offene, 2 Versuche."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
from datetime import timedelta, timezone

from pypdf import PdfReader

from kern import config
from kern.personen import register

STATUS = ("wartend", "laeuft", "fertig", "fehler", "abgebrochen")
TAGESLIMIT, MAX_OFFEN, MAX_VERSUCHE = 5, 3, 2
PDF_MAX, SEITEN_MAX, NAME_MAX, BLATT_FRIST_H = 10 * 1024 * 1024, 40, 120, 24
HINWEISE_MAX, MAX_NOTIZEN, NOTIZ_MAX = 600, 40, 800
LOESUNGEN_MAX, FELD_MAX, KURZ_MAX = 40, 3000, 300
SICHERHEIT = ("belegt", "teilweise", "unsicher")

SCHEMA = """
CREATE TABLE IF NOT EXISTS blaetter (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, sha TEXT NOT NULL UNIQUE, seiten INTEGER NOT NULL, erstellt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auftraege (
    id INTEGER PRIMARY KEY AUTOINCREMENT, blatt INTEGER NOT NULL, hinweise TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'wartend', erstellt TEXT NOT NULL, beginn TEXT, ende TEXT, versuche INTEGER NOT NULL DEFAULT 0,
    notizen TEXT NOT NULL DEFAULT '[]', schritte TEXT NOT NULL DEFAULT '{}', loesungen TEXT NOT NULL DEFAULT '[]',
    sicherheit TEXT NOT NULL DEFAULT '', offen TEXT NOT NULL DEFAULT '', fehler TEXT NOT NULL DEFAULT ''
);
"""
_sperre = threading.RLock()


class AufgabenFehler(Exception):
    """Mit deutscher Meldung, die Friday Eike sagen darf."""


def _jetzt() -> str:
    return config.jetzt().astimezone(timezone.utc).isoformat(timespec="seconds")


def _ordner():
    return config.daten_ordner() / "aufgabenblatt"


def _verbinden() -> sqlite3.Connection:
    ordner = config.daten_ordner()
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / "aufgabenblatt.db"
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


# ---------- Blätter ----------

def blatt_ablegen(name, daten_b64) -> dict:
    """Prüft das PDF und legt es ab. Wirft `AufgabenFehler` mit Klartext; bei Erfolg `{"id","name","seiten","neu"}`."""
    try:
        roh = base64.b64decode(str(daten_b64 or ""), validate=True)
    except (binascii.Error, ValueError):
        raise AufgabenFehler("Das PDF ist nicht angekommen (ungültige Daten). Häng es bitte noch einmal an.") from None
    if len(roh) > PDF_MAX:
        raise AufgabenFehler(f"Das PDF ist zu groß: erlaubt sind {PDF_MAX // (1024 * 1024)} MB.")
    if not roh.startswith(b"%PDF-"):
        raise AufgabenFehler("Das ist kein PDF.")
    try:
        leser = PdfReader(io.BytesIO(roh))
        if leser.is_encrypted:
            raise AufgabenFehler("Das PDF ist verschlüsselt — ich kann es nicht öffnen. Nimm eine Kopie ohne Passwort.")
        seiten = len(leser.pages)
    except AufgabenFehler:
        raise
    except Exception:  # noqa: BLE001 — jede Lesepanne eines kaputten PDFs wird zur Meldung
        raise AufgabenFehler("Das PDF ist beschädigt — ich kann es nicht lesen.") from None
    if seiten < 1:
        raise AufgabenFehler("Das PDF hat keine Seiten.")
    if seiten > SEITEN_MAX:
        raise AufgabenFehler(f"Das PDF hat {seiten} Seiten — erlaubt sind {SEITEN_MAX} Seiten.")
    sha = hashlib.sha256(roh).hexdigest()
    sauber = _sauber(name, NAME_MAX) or "Aufgabenblatt.pdf"
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT id, name, seiten FROM blaetter WHERE sha=?", (sha,)).fetchone()
        if z:
            return {"id": z["id"], "name": z["name"], "seiten": z["seiten"], "neu": False}
        bid = db.execute("INSERT INTO blaetter (name, sha, seiten, erstellt) VALUES (?,?,?,?)", (sauber, sha, seiten, _jetzt())).lastrowid
        ordner = _ordner()
        ordner.mkdir(parents=True, exist_ok=True)
        pfad = ordner / f"blatt-{bid}.pdf"
        fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(roh)
    return {"id": bid, "name": sauber, "seiten": seiten, "neu": True}


def blatt_holen(bid) -> dict | None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT * FROM blaetter WHERE id=?", (bid,)).fetchone()
    return dict(z) if z else None


def blatt_bytes(bid) -> bytes:
    pfad = _ordner() / f"blatt-{int(bid)}.pdf"
    if not pfad.exists():
        raise AufgabenFehler("Das Aufgabenblatt liegt nicht mehr vor.")
    return pfad.read_bytes()


def neuestes_blatt() -> dict | None:
    ab = (config.jetzt() - timedelta(hours=BLATT_FRIST_H)).astimezone(timezone.utc).isoformat(timespec="seconds")
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT * FROM blaetter WHERE erstellt>? ORDER BY id DESC LIMIT 1", (ab,)).fetchone()
    return dict(z) if z else None


# ---------- Aufträge ----------

def _zeile(z) -> dict:
    d = dict(z)
    for k in ("notizen", "schritte", "loesungen"):
        d[k] = json.loads(d[k])
    return d


def anlegen(blatt_id, hinweise="") -> dict:
    blatt = blatt_holen(blatt_id) if blatt_id else neuestes_blatt()
    if blatt is None:
        raise AufgabenFehler("Es liegt kein Aufgabenblatt vor. Eike soll das PDF bitte im Chat anhängen.")
    h = _sauber(hinweise, 10_000)
    if len(h) > HINWEISE_MAX:
        raise AufgabenFehler(f"Die Hinweise dürfen höchstens {HINWEISE_MAX} Zeichen lang sein.")
    tag_ab = config.jetzt().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")
    with _sperre, _verbinden() as db:
        if db.execute("SELECT COUNT(*) FROM auftraege WHERE erstellt>=?", (tag_ab,)).fetchone()[0] >= TAGESLIMIT:
            raise AufgabenFehler(f"Heute habe ich schon {TAGESLIMIT} Aufgabenblätter im Hintergrund bearbeitet — das reicht für einen Tag.")
        if db.execute("SELECT COUNT(*) FROM auftraege WHERE status IN ('wartend','laeuft')").fetchone()[0] >= MAX_OFFEN:
            raise AufgabenFehler(f"Es warten schon {MAX_OFFEN} Aufgabenblätter. Erst wenn eins fertig ist, nehme ich ein neues an.")
        aid = db.execute("INSERT INTO auftraege (blatt, hinweise, erstellt) VALUES (?,?,?)", (blatt["id"], h, _jetzt())).lastrowid
    return holen(aid)


def holen(aid) -> dict | None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT * FROM auftraege WHERE id=?", (aid,)).fetchone()
    return _zeile(z) if z else None


def liste(limit: int = 20) -> list[dict]:
    with _sperre, _verbinden() as db:
        return [_zeile(z) for z in db.execute("SELECT * FROM auftraege ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def naechster_wartender() -> dict | None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT * FROM auftraege WHERE status='wartend' ORDER BY id LIMIT 1").fetchone()
    return _zeile(z) if z else None


def beginnen(aid) -> dict:
    with _sperre, _verbinden() as db:
        db.execute("UPDATE auftraege SET status='laeuft', beginn=? WHERE id=? AND status='wartend'", (_jetzt(), aid))
    return holen(aid)


def notiz(aid, text, quelle="") -> None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT notizen FROM auftraege WHERE id=?", (aid,)).fetchone()
        if not z:
            return
        n = json.loads(z["notizen"])
        if len(n) < MAX_NOTIZEN and _sauber(text, NOTIZ_MAX):
            n.append({"text": _sauber(text, NOTIZ_MAX), "quelle": _sauber(quelle, KURZ_MAX)})
            db.execute("UPDATE auftraege SET notizen=? WHERE id=?", (json.dumps(n, ensure_ascii=False), aid))


def zaehlen(aid, art: str) -> None:
    with _sperre, _verbinden() as db:
        z = db.execute("SELECT schritte FROM auftraege WHERE id=?", (aid,)).fetchone()
        if z:
            s = json.loads(z["schritte"])
            s[art] = s.get(art, 0) + 1
            db.execute("UPDATE auftraege SET schritte=? WHERE id=?", (json.dumps(s), aid))


def loesung_sauber(d) -> dict | None:
    """Eine Lösung aus Modellausgabe in feste Form bringen; None, wenn keine Lösung drinsteht."""
    if not isinstance(d, dict):
        return None
    loesung = _sauber(d.get("loesung"), FELD_MAX, zeilen=True)
    if not loesung:
        return None
    try:
        seite = max(0, int(d.get("seite") or 0))
    except (TypeError, ValueError):
        seite = 0
    nummer = re.sub(r"^aufgabe\s*:?\s*", "", _sauber(d.get("nummer"), 20), flags=re.I)          # Gemini schreibt oft „Aufgabe 1“ als Nummer
    return {"nummer": nummer or "–", "aufgabe": _sauber(d.get("aufgabe"), KURZ_MAX), "seite": seite,
            "loesung": loesung, "weg": _sauber(d.get("weg"), FELD_MAX, zeilen=True), "sicher": d.get("sicher") is True,
            "quelle": _sauber(d.get("quelle"), KURZ_MAX)}


def sicherheit_von(loesungen) -> str:
    sicher = [bool(l.get("sicher")) for l in loesungen or []]
    if not sicher or not any(sicher):
        return "unsicher"
    return "belegt" if all(sicher) else "teilweise"


def abschliessen(aid, loesungen, offen, hoechstens="belegt") -> bool:
    """Nur ein laufender Auftrag wird fertig — ein inzwischen abgebrochener bleibt abgebrochen. `hoechstens` kappt die Sicherheit (ungeprüft: nie „belegt“)."""
    sauber = [l for l in (loesung_sauber(x) for x in (loesungen or [])[:LOESUNGEN_MAX]) if l]
    sicherheit = sicherheit_von(sauber)
    if hoechstens in SICHERHEIT and SICHERHEIT.index(hoechstens) > SICHERHEIT.index(sicherheit):
        sicherheit = hoechstens                       # SICHERHEIT ist von stark nach schwach geordnet
    with _sperre, _verbinden() as db:
        return db.execute("UPDATE auftraege SET status='fertig', ende=?, loesungen=?, sicherheit=?, offen=? WHERE id=? AND status IN ('laeuft','wartend')",
                          (_jetzt(), json.dumps(sauber, ensure_ascii=False), sicherheit, _sauber(offen, 1000), aid)).rowcount > 0


def scheitern(aid, fehler) -> bool:
    with _sperre, _verbinden() as db:
        return db.execute("UPDATE auftraege SET status='fehler', ende=?, fehler=? WHERE id=? AND status IN ('laeuft','wartend')",
                          (_jetzt(), _sauber(fehler, 300), aid)).rowcount > 0


def abbrechen(aid) -> bool:
    with _sperre, _verbinden() as db:
        return db.execute("UPDATE auftraege SET status='abgebrochen', ende=? WHERE id=? AND status IN ('wartend','laeuft')",
                          (_jetzt(), aid)).rowcount > 0


def laufende_zuruecksetzen() -> int:
    """Beim Start: ein Lauf, der noch auf „laeuft“ steht, wurde unterbrochen — zurück in die Warteschlange, nach 2 Versuchen Fehler."""
    n = 0
    with _sperre, _verbinden() as db:
        for z in db.execute("SELECT id, versuche FROM auftraege WHERE status='laeuft'").fetchall():
            v = z["versuche"] + 1
            if v >= MAX_VERSUCHE:
                db.execute("UPDATE auftraege SET status='fehler', versuche=?, ende=?, fehler=? WHERE id=?",
                           (v, _jetzt(), "Unterbrochen (Neustart), zu viele Versuche", z["id"]))
            else:
                db.execute("UPDATE auftraege SET status='wartend', versuche=? WHERE id=?", (v, z["id"]))
            n += 1
    return n


def loesungstext(a: dict) -> str:
    """Die Lösungen als lesbarer Text (für Chat, Karte, Vorlesen). Unsicheres ist ausdrücklich so gekennzeichnet."""
    teile = []
    for l in a["loesungen"]:
        kopf = f"Aufgabe {l['nummer']}" + (f" (Seite {l['seite']})" if l["seite"] else "") + ("" if l["sicher"] else " — unsicher")
        zeilen = [kopf, l["loesung"]]
        if l["weg"]:
            zeilen.append(f"Weg: {l['weg']}")
        if l["quelle"]:
            zeilen.append(f"Quelle: {l['quelle']}")
        teile.append("\n".join(zeilen))
    if a["offen"]:
        teile.append(f"Offen: {a['offen']}")
    return "\n\n".join(teile) or "Keine Lösung vorhanden."
