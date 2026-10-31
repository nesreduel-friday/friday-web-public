"""Flammen: Friday hält mit Personen, bei denen der Hebel „flammen“ an ist, einen täglichen Snap (14.10.2026, Spec
2026-10-14-kontakte-module-design.md, Abschnitt 4).

Der SERVER entscheidet (nicht das Modell): ein Auftrag je Person und Kalendertag, nur wenn der letzte von Friday gesendete Snap
an sie ≥ 20 h zurückliegt, im Fenster 07:00–21:30 (Ruhezeit 22:30–06:30), je Person und Tag um einen festen Versatz gestreut.
Ob die Flamme besteht, liest Friday NICHT aus Snapchat — sie führt eigenes Protokoll. Schalter `flammen` in
`data/snapchat/einstellungen.json`: aus (Standard) · trocken (nur Probe-Protokoll, nichts senden) · an.

Meldet der Helfer einen Empfänger-/Namensfehler (der Snapchat-Name steht nicht in der Send-to-Liste, ist mehrdeutig oder
lässt sich nicht auswählen), wird die Flamme dieser Person angehalten, bis Eike den Namen in Kontakte korrigiert oder
die Person pausiert und wieder aktiviert — die Meldung nennt den Grund des Helfers. Ein täglicher Neuversuch am selben
falschen Namen bringt nichts (30.10.2026)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from datetime import date, datetime, time, timedelta

from kern import config, konnektoren
from kern.dienste import benachrichtigungen, snapbild, snapchat
from kern.personen import module, register
from kern.werkbank import stoerung

log = logging.getLogger("friday.flammen")

DATEI = "flammen.json"
MODI = ("aus", "trocken", "an")
FENSTER_AB = time(7, 0)
FENSTER_BIS = time(21, 30)
ABSTAND = timedelta(hours=20)
VERSATZ_MAX_MIN = 120
FEHLER_BIS_PAUSE = 3
RESERVIERT = "reserviert"                       # Platzhalter, solange das Bild gesucht wird (kein zweiter Snap in der Zeit)
RESERVIERUNG_MAX = timedelta(minutes=15)        # bleibt eine Reservierung hängen (Neustart), wird sie wieder frei
OHNE_ERGEBNIS_MAX = timedelta(hours=6)          # meldet der Helfer so lange nichts, gilt der Auftrag als verloren
ERGEBNIS_OK = ("gesendet", "unbestaetigt")      # „unbestätigt“: Senden gedrückt, nicht gesehen — der Snap ist meist draußen
_sperre = threading.RLock()                     # Lauf und Werkzeug snap_wunsch schreiben dieselbe Datei
BILDER_MERKEN = 60
RETRY_ABSTAND = timedelta(minutes=10)

# Gründe des Helfers, die sich am selben Snapchat-Namen nicht beheben lassen: Er hat den Namen nach allen Versuchen
# nicht (eindeutig) in Snapchats Send-to-Liste gefunden, die Auswahl war falsch, oder Person und Empfänger passen laut
# Kontaktliste nicht zusammen. Dann ist ein neuer Auftrag am nächsten Tag nur dieselbe Wiederholung (30.10.2026:
# Flammen-Snap an Kontakt G kam nie raus, weil „Kontakt G“ in Snapchat nur als Telefonkontakt steht).
# Erst ein anderer Name — oder ein anderer Pausiert-Zustand — macht einen Versuch wieder sinnvoll.
DAUERHAFT = (
    "steht nicht in der send-to-liste",
    "in der send-to-liste mehrdeutig",
    "falscher empfänger ausgewählt",
    "steht nicht in der kontaktliste",
    "person und empfänger",
)


def _dauerhaft(grund) -> bool:
    """Ist das ein Empfänger-/Namensfehler, der ohne Änderung an der Person wieder genauso scheitern wird?"""
    text = " ".join(str(grund or "").split()).casefold()
    return any(m in text for m in DAUERHAFT)


def gesperrt(z: dict, name: str) -> bool:
    """Ist die Flamme dieser Person nach einem dauerhaften Empfängerfehler angehalten? Sie läuft erst wieder, wenn
    Eike den Snapchat-Namen ändert — oder die Person pausiert und wieder aktiviert (Pausieren löscht die Sperre in
    `einmal`, denn `pausiert` allein würde beim Weiteraktivieren denselben Wert haben und die Sperre bliebe)."""
    g = z.get("gesperrt")
    return isinstance(g, dict) and g.get("name") == name


def _neu() -> dict:
    return {"auftrag_zeit": None, "retry_nach": None, "letzter": None, "letzter_tag": None, "auftrag": None, "fehler": 0, "pause_bis": None, "bilder": [], "probe_tag": None}


def versatz(pid: str, tag: date) -> int:
    """Minuten nach 07:00, ab denen diese Person heute fällig ist — stabil über Neustarts, je Person und Tag anders."""
    h = hashlib.sha256(f"{pid}|{tag.isoformat()}".encode()).hexdigest()
    return int(h, 16) % (VERSATZ_MAX_MIN + 1)


def _zeit(iso) -> datetime | None:
    try:
        d = datetime.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None
    return d if d is not None and d.tzinfo else None        # ohne Zeitzone nicht vergleichbar → wie nicht vorhanden


def faellig(z: dict, pid: str, jetzt: datetime) -> bool:
    if z.get("auftrag"):
        return False
    pause = _zeit(z.get("pause_bis"))
    if pause and jetzt < pause:
        return False
    start = datetime.combine(jetzt.date(), FENSTER_AB, tzinfo=jetzt.tzinfo) + timedelta(minutes=versatz(pid, jetzt.date()))
    if not (start <= jetzt and jetzt.time() < FENSTER_BIS):
        return False
    if z.get("letzter_tag") == jetzt.date().isoformat():
        return False
    nach = _zeit(z.get("retry_nach"))
    if nach and jetzt < nach:
        return False
    letzter = _zeit(z.get("letzter"))
    return not (letzter and jetzt - letzter < ABSTAND)


EIKE = "eike"          # Schlüssel von Eikes eigener Flamme im Zustand (er steht nicht im Personen-Register)


def _einstellungen_pfad():
    return config.daten_ordner() / "snapchat" / "einstellungen.json"


def eike_an() -> bool:
    """Hebel „Flamme mit Eike“ (Schlüssel `flammen_eike` in `einstellungen.json`; nur echtes true zählt)."""
    try:
        e = json.loads(_einstellungen_pfad().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(e, dict) and e.get("flammen_eike") is True


def eike_setzen(an: bool) -> None:
    """Schreibt nur diesen Schlüssel; alle anderen Einstellungen (Ticket-Annahme, Heimat-Modus …) bleiben unberührt."""
    with _sperre:
        pfad = _einstellungen_pfad()
        try:
            e = json.loads(pfad.read_text(encoding="utf-8"))
            e = e if isinstance(e, dict) else {}
        except (OSError, ValueError):
            e = {}
        e["flammen_eike"] = bool(an)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        neu = pfad.with_suffix(".neu")
        fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(e, f, ensure_ascii=False, indent=1)
        os.replace(neu, pfad)


def modus_setzen(wert: str) -> None:
    """Schalter `flammen` (aus · trocken · an); schreibt nur diesen Schlüssel."""
    if wert not in MODI:
        raise ValueError("Modus muss aus, trocken oder an sein")
    with _sperre:
        pfad = _einstellungen_pfad()
        try:
            e = json.loads(pfad.read_text(encoding="utf-8"))
            e = e if isinstance(e, dict) else {}
        except (OSError, ValueError):
            e = {}
        e["flammen"] = wert
        pfad.parent.mkdir(parents=True, exist_ok=True)
        neu = pfad.with_suffix(".neu")
        fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(e, f, ensure_ascii=False, indent=1)
        os.replace(neu, pfad)


def eike_kontext() -> str:
    """Prompt-Hinweis für Eikes Snapchat-Chat — nur, wenn die Flamme wirklich läuft (Hebel an UND Schalter „an“)."""
    if not (eike_an() and modus() == "an"):
        return ""
    return ("\n\n[FLAMME MIT EIKE] Du und Eike haltet eine Snapchat-Flamme: Du schickst ihm täglich von dir aus einen Snap, und er "
            "schickt dir täglich einen. Erwähne sie hin und wieder beiläufig und freundlich, ohne zu drängen. Wie lang sie ist, "
            "siehst du nicht — nenne nie eine Zahl.")


def _pfad():
    return config.daten_ordner() / DATEI


def zustand_lesen() -> dict:
    try:
        d = json.loads(_pfad().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"personen": {}}
    return d if isinstance(d, dict) and isinstance(d.get("personen"), dict) else {"personen": {}}


def zustand_schreiben(d: dict) -> None:
    ziel = _pfad()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(neu, ziel)


def modus() -> str:
    try:
        e = json.loads((config.daten_ordner() / "snapchat" / "einstellungen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "aus"
    wert = e.get("flammen") if isinstance(e, dict) else None
    return wert if wert in MODI else "aus"


def zustand_aendern(fn):
    """Frisch lesen, ändern, schreiben — unter einer Sperre. Nichts hält den Zustand über ein `await` hinweg."""
    with _sperre:
        zust = zustand_lesen()
        ergebnis = fn(zust)
        zustand_schreiben(zust)
        return ergebnis


def merken_auftrag(pid: str, auftrag_id: str, jetzt: datetime | None = None) -> None:
    """Ein Wunsch-Snap zählt bei Personen MIT Flamme als Fridays Snap an sie: Sein Ergebnis setzt `letzter` wie bei einer Flamme.
    Läuft schon ein Auftrag für sie, bleibt der alte (sein Ergebnis klärt den Fall)."""
    if "flammen" not in module.wirksam(register.holen(pid) or {}):
        return
    jetzt = jetzt or config.jetzt()

    def tun(zust):
        z = zust["personen"].setdefault(pid, _neu())
        if not z.get("auftrag"):
            z.update(auftrag=auftrag_id, auftrag_zeit=jetzt.isoformat(timespec="seconds"))
    zustand_aendern(tun)


def _snapchat_name(p: dict) -> str:
    return ((p.get("kanaele") or {}).get("snapchat") or "").strip()


def freigegeben() -> bool:
    """Snapchat muss auf der Freigaben-Seite an sein UND das Schreibrecht haben — wie bei jedem anderen Senden."""
    return konnektoren.benutzbar("snapchat") and konnektoren.schreiben_erlaubt("snapchat")


def _stoerung(aktion, *args) -> None:
    """Die Werkbank-Ablage darf den Flammen-Lauf nie abbrechen."""
    try:
        aktion(*args)
    except Exception:
        log.warning("Störungs-Erkennung fehlgeschlagen", exc_info=True)


def abrechnen(zust: dict, jetzt: datetime, melden: bool = False) -> None:
    """Offene Aufträge auflösen: gesendet (auch unbestätigt) → merken; verworfen/aufgegeben → Fehler zählen, nach 3 Pause bis
    morgen. Ein Empfänger-/Namensfehler (siehe `_dauerhaft`) pausiert sofort, bis Eike den Snapchat-Namen ändert oder die
    Person pausiert/weiter aktiviert (siehe `einmal`) — sonst wiederholt sich derselbe Fehlschlag jeden Tag. Jeder Fehlschlag geht (nur im Modus „an“ und nur für
    Flammenpersonen) mit dem Grund des Helfers an die Störungs-Erkennung der Werkbank, jeder Erfolg setzt sie zurück. Ohne
    Ergebnis nach 6 h gilt der Auftrag als verloren: kein Neuversuch am selben Tag (sonst droht ein Doppel)."""
    for pid, z in zust["personen"].items():
        auftrag = z.get("auftrag")
        if not auftrag:
            continue
        seit = _zeit(z.get("auftrag_zeit"))
        if auftrag == RESERVIERT:
            if seit is None or jetzt - seit > RESERVIERUNG_MAX:
                z.update(auftrag=None, auftrag_zeit=None)
            continue
        e = snapchat.ergebnis_fuer(auftrag)
        if not e:
            if seit is None:
                z["auftrag_zeit"] = jetzt.isoformat(timespec="seconds")       # Bestand ohne Zeit: ab jetzt zählen
            elif jetzt - seit > OHNE_ERGEBNIS_MAX:
                z.update(auftrag=None, auftrag_zeit=None, letzter_tag=jetzt.date().isoformat())
            continue
        z.update(auftrag=None, auftrag_zeit=None)
        p = register.holen(pid) or ({"name": "Eike"} if pid == EIKE else {})
        grund = " ".join(str(e.get("grund") or "").split())
        gilt = melden and (eike_an() if pid == EIKE else "flammen" in module.wirksam(p))      # nur Modus „an“, nur Flammenpersonen
        if e.get("ergebnis") in ERGEBNIS_OK:
            t = jetzt
            if isinstance(e.get("zeit"), (int, float)):
                t = datetime.fromtimestamp(e["zeit"], jetzt.tzinfo)           # wann der Helfer gesendet hat, nicht wann wir es merken
            z.update(letzter=t.isoformat(timespec="seconds"), letzter_tag=t.date().isoformat(), fehler=0)
            z.pop("gesperrt", None)                                           # Eike hat den Namen korrigiert (oder der Fehler war vorbei)
            if gilt:
                _stoerung(stoerung.erledigt, f"flamme:{pid}")
            continue
        beleg = f"Flammen-Snap ging nicht raus (Ergebnis: {e.get('ergebnis') or 'unbekannt'}"
        beleg += f"; Grund: {grund[:200]})." if grund else ")."
        if gilt:
            _stoerung(stoerung.melden, f"Flammen-Snap an {p.get('name', pid)}", f"flamme:{pid}", beleg, jetzt)
        if _dauerhaft(grund):
            # Der Helfer hat den Empfänger nach allen Versuchen nicht gefunden oder nicht auswählen können. Bis Eike
            # den Snapchat-Namen ändert (oder die Person pausiert/weiter aktiviert), würde jeder neue Auftrag genauso
            # scheitern. Ein Fehlversuch reicht: Der Helfer hat es innerhalb des Auftrags schon mehrfach probiert.
            # Maßgeblich ist der Name, den der Helfer wirklich versucht hat: Wurde die Person während des Auftrags
            # umbenannt, ist die Sperre mit dem neuen Namen schon erledigt und der nächste Lauf versucht es.
            name = str(e.get("empfaenger") or "").strip() \
                or (snapchat.eike_name() if pid == EIKE else _snapchat_name(p))
            pausiert = False if pid == EIKE else bool(p.get("pausiert"))
            z.update(fehler=0, pause_bis=None,
                     gesperrt={"name": name, "pausiert": pausiert, "grund": grund[:200],
                               "zeit": jetzt.isoformat(timespec="seconds")})
            if gilt:
                _stoerung(benachrichtigungen.senden, "kontakte", "Flamme pausiert",
                          f"Der Snap an {p.get('name', pid)} ging nicht raus: {grund[:160] or 'unbekannter Grund.'} "
                          "Ich pausiere die Flamme, bis du den Snapchat-Namen änderst oder die Freundschaft prüfst.",
                          "/#studio", f"flamme-{pid}")
            continue
        z["fehler"] = int(z.get("fehler") or 0) + 1
        if z["fehler"] >= FEHLER_BIS_PAUSE:
            morgen = datetime.combine(jetzt.date() + timedelta(days=1), time(0, 0), tzinfo=jetzt.tzinfo)
            z.update(fehler=0, pause_bis=morgen.isoformat(timespec="seconds"))
            if gilt:                                                  # Pause bis morgen: höchstens einmal je Tag und Person
                zuletzt = f" (zuletzt: {grund[:120]})" if grund else ""
                _stoerung(benachrichtigungen.senden, "kontakte", "Flamme klappt gerade nicht",
                          f"Der Snap an {p.get('name', pid)} ging dreimal nicht raus{zuletzt}. Ich pausiere die Flamme "
                          "bis morgen — schau bitte nach dem Handy.", "/#studio", f"flamme-{pid}")


def _probe(pid: str, name: str, jetzt: datetime) -> None:
    pfad = config.daten_ordner() / "snapchat" / "flammen_probe.jsonl"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps({"zeit": jetzt.isoformat(timespec="seconds"), "person": pid, "name": name, "wuerde": "snap"},
                           ensure_ascii=False) + "\n")


async def _standardbild(pid: str, name: str):
    from kern.dienste import spassbild
    z = zustand_lesen()["personen"].get(pid, {})
    return await spassbild.finden("", set(z.get("bilder") or []), hochkant=True)


def _freigeben(pid: str, jetzt: datetime) -> None:
    """Eine Reservierung wieder aufgeben (kein Bild, Fehler) und sie 10 Minuten aussetzen, damit eine Person, bei der es hakt,
    die anderen nicht aufhält."""
    def tun(zust):
        z = zust["personen"].get(pid)
        if z and z.get("auftrag") == RESERVIERT:
            z.update(auftrag=None, auftrag_zeit=None, retry_nach=(jetzt + RETRY_ABSTAND).isoformat(timespec="seconds"))
    zustand_aendern(tun)


async def einmal(jetzt: datetime | None = None, beschaffen=None) -> list[str]:
    """Ein Durchgang. Legt höchstens EINEN Auftrag (der Helfer steuert ein Handy) und gibt die Personen-Ids zurück.
    Drei Phasen, damit der Zustand nie über das Suchen des Bildes hinweg gehalten wird: (1) unter Sperre abrechnen und eine
    Person RESERVIEREN, (2) Bild holen, (3) unter Sperre den Auftrag eintragen."""
    schalter = modus()
    jetzt = jetzt or config.jetzt()
    beschaffen = beschaffen or _standardbild
    if schalter == "aus" and not any(z.get("auftrag") for z in zustand_lesen()["personen"].values()):
        return []

    def phase1(zust):
        abrechnen(zust, jetzt, melden=schalter == "an")
        if schalter == "aus" or not freigegeben() or snapchat.snap_wartet():
            return None
        kandidaten = ([{"id": EIKE, "name": "Eike"}] if eike_an() else []) + register.liste()     # Eike zuerst
        for p in kandidaten:
            pid = p["id"]
            if pid != EIKE:
                if p.get("pausiert"):
                    # Eike hat pausiert: Eine Empfänger-Sperre ist damit erledigt. Nach dem Weiteraktivieren darf
                    # es neu versuchen (etwa weil er die Freundschaft in Snapchat geprüft hat), ohne Umbenennen.
                    z = zust["personen"].get(pid)
                    if z is not None:
                        z.pop("gesperrt", None)
                    continue
                if "flammen" not in module.wirksam(p) or not _snapchat_name(p):
                    continue
            z = zust["personen"].setdefault(pid, _neu())
            # Nach einem Empfängerfehler erst wieder versuchen, wenn sich der Snapchat-Name geändert hat (oder die
            # Person pausiert/weiter aktiviert wurde, siehe oben) — sonst scheitert derselbe Auftrag jeden Tag neu.
            name = snapchat.eike_name() if pid == EIKE else _snapchat_name(p)
            if gesperrt(z, name):
                continue
            if not faellig(z, pid, jetzt):
                continue
            if schalter == "trocken":
                if z.get("probe_tag") != jetzt.date().isoformat():
                    _probe(pid, p["name"], jetzt)
                    z["probe_tag"] = jetzt.date().isoformat()
                continue
            z.update(auftrag=RESERVIERT, auftrag_zeit=jetzt.isoformat(timespec="seconds"))
            return pid, p["name"]
        return None

    gewaehlt = zustand_aendern(phase1)
    if not gewaehlt:
        return []
    pid, name = gewaehlt
    try:
        gefunden = await beschaffen(pid, name)
        if not gefunden or snapchat.snap_wartet() or not freigegeben():
            _freigeben(pid, jetzt)
            return []
        daten, url, _titel = gefunden
        pfad = snapbild.ablegen(snapbild.vorbereiten(daten, "", hochformat=True))
        auftrag = snapchat.auftrag_legen("snap", bild_pfad=pfad, person=None if pid == EIKE else pid)
    except Exception:  # noqa: BLE001 — ein Fehler bei EINER Person beendet nur diese
        log.exception("Flamme für %s nicht gelegt", pid)
        _freigeben(pid, jetzt)
        return []

    def eintragen(zust):
        z = zust["personen"].setdefault(pid, _neu())
        z.update(auftrag=auftrag, auftrag_zeit=jetzt.isoformat(timespec="seconds"))
        z["bilder"] = ([url] + list(z.get("bilder") or []))[:BILDER_MERKEN]
    zustand_aendern(eintragen)
    return [pid]


async def dauerlauf() -> None:
    """Alle 5 Minuten prüfen; ein Fehler stoppt den Lauf nie."""
    await asyncio.sleep(180)
    while True:
        try:
            await einmal()
        except Exception:  # noqa: BLE001
            log.exception("Flammen-Lauf gescheitert")
        await asyncio.sleep(300)
