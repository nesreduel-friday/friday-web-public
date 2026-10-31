"""Chat-Ablage — die WhatsApp-Verläufe, gespeichert und durchsuchbar.

SQLite im WAL-Modus (`data/chats.db`) mit Volltextsuche (FTS5, Umlaute egal).
Roh-Uploads liegen unter `data/chats/roh/`. Nichts wird gelöscht.

Vier Tabellen:
- chats:       eine Datei = ein Verlauf (mit wem, Zeitraum, Zahl der Nachrichten)
- nachrichten: jede einzelne Nachricht (Zeit, Absender, Text)
- personen:    echte Menschen; jede sammelt ihre Absendernamen (Aliase) ein.
               Genau eine ist Eike selbst (`ich = 1`).
- abschnitte:  Auswert-Häppchen (ein Chat, ein Monat) mit Gemini-Ergebnis

Absender ↔ Person läuft über `absender_person`: Eike ordnet einmal zu, wer
hinter einem Namen steckt ("Kontakt P", "Kontakt A 🏀"). Erst dann wird ausgewertet.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from kern import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id          INTEGER PRIMARY KEY,
    dateiname   TEXT NOT NULL,
    partner     TEXT NOT NULL DEFAULT '',   -- aus dem Dateinamen geraten
    von         TEXT NOT NULL DEFAULT '',
    bis         TEXT NOT NULL DEFAULT '',
    anzahl      INTEGER NOT NULL DEFAULT 0,
    absender    TEXT NOT NULL DEFAULT '[]', -- vorkommende Absendernamen
    auswerten   INTEGER NOT NULL DEFAULT 1, -- 0 = nur lokal, nicht an Gemini
    gruppe      INTEGER NOT NULL DEFAULT 0, -- mehr als zwei Schreibende
    hochgeladen TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS nachrichten (
    id       INTEGER PRIMARY KEY,
    chat     INTEGER NOT NULL,
    zeit     TEXT NOT NULL DEFAULT '',
    absender TEXT NOT NULL DEFAULT '',
    text     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS nachrichten_chat ON nachrichten(chat, id);
CREATE VIRTUAL TABLE IF NOT EXISTS nachrichten_suche USING fts5(
    text, absender, tokenize = "unicode61 remove_diacritics 2");
CREATE TABLE IF NOT EXISTS personen (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    rolle   TEXT NOT NULL DEFAULT '',        -- Freund, Mutter, Vater …
    ich     INTEGER NOT NULL DEFAULT 0,
    profil  TEXT,                            -- JSON: Gemini-Auswertung
    modell  TEXT,
    stand   TEXT
);
CREATE TABLE IF NOT EXISTS absender_person (
    absender TEXT PRIMARY KEY,
    person   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS abschnitte (
    id       INTEGER PRIMARY KEY,
    chat     INTEGER NOT NULL,
    monat    TEXT NOT NULL DEFAULT '',       -- YYYY-MM
    von_id   INTEGER NOT NULL,
    bis_id   INTEGER NOT NULL,
    anzahl   INTEGER NOT NULL DEFAULT 0,
    status   TEXT NOT NULL DEFAULT 'neu',    -- neu | fertig | fehler
    ergebnis TEXT,
    modell   TEXT,
    versuche INTEGER NOT NULL DEFAULT 0,
    fehler   TEXT,
    UNIQUE (chat, monat)
);
"""

MAX_VERSUCHE = 3


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def name_saeubern(name: str) -> str:
    """Anzeigename ohne Emoji und Zierrat: „Kontakt A 🏀" wird „Kontakt A"."""
    ohne = "".join(z for z in (name or "")
                   if not unicodedata.category(z).startswith("S") and z not in "•·~")
    return " ".join(ohne.split()).strip(" -–—:;,.") or (name or "").strip()


def _schluessel(name: str) -> str:
    """Zum Vergleichen: gesäubert und ohne Groß-/Kleinschreibung."""
    return name_saeubern(name).casefold()


def suchausdruck(text: str) -> str:
    """Freie Wörter → FTS5-Ausdruck. Sonderzeichen fallen weg, damit eine
    Eingabe die Abfrage nicht verbiegen kann."""
    return " ".join(f'"{w}"*' for w in re.findall(r"\w+", text or "")[:8])


class Ablage:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self.ordner = ordner
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(ordner / "chats.db", check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        # Spalte nachrüsten, ohne vorhandene Verläufe anzutasten.
        if "gruppe" not in {z[1] for z in self._db.execute("PRAGMA table_info(chats)")}:
            self._db.execute("ALTER TABLE chats ADD COLUMN gruppe INTEGER NOT NULL DEFAULT 0")
        self._db.commit()

    def roh_ordner(self) -> Path:
        p = self.ordner / "chats" / "roh"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---------- Aufnehmen ----------

    def chat_anlegen(self, dateiname: str, partner: str) -> int:
        with self._sperre, self._db:
            return self._db.execute(
                "INSERT INTO chats (dateiname, partner, hochgeladen) VALUES (?, ?, ?)",
                (dateiname, partner, _jetzt())).lastrowid

    def nachrichten_schreiben(self, chat: int, häppchen: list[dict]) -> None:
        """Ein Schwung Nachrichten. In Blöcken aufgerufen — der Verlauf ist riesig."""
        with self._sperre, self._db:
            self._db.executemany(
                "INSERT INTO nachrichten (chat, zeit, absender, text) VALUES (?, ?, ?, ?)",
                [(chat, n["zeit"], n["absender"], n["text"]) for n in häppchen])

    def chat_abschliessen(self, chat: int) -> dict:
        """FTS füllen, Kennzahlen setzen, Monats-Abschnitte anlegen."""
        with self._sperre, self._db:
            # Einmal je Chat, direkt nach dem Einlesen — daher keine Dubletten.
            self._db.execute(
                "INSERT INTO nachrichten_suche (rowid, text, absender) "
                "SELECT id, text, absender FROM nachrichten WHERE chat = ?", (chat,))
            z = self._db.execute("SELECT COUNT(*) n, MIN(zeit) v, MAX(zeit) b FROM nachrichten WHERE chat = ?",
                                 (chat,)).fetchone()
            absender = [r[0] for r in self._db.execute(
                "SELECT absender, COUNT(*) c FROM nachrichten WHERE chat = ? GROUP BY absender ORDER BY c DESC",
                (chat,))]
            self._db.execute("UPDATE chats SET anzahl = ?, von = ?, bis = ?, absender = ? WHERE id = ?",
                             (z["n"], z["v"] or "", z["b"] or "", json.dumps(absender, ensure_ascii=False), chat))
            for monat, vid, bid, anz in self._db.execute(
                    "SELECT substr(zeit, 1, 7) m, MIN(id), MAX(id), COUNT(*) FROM nachrichten "
                    "WHERE chat = ? AND zeit != '' GROUP BY m", (chat,)).fetchall():
                self._db.execute("INSERT OR IGNORE INTO abschnitte (chat, monat, von_id, bis_id, anzahl) "
                                 "VALUES (?, ?, ?, ?, ?)", (chat, monat, vid, bid, anz))
        return self.chat(chat)

    # ---------- Lesen ----------

    @staticmethod
    def _chat(z: sqlite3.Row) -> dict:
        d = dict(z)
        d["absender"] = json.loads(d["absender"] or "[]")
        return d

    def chat(self, cid: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM chats WHERE id = ?", (cid,)).fetchone()
        return self._chat(z) if z else None

    def chats(self) -> list[dict]:
        with self._sperre:
            return [self._chat(z) for z in self._db.execute("SELECT * FROM chats ORDER BY bis DESC, id DESC")]

    def nachrichten(self, von_id: int, bis_id: int) -> list[dict]:
        with self._sperre:
            return [dict(z) for z in self._db.execute(
                "SELECT zeit, absender, text FROM nachrichten WHERE id BETWEEN ? AND ? ORDER BY id",
                (von_id, bis_id))]

    def suchen(self, text: str, anzahl: int = 20, person: int | None = None) -> list[dict]:
        ausdruck = suchausdruck(text)
        if not ausdruck:
            return []
        sql = ("SELECT n.zeit, n.absender, n.text, c.partner FROM nachrichten_suche s "
               "JOIN nachrichten n ON n.id = s.rowid JOIN chats c ON c.id = n.chat "
               "WHERE nachrichten_suche MATCH ?")
        werte: list = [ausdruck]
        if person is not None:
            sql += (" AND n.absender IN (SELECT absender FROM absender_person WHERE person = ?)")
            werte.append(person)
        sql += " ORDER BY n.zeit DESC LIMIT ?"
        werte.append(int(anzahl))
        with self._sperre:
            return [dict(z) for z in self._db.execute(sql, werte)]

    # ---------- Personen ----------

    def offene_absender(self) -> list[dict]:
        """Absendernamen, die noch keiner Person zugeordnet sind, häufigste zuerst."""
        with self._sperre:
            return [{"absender": r[0], "anzahl": r[1], "chats": r[2]} for r in self._db.execute(
                "SELECT absender, COUNT(*) c, COUNT(DISTINCT chat) k FROM nachrichten "
                "WHERE absender != '' AND absender NOT IN (SELECT absender FROM absender_person) "
                "GROUP BY absender ORDER BY c DESC")]

    def namen_nachtragen(self) -> int:
        """Chats, die nur „_chat" heißen, aus dem Inhalt benennen.

        iPhone-Exporte heißen im ZIP IMMER `_chat.txt` — aus dem Dateinamen ist
        also nichts zu holen (bei Eike hießen alle 11 Verläufe gleich). Aus den
        Absendern geht es: bei zweien ist der Partner der, der nicht Eike ist;
        bei mehr ist es eine Gruppe.
        """
        ich = self.ich()
        if not ich:
            return 0        # ohne bekannte Ich-Person wäre jeder Zweier-Chat fälschlich eine Gruppe
        with self._sperre:
            eigene = {a for (a,) in self._db.execute(
                "SELECT absender FROM absender_person WHERE person = ?", (ich["id"],))}
        geaendert = 0
        for c in self.chats():
            if c["partner"] and c["partner"] not in ("_chat", "chat", ""):
                continue
            andere = [a for a in c["absender"] if a not in eigene]
            if not andere:
                continue
            gruppe = len(andere) > 1
            name = (f"Gruppe: {', '.join(name_saeubern(a) for a in andere[:3])}"
                    + (" …" if len(andere) > 3 else "")) if gruppe else name_saeubern(andere[0])
            with self._sperre, self._db:
                self._db.execute("UPDATE chats SET partner = ?, gruppe = ? WHERE id = ?",
                                 (name, int(gruppe), c["id"]))
            geaendert += 1
        return geaendert

    def zuordnen_automatisch(self) -> dict:
        """Jeden offenen Absender selbst zuordnen — damit Eike das nicht 21-mal tun muss.

        Der Reihe nach: bekannte Namen wiedererkennen (auch mit Emoji oder anderer
        Schreibweise), sonst eine neue Person anlegen. Wer Eike selbst ist, lässt
        sich nur über MEHRERE Chats erkennen — er ist der Einzige, der überall
        schreibt. Geht das nicht, bleibt genau diese eine Frage offen, statt zu raten.
        """
        offen = self.offene_absender()
        if not offen:
            return {"zugeordnet": 0, "neu": 0, "ich": None, "offen": 0}

        personen = self.personen()
        bekannt: dict[str, int] = {}
        with self._sperre:
            for absender, pid in self._db.execute("SELECT absender, person FROM absender_person"):
                bekannt[_schluessel(absender)] = pid
        for p in personen:
            bekannt.setdefault(_schluessel(p["name"]), p["id"])

        neu = zugeordnet = 0
        ich_name = None
        # Zuerst Eike selbst: sonst würde er als gewöhnliche Person angelegt.
        if not any(p["ich"] for p in personen):
            anzahl_chats = len(self.chats())
            kandidat = max(offen, key=lambda o: (o["chats"], o["anzahl"]))
            if anzahl_chats >= 2 and kandidat["chats"] > anzahl_chats / 2:
                pid = self.person_anlegen(name_saeubern(kandidat["absender"]), ich=True)
                self.zuordnen(kandidat["absender"], pid)
                bekannt[_schluessel(kandidat["absender"])] = pid
                ich_name, zugeordnet = kandidat["absender"], zugeordnet + 1

        for o in offen:
            absender = o["absender"]
            s = _schluessel(absender)
            if s in bekannt:
                if absender != ich_name:
                    self.zuordnen(absender, bekannt[s])
                    zugeordnet += 1
                continue
            # Gleiche Person, andere Schreibweise? („Kontakt A" ↔ „Kontakt A 🏀")
            treffer = next((pid for k, pid in bekannt.items()
                            if len(k) >= 3 and (k in s or s in k)), None)
            if treffer is None:
                if not any(p["ich"] for p in self.personen()) and len(self.chats()) < 2:
                    continue          # ohne zweiten Chat ist unklar, wer Eike ist
                treffer = self.person_anlegen(name_saeubern(absender))
                neu += 1
            self.zuordnen(absender, treffer)
            bekannt[s] = treffer
            zugeordnet += 1
        return {"zugeordnet": zugeordnet, "neu": neu, "ich": ich_name,
                "offen": len(self.offene_absender())}

    def person_anlegen(self, name: str, rolle: str = "", ich: bool = False) -> int:
        with self._sperre, self._db:
            if ich:
                self._db.execute("UPDATE personen SET ich = 0")   # nur eine ist Eike
            return self._db.execute("INSERT INTO personen (name, rolle, ich) VALUES (?, ?, ?)",
                                    (name.strip(), rolle.strip(), int(ich))).lastrowid

    def person_umbenennen(self, person: int, name: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE personen SET name = ? WHERE id = ?", (name.strip(), person))

    def zuordnen(self, absender: str, person: int) -> None:
        with self._sperre, self._db:
            self._db.execute("INSERT OR REPLACE INTO absender_person (absender, person) VALUES (?, ?)",
                             (absender, person))

    def personen(self) -> list[dict]:
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM absender_person a WHERE a.person = p.id) aliase "
                "FROM personen p ORDER BY p.ich DESC, p.name").fetchall()
        out = []
        for z in zeilen:
            d = dict(z)
            try:
                d["profil"] = json.loads(d["profil"]) if d["profil"] else None
            except ValueError:
                d["profil"] = None
            out.append(d)
        return out

    def ich(self) -> dict | None:
        return next((p for p in self.personen() if p["ich"]), None)

    def person_nach_name(self, name: str) -> dict | None:
        name = (name or "").strip().lower()
        return next((p for p in self.personen()
                     if p["name"].lower() == name or name in p["name"].lower()), None)

    def profil_setzen(self, person: int, profil: dict, modell: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE personen SET profil = ?, modell = ?, stand = ? WHERE id = ?",
                             (json.dumps(profil, ensure_ascii=False), modell, _jetzt(), person))

    # ---------- Abschnitte (Auswertung) ----------

    def offene_abschnitte(self, nur_auswertbare: bool = True) -> list[int]:
        sql = ("SELECT a.id FROM abschnitte a JOIN chats c ON c.id = a.chat "
               "WHERE (a.status = 'neu' OR (a.status = 'fehler' AND a.versuche < ?))")
        if nur_auswertbare:
            sql += " AND c.auswerten = 1"
        sql += " ORDER BY a.monat DESC, a.anzahl DESC"
        with self._sperre:
            return [z[0] for z in self._db.execute(sql, (MAX_VERSUCHE,))]

    def abschnitt(self, aid: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM abschnitte WHERE id = ?", (aid,)).fetchone()
        return dict(z) if z else None

    def abschnitt_fertig(self, aid: int, ergebnis: dict, modell: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE abschnitte SET status = 'fertig', ergebnis = ?, modell = ?, fehler = NULL "
                             "WHERE id = ?", (json.dumps(ergebnis, ensure_ascii=False), modell, aid))

    def abschnitt_fehler(self, aid: int, text: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE abschnitte SET status = 'fehler', versuche = versuche + 1, fehler = ? "
                             "WHERE id = ?", (text, aid))

    def abschnitte_fuer_person(self, person: int) -> list[dict]:
        """Fertige Auswert-Häppchen aller Chats, in denen die Person schreibt."""
        with self._sperre:
            zeilen = self._db.execute(
                "SELECT a.monat, a.ergebnis, c.partner FROM abschnitte a JOIN chats c ON c.id = a.chat "
                "WHERE a.status = 'fertig' AND a.chat IN "
                "(SELECT DISTINCT n.chat FROM nachrichten n JOIN absender_person ap ON ap.absender = n.absender "
                " WHERE ap.person = ?) ORDER BY a.monat", (person,)).fetchall()
        out = []
        for z in zeilen:
            try:
                out.append({"monat": z["monat"], "partner": z["partner"], "ergebnis": json.loads(z["ergebnis"])})
            except ValueError:
                pass
        return out

    def stand(self) -> dict:
        with self._sperre:
            chats = self._db.execute("SELECT COUNT(*), COALESCE(SUM(anzahl), 0) FROM chats").fetchone()
            absch = {s: n for s, n in self._db.execute("SELECT status, COUNT(*) FROM abschnitte GROUP BY status")}
            personen = self._db.execute("SELECT COUNT(*) FROM personen WHERE ich = 0").fetchone()[0]
            profile = self._db.execute("SELECT COUNT(*) FROM personen WHERE profil IS NOT NULL").fetchone()[0]
            offen = self._db.execute("SELECT COUNT(*) FROM nachrichten WHERE absender != '' AND absender NOT IN "
                                     "(SELECT absender FROM absender_person)").fetchone()[0]
        return {"chats": chats[0], "nachrichten": chats[1], "abschnitte": absch,
                "personen": personen, "profile": profile, "nicht_zugeordnet": offen}


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Ablage(ordner)
    return _instanzen[str(ordner)]
