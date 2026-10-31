"""Störungs-Erkennung der Werkbank — zählt wiederkehrende Fehler und macht ab einer Schwelle ein Vorhaben.

Die Zähler liegen in der Tabelle `stoerungen` von `werkbank.db` (über `Fach.verbindung()`). Ab `SCHWELLE_TAGE` verschiedenen
Tagen oder `SCHWELLE_ANZAHL` Vorkommen entsteht ein Vorhaben (`art="fehler"`, `quelle="stoerung"`) und ein Anliegen an Eike.
Danach nur noch alle 10 Vorkommen, solange das Vorhaben unbearbeitet (`vorgeschlagen`) ist.
"""

from __future__ import annotations

import json
from datetime import datetime

from kern import config
from kern.werkbank import speicher

SCHWELLE_TAGE = 3
SCHWELLE_ANZAHL = 5
ERNEUT_ALLE = 10


def _tage(roh) -> list[str]:
    try:
        t = json.loads(roh or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in t] if isinstance(t, list) else []


def melden(ort: str, fingerabdruck: str, text: str, jetzt: datetime | None = None, fach=None, anliegen=None) -> dict:
    """Zählt ein Vorkommen. Rückgabe: {"vorhaben": id | None, "gemeldet": ob ein Anliegen entstand}."""
    fach = fach or speicher.standard()
    jetzt = jetzt or config.jetzt()
    tag = jetzt.date().isoformat()
    stempel = speicher._iso(jetzt)
    with fach.verbindung() as db:
        z = db.execute("SELECT * FROM stoerungen WHERE fingerabdruck = ?", (fingerabdruck,)).fetchone()
        if z:
            tage = _tage(z["tage"])
            if tag not in tage:
                tage.append(tag)
            anzahl = (z["anzahl"] or 0) + 1
            vid = z["vorhaben_id"]
            if vid is not None:
                alt = fach.eintrag(vid)
                if alt and alt["status"] in ("verworfen", "uebernommen"):      # abgeschlossen: neu zählen, später neu vorschlagen
                    vid, tage, anzahl = None, [tag], 1
            db.execute("UPDATE stoerungen SET ort = ?, zuletzt = ?, anzahl = ?, tage = ?, vorhaben_id = ? "
                       "WHERE fingerabdruck = ?", (ort, stempel, anzahl, json.dumps(tage), vid, fingerabdruck))
        else:
            tage, anzahl, vid = [tag], 1, None
            db.execute("INSERT INTO stoerungen (fingerabdruck, ort, erstmals, zuletzt, anzahl, tage, vorhaben_id) "
                       "VALUES (?,?,?,?,?,?,NULL)", (fingerabdruck, ort, stempel, stempel, anzahl, json.dumps(tage)))

    if vid is None:
        if len(tage) < SCHWELLE_TAGE and anzahl < SCHWELLE_ANZAHL:
            return {"vorhaben": None, "gemeldet": False}
    else:
        v = fach.eintrag(vid)
        if not v or v["status"] != "vorgeschlagen" or anzahl % ERNEUT_ALLE:
            return {"vorhaben": vid, "gemeldet": False}

    r = fach.anlegen(f"Störung: {ort}", "fehler", "stoerung", beleg=text, fingerabdruck=fingerabdruck)
    if not r.get("ok"):
        return {"vorhaben": None, "gemeldet": False}
    with fach.verbindung() as db:
        db.execute("UPDATE stoerungen SET vorhaben_id = ? WHERE fingerabdruck = ?", (r["id"], fingerabdruck))
    if anliegen is None:
        from kern.anliegen import fach as anliegen_fach
        anliegen = anliegen_fach.standard()
    a = anliegen.anlegen("eike", "werkbank", f"Störung bei {ort}",
                         f"{ort} ist {anzahl} Mal an {len(tage)} Tagen aufgefallen. Soll sich die Werkbank darum kümmern?")
    return {"vorhaben": r["id"], "gemeldet": bool(a.get("ok"))}


def erledigt(fingerabdruck: str, fach=None) -> None:
    """Setzt Tage und Anzahl zurück; ein offenes Vorhaben bleibt unberührt."""
    fach = fach or speicher.standard()
    with fach.verbindung() as db:
        db.execute("UPDATE stoerungen SET anzahl = 0, tage = '[]', vorhaben_id = NULL WHERE fingerabdruck = ?",
                   (fingerabdruck,))
