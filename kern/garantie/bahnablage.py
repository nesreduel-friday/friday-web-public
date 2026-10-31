"""Ablage der Bahn-Fahrgastrechte (21.09.2026) — in derselben `data/garantie.db` wie die ÜSTRA-Garantie.

Dieselbe Verbindung und dieselbe Sperre wie `ablage.Ablage` (eine SQLite-Datei, eine Verbindung — so stehen die Fälle auch
in der nächtlichen Sicherung, und die Tests schließen sie mit `ablagen_schliessen` gleich mit). Die Tabellen legt der erste
Zugriff an; vorhandene Daten werden nie verändert.

- `bahnfaelle`: eine Zugfahrt EINER Person (Profil, 0 = Eike) mit Start, Ziel und der gemessenen Ankunft am Ziel.
- `bahnantraege`: was Eike eingereicht hat — ein Sammelantrag einer Zeitkarte oder eine Einzelfahrkarte, mit den Fällen,
  dem erwarteten Betrag, der Rechnung und später dem Bescheid. Eingereichtes lässt sich nicht löschen: Es ist der Beleg,
  dass eine Fahrt schon beantragt ist (dieselbe Fahrt geht nie zweimal raus — auch nicht als ÜSTRA-Garantie).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from kern.garantie import ablage

SCHEMA = """
CREATE TABLE IF NOT EXISTS bahnfaelle (
    id INTEGER PRIMARY KEY, erstellt TEXT NOT NULL, profil INTEGER NOT NULL DEFAULT 0, reise TEXT, jid TEXT,
    linie TEXT, kategorie TEXT, betreiber TEXT, richtung TEXT, start TEXT, ziel TEXT, start_ref TEXT, ziel_ref TEXT,
    ab TEXT, an TEXT, an_echt TEXT, verspaetung INTEGER, quelle TEXT, faellig_ab TEXT,
    nachgeprueft INTEGER NOT NULL DEFAULT 0, ziel_faellt_aus INTEGER NOT NULL DEFAULT 0,
    fahrkarte TEXT NOT NULL DEFAULT 'zeitkarte', preis_cent INTEGER, auftrag TEXT, rueckfahrt INTEGER NOT NULL DEFAULT 0,
    klasse INTEGER NOT NULL DEFAULT 2, status TEXT NOT NULL DEFAULT 'offen', antrag INTEGER, hinweise TEXT);
CREATE TABLE IF NOT EXISTS bahnantraege (
    id INTEGER PRIMARY KEY, erstellt TEXT NOT NULL, profil INTEGER NOT NULL DEFAULT 0, schluessel TEXT NOT NULL,
    art TEXT, stelle TEXT, ticket TEXT, von TEXT, bis TEXT, faelle TEXT, erwartet_cent INTEGER, rechnung TEXT,
    status TEXT NOT NULL DEFAULT 'eingereicht', eingereicht_am TEXT, vorgang TEXT, erhalten_cent INTEGER,
    entschieden_am TEXT, grund TEXT);
"""
FALL_FELDER = ("profil", "reise", "jid", "linie", "kategorie", "betreiber", "richtung", "start", "ziel", "start_ref",
               "ziel_ref", "ab", "an", "an_echt", "verspaetung", "quelle", "faellig_ab", "nachgeprueft", "ziel_faellt_aus",
               "fahrkarte", "preis_cent", "auftrag", "rueckfahrt", "klasse", "status", "antrag", "hinweise")
FALL_AENDERBAR = {"an_echt", "verspaetung", "quelle", "faellig_ab", "nachgeprueft", "ziel_faellt_aus", "status", "antrag",
                  "hinweise", "betreiber"}
ANTRAG_FELDER = ("profil", "schluessel", "art", "stelle", "ticket", "von", "bis", "faelle", "erwartet_cent", "rechnung",
                 "status", "eingereicht_am", "vorgang", "erhalten_cent", "entschieden_am", "grund")
ANTRAG_AENDERBAR = {"status", "vorgang", "erhalten_cent", "entschieden_am", "grund"}
_JSON = ("hinweise", "faelle", "rechnung")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(wert):
    return wert.isoformat() if isinstance(wert, datetime) else wert


def _wert(k: str, v):
    if k in _JSON:
        return json.dumps(v if v is not None else ([] if k != "rechnung" else {}), ensure_ascii=False)
    if k in ("nachgeprueft", "ziel_faellt_aus", "rueckfahrt"):
        return int(bool(v))
    return _iso(v)


def _a():
    """Die Ablage der Garantie — und beim ersten Zugriff je Verbindung die beiden Tabellen."""
    a = ablage.standard()
    if not getattr(a, "_bahn_bereit", False):
        with a._sperre, a._db:
            a._db.executescript(SCHEMA)
        a._bahn_bereit = True
    return a


def _lesen(z) -> dict:
    d = dict(z)
    for k in _JSON:
        if k in d:
            try:
                d[k] = json.loads(d[k]) if d[k] else ({} if k == "rechnung" else [])
            except ValueError:
                d[k] = {} if k == "rechnung" else []
    return d


# ---------- Fälle ----------

def fall_speichern(f: dict) -> int:
    a = _a()
    werte = {k: _wert(k, f.get(k)) for k in FALL_FELDER}
    werte["profil"] = int(f.get("profil") or 0)
    werte["status"] = f.get("status") or "offen"
    werte["fahrkarte"] = f.get("fahrkarte") or "zeitkarte"
    werte["klasse"] = int(f.get("klasse") or 2)
    with a._sperre, a._db:
        return a._db.execute(
            f"INSERT INTO bahnfaelle (erstellt, {', '.join(werte)}) VALUES (?, {', '.join('?' * len(werte))})",
            (_jetzt(), *werte.values())).lastrowid


def fall(fid: int) -> dict | None:
    a = _a()
    with a._sperre:
        z = a._db.execute("SELECT * FROM bahnfaelle WHERE id = ?", (int(fid),)).fetchone()
    return _lesen(z) if z else None


def faelle(profil: int | None = 0, anzahl: int = 2000) -> list[dict]:
    """Die Fälle einer Person (Vorgabe: Eike), neueste Fahrt zuerst — mit `profil=None` die aller."""
    a = _a()
    with a._sperre:
        if profil is None:
            zeilen = a._db.execute("SELECT * FROM bahnfaelle ORDER BY an DESC, id DESC LIMIT ?", (int(anzahl),)).fetchall()
        else:
            zeilen = a._db.execute("SELECT * FROM bahnfaelle WHERE profil = ? ORDER BY an DESC, id DESC LIMIT ?",
                                   (int(profil), int(anzahl))).fetchall()
    return [_lesen(z) for z in zeilen]


def fall_aendern(fid: int, **felder) -> None:
    werte = {k: _wert(k, v) for k, v in felder.items() if k in FALL_AENDERBAR}
    if not werte:
        return
    a = _a()
    with a._sperre, a._db:
        a._db.execute(f"UPDATE bahnfaelle SET {', '.join(f'{k} = ?' for k in werte)} WHERE id = ?", (*werte.values(), int(fid)))


def fall_loeschen(fid: int) -> bool:
    """Nur, was noch nicht eingereicht ist — ein eingereichter Fall belegt, dass die Fahrt beantragt wurde."""
    a = _a()
    with a._sperre, a._db:
        return a._db.execute("DELETE FROM bahnfaelle WHERE id = ? AND antrag IS NULL", (int(fid),)).rowcount > 0


def fall_zur_reise(rid: str, profil: int = 0) -> dict | None:
    """Eine Fahrt = ein Fall PRO PERSON (verworfene zählen nicht)."""
    a = _a()
    with a._sperre:
        z = a._db.execute("SELECT * FROM bahnfaelle WHERE reise = ? AND profil = ? AND status != 'verworfen' "
                          "ORDER BY id DESC", (rid, int(profil))).fetchone()
    return _lesen(z) if z else None


# ---------- Eingereichte Anträge ----------

def antrag_speichern(e: dict) -> int:
    a = _a()
    werte = {k: _wert(k, e.get(k)) for k in ANTRAG_FELDER}
    werte["profil"] = int(e.get("profil") or 0)
    werte["status"] = e.get("status") or "eingereicht"
    with a._sperre, a._db:
        return a._db.execute(
            f"INSERT INTO bahnantraege (erstellt, {', '.join(werte)}) VALUES (?, {', '.join('?' * len(werte))})",
            (_jetzt(), *werte.values())).lastrowid


def antrag(aid: int) -> dict | None:
    a = _a()
    with a._sperre:
        z = a._db.execute("SELECT * FROM bahnantraege WHERE id = ?", (int(aid),)).fetchone()
    return _lesen(z) if z else None


def antraege(profil: int | None = 0, anzahl: int = 500) -> list[dict]:
    a = _a()
    with a._sperre:
        if profil is None:
            zeilen = a._db.execute("SELECT * FROM bahnantraege ORDER BY id DESC LIMIT ?", (int(anzahl),)).fetchall()
        else:
            zeilen = a._db.execute("SELECT * FROM bahnantraege WHERE profil = ? ORDER BY id DESC LIMIT ?",
                                   (int(profil), int(anzahl))).fetchall()
    return [_lesen(z) for z in zeilen]


def antrag_zum_schluessel(schluessel: str) -> dict | None:
    a = _a()
    with a._sperre:
        z = a._db.execute("SELECT * FROM bahnantraege WHERE schluessel = ? ORDER BY id DESC", (schluessel,)).fetchone()
    return _lesen(z) if z else None


def antrag_aendern(aid: int, **felder) -> None:
    werte = {k: _wert(k, v) for k, v in felder.items() if k in ANTRAG_AENDERBAR}
    if not werte:
        return
    a = _a()
    with a._sperre, a._db:
        a._db.execute(f"UPDATE bahnantraege SET {', '.join(f'{k} = ?' for k in werte)} WHERE id = ?",
                      (*werte.values(), int(aid)))


def einreichen(e: dict, fall_ids: list[int]) -> int:
    """Einen Antrag ablegen und seine Fälle daran binden — in EINER Transaktion, damit nie ein Fall halb eingereicht ist."""
    a = _a()
    werte = {k: _wert(k, e.get(k)) for k in ANTRAG_FELDER}
    werte["profil"] = int(e.get("profil") or 0)
    werte["status"] = "eingereicht"
    with a._sperre, a._db:
        aid = a._db.execute(
            f"INSERT INTO bahnantraege (erstellt, {', '.join(werte)}) VALUES (?, {', '.join('?' * len(werte))})",
            (_jetzt(), *werte.values())).lastrowid
        for fid in fall_ids:
            geaendert = a._db.execute("UPDATE bahnfaelle SET antrag = ? WHERE id = ? AND antrag IS NULL",
                                      (aid, int(fid))).rowcount
            if geaendert != 1:
                raise ValueError(f"Fall #{fid} ist schon eingereicht oder gelöscht.")
    return aid
