"""Gesprächsprotokoll — SQLite im WAL-Modus. Nichts wird gelöscht.

Das ist Fridays episodisches Gedächtnis: was wann gesagt wurde. Nach einem
Gespräch fasst der Rückblick es in einem Satz zusammen (`zusammenfassung`),
und das Werkzeug `rueckblick` kann darin suchen — die Lücke, die im alten
Friday offen war ("Worüber haben wir gestern gesprochen?").
Gespeichert wird Text, keine Audiodaten und keine Bilder.
"""

import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS gespraeche (
    id     INTEGER PRIMARY KEY,
    art    TEXT NOT NULL,            -- sprache | chat | snapchat
    beginn TEXT NOT NULL,
    ende   TEXT
);
CREATE TABLE IF NOT EXISTS beitraege (
    id        INTEGER PRIMARY KEY,
    gespraech INTEGER NOT NULL REFERENCES gespraeche(id),
    zeit      TEXT NOT NULL,
    rolle     TEXT NOT NULL,         -- du | friday | system
    art       TEXT NOT NULL DEFAULT 'text',   -- text | bild | link
    text      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS beitraege_gespraech ON beitraege(gespraech, id);
"""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Protokoll:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self.pfad = ordner / "friday.db"
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(self.pfad, check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        # Spalte nachrüsten, ohne vorhandene Gespräche anzutasten.
        spalten = {z[1] for z in self._db.execute("PRAGMA table_info(gespraeche)")}
        for name, typ in [("zusammenfassung", "TEXT"),
                          # Langzeitgedächtnis: NULL = noch nicht gesendet,
                          # gesendet | angekommen | aufgegeben
                          ("graph_status", "TEXT"), ("graph_versuche", "INTEGER DEFAULT 0"),
                          ("graph_gesendet", "TEXT"),
                          # Person des Gesprächs (Register-Id `p1`); NULL = Eike. Kopie in Eikes Gruppe verschickt: 0/1.
                          ("person", "TEXT"),
                          # Kopie in Eikes Gruppe: 0 offen, 1 gesendet, 2 angekommen, 3 aufgegeben; Versuche dazu
                          ("graph_eike_kopie", "INTEGER DEFAULT 0"), ("graph_eike_versuche", "INTEGER DEFAULT 0")]:
            if name not in spalten:
                self._db.execute(f"ALTER TABLE gespraeche ADD COLUMN {name} {typ}")
        self._db.commit()

    def _schreiben(self, sql: str, werte: tuple) -> int:
        with self._sperre, self._db:
            return self._db.execute(sql, werte).lastrowid

    def gespraech_beginnen(self, art: str, person: str | None = None) -> int:
        # Nur „Eike“ (None) oder eine Register-Id: ein leerer Text wäre „fremd“ für SQL, aber „Eike“ für Python.
        if person is not None and not (isinstance(person, str) and re.fullmatch(r"p[1-9][0-9]{0,5}", person)):
            raise ValueError(f"Ungültige Person für ein Gespräch: {person!r}")
        return self._schreiben("INSERT INTO gespraeche (art, beginn, person) VALUES (?, ?, ?)", (art, _jetzt(), person))

    def gespraech_beenden(self, gespraech: int) -> None:
        self._schreiben("UPDATE gespraeche SET ende = ? WHERE id = ? AND ende IS NULL",
                        (_jetzt(), gespraech))

    def gibt_es(self, gespraech: int, art: str) -> bool:
        with self._sperre:
            return self._db.execute("SELECT 1 FROM gespraeche WHERE id = ? AND art = ?",
                                    (gespraech, art)).fetchone() is not None

    def beitrag(self, gespraech: int, rolle: str, text: str, art: str = "text") -> None:
        text = (text or "").strip()
        if text:
            self._schreiben(
                "INSERT INTO beitraege (gespraech, zeit, rolle, art, text) VALUES (?, ?, ?, ?, ?)",
                (gespraech, _jetzt(), rolle, art, text))

    def beitraege(self, gespraech: int) -> list[dict]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT zeit, rolle, art, text FROM beitraege WHERE gespraech = ? ORDER BY id",
                (gespraech,)).fetchall()
        return [dict(zip(("zeit", "rolle", "art", "text"), z)) for z in zeilen]


    def zusammenfassen(self, gespraech: int, text: str) -> None:
        self._schreiben("UPDATE gespraeche SET zusammenfassung = ? WHERE id = ?", (text.strip(), gespraech))

    def gespraeche(self, limit: int = 30) -> list[dict]:
        """Die letzten Gespräche, neueste zuerst, mit Anzahl und letzter Aktivität."""
        with self._sperre:
            zeilen = self._db.execute("""
                SELECT g.id, g.art, g.beginn, g.ende, g.zusammenfassung,
                       COUNT(b.id), MAX(b.zeit), g.person
                FROM gespraeche g LEFT JOIN beitraege b ON b.gespraech = g.id
                GROUP BY g.id ORDER BY g.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(zip(("id", "art", "beginn", "ende", "zusammenfassung", "beitraege", "zuletzt", "person"), z))
                for z in zeilen]

    def letzter_austausch(self, art: str) -> dict[str, dict]:
        """Je Person (Register-Id) die letzte Nachricht der Person (`du`) und die letzte von Friday in dieser Art
        von Gespräch — die Zeiten als ISO-Text (UTC). Eikes eigene Gespräche (person NULL) fehlen."""
        with self._sperre:
            zeilen = self._db.execute("""
                SELECT g.person, b.rolle, MAX(b.zeit) FROM beitraege b JOIN gespraeche g ON g.id = b.gespraech
                WHERE g.art = ? AND g.person IS NOT NULL AND b.rolle IN ('du', 'friday') GROUP BY g.person, b.rolle""",
                (art,)).fetchall()
        aus: dict[str, dict] = {}
        for person, rolle, zeit in zeilen:
            aus.setdefault(person, {})[rolle] = zeit
        return aus

    def offene_rueckblicke(self, ruhe_minuten: int = 30) -> list[int]:
        """Gespräche ohne Zusammenfassung, die vorbei sind: beendet, oder seit
        `ruhe_minuten` still. Ein Chat hat kein Ende, er wird einfach nicht
        mehr fortgesetzt — deshalb die Ruhezeit."""
        grenze = (datetime.now(timezone.utc) - timedelta(minutes=ruhe_minuten)).isoformat(timespec="seconds")
        with self._sperre:
            zeilen = self._db.execute("""
                SELECT g.id FROM gespraeche g
                WHERE g.zusammenfassung IS NULL
                  AND EXISTS (SELECT 1 FROM beitraege b WHERE b.gespraech = g.id AND b.rolle = 'du')
                  AND (g.ende IS NOT NULL
                       OR (SELECT MAX(zeit) FROM beitraege b WHERE b.gespraech = g.id) < ?)
                ORDER BY g.id""", (grenze,)).fetchall()
        return [z[0] for z in zeilen]

    def rueckblick(self, suchbegriff: str = "", tage: int = 7, limit: int = 8,
                   person: str | None = None) -> list[dict]:
        """Gespräche der letzten `tage`, optional mit Suchbegriff — neueste zuerst.
        Mit `person` (Register-Id `p<Zahl>`) nur Gespräche dieser Person, über alle
        Arten (Chat, Sprache, Snapchat): Der Name steht nie im Text, nur in
        `gespraeche.person` — die Textsuche fände sie nicht."""
        seit = (datetime.now(timezone.utc) - timedelta(days=max(1, int(tage)))).isoformat(timespec="seconds")
        sql = ("SELECT g.id, g.art, g.beginn, g.zusammenfassung, g.person FROM gespraeche g WHERE g.beginn >= ?")
        werte: list = [seit]
        if person is not None:
            sql += " AND g.person = ?"
            werte.append(person)
        muster = f"%{suchbegriff.strip().lower()}%" if suchbegriff.strip() else None
        if muster:
            sql += (" AND (LOWER(COALESCE(g.zusammenfassung, '')) LIKE ? OR EXISTS ("
                    "SELECT 1 FROM beitraege b WHERE b.gespraech = g.id AND LOWER(b.text) LIKE ?))")
            werte += [muster, muster]
        sql += " ORDER BY g.id DESC LIMIT ?"
        werte.append(int(limit))
        with self._sperre:
            gespraeche = [dict(zip(("id", "art", "beginn", "zusammenfassung", "person"), z))
                          for z in self._db.execute(sql, werte).fetchall()]
            for g in gespraeche:
                treffer = None
                if muster:
                    treffer = self._db.execute(
                        "SELECT rolle, text FROM beitraege WHERE gespraech = ? AND LOWER(text) LIKE ? "
                        "ORDER BY id LIMIT 1", (g["id"], muster)).fetchone()
                g["treffer"] = {"rolle": treffer[0], "text": treffer[1]} if treffer else None
        return gespraeche


    def abschrift(self, gespraech: int, sprecher: str = "Eike") -> str:
        """Das Gespräch als Text, ohne Systemzeilen — so geht es an Graphiti. `sprecher` nennt die Gegenseite: Bei
        einer anderen Person MUSS ihr Name stehen, sonst würde der Graph ihre Worte als Eikes lesen."""
        zeilen = []
        for b in self.beitraege(gespraech):
            if b["rolle"] == "du":
                zeilen.append(f"{sprecher}: {b['text']}")
            elif b["rolle"] == "friday":
                zeilen.append(f"Friday: {b['text']}")
        return "\n".join(zeilen)

    def kopf(self, gespraech: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT id, art, beginn, zusammenfassung, graph_status, graph_versuche, person, "
                                 "COALESCE(graph_eike_kopie, 0), COALESCE(graph_eike_versuche, 0) FROM gespraeche WHERE id = ?",
                                 (gespraech,)).fetchone()
        return dict(zip(("id", "art", "beginn", "zusammenfassung", "graph_status", "graph_versuche", "person",
                         "graph_eike_kopie", "graph_eike_versuche"), z)) if z else None

    def graph_offen(self) -> list[int]:
        """Zusammengefasst, aber noch nie an Graphiti geschickt — Eikes eigene Gespräche vor denen mit anderen Personen
        (Eikes Entscheidung 05.10.2026: das Gemini-Tageslimit reicht nur für ≈ 40 Episoden am Tag)."""
        with self._sperre:
            return [z[0] for z in self._db.execute(
                "SELECT id FROM gespraeche WHERE zusammenfassung IS NOT NULL AND graph_status IS NULL "
                "ORDER BY person IS NOT NULL, id")]   # Eikes eigene Gespräche zuerst, dann die mit anderen Personen

    def graph_zu_pruefen(self, minuten: int = 10) -> list[int]:
        """Gesendet und lange genug her, um nachzusehen, ob es angekommen ist."""
        grenze = (datetime.now(timezone.utc) - timedelta(minutes=minuten)).isoformat(timespec="seconds")
        with self._sperre:
            return [z[0] for z in self._db.execute(
                "SELECT id FROM gespraeche WHERE graph_status = 'gesendet' AND graph_gesendet < ? "
                "ORDER BY person IS NOT NULL, id",
                (grenze,))]

    def graph_eigene_offen(self) -> bool:
        """Gibt es Gespräche mit Friday (Eikes eigene), die noch nicht im Graphen angekommen sind (nie geschickt oder
        gesendet, ohne Bestätigung)? Solange ja, kommen WhatsApp-Profile danach."""
        with self._sperre:
            return self._db.execute(
                "SELECT 1 FROM gespraeche WHERE person IS NULL AND zusammenfassung IS NOT NULL "
                "AND (graph_status IS NULL OR graph_status = 'gesendet') LIMIT 1").fetchone() is not None

    def zusammenfassungen_mit(self, begriff: str, limit: int = 6) -> list[str]:
        """Zusammenfassungen von Eikes eigenen Gesprächen mit Friday, in denen `begriff` vorkommt (neueste zuerst)."""
        muster = "%" + begriff.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._sperre:
            return [z[0] for z in self._db.execute(
                "SELECT zusammenfassung FROM gespraeche WHERE person IS NULL AND zusammenfassung IS NOT NULL "
                "AND zusammenfassung LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?", (muster, limit))]

    def graph_in_arbeit(self, minuten: int = 10) -> int:
        """Gesendet und jünger als `minuten`: so viele liegen gerade in Graphitis Schlange oder werden verarbeitet."""
        grenze = (datetime.now(timezone.utc) - timedelta(minutes=minuten)).isoformat(timespec="seconds")
        with self._sperre:
            return self._db.execute("SELECT COUNT(*) FROM gespraeche WHERE graph_status = 'gesendet' "
                                    "AND graph_gesendet >= ?", (grenze,)).fetchone()[0]

    def graph_stand(self) -> dict:
        """Wie viele Gespräche das Langzeitgedächtnis schon gelernt hat."""
        with self._sperre:
            zeilen = self._db.execute("SELECT COALESCE(graph_status, 'wartet'), COUNT(*) FROM gespraeche "
                                      "WHERE EXISTS (SELECT 1 FROM beitraege b WHERE b.gespraech = gespraeche.id "
                                      "AND b.rolle = 'du') GROUP BY 1").fetchall()
        return {status: anzahl for status, anzahl in zeilen}

    def eike_kopie_offen(self) -> list[int]:
        """Gespräche mit einer anderen Person, deren Episode in ihrer Gruppe angekommen ist und die noch nicht als
        Kopie mit Vermerk in Eikes Gruppe stehen."""
        with self._sperre:
            return [z[0] for z in self._db.execute(
                "SELECT id FROM gespraeche WHERE person IS NOT NULL AND graph_status = 'angekommen' "
                "AND COALESCE(graph_eike_kopie, 0) = 0 ORDER BY id")]

    def eike_kopie_zu_pruefen(self) -> list[int]:
        """Kopien, die gesendet, aber noch nicht als angekommen bestätigt sind."""
        with self._sperre:
            return [z[0] for z in self._db.execute(
                "SELECT id FROM gespraeche WHERE person IS NOT NULL AND graph_eike_kopie = 1 ORDER BY id")]

    def eike_kopie_setzen(self, gespraech: int, status: int, versuch: bool = False) -> None:
        """0 offen, 1 gesendet, 2 angekommen, 3 aufgegeben; `versuch` zählt einen Sendeversuch mit."""
        self._schreiben("UPDATE gespraeche SET graph_eike_kopie = ?, graph_eike_versuche = "
                        "COALESCE(graph_eike_versuche, 0) + ? WHERE id = ?", (int(status), 1 if versuch else 0, gespraech))

    def eike_kopie_markieren(self, gespraech: int) -> None:
        self.eike_kopie_setzen(gespraech, 1, versuch=True)

    def graph_wieder_versuchen(self, stunden: int = 12) -> int:
        """Aufgegebene nach einer Weile neu anstellen — meist war nur das Tageslimit
        von Gemini erschöpft, und Graphiti verwirft das still."""
        grenze = (datetime.now(timezone.utc) - timedelta(hours=stunden)).isoformat(timespec="seconds")
        with self._sperre:
            n = self._db.execute("SELECT COUNT(*) FROM gespraeche WHERE graph_status = 'aufgegeben' "
                                 "AND graph_gesendet < ?", (grenze,)).fetchone()[0]
        if n:
            self._schreiben("UPDATE gespraeche SET graph_status = NULL, graph_versuche = 0, graph_gesendet = NULL "
                            "WHERE graph_status = 'aufgegeben' AND graph_gesendet < ?", (grenze,))
        return n

    def graph_markieren(self, gespraech: int, status: str, neuer_versuch: bool = False) -> None:
        if neuer_versuch:
            self._schreiben("UPDATE gespraeche SET graph_status = ?, graph_gesendet = ?, "
                            "graph_versuche = COALESCE(graph_versuche, 0) + 1 WHERE id = ?",
                            (status, _jetzt(), gespraech))
        else:
            self._schreiben("UPDATE gespraeche SET graph_status = ? WHERE id = ?", (status, gespraech))


_instanzen: dict[str, Protokoll] = {}


def standard() -> Protokoll:
    from kern import config
    ordner = config.daten_ordner()
    schluessel = str(ordner.resolve())
    if schluessel not in _instanzen:
        _instanzen[schluessel] = Protokoll(ordner)
    return _instanzen[schluessel]
