"""Goodnotes-Ablage: Hefte, Seiten, Abschriften, Volltextsuche.

`data/goodnotes.db` (WAL), PDFs unter `data/goodnotes/pdf/<id>.pdf`.

- hefte: eine Zeile je PDF im WebDAV-Ordner. Verschwindet eine Datei, wird sie
  als `weg` markiert, nicht gelöscht.
- seiten: je Seite ein Fingerabdruck des Seitenbilds. Nur Seiten mit neuem
  Fingerabdruck gehen wieder an Gemini. Goodnotes schreibt beim Backup das
  ganze PDF neu, und eingefügte Seiten verschieben die Nummern — deshalb wird
  eine Abschrift auch übernommen, wenn derselbe Fingerabdruck an anderer
  Stelle im Heft auftaucht.
- suche: FTS5 über Ort (Ordner/Heft), Titel und Abschrift. Das ist ein
  abgeleiteter Index; eine veraltete Zeile wird ersetzt, nicht aufgehoben.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from kern import config

MAX_VERSUCHE = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS hefte (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pfad      TEXT NOT NULL UNIQUE,        -- relativ zu /webdav/
    ordner    TEXT NOT NULL DEFAULT '',    -- relativ zum Goodnotes-Ordner
    name      TEXT NOT NULL DEFAULT '',
    groesse   INTEGER NOT NULL DEFAULT 0,
    geaendert TEXT NOT NULL DEFAULT '',
    seiten    INTEGER NOT NULL DEFAULT 0,
    status    TEXT NOT NULL DEFAULT 'neu', -- neu (laden), geladen, fehler
    geladen_groesse   INTEGER,
    geladen_geaendert TEXT,
    weg       INTEGER NOT NULL DEFAULT 0,
    fehler    TEXT,
    gesehen   TEXT
);
CREATE TABLE IF NOT EXISTS seiten (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    heft      INTEGER NOT NULL,
    nr        INTEGER NOT NULL,            -- ab 1
    hash      TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'neu', -- neu, fertig, fehler
    art       TEXT NOT NULL DEFAULT '',
    titel     TEXT NOT NULL DEFAULT '',
    datum     TEXT NOT NULL DEFAULT '',
    text      TEXT NOT NULL DEFAULT '',
    versuche  INTEGER NOT NULL DEFAULT 0,
    fehler    TEXT,
    weg       INTEGER NOT NULL DEFAULT 0,
    gelesen   TEXT,
    UNIQUE (heft, nr)
);
CREATE VIRTUAL TABLE IF NOT EXISTS suche USING fts5(wo, titel, text, tokenize = "unicode61 remove_diacritics 2");
CREATE TABLE IF NOT EXISTS einstellungen (schluessel TEXT PRIMARY KEY, wert TEXT NOT NULL);
"""

STOPPWOERTER = set("""
der die das den dem des ein eine einer eines einem einen und oder aber was wie wer wo wann warum wieso
ist sind war waren hat haben hatte wird werden wurde mit von vom zum zur zu im in am an auf aus bei für
über unter nach vor nicht kein keine noch auch schon nur mal mir mich dir dich ich du er sie es wir ihr
the and was were how what why which that this with from for about zu da dazu davon mein meine meinem
steht stand gemacht machen haben heft heften notizen notiz goodnotes seite seiten bitte kannst sag gibt
""".split())


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def anfrage(text: str) -> str:
    """Freitext → FTS5-Abfrage: Wörter ab drei Zeichen ohne Füllwörter, als Präfix, ODER-verknüpft."""
    woerter = []
    for w in re.findall(r"\w{3,}", (text or "").casefold()):
        if w not in STOPPWOERTER and w not in woerter:
            woerter.append(w)
    return " OR ".join(f'"{w}"*' for w in woerter[:12])


class Ablage:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self.ordner = ordner
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(ordner / "goodnotes.db", check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        # Beim Start ein neuer Versuch für „PDF nicht lesbar“ — am 16.09.2026 lag das an
        # gleichzeitigem PDFium-Zugriff, nicht an den Dateien.
        self._db.execute("UPDATE hefte SET status = 'neu' WHERE status = 'fehler' AND fehler LIKE 'PDF nicht lesbar%'")
        self._db.commit()

    def pdf_pfad(self, hid: int) -> Path:
        return self.ordner / "goodnotes" / "pdf" / f"{int(hid)}.pdf"

    def bild_ordner(self) -> Path:
        return self.ordner / "goodnotes" / "bilder"

    # ---------- Einstellungen ----------

    def ausgeschlossen(self) -> list[str]:
        with self._sperre:
            z = self._db.execute("SELECT wert FROM einstellungen WHERE schluessel = 'ausgeschlossen'").fetchone()
        return json.loads(z[0]) if z else []

    def ausschliessen(self, ordner: list[str]) -> None:
        with self._sperre, self._db:
            self._db.execute("INSERT INTO einstellungen VALUES ('ausgeschlossen', ?) "
                             "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert",
                             (json.dumps(sorted(set(ordner)), ensure_ascii=False),))

    # ---------- Hefte ----------

    def hefte_abgleichen(self, dateien: list[dict], wurzel: str) -> list[dict]:
        """WebDAV-Liste eintragen. Gibt die Hefte zurück, die (neu) geladen werden müssen."""
        wurzel = wurzel.strip("/")
        gesehen = set()
        with self._sperre, self._db:
            for d in dateien:
                rel = d["pfad"][len(wurzel):].strip("/") if d["pfad"].startswith(wurzel) else d["pfad"]
                ordner, _, datei = rel.rpartition("/")
                name = datei.rsplit(".", 1)[0]
                gesehen.add(d["pfad"])
                self._db.execute(
                    "INSERT INTO hefte (pfad, ordner, name, groesse, geaendert, gesehen) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(pfad) DO UPDATE SET ordner = excluded.ordner, name = excluded.name, "
                    "groesse = excluded.groesse, geaendert = excluded.geaendert, gesehen = excluded.gesehen, weg = 0",
                    (d["pfad"], ordner, name, d["groesse"], d["geaendert"], _jetzt()))
            for z in self._db.execute("SELECT id, pfad FROM hefte WHERE weg = 0").fetchall():
                if z["pfad"] not in gesehen:
                    self._db.execute("UPDATE hefte SET weg = 1 WHERE id = ?", (z["id"],))
            zeilen = self._db.execute(
                "SELECT * FROM hefte WHERE weg = 0 AND (status = 'neu' OR geladen_groesse IS NULL "
                "OR geladen_groesse != groesse OR geladen_geaendert != geaendert) ORDER BY geaendert DESC").fetchall()
        return [dict(z) for z in zeilen]

    def heft_fehler(self, hid: int, text: str, endgueltig: bool = False) -> None:
        """`endgueltig` (PDF kaputt): erst wieder laden, wenn sich die Datei ändert.
        Sonst (Netz) beim nächsten Lauf noch einmal."""
        with self._sperre, self._db:
            if endgueltig:
                self._db.execute("UPDATE hefte SET status = 'fehler', fehler = ?, geladen_groesse = groesse, "
                                 "geladen_geaendert = geaendert WHERE id = ?", (text, hid))
            else:
                self._db.execute("UPDATE hefte SET fehler = ? WHERE id = ?", (text, hid))

    def heft_geladen(self, hid: int, hashes: list[str], groesse: int, geaendert: str) -> int:
        """Neue Fingerabdrücke eintragen. Gibt die Zahl der Seiten zurück, die neu gelesen werden müssen."""
        neu = 0
        with self._sperre, self._db:
            h = self._db.execute("SELECT ordner, name FROM hefte WHERE id = ?", (hid,)).fetchone()
            wo = f"{h['ordner']} {h['name']}".strip()
            alt = {z["nr"]: dict(z) for z in self._db.execute("SELECT * FROM seiten WHERE heft = ?", (hid,))}
            nach_hash = {}
            for z in alt.values():
                if z["status"] == "fertig":
                    nach_hash.setdefault(z["hash"], z)
            for nr, fp in enumerate(hashes, start=1):
                bisher = alt.get(nr)
                if bisher and bisher["hash"] == fp and not bisher["weg"]:
                    continue
                vorlage = nach_hash.get(fp)
                if vorlage:
                    werte = ("fertig", vorlage["art"], vorlage["titel"], vorlage["datum"], vorlage["text"],
                             vorlage["gelesen"])
                else:
                    werte = ("neu", "", "", "", "", None)
                    neu += 1
                self._db.execute(
                    "INSERT INTO seiten (heft, nr, hash, status, art, titel, datum, text, gelesen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(heft, nr) DO UPDATE SET hash = excluded.hash, "
                    "status = excluded.status, art = excluded.art, titel = excluded.titel, datum = excluded.datum, "
                    "text = excluded.text, gelesen = excluded.gelesen, versuche = 0, fehler = NULL, weg = 0",
                    (hid, nr, fp, *werte))
                sid = self._db.execute("SELECT id FROM seiten WHERE heft = ? AND nr = ?", (hid, nr)).fetchone()[0]
                self._db.execute("DELETE FROM suche WHERE rowid = ?", (sid,))
                if vorlage:
                    self._db.execute("INSERT INTO suche (rowid, wo, titel, text) VALUES (?, ?, ?, ?)",
                                     (sid, wo, vorlage["titel"], vorlage["text"]))
            for nr, z in alt.items():
                if nr > len(hashes) and not z["weg"]:
                    self._db.execute("UPDATE seiten SET weg = 1 WHERE id = ?", (z["id"],))
                    self._db.execute("DELETE FROM suche WHERE rowid = ?", (z["id"],))
            self._db.execute("UPDATE hefte SET seiten = ?, status = 'geladen', fehler = NULL, geladen_groesse = ?, "
                             "geladen_geaendert = ? WHERE id = ?", (len(hashes), groesse, geaendert, hid))
        return neu

    def heft(self, hid: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM hefte WHERE id = ?", (hid,)).fetchone()
        return dict(z) if z else None

    def hefte(self, mit_weg: bool = False) -> list[dict]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT h.*, COUNT(s.id) AS gezaehlt, SUM(s.status = 'fertig') AS gelesen, "
                "SUM(s.status = 'fehler' AND s.versuche >= ?) AS fehlerhaft, MAX(s.datum) AS letztes_datum "
                "FROM hefte h LEFT JOIN seiten s ON s.heft = h.id AND s.weg = 0 "
                f"{'' if mit_weg else 'WHERE h.weg = 0'} GROUP BY h.id ORDER BY h.ordner, h.name",
                (MAX_VERSUCHE,)).fetchall()
        return [dict(z) for z in zeilen]

    # ---------- Seiten ----------

    def offene_seiten(self, anzahl: int) -> list[dict]:
        """Noch zu lesen: neueste Hefte zuerst, im Heft von vorn. Ausgeschlossene Ordner nicht."""
        aus = self.ausgeschlossen()
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT s.id, s.heft, s.nr, h.ordner, h.name FROM seiten s JOIN hefte h ON h.id = s.heft "
                "WHERE s.weg = 0 AND h.weg = 0 AND h.status = 'geladen' "
                "AND (s.status = 'neu' OR (s.status = 'fehler' AND s.versuche < ?)) "
                "ORDER BY h.geaendert DESC, h.id, s.nr", (MAX_VERSUCHE,)).fetchall()
        return [dict(z) for z in zeilen if not ausgeschlossen(z["ordner"], aus)][:anzahl]

    def seite_setzen(self, sid: int, art: str, titel: str, datum: str, text: str) -> None:
        with self._sperre, self._db:
            z = self._db.execute("SELECT h.ordner, h.name FROM seiten s JOIN hefte h ON h.id = s.heft "
                                 "WHERE s.id = ?", (sid,)).fetchone()
            self._db.execute("UPDATE seiten SET status = 'fertig', art = ?, titel = ?, datum = ?, text = ?, "
                             "fehler = NULL, gelesen = ? WHERE id = ?", (art, titel, datum, text, _jetzt(), sid))
            self._db.execute("DELETE FROM suche WHERE rowid = ?", (sid,))
            self._db.execute("INSERT INTO suche (rowid, wo, titel, text) VALUES (?, ?, ?, ?)",
                             (sid, f"{z['ordner']} {z['name']}".strip(), titel, text))

    def seite_fehler(self, sid: int, text: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE seiten SET status = 'fehler', versuche = versuche + 1, fehler = ? WHERE id = ?",
                             (text, sid))

    def seite(self, hid: int, nr: int) -> dict | None:
        with self._sperre:
            z = self._db.execute(
                "SELECT s.*, h.ordner, h.name, h.seiten AS heft_seiten FROM seiten s JOIN hefte h ON h.id = s.heft "
                "WHERE s.heft = ? AND s.nr = ? AND s.weg = 0", (hid, nr)).fetchone()
        return dict(z) if z else None

    def neueste_seiten(self, heft_ids: list[int], anzahl: int = 5) -> list[dict]:
        """Fertig gelesene Seiten mehrerer Hefte, nach dem auf der Seite notierten Datum
        sortiert (neueste zuerst) — für „was zuletzt im Unterricht dran war“. Seiten ohne
        erkanntes Datum stehen hinten, sortiert nach Lesezeitpunkt."""
        if not heft_ids:
            return []
        platzhalter = ",".join("?" * len(heft_ids))
        with self._sperre:
            zeilen = self._db.execute(
                f"SELECT s.nr, s.titel, s.datum, s.art, s.text, h.id AS heft, h.ordner, h.name FROM seiten s "
                f"JOIN hefte h ON h.id = s.heft WHERE s.heft IN ({platzhalter}) AND s.weg = 0 AND h.weg = 0 "
                "AND s.status = 'fertig' AND s.text != '' "
                "ORDER BY s.datum = '' , s.datum DESC, s.gelesen DESC LIMIT ?",
                (*heft_ids, anzahl)).fetchall()
        return [dict(z) for z in zeilen]

    def letzte_seiten(self, hid: int, anzahl: int = 3) -> list[dict]:
        with self._sperre:
            return [dict(z) for z in self._db.execute(
                "SELECT nr, titel, datum, status FROM seiten WHERE heft = ? AND weg = 0 ORDER BY nr DESC LIMIT ?",
                (hid, anzahl))]

    def suchen(self, text: str, fach: str = "", anzahl: int = 8) -> list[dict]:
        abfrage = anfrage(text)
        if not abfrage:
            return []
        aus = self.ausgeschlossen()
        with self._sperre:
            try:
                zeilen = self._db.execute(
                    "SELECT s.heft, s.nr, s.titel, s.datum, s.art, h.ordner, h.name, "
                    "snippet(suche, 2, '«', '»', ' … ', 18) AS stelle, bm25(suche, 2.0, 3.0, 1.0) AS rang "
                    "FROM suche JOIN seiten s ON s.id = suche.rowid JOIN hefte h ON h.id = s.heft "
                    "WHERE suche MATCH ? AND s.weg = 0 AND h.weg = 0 ORDER BY rang LIMIT 200", (abfrage,)).fetchall()
            except sqlite3.OperationalError:
                return []
        fach_klein = (fach or "").casefold()[:4]
        treffer = [dict(z) for z in zeilen if not ausgeschlossen(z["ordner"], aus)
                   and (not fach_klein or fach_klein in f"{z['ordner']} {z['name']}".casefold())]
        return treffer[:anzahl]

    def stand(self) -> dict:
        """Zahlen nur über die Ordner, die Friday lesen soll."""
        aus = self.ausgeschlossen()
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT h.ordner, s.status, s.versuche >= ? AS aufgegeben, COUNT(*) AS n FROM seiten s "
                "JOIN hefte h ON h.id = s.heft WHERE s.weg = 0 AND h.weg = 0 GROUP BY 1, 2, 3",
                (MAX_VERSUCHE,)).fetchall()
        st = {"fertig": 0, "offen": 0, "fehlerhaft": 0, "gesamt": 0}
        for z in zeilen:
            if ausgeschlossen(z["ordner"], aus):
                continue
            st["gesamt"] += z["n"]
            if z["status"] == "fertig":
                st["fertig"] += z["n"]
            elif z["status"] == "fehler" and z["aufgegeben"]:
                st["fehlerhaft"] += z["n"]
            else:
                st["offen"] += z["n"]
        return st


def ausgeschlossen(ordner: str, liste: list[str]) -> bool:
    """Ein Ordner ist ausgeschlossen, wenn er oder ein übergeordneter in der Liste steht."""
    return any(ordner == x or ordner.startswith(x + "/") for x in liste)


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Ablage(ordner)
    return _instanzen[str(ordner)]
