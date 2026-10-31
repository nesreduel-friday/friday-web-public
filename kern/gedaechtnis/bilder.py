"""Bilder, die Eike Friday zeigt — gespeichert, beschrieben, benannt.

Jedes Bild aus Chat oder Sprache landet als Datei in data/bilder/ und als
Zeile in friday.db. Fridays Antwort auf das Bild wird zur Beschreibung — ohne
zusätzlichen Modellaufruf. Mit dem Werkzeug `bild_benennen` ordnet Friday es
einem Namen zu ("das ist Kontakt Q"), Eike kann das auch auf der Gedächtnis-Seite.
Name und Beschreibung gehen mit ins Langzeitgedächtnis; so hängt das Bild an
der richtigen Person, und die Person-Ansicht zeigt ihre Bilder.

Die Dateien liegen nur auf hphome. Die nächtliche Sicherung erfasst die
Datenbank, nicht die Bilddateien selbst.
"""

from __future__ import annotations

import base64
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from kern import config

ENDUNG = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_BYTES = 6_000_000
BILD_ID = re.compile(r"^[0-9a-f]{32}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bilder (
    id           TEXT PRIMARY KEY,
    gespraech    INTEGER,
    zeit         TEXT NOT NULL,
    mime         TEXT NOT NULL,
    name         TEXT,
    beschreibung TEXT,
    frage        TEXT,
    quelle       TEXT,          -- woher das Bild kam, z.B. „Snapchat · Eike“ (leer = Chat/Sprache in der App)
    notizen      TEXT,          -- spätere Ergänzungen, eine je Zeile
    phash        TEXT,          -- Wahrnehmungs-Hash (16 Hex): dasselbe Bild wiedererkennen, auch skaliert/zugeschnitten
    person       TEXT           -- Register-Id (`p1`) der Person, von der das Bild kam; NULL = Eike
);
CREATE INDEX IF NOT EXISTS bilder_gespraech ON bilder(gespraech, zeit);
"""

_sperre = threading.Lock()
_verbindungen: dict[str, sqlite3.Connection] = {}


def _ordner() -> Path:
    o = config.daten_ordner() / "bilder"
    o.mkdir(parents=True, exist_ok=True)
    return o


def _db() -> sqlite3.Connection:
    pfad = str((config.daten_ordner() / "friday.db").resolve())
    if pfad not in _verbindungen:
        config.daten_ordner().mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(pfad, check_same_thread=False, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        # Ältere Datenbanken kennen `quelle` und `notizen` noch nicht.
        vorhanden = {r[1] for r in c.execute("PRAGMA table_info(bilder)")}
        for spalte in ("quelle", "notizen", "phash", "person"):
            if spalte not in vorhanden:
                c.execute(f"ALTER TABLE bilder ADD COLUMN {spalte} TEXT")
                if spalte == "phash":
                    _phash_nachtragen(c)
        c.commit()
        _verbindungen[pfad] = c
    return _verbindungen[pfad]


PHASH_SCHWELLE = 6  # höchstens so viele von 64 Bits dürfen abweichen (Skalierung, Neu-Kodierung, leichter Zuschnitt)


def phash(roh: bytes) -> str | None:
    """dHash: Graustufen, 9x8, je Zeile „links heller als rechts“ — 64 Bit, als 16 Hex. Fast einfarbige
    Bilder (schwarz, weiß) haben keinen brauchbaren Fingerabdruck und geben None, sonst gälten alle
    dunklen Bilder als „dasselbe“."""
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(roh)) as bild:
            grau = list(bild.convert("L").resize((9, 8), Image.LANCZOS).tobytes())
    except Exception:  # kaputte Datei, fehlendes Pillow: dann eben ohne Fingerabdruck
        return None
    wert = 0
    for zeile in range(8):
        for spalte in range(8):
            wert = (wert << 1) | (grau[zeile * 9 + spalte] > grau[zeile * 9 + spalte + 1])
    return None if not 8 <= bin(wert).count("1") <= 56 else f"{wert:016x}"


def _phash_nachtragen(c: sqlite3.Connection) -> None:
    """Einmalig beim Anlegen der Spalte: Fingerabdruck für die schon gespeicherten Bilder."""
    for bild_id, mime in c.execute("SELECT id, mime FROM bilder").fetchall():
        try:
            h = phash((_ordner() / f"{bild_id}.{ENDUNG.get(mime, 'jpg')}").read_bytes())
        except OSError:
            continue
        if h:
            c.execute("UPDATE bilder SET phash = ? WHERE id = ?", (h, bild_id))


def gleiches(roh: bytes, ausser: str = "", person: str | None = None) -> dict | None:
    """Das früher gespeicherte Bild, das diesem entspricht (nächster Treffer unter der Schwelle), sonst None.
    So erkennt Friday ein Bild wieder — auch als Snap-Screenshot statt Chat-Bild — und kann Namen,
    Beschreibung und Notizen des alten Eintrags verbinden."""
    h = phash(roh)
    if not h:
        return None
    bester, abstand = None, PHASH_SCHWELLE + 1
    # Eike sieht alles; eine Person nur ihre eigenen Bilder (sonst kämen Eikes Notizen in ihr Gespräch).
    sql, werte = "SELECT id, phash FROM bilder WHERE phash IS NOT NULL AND id != ?", (ausser,)
    if person is not None:
        sql, werte = sql + " AND person = ?", (ausser, person)
    for z in _zeilen(sql, werte):
        d = bin(int(z["phash"], 16) ^ int(h, 16)).count("1")
        if d < abstand:
            bester, abstand = z["id"], d
    return eintrag(bester) if bester else None


def _zeilen(sql: str, werte: tuple = ()) -> list[dict]:
    with _sperre:
        cur = _db().execute(sql, werte)
        namen = [d[0] for d in cur.description]
        return [dict(zip(namen, z)) for z in cur.fetchall()]


def _schreiben(sql: str, werte: tuple) -> int:
    with _sperre, _db():
        return _db().execute(sql, werte).rowcount


def speichern(gespraech: int | None, mime: str, daten_b64: str, frage: str = "", quelle: str = "",
              person: str | None = None) -> str | None:
    if mime not in ENDUNG:
        return None
    try:
        roh = base64.b64decode(daten_b64, validate=True)
    except (ValueError, TypeError):
        return None
    if not roh or len(roh) > MAX_BYTES:
        return None
    bild_id = uuid.uuid4().hex
    (_ordner() / f"{bild_id}.{ENDUNG[mime]}").write_bytes(roh)
    alt = gleiches(roh, person=person)
    _schreiben("INSERT INTO bilder (id, gespraech, zeit, mime, frage, quelle, notizen, phash, person) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (bild_id, gespraech, datetime.now(timezone.utc).isoformat(timespec="seconds"), mime,
                (frage or "").strip()[:2000], (quelle or "").strip()[:200],
                f"Dasselbe Bild wie {alt['id']}" if alt else None, phash(roh), person))
    return bild_id


_FEHLERSATZ = re.compile(r"^\W*das hat (gerade |noch )?nicht geklappt", re.I)


def beschreiben(bild_id: str, text: str) -> None:
    # Ein gescheiterter Zug („Das hat gerade nicht geklappt“) ist keine Bildbeschreibung.
    if BILD_ID.match(bild_id or "") and (text or "").strip() and not _FEHLERSATZ.match(text.strip()):
        _schreiben("UPDATE bilder SET beschreibung = ? WHERE id = ?", (text.strip()[:1500], bild_id))


def ergaenzen(bild_id: str, info: str) -> bool:
    """Hängt eine weitere Information an ein Bild („aufgenommen bei Torges Geburtstag“). Die
    Beschreibung bleibt, wie sie war; Ergänzungen stehen extra und wachsen (Ende bleibt erhalten)."""
    info = (info or "").strip()[:500]
    e = eintrag(bild_id)
    if not info or not e:
        return False
    neu = ((e.get("notizen") or "").strip() + "\n" + info).strip()[-1500:]
    return _schreiben("UPDATE bilder SET notizen = ? WHERE id = ?", (neu, bild_id)) > 0


def benennen(bild_id: str, name: str) -> bool:
    name = (name or "").strip()[:200]
    if not BILD_ID.match(bild_id or "") or not name:
        return False
    return _schreiben("UPDATE bilder SET name = ? WHERE id = ?", (name, bild_id)) > 0


def eintrag(bild_id: str) -> dict | None:
    if not BILD_ID.match(bild_id or ""):
        return None
    z = _zeilen("SELECT * FROM bilder WHERE id = ?", (bild_id,))
    return z[0] if z else None


def pfad(bild_id: str) -> Path | None:
    e = eintrag(bild_id)
    if not e:
        return None
    p = _ordner() / f"{bild_id}.{ENDUNG[e['mime']]}"
    return p if p.exists() else None


def entfernen(bild_id: str) -> bool:
    """Ein Bild ENDGÜLTIG entfernen: Datei und Eintrag. Nur auf Eikes Wunsch in der App (`DELETE /api/bilder/<id>`) —
    das Modell hat dafür kein Werkzeug. Der Dateiname entsteht nur aus einer geprüften Kennung (32 Hex) und der
    bekannten Endung, nie aus Eingaben; ein Pfad außerhalb des Bilderordners kommt so nicht in Frage."""
    e = eintrag(bild_id)
    if not e:
        return False
    datei = _ordner() / f"{bild_id}.{ENDUNG[e['mime']]}"
    _schreiben("DELETE FROM bilder WHERE id = ?", (bild_id,))
    try:
        datei.unlink(missing_ok=True)
    except OSError:
        pass                                   # der Eintrag ist weg; eine Datei, die nicht zu löschen ist, bleibt ohne Verweis
    return True


def letztes(gespraech: int | None) -> str | None:
    if gespraech is None:
        return None
    z = _zeilen("SELECT id FROM bilder WHERE gespraech = ? ORDER BY zeit DESC, rowid DESC LIMIT 1", (gespraech,))
    return z[0]["id"] if z else None


def liste(limit: int = 200, person: str | None = None) -> list[dict]:
    """`person=None`: alle (Eike sieht alles); mit Person nur ihre eigenen Bilder."""
    if person is None:
        return _zeilen("SELECT id, gespraech, zeit, name, beschreibung, frage, quelle, notizen, person FROM bilder "
                       "ORDER BY zeit DESC, rowid DESC LIMIT ?", (int(limit),))
    return _zeilen("SELECT id, gespraech, zeit, name, beschreibung, frage, quelle, notizen, person FROM bilder "
                   "WHERE person = ? ORDER BY zeit DESC, rowid DESC LIMIT ?", (person, int(limit)))


def fuer_gespraech(gespraech: int) -> list[dict]:
    return _zeilen("SELECT id, name, beschreibung, quelle, notizen FROM bilder WHERE gespraech = ? ORDER BY zeit, rowid", (gespraech,))


def mit_namen(name: str) -> list[dict]:
    if not (name or "").strip():
        return []
    return _zeilen("SELECT id, zeit, name, beschreibung FROM bilder WHERE LOWER(name) = LOWER(?) "
                   "ORDER BY zeit DESC", (name.strip(),))


_FUELLWOERTER = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "und", "von", "mit", "aus", "auf", "ich",
                 "mir", "mich", "dir", "ist", "sind", "bild", "bilder", "foto", "zeig", "zeige", "noch", "mal", "vom", "zum", "zur"}


def _woerter(begriff: str) -> list[str]:
    """Die tragenden Wörter einer Suche; ein Genitiv-s („Kontakt Bs“) fällt ab."""
    ergebnis = []
    for w in re.findall(r"\w+", (begriff or "").lower()):
        if len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        if len(w) >= 3 and w not in _FUELLWOERTER:
            ergebnis.append(w)
    return ergebnis


def suchen(begriff: str, limit: int = 5) -> list[dict]:
    """Bilder nach Name, Beschreibung oder ursprünglicher Frage — Namenstreffer zuerst. Ein % oder _ ist kein Joker.

    Passt der ganze Begriff nirgends, zählen einzelne Wörter („Kontakt Bs Hund“ → „Mila (Hund Kontakt H)“): Wer die meisten
    Wörter trifft, kommt zuerst."""
    t = (begriff or "").strip().lower()[:100]
    if not t:
        return []
    muster = "%" + re.sub(r"([\\%_])", r"\\\1", t) + "%"
    ganz = _zeilen(
        "SELECT id, zeit, name, beschreibung FROM bilder "
        "WHERE LOWER(name) LIKE ? ESCAPE '\\' OR LOWER(beschreibung) LIKE ? ESCAPE '\\' OR LOWER(frage) LIKE ? ESCAPE '\\' "
        "ORDER BY (LOWER(COALESCE(name, '')) LIKE ? ESCAPE '\\') DESC, zeit DESC, rowid DESC LIMIT ?",
        (muster, muster, muster, muster, int(limit)))
    if ganz:
        return ganz
    woerter = _woerter(t)
    if not woerter:
        return []
    treffer = []
    for z in _zeilen("SELECT id, zeit, name, beschreibung, frage FROM bilder ORDER BY zeit DESC, rowid DESC LIMIT 500"):
        heu = " ".join(str(z.get(f) or "") for f in ("name", "beschreibung", "frage")).lower()
        punkte = sum(1 for w in woerter if w in heu)
        if punkte:
            treffer.append((punkte, z))
    treffer.sort(key=lambda x: -x[0])                      # stabil: bei gleichen Punkten bleibt „neueste zuerst“
    return [{k: z[k] for k in ("id", "zeit", "name", "beschreibung")} for _, z in treffer[:int(limit)]]
