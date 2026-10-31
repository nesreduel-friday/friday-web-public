"""Erinnerungen — Friday meldet sich von selbst.

„Erinner mich dran, die Wasserpest für Bio morgen mitzunehmen." Friday sucht
sich Zeitpunkt, Überschrift und Text selbst aus (siehe Persona) und legt hier
einen Eintrag ab. Ein Hintergrundlauf schickt ihn zur fälligen Zeit als
Benachrichtigung aufs Handy.

SQLite im WAL-Modus (`data/erinnerungen.db`), Zeiten in UTC. Nichts wird
gelöscht: eine abgesagte oder verschickte Erinnerung bleibt nachlesbar.

Zwei Auslöser: `zeit` (fester Zeitpunkt) und `ankunft` („erinnere mich, wenn
ich nach Hause komme“). Die Ankunft misst `ankuenfte_pruefen` am Haus aus
`FRIDAY_HEIMADRESSE`; die Position kommt aus derselben Quelle wie die
Live-Route (App, OwnTracks oder Home Assistant), das Handy wird dabei nie gefragt. Scharf wird
eine Ankunftserinnerung erst, wenn Eike zwischendurch weg war — wer beim
Anlegen schon zu Hause ist, bekommt sie also nicht sofort.

Kommt eine Benachrichtigung nicht raus (kein Gerät angemeldet, Push-Dienst
weg), wird es MAX_VERSUCHE-mal erneut probiert und dann aufgegeben — lieber
ehrlich stehen lassen als still verschwinden.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from kern import config

log = logging.getLogger("friday.erinnerungen")

TAKT_S = 30
MAX_VERSUCHE = 3
MAX_VORAUS_TAGE = 730
BESTAETIGUNG_MIN = 30
KANAELE = ("push", "snapchat", "beides", "gespraech")
AUSLOESER = ("zeit", "ankunft")
ANKUNFT_WEG_M = 200     # weiter als das vom Haus weg heißt „unterwegs“ — erst dann ist die Ankunft scharf
VORLAUF_S = 60          # so früh darf sie höchstens zu spät kommen
PERSON_RUHE_AB = time(22, 30)
PERSON_RUHE_BIS = time(6, 30)
PERSON_START = time(7, 0)
MAX_OFFEN_JE_PERSON = 3
MAX_OFFEN_PERSONEN = 20
MAX_TAG_JE_PERSON = 5
SNAP_TEXT_MAX = 280

SCHEMA = """
CREATE TABLE IF NOT EXISTS erinnerungen (
    id        INTEGER PRIMARY KEY,
    titel     TEXT NOT NULL,
    text      TEXT NOT NULL DEFAULT '',
    faellig   TEXT NOT NULL,              -- UTC, ISO; bei ausloeser=ankunft nur der Anlagezeitpunkt
    status    TEXT NOT NULL DEFAULT 'offen',   -- offen | geschickt | abgesagt | gescheitert
    versuche  INTEGER NOT NULL DEFAULT 0,
    erstellt  TEXT NOT NULL,
    geschickt TEXT,
    ausloeser TEXT NOT NULL DEFAULT 'zeit',    -- zeit | ankunft
    ankunft_bereit INTEGER NOT NULL DEFAULT 0  -- bei ankunft: Eike war seit dem Anlegen schon weg
);
CREATE INDEX IF NOT EXISTS erinnerungen_faellig ON erinnerungen(status, faellig);
"""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jetzt_utc() -> datetime:
    return datetime.now(timezone.utc)


def person_zeit(wann: datetime) -> tuple[datetime, bool]:
    """Erinnerungen an andere gehen nie in der Nacht raus: Zwischen 22:30 und 06:30 (Berliner Zeit) wird auf 07:00 verschoben."""
    lokal = wann.astimezone(config.jetzt().tzinfo)
    h = lokal.time()
    if h >= PERSON_RUHE_AB:
        return (lokal + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0), True
    if h < PERSON_RUHE_BIS:
        return lokal.replace(hour=7, minute=0, second=0, microsecond=0), True
    return wann, False


class Ablage:
    def __init__(self, ordner: Path):
        ordner.mkdir(parents=True, exist_ok=True)
        self.pfad = ordner / "erinnerungen.db"
        self._sperre = threading.Lock()
        self._db = sqlite3.connect(self.pfad, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        spalten = {z[1] for z in self._db.execute("PRAGMA table_info(erinnerungen)")}
        for name, typ in (("kanal", "TEXT NOT NULL DEFAULT 'push'"), ("empfaenger", "TEXT"), ("auftrag_id", "TEXT"),
                          ("bestaetigt", "TEXT"), ("ausloeser", "TEXT NOT NULL DEFAULT 'zeit'"),
                          ("ankunft_bereit", "INTEGER NOT NULL DEFAULT 0")):
            if name not in spalten:
                self._db.execute(f"ALTER TABLE erinnerungen ADD COLUMN {name} {typ}")
        self._db.commit()

    def anlegen(self, titel: str, text: str, faellig: datetime, kanal: str = "push", empfaenger: str | None = None,
                ausloeser: str = "zeit") -> int:
        if kanal not in KANAELE:
            raise ValueError(f"Unbekannter Kanal: {kanal}")
        if ausloeser not in AUSLOESER:
            raise ValueError(f"Unbekannter Auslöser: {ausloeser}")
        with self._sperre, self._db:
            return self._db.execute(
                "INSERT INTO erinnerungen (titel, text, faellig, erstellt, kanal, empfaenger, ausloeser) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (titel.strip(), (text or "").strip(), faellig.astimezone(timezone.utc).isoformat(timespec="seconds"),
                 _jetzt(), kanal, empfaenger or None, ausloeser)).lastrowid

    def auftrag_setzen(self, eid: int, auftrag_id: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE erinnerungen SET auftrag_id = ? WHERE id = ?", (auftrag_id, eid))

    def bestaetigung_setzen(self, eid: int, ja: bool) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE erinnerungen SET bestaetigt = ? WHERE id = ?", ("ja" if ja else "nein", eid))

    def zu_pruefen(self) -> list[dict]:
        with self._sperre:
            return [dict(z) for z in self._db.execute(
                "SELECT * FROM erinnerungen WHERE status = 'geschickt' AND kanal IN ('snapchat', 'beides') "
                "AND auftrag_id IS NOT NULL AND bestaetigt IS NULL")]

    def offene_an(self, empfaenger: str) -> int:
        with self._sperre:
            return self._db.execute("SELECT COUNT(*) FROM erinnerungen WHERE status = 'offen' AND empfaenger = ?",
                                    (empfaenger,)).fetchone()[0]

    def offene_an_personen(self) -> int:
        with self._sperre:
            return self._db.execute("SELECT COUNT(*) FROM erinnerungen WHERE status = 'offen' AND empfaenger IS NOT NULL").fetchone()[0]

    def gesendet_heute(self, empfaenger: str) -> int:
        tag = config.jetzt().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat(timespec="seconds")
        with self._sperre:
            return self._db.execute("SELECT COUNT(*) FROM erinnerungen WHERE status = 'geschickt' AND empfaenger = ? AND geschickt >= ?",
                                    (empfaenger, tag)).fetchone()[0]

    def faellige(self) -> list[dict]:
        grenze = (datetime.now(timezone.utc) + timedelta(seconds=VORLAUF_S)).isoformat()
        with self._sperre:
            return [dict(z) for z in self._db.execute(
                "SELECT * FROM erinnerungen WHERE status = 'offen' AND ausloeser = 'zeit' AND faellig <= ? "
                "ORDER BY faellig", (grenze,))]

    def offene_ankuenfte(self) -> list[dict]:
        """Ankunftserinnerungen, die noch offen sind — nur die werden gegen den Standort geprüft."""
        with self._sperre:
            return [dict(z) for z in self._db.execute(
                "SELECT * FROM erinnerungen WHERE status = 'offen' AND ausloeser = 'ankunft' ORDER BY id")]

    def ankunft_bereit_markieren(self) -> None:
        """Eike war weg: alle offenen Ankunftserinnerungen werden scharf (feuern bei der Rückkehr)."""
        with self._sperre, self._db:
            self._db.execute("UPDATE erinnerungen SET ankunft_bereit = 1 "
                             "WHERE status = 'offen' AND ausloeser = 'ankunft'")

    def offene(self, anzahl: int = 20) -> list[dict]:
        with self._sperre:
            return [dict(z) for z in self._db.execute(
                "SELECT * FROM erinnerungen WHERE status = 'offen' ORDER BY faellig LIMIT ?", (int(anzahl),))]

    def eintrag(self, eid: int) -> dict | None:
        with self._sperre:
            z = self._db.execute("SELECT * FROM erinnerungen WHERE id = ?", (eid,)).fetchone()
        return dict(z) if z else None

    def markieren(self, eid: int, status: str) -> None:
        with self._sperre, self._db:
            self._db.execute("UPDATE erinnerungen SET status = ?, geschickt = ? WHERE id = ?",
                             (status, _jetzt(), eid))

    def fehlversuch(self, eid: int) -> int:
        with self._sperre, self._db:
            self._db.execute("UPDATE erinnerungen SET versuche = versuche + 1 WHERE id = ?", (eid,))
            return self._db.execute("SELECT versuche FROM erinnerungen WHERE id = ?", (eid,)).fetchone()[0]

    def absagen(self, eid: int) -> bool:
        with self._sperre, self._db:
            return self._db.execute("UPDATE erinnerungen SET status = 'abgesagt' WHERE id = ? AND status = 'offen'",
                                    (eid,)).rowcount > 0

    def stand(self) -> dict:
        with self._sperre:
            return {s: n for s, n in self._db.execute(
                "SELECT status, COUNT(*) FROM erinnerungen GROUP BY status")}


_instanzen: dict[str, Ablage] = {}


def standard() -> Ablage:
    ordner = config.daten_ordner()
    if str(ordner) not in _instanzen:
        _instanzen[str(ordner)] = Ablage(ordner)
    return _instanzen[str(ordner)]


async def _zustellen(e: dict) -> tuple[bool, str]:
    """(zugestellt, Grund). Push wie bisher; Snapchat über den geprüften Sendeweg; Gespräch als Anliegen."""
    from kern.dienste import benachrichtigungen, snapchat
    text = e["text"] or e["titel"]
    kanal, pid = e["kanal"], e["empfaenger"]
    if kanal == "gespraech":
        from kern.anliegen import fach
        r = fach.standard().anlegen(pid or "eike", "erinnerung", e["titel"], text, gueltig_stunden=168)
        return (True, "") if r["ok"] or "schon ein Anliegen" in r["grund"] else (False, r["grund"])
    if kanal in ("snapchat", "beides"):
        from kern.personen import register
        from kern.werkzeuge import sendepruefung
        from kern import konnektoren
        if pid:
            p = register.holen(pid)
            if not p:
                return False, "Die Person gibt es nicht mehr"
            if p.get("pausiert"):
                return False, f"{p['name']} ist pausiert"
            if not ((p.get("kanaele") or {}).get("snapchat") or "").strip():
                return False, f"{p['name']} hat keinen Snapchat-Namen mehr"
            if standard().gesendet_heute(pid) >= MAX_TAG_JE_PERSON:
                return False, f"Tageslimit für {p['name']} erreicht"
        if not (konnektoren.benutzbar("snapchat") and konnektoren.schreiben_erlaubt("snapchat")):
            return False, "Das Snapchat-Schreibrecht ist aus"
        if grund := sendepruefung.heikel(text):
            return False, f"Text gesperrt ({grund[:60]})"
        try:
            auftrag_id = await asyncio.to_thread(snapchat.auftrag_legen, "text", text=text, person=pid, einmalig=True)
        except snapchat.SnapchatFehler as exc:
            return False, str(exc)
        standard().auftrag_setzen(e["id"], auftrag_id)
    if kanal in ("push", "beides"):
        ergebnis = await asyncio.to_thread(benachrichtigungen.senden, "erinnerungen", e["titel"], e["text"], "/",
                                           f"erinnerung-{e['id']}", dringend=True)
        if not ergebnis.get("gesendet") and kanal == "push":
            return False, ergebnis.get("grund") or "kein Gerät"
    return True, ""


def _name_von(pid: str | None) -> str:
    if not pid:
        return "Eike"
    from kern.personen import register
    p = register.holen(pid)
    return p["name"] if p else pid


async def _rueckfall(e: dict, grund: str) -> None:
    """Eine Erinnerung ist nicht angekommen: Eike erfährt es per Push und als hohes Anliegen."""
    from kern.dienste import benachrichtigungen
    wer = _name_von(e["empfaenger"])
    satz = f"Die Erinnerung „{e['titel']}“ an {wer} ist nicht angekommen: {grund}."
    standard().bestaetigung_setzen(e["id"], False)
    standard().markieren(e["id"], "gescheitert")
    try:
        await asyncio.to_thread(benachrichtigungen.senden, "erinnerungen", "Erinnerung nicht angekommen", satz, "/",
                                f"erinnerung-{e['id']}-rueckfall", dringend=True)
    except Exception:                                  # noqa: BLE001
        log.exception("Rückfall-Push nicht gesendet")
    try:
        from kern.anliegen import fach
        fach.standard().anlegen("eike", "erinnerung", "Erinnerung nicht angekommen", satz[:300], "hoch")
    except Exception:                                  # noqa: BLE001
        log.exception("Rückfall-Anliegen nicht angelegt")


async def _zustellen_und_buchen(e: dict) -> bool:
    """Eine Erinnerung zustellen und den Stand fortschreiben — gemeinsam für Zeit- und Ankunftserinnerungen.

    Fehlversuche zählen; nach MAX_VERSUCHE wird aufgegeben (Eikes Push direkt, an andere mit Rückfall an Eike).
    """
    a = standard()
    ok, grund = await _zustellen(e)
    if ok:
        a.markieren(e["id"], "geschickt")
        log.info("Erinnerung %s geschickt (%s): %s", e["id"], e["kanal"], e["titel"])
        return True
    if a.fehlversuch(e["id"]) < MAX_VERSUCHE:
        return False
    if e["kanal"] == "push" and not e["empfaenger"]:
        a.markieren(e["id"], "gescheitert")
        log.warning("Erinnerung %s nicht zustellbar: %s", e["id"], grund)
        try:
            from kern.anliegen import fach
            fach.standard().anlegen("eike", "erinnerung", "Erinnerung nicht zugestellt",
                                    f"„{e['titel']}“ ist nicht aufs Handy gegangen (kein Gerät angemeldet?).", "hoch")
        except Exception:                              # noqa: BLE001
            log.exception("Anliegen zur Erinnerung nicht angelegt")
    else:
        await _rueckfall(e, grund)
    return False


async def faellige_schicken() -> int:
    """Fällige Erinnerungen (Auslöser `zeit`) zustellen — je Kanal; Fehlversuche zählen, am Ende Rückfall an Eike."""
    geschickt = 0
    for e in standard().faellige():
        if await _zustellen_und_buchen(e):
            geschickt += 1
    return geschickt


async def ankuenfte_pruefen() -> int:
    """Erinnerungen „bei Ankunft zu Hause“ auslösen — gemessen am Haus, ohne das Handy zu fragen.

    Eine Erinnerung feuert erst, wenn Eike seit dem Anlegen mindestens einmal weiter als
    `ANKUNFT_WEG_M` vom Haus weg war (`ankunft_bereit`). Wer beim Anlegen schon zu Hause ist,
    bekommt sie also nicht sofort. Positionen, die zu alt oder zu grob sind, lösen nichts aus —
    lieber warten als am falschen Ort melden.
    """
    a = standard()
    offen = a.offene_ankuenfte()
    if not offen:
        return 0
    from kern import konnektoren
    from kern.dienste import homeassistant
    if not (konnektoren.benutzbar("standort") or konnektoren.benutzbar("personenstandort")
            or konnektoren.benutzbar(homeassistant.KONNEKTOR)):
        return 0                                        # ohne Standortquelle gibt es keine Ankunft
    from kern.dienste import standort
    try:
        haus = await standort.heim()
    except Exception:                                   # noqa: BLE001
        haus = None
    if not haus:
        return 0                                        # ohne Hauspunkt kein „zu Hause“ (nur die Ortsmitte zählt nicht)
    try:
        kandidaten = await standort.eigene_kandidaten(frisch=False)
    except Exception:                                   # noqa: BLE001
        return 0
    brauchbar = [k for k in kandidaten
                 if k.get("genau") is not None and k["genau"] <= standort.HEIM_GENAU_M
                 and k["alter_s"] <= standort.AKTUELL_S]
    if not brauchbar:
        return 0
    k = min(brauchbar, key=lambda x: x["alter_s"])
    weg = standort.abstand_m((k["breite"], k["laenge"]), (haus["breite"], haus["laenge"]))
    if weg > ANKUNFT_WEG_M:
        a.ankunft_bereit_markieren()
        return 0
    if weg > standort.HEIM_NAH_M:
        return 0
    geschickt = 0
    for e in offen:
        if e.get("ankunft_bereit") and await _zustellen_und_buchen(e):
            geschickt += 1
    return geschickt


async def nachsehen() -> None:
    """Hat der Helfer die Snapchat-Erinnerungen wirklich gesendet? Ergebnis `gesendet` bestätigt, `verworfen`/`aufgegeben` oder
    {BESTAETIGUNG_MIN} Minuten Schweigen sind Rückfall."""
    from kern.dienste import snapchat
    a = standard()
    for e in a.zu_pruefen():
        r = snapchat.ergebnis_fuer(e["auftrag_id"])
        if r and r.get("ergebnis") == "gesendet":
            a.bestaetigung_setzen(e["id"], True)
        elif r and r.get("ergebnis") in ("verworfen", "aufgegeben"):
            await _rueckfall(e, str(r.get("grund") or r["ergebnis"])[:120])
        else:
            gelegt = datetime.fromisoformat(e["geschickt"]) if e.get("geschickt") else None
            if gelegt and _jetzt_utc() - gelegt > timedelta(minutes=BESTAETIGUNG_MIN):
                await _rueckfall(e, "Das Handy hat nichts gemeldet")


async def dauerlauf() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await faellige_schicken()
            await ankuenfte_pruefen()
            await nachsehen()
        except Exception:
            log.exception("Erinnerungen konnten nicht geschickt werden")
        await asyncio.sleep(TAKT_S)
