"""Token-Zähler für Gemini (28.10.2026): Wie viel verbraucht Friday, wofür und mit welchem Modell?

Google schickt in jeder Antwort `usageMetadata`. `zaehlen` addiert sie je Tag (Berliner Zeit), Modell und Bereich in
`data/tokenzaehler.db`. Es werden nur Zahlen gespeichert, nie Inhalte. Ein Fehler hier darf nie eine Antwort verhindern."""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("friday.tokens")

ZONE = ZoneInfo("Europe/Berlin")
FELDER = (("promptTokenCount", "eingabe"), ("candidatesTokenCount", "ausgabe"),
          ("thoughtsTokenCount", "denken"), ("cachedContentTokenCount", "zwischenspeicher"))
SCHEMA = """
CREATE TABLE IF NOT EXISTS verbrauch (
    tag TEXT NOT NULL, modell TEXT NOT NULL, bereich TEXT NOT NULL,
    aufrufe INTEGER NOT NULL DEFAULT 0, eingabe INTEGER NOT NULL DEFAULT 0, ausgabe INTEGER NOT NULL DEFAULT 0,
    denken INTEGER NOT NULL DEFAULT 0, zwischenspeicher INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tag, modell, bereich));
"""
_sperre = threading.Lock()
_verbindungen: dict[str, sqlite3.Connection] = {}


def _db() -> sqlite3.Connection:
    from kern import config
    ordner = config.daten_ordner()
    schluessel = str(ordner.resolve())
    if schluessel not in _verbindungen:
        ordner.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(ordner / "tokenzaehler.db", check_same_thread=False, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        _verbindungen[schluessel] = db
    return _verbindungen[schluessel]


def bereich_des_aufrufers(tiefe: int = 2) -> str:
    """Zweite Namensebene des aufrufenden Moduls (`kern.denken.runde` → „denken“); unbekannt → „sonstiges“."""
    try:
        teile = sys._getframe(tiefe).f_globals.get("__name__", "").split(".")
        return teile[1] if len(teile) > 2 else (teile[-1] if teile and teile[-1] else "sonstiges")
    except Exception:
        return "sonstiges"


def zaehlen(modell: str, antwort: dict | None, bereich: str = "sonstiges") -> None:
    """Addiert die `usageMetadata` einer Antwort. Ohne sie (Attrappen in Tests) wird nichts gezählt."""
    try:
        nutz = (antwort or {}).get("usageMetadata") or {}
        if not nutz:
            return
        werte = [int(nutz.get(quelle) or 0) for quelle, _ in FELDER]
        werte[1] = werte[1] or int(nutz.get("responseTokenCount") or 0)      # Live-Sitzungen nennen die Ausgabe so
        tag = datetime.now(ZONE).strftime("%Y-%m-%d")
        with _sperre, _db() as db:
            db.execute("INSERT OR IGNORE INTO verbrauch (tag, modell, bereich) VALUES (?,?,?)", (tag, modell, bereich))
            db.execute("UPDATE verbrauch SET aufrufe = aufrufe + 1, eingabe = eingabe + ?, ausgabe = ausgabe + ?, "
                       "denken = denken + ?, zwischenspeicher = zwischenspeicher + ? WHERE tag = ? AND modell = ? AND bereich = ?",
                       (*werte, tag, modell, bereich))
    except Exception as exc:
        log.warning("Token-Zähler: %s", type(exc).__name__)


def uebersicht(tage: int = 1) -> dict:
    """Summe der letzten `tage` Tage (1 = heute), aufgeteilt nach Modell und Bereich."""
    tage = max(1, min(int(tage), 90))
    ab = (datetime.now(ZONE) - timedelta(days=tage - 1)).strftime("%Y-%m-%d")
    with _sperre:
        zeilen = _db().execute("SELECT modell, bereich, SUM(aufrufe), SUM(eingabe), SUM(ausgabe), SUM(denken), SUM(zwischenspeicher) "
                               "FROM verbrauch WHERE tag >= ? GROUP BY modell, bereich ORDER BY SUM(eingabe + ausgabe + denken) DESC",
                               (ab,)).fetchall()
    eintraege = [dict(zip(("modell", "bereich", "aufrufe", "eingabe", "ausgabe", "denken", "zwischenspeicher"), z)) for z in zeilen]
    summe = {k: sum(e[k] for e in eintraege) for k in ("aufrufe", "eingabe", "ausgabe", "denken", "zwischenspeicher")}
    return {"seit": ab, "tage": tage, "summe": summe, "eintraege": eintraege}


def als_text(tage: int = 1) -> str:
    u = uebersicht(tage)
    s = u["summe"]
    kopf = "heute" if u["tage"] == 1 else f"letzte {u['tage']} Tage"
    if not s["aufrufe"]:
        return f"Noch keine Gemini-Aufrufe gezählt ({kopf})."
    zeilen = [f"Gemini {kopf}: {s['aufrufe']} Aufrufe, {s['eingabe']} Tokens rein, {s['ausgabe']} raus, {s['denken']} zum Denken."]
    for e in u["eintraege"][:8]:
        zeilen.append(f"- {e['bereich']} / {e['modell']}: {e['aufrufe']}× · {e['eingabe']} rein · {e['ausgabe']} raus · {e['denken']} Denken")
    return "\n".join(zeilen)
