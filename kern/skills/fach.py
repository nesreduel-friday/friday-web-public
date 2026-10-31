"""Skill-Speicher (`data/skills.db`): versioniert, löscht NIE. Status entwurf -> aktiv -> widerrufen | ersetzt.

Ein Skill ist freier Text (Zweck, Auslöser, Anweisung), aber die Grenzen sind Code: `werkzeuge` nur aus `primitive.PERSONENFAEHIG`,
`optionen` nur solche, die diese Primitive deklarieren (Werte geprüft, fehlende = strengster Standard), `zielgruppe` nur
Person-Ids aus dem Register, `eike`, `system` oder eine im Code registrierte Gruppenregel. Ein Skill wirkt für Personen nur
im Status `aktiv`; die Aktivierung (Eikes Ja) liegt im Werkzeug."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import timezone
from pathlib import Path

from kern import config
from kern.personen import register
from kern.skills import primitive

NAME_MAX, ZWECK_MAX, AUSLOESER_MAX, ANWEISUNG_MAX, MAX_AKTIV = 60, 200, 300, 2500, 30
STATUS = ("entwurf", "aktiv", "widerrufen", "ersetzt")


def _garantie_beteiligte(person: dict) -> bool:
    return (person.get("quellen") or {}).get("garantie") is not None and not person.get("pausiert")


def _alle_kontakte(person: dict) -> bool:
    return not person.get("pausiert")


# Gruppenregeln wie „alle Beteiligten der Garantieanträge“: name -> Funktion(person_dict) -> bool. Nur im Code eintragbar;
# ein Skill kann Gruppen nur aus dieser Registry wählen (Zielgruppe `gruppe:<name>`, Primitive `personen_auswaehlen`).
GRUPPEN: dict = {"garantie_beteiligte": _garantie_beteiligte, "alle_kontakte": _alle_kontakte}

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    zweck TEXT NOT NULL, ausloeser TEXT NOT NULL, zielgruppe TEXT NOT NULL, anweisung TEXT NOT NULL,
    werkzeuge TEXT NOT NULL, optionen TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'entwurf',
    erstellt TEXT NOT NULL, geaendert TEXT NOT NULL, ersetzt_durch INTEGER, quelle TEXT NOT NULL DEFAULT 'eike', vorgabe INTEGER,
    felder TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS skills_name ON skills(name, status);
"""


def _jetzt() -> str:
    return config.jetzt().astimezone(timezone.utc).isoformat(timespec="seconds")


def _sauber(t, zeilen: bool = False) -> str:
    s = register._sichtbar(str(t or ""), zeilen=zeilen)
    return ("\n".join(" ".join(z.split()) for z in s.splitlines()).strip() if zeilen else " ".join(s.split()))


def _zeile(z) -> dict:
    d = dict(z)
    for k in ("zielgruppe", "werkzeuge", "optionen", "felder"):
        d[k] = json.loads(d[k] or "[]") if k in d else []
    return d


# Akten-Felder (12.10.2026): Der Skill deklariert, was die Instanz in der Akte erfassen soll (`akte_erfassen`, `kern/skills/akten.py`).
FELD_TYPEN = ("text", "datum", "zahl", "auswahl")
FELDER_MAX, AUSWAHL_MAX, WERT_MAX, HINWEIS_MAX = 12, 10, 40, 120
_FELDNAME = re.compile(r"^[a-z][a-z0-9_]{0,29}$")


def felder_pruefen(felder) -> tuple[list[dict] | None, str]:
    """(bereinigte Feldliste, Absage). Jedes Feld: name, typ (text|datum|zahl|auswahl), pflicht, bei auswahl `werte`, hinweis."""
    if felder in (None, ""):
        return [], ""
    if not isinstance(felder, (list, tuple)):
        return None, "Die Felder müssen eine Liste sein."
    if len(felder) > FELDER_MAX:
        return None, f"Höchstens {FELDER_MAX} Felder."
    aus, gesehen = [], set()
    for f in felder:
        if not isinstance(f, dict):
            return None, "Jedes Feld braucht name und typ."
        name = _sauber(f.get("name")).lower().replace(" ", "_")
        typ = _sauber(f.get("typ") or "text").lower()
        if not _FELDNAME.match(name):
            return None, f"Feldname „{name}“ ungültig (klein, Buchstaben/Ziffern/_, höchstens 30 Zeichen)."
        if name in gesehen:
            return None, f"Das Feld {name} steht doppelt."
        if typ not in FELD_TYPEN:
            return None, f"Typ „{typ}“ gibt es nicht (möglich: {', '.join(FELD_TYPEN)})."
        pflicht = f.get("pflicht", True)
        if not isinstance(pflicht, bool):
            return None, f"„pflicht“ bei {name} muss wahr oder falsch sein."
        werte = []
        if typ == "auswahl":
            roh = f.get("werte")
            if not isinstance(roh, (list, tuple)) or not 2 <= len(roh) <= AUSWAHL_MAX:
                return None, f"Das Feld {name} (auswahl) braucht 2 bis {AUSWAHL_MAX} Werte."
            for w in roh:
                w = _sauber(w)
                if not w or len(w) > WERT_MAX:
                    return None, f"Ein Auswahlwert bei {name} ist leer oder länger als {WERT_MAX} Zeichen."
                if w.casefold() not in {x.casefold() for x in werte}:
                    werte.append(w)
        hinweis = _sauber(f.get("hinweis"))
        if len(hinweis) > HINWEIS_MAX:
            return None, f"Der Hinweis zu {name} ist zu lang (höchstens {HINWEIS_MAX} Zeichen)."
        gesehen.add(name)
        aus.append({"name": name, "typ": typ, "pflicht": pflicht, "werte": werte, "hinweis": hinweis})
    return aus, ""


def pruefen(name, zweck, ausloeser, zielgruppe, anweisung, werkzeuge, optionen, felder=None) -> tuple[dict | None, str]:
    """(bereinigte Felder, Absage). Hier stehen die Grenzen."""
    f = {"name": _sauber(name).lower().replace(" ", "_"), "zweck": _sauber(zweck), "ausloeser": _sauber(ausloeser),
         "anweisung": _sauber(anweisung, zeilen=True)}
    for k, m in (("name", NAME_MAX), ("zweck", ZWECK_MAX), ("ausloeser", AUSLOESER_MAX), ("anweisung", ANWEISUNG_MAX)):
        if not f[k]:
            return None, f"Das Feld {k} fehlt."
        if len(f[k]) > m:
            return None, f"Das Feld {k} ist zu lang (höchstens {m} Zeichen)."
    ziel = []
    for z in (zielgruppe if isinstance(zielgruppe, (list, tuple)) else [zielgruppe]):
        z = str(z or "").strip()
        if z in ("eike", "system") or (z.startswith("gruppe:") and z[7:] in GRUPPEN) or (z[:1] == "p" and register.holen(z)):
            if z not in ziel:
                ziel.append(z)
        else:
            return None, (f"Zielgruppe „{z}“ gibt es nicht (erlaubt: Person-Id aus dem Register, eike, system, "
                          f"gruppe:<{', '.join(GRUPPEN) or 'keine registriert'}>; für alle, die Anträge haben: gruppe:garantie_beteiligte).")
    if not ziel:
        return None, "Die Zielgruppe fehlt."
    wz = []
    for w in (werkzeuge or []):
        if w not in primitive.PERSONENFAEHIG:
            return None, (f"„{w}“ kann einer Person nicht gegeben werden (möglich: {', '.join(primitive.PERSONENFAEHIG) or 'noch nichts'}).")
        if w not in wz:
            wz.append(w)
    if wz and ziel == ["eike"]:
        return None, "Für Personen braucht es eine Zielgruppe mit Personen."
    if not isinstance(optionen or {}, dict):
        return None, "Die Optionen müssen Name/Wert sein."
    erlaubt = {}
    for w in wz:
        for o, d in primitive.PERSONENFAEHIG[w]["optionen"].items():
            erlaubt.setdefault(o, d)
    opt = {}
    for o, v in (optionen or {}).items():
        if o not in erlaubt:
            return None, f"Die Einstellung „{o}“ passt zu nichts davon (möglich: {', '.join(erlaubt) or 'keine'})."
        if v not in erlaubt[o]["werte"]:
            return None, f"„{v}“ ist für {o} nicht erlaubt (möglich: {', '.join(map(str, erlaubt[o]['werte']))})."
        if callable(erlaubt[o].get("pruefen")) and (absage := erlaubt[o]["pruefen"](v)):
            return None, absage  # ein Wert, den die Primitive gerade nicht zulässt (z. B. fehlendes Schreibrecht)
        opt[o] = v
    for o, d in erlaubt.items():
        opt.setdefault(o, d["standard"])
    fl, absage = felder_pruefen(felder)
    if absage:
        return None, absage
    return {**f, "zielgruppe": ziel, "werkzeuge": wz, "optionen": opt, "felder": fl}, ""


def betrifft_personen(s: dict) -> bool:
    return any(z not in ("eike", "system") for z in s["zielgruppe"])


def trifft(s: dict, person: dict | None) -> bool:
    """Gilt die Zielgruppe des Skills für diese Person? `person=None` ist Eike."""
    if person is None:
        return "eike" in s["zielgruppe"]
    for z in s["zielgruppe"]:
        if z == person.get("id"):
            return True
        if z.startswith("gruppe:"):
            try:
                if GRUPPEN[z[7:]](person):
                    return True
            except Exception:  # noqa: BLE001 — im Zweifel zu
                pass
    return False


class Fach:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.Lock()
        self._ablauf = threading.RLock()
        self._db = sqlite3.connect(ordner / "skills.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        if "felder" not in {z[1] for z in self._db.execute("PRAGMA table_info(skills)")}:
            self._db.execute("ALTER TABLE skills ADD COLUMN felder TEXT NOT NULL DEFAULT '[]'")   # ältere Datenbank nachrüsten
        self._db.commit()
        try:
            (ordner / "skills.db").chmod(0o600)
        except OSError:
            pass

    def _alle(self, sql, werte=()) -> list[dict]:
        with self._sperre:
            return [_zeile(z) for z in self._db.execute(sql, werte).fetchall()]

    def _schreib(self, sql, werte=()) -> int:
        with self._sperre:
            c = self._db.execute(sql, werte)
            self._db.commit()
            return c.lastrowid

    def eintrag(self, sid: int) -> dict | None:
        z = self._alle("SELECT * FROM skills WHERE id=?", (sid,))
        return z[0] if z else None

    def aktive(self) -> list[dict]:
        return self._alle("SELECT * FROM skills WHERE status='aktiv' ORDER BY id")

    def entwuerfe(self) -> list[dict]:
        return self._alle("SELECT * FROM skills WHERE status='entwurf' ORDER BY id")

    def verlauf(self, name: str | None = None, limit: int = 100) -> list[dict]:
        if name:
            return self._alle("SELECT * FROM skills WHERE name=? ORDER BY id DESC LIMIT ?", (name, limit))
        return self._alle("SELECT * FROM skills ORDER BY id DESC LIMIT ?", (limit,))

    def finden(self, id_oder_name, stati=("aktiv", "entwurf")) -> list[dict]:
        s = _sauber(id_oder_name).lstrip("#").lower().replace(" ", "_")
        if not s:
            return []
        treffer = [e for e in self._alle("SELECT * FROM skills ORDER BY id") if e["status"] in stati]
        if s.isdigit():
            return [e for e in treffer if e["id"] == int(s)]
        genau = [e for e in treffer if e["name"] == s]
        return genau or [e for e in treffer if s in e["name"]]

    def anlegen(self, name, zweck, ausloeser, zielgruppe, anweisung, werkzeuge=None, optionen=None, quelle="eike",
                version: int = 1, felder=None) -> dict:
        """Legt einen ENTWURF an (wirkt nie). Ein Entwurf desselben Namens wird dabei nicht still überschrieben: er bleibt
        als `ersetzt` im Verlauf."""
        with self._ablauf:
            f, absage = pruefen(name, zweck, ausloeser, zielgruppe, anweisung, werkzeuge, optionen, felder)
            if absage:
                return {"ok": False, "grund": absage}
            jetzt = _jetzt()
            sid = self._schreib("INSERT INTO skills (name, version, zweck, ausloeser, zielgruppe, anweisung, werkzeuge, optionen, "
                                "erstellt, geaendert, quelle, felder) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (f["name"], version, f["zweck"], f["ausloeser"], json.dumps(f["zielgruppe"]), f["anweisung"],
                                 json.dumps(f["werkzeuge"]), json.dumps(f["optionen"]), jetzt, jetzt, str(quelle)[:40],
                                 json.dumps(f["felder"], ensure_ascii=False)))
            for e in self._alle("SELECT * FROM skills WHERE name=? AND status='entwurf' AND id<>?", (f["name"], sid)):
                self._schreib("UPDATE skills SET status='ersetzt', ersetzt_durch=?, geaendert=? WHERE id=?", (sid, jetzt, e["id"]))
            return {"ok": True, "id": sid}

    def aktivieren(self, sid: int, vorgabe: int | None = None) -> dict:
        """Entwurf -> aktiv; eine bisher aktive Version desselben Namens wird `ersetzt`."""
        with self._ablauf:
            e = self.eintrag(sid)
            if not e or e["status"] != "entwurf":
                return {"ok": False, "grund": "Das wartet auf keine Bestätigung."}
            # Grenzen erneut prüfen (Register/Primitive können sich seit dem Entwurf geändert haben).
            _, absage = pruefen(e["name"], e["zweck"], e["ausloeser"], e["zielgruppe"], e["anweisung"], e["werkzeuge"], e["optionen"], e["felder"])
            if absage:
                return {"ok": False, "grund": absage}
            if len(self.aktive()) >= MAX_AKTIV and not any(a["name"] == e["name"] for a in self.aktive()):
                return {"ok": False, "grund": f"Es laufen schon {MAX_AKTIV} solcher Aufgaben. Beende zuerst eine."}
            jetzt = _jetzt()
            for a in self._alle("SELECT * FROM skills WHERE name=? AND status='aktiv'", (e["name"],)):
                self._schreib("UPDATE skills SET status='ersetzt', ersetzt_durch=?, geaendert=? WHERE id=?", (sid, jetzt, a["id"]))
            self._schreib("UPDATE skills SET status='aktiv', geaendert=?, vorgabe=? WHERE id=?", (jetzt, vorgabe, sid))
            return {"ok": True, "id": sid}

    def widerrufen(self, sid: int) -> dict:
        with self._ablauf:
            e = self.eintrag(sid)
            if not e or e["status"] not in ("aktiv", "entwurf"):
                return {"ok": False, "grund": "Das gibt es nicht oder es gilt schon nicht mehr."}
            self._schreib("UPDATE skills SET status='widerrufen', geaendert=? WHERE id=?", (_jetzt(), sid))
            return {"ok": True, "id": sid, "vorgabe": e.get("vorgabe")}


_instanzen: dict[str, Fach] = {}


def standard() -> Fach:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Fach(ordner)
    return _instanzen[str(ordner)]
