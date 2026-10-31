"""Der Chat-Strang: einzige Wahrheit für das, was der Chat zeigt (28.10.2026, Plan 2026-10-28-chat-strang).

Eine Zeile je Nachricht von Eike oder Friday (auch gesprochene Runden und Meldungen von Friday). Das Protokoll (`friday.db`) bleibt
daneben bestehen — es füttert Rückblick und Gedächtnis; der Strang füttert Anzeige und Kontext.

„Chat leeren“ setzt nur die Marke `leer_ab`: Die Zeilen darüber verschwinden aus Anzeige und Kontext, gelöscht wird nichts. Älteres
holt sich Friday nur bei Bedarf (`frueher`, `suchen`). `kontext_text` ersetzt den Text im Modellkontext; so landet Fremdmaterial
(z. B. die Antwort eines Prüf-Workers) nie als Friday-Wort im Kontext."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

KONTEXT_MAX = 40                       # so viele Zeilen gehen höchstens als Kontext an das Modell
TEXT_MAX = 20_000
ROLLEN = ("du", "friday")
ARTEN = ("text", "hinweis", "quittung", "meldung")
KONTEXT_ARTEN = ("text", "meldung")
SUCHE_MAX_ZEILEN = 3000                # so viele Zeilen im Zeitraum werden höchstens durchsucht

SCHEMA = """
CREATE TABLE IF NOT EXISTS eintraege (
    id           INTEGER PRIMARY KEY,
    zeit         TEXT NOT NULL,
    rolle        TEXT NOT NULL,                   -- du | friday
    art          TEXT NOT NULL DEFAULT 'text',    -- text | hinweis | quittung | meldung
    quelle       TEXT NOT NULL DEFAULT 'chat',    -- chat | live | meldung
    gespraech    INTEGER,
    text         TEXT NOT NULL,
    kontext_text TEXT,
    status       TEXT                             -- nur quittung: ok | nicht | rueckfrage
);
CREATE TABLE IF NOT EXISTS zustand (schluessel TEXT PRIMARY KEY, wert TEXT NOT NULL);
"""

_TEXT = "COALESCE(kontext_text, text)"
_ART_IN = ",".join("?" * len(KONTEXT_ARTEN))


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Strang:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self.pfad = ordner / "chatstrang.db"
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(self.pfad, check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _leer_ab(self) -> int:
        z = self._db.execute("SELECT wert FROM zustand WHERE schluessel = 'leer_ab'").fetchone()
        return int(z[0]) if z else 0

    def anhaengen(self, rolle: str, text: str, art: str = "text", quelle: str = "chat", gespraech: int | None = None,
                  kontext_text: str | None = None, status: str | None = None) -> int:
        if rolle not in ROLLEN or art not in ARTEN:
            raise ValueError(f"Unbekannte Rolle oder Art: {rolle}/{art}")
        text = str(text or "")
        if not text.strip():
            raise ValueError("Ein leerer Text gehört nicht in den Chat.")
        if len(text) > TEXT_MAX:
            raise ValueError(f"Der Text ist zu lang (höchstens {TEXT_MAX} Zeichen).")
        with self._sperre, self._db:
            return self._db.execute(
                "INSERT INTO eintraege (zeit, rolle, art, quelle, gespraech, text, kontext_text, status) VALUES (?,?,?,?,?,?,?,?)",
                (_jetzt(), rolle, art, quelle, gespraech, text, kontext_text, status)).lastrowid

    def leer_ab(self) -> int:
        return self._leer_ab()

    def letzte_id(self) -> int:
        z = self._db.execute("SELECT MAX(id) FROM eintraege").fetchone()
        return int(z[0] or 0)

    def sichtbar(self, nach: int = 0, limit: int = 200) -> list[dict]:
        grenze = max(int(nach or 0), self._leer_ab())
        zeilen = self._db.execute(
            "SELECT id, zeit, rolle, art, text, quelle, status FROM eintraege WHERE id > ? ORDER BY id LIMIT ?",
            (grenze, max(1, min(int(limit), 500)))).fetchall()
        return [dict(zip(("id", "zeit", "rolle", "art", "text", "quelle", "status"), z)) for z in zeilen]

    def kontext(self, maximal: int = KONTEXT_MAX) -> list[dict]:
        zeilen = self._db.execute(
            f"SELECT rolle, {_TEXT} FROM eintraege WHERE id > ? AND art IN ({_ART_IN}) ORDER BY id DESC LIMIT ?",
            (self._leer_ab(), *KONTEXT_ARTEN, max(1, int(maximal)))).fetchall()
        return [{"rolle": r, "text": t} for r, t in reversed(zeilen)]

    def leeren(self) -> int:
        with self._sperre, self._db:
            marke = self.letzte_id()
            self._db.execute("INSERT INTO zustand (schluessel, wert) VALUES ('leer_ab', ?) "
                             "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert", (str(marke),))
            return marke

    def _fenster_beginn(self) -> int:
        """Erste Id, die noch im Kontextfenster liegt; alles davor ist „älter“."""
        leer_ab = self._leer_ab()
        ids = self._db.execute(
            f"SELECT id FROM eintraege WHERE id > ? AND art IN ({_ART_IN}) ORDER BY id DESC LIMIT ?",
            (leer_ab, *KONTEXT_ARTEN, KONTEXT_MAX)).fetchall()
        if len(ids) < KONTEXT_MAX:
            return leer_ab + 1
        return int(ids[-1][0])

    def frueher(self, vor_id: int = 0, anzahl: int = 6) -> list[dict]:
        """Die `anzahl` Text-Zeilen unmittelbar vor dem Kontextfenster (mit `vor_id`: vor dieser Id), aufsteigend."""
        grenze = self._fenster_beginn()
        if vor_id:
            grenze = min(grenze, int(vor_id))
        zeilen = self._db.execute(
            f"SELECT id, zeit, rolle, art, {_TEXT}, quelle FROM eintraege WHERE id < ? AND art IN ({_ART_IN}) "
            "ORDER BY id DESC LIMIT ?", (grenze, *KONTEXT_ARTEN, max(1, int(anzahl)))).fetchall()
        return [dict(zip(("id", "zeit", "rolle", "art", "text", "quelle"), z)) for z in reversed(zeilen)]

    def suchen(self, begriff: str, tage: int = 2, limit: int = 20) -> list[dict]:
        """Teilstring ohne Groß-/Kleinschreibung über ALLE Text-Zeilen (auch vor der Marke), neueste zuerst."""
        wort = str(begriff or "").strip().casefold()
        if not wort:
            return []
        seit = (datetime.now(timezone.utc) - timedelta(days=max(1, int(tage)))).isoformat(timespec="seconds")
        zeilen = self._db.execute(
            f"SELECT id, zeit, rolle, art, {_TEXT}, quelle FROM eintraege WHERE zeit >= ? AND art IN ({_ART_IN}) "
            "ORDER BY id DESC LIMIT ?", (seit, *KONTEXT_ARTEN, SUCHE_MAX_ZEILEN)).fetchall()
        treffer = [dict(zip(("id", "zeit", "rolle", "art", "text", "quelle"), z)) for z in zeilen if wort in z[4].casefold()]
        return treffer[:max(1, int(limit))]


_instanzen: dict[str, Strang] = {}


def standard() -> Strang:
    from kern import config
    ordner = config.daten_ordner()
    schluessel = str(ordner.resolve())
    if schluessel not in _instanzen:
        _instanzen[schluessel] = Strang(ordner)
    return _instanzen[schluessel]
