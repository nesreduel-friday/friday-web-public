"""Benachrichtigungen — eine Stelle, an der entschieden wird, was aufs Handy darf.

Vorher schickte jeder Teil von Friday selbst (`push.senden`): das Briefing, die
Erinnerungen, der Garantie-Sammler, das Studio. Wer nur das Morgenbriefing
abstellen wollte, musste den Konnektor abschalten — oder Benachrichtigungen ganz.
Seit dem 20.09.2026 geht alles hier durch, und Eike kann jede **Art** einzeln
schalten (Einstellungen → Benachrichtigungen). Friday kann es auch im Gespräch.

Zwei Regeln, beide im Server und nicht im Modell:

- **Eine unbekannte Art geht nicht hinaus.** Ein Tippfehler im Aufruf würde sonst
  still an allen Schaltern vorbeigehen. Ein Test hält `ARTEN` und die Aufrufe
  im Gleichlauf.
- **Ruhezeit.** Zwischen `ab` und `bis` bleibt es still — außer bei Nachrichten,
  deren Zeitpunkt Eike selbst gesetzt hat (`dringend=True`, bislang nur die
  Erinnerungen und eine Abfahrt, die gleich weg ist). Wer sich um 23:40 wecken
  lässt, will um 23:40 geweckt werden.

`push.py` bleibt, was es war: die Technik (VAPID, Verschlüsselung, tote Geräte
austragen). Hier steht nur, OB etwas geschickt wird.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, time as tageszeit

from kern import config
from kern.dienste import push

log = logging.getLogger("friday.benachrichtigungen")

DATEI = "benachrichtigungen.json"

# Reihenfolge = Reihenfolge auf der Einstellungen-Seite.
ARTEN: list[dict] = [
    {"id": "briefing", "name": "Morgenbriefing",
     "beschreibung": "Der Tagesüberblick am Morgen.", "standard": True},
    {"id": "abendblick", "name": "Abendblick",
     "beschreibung": "Was morgen ansteht, am Abend davor.", "standard": True},
    {"id": "erinnerungen", "name": "Erinnerungen",
     "beschreibung": "Was du Friday zum Erinnern gesagt hast. Kommt auch in der Ruhezeit.",
     "standard": True},
    {"id": "fahrt", "name": "Abfahrten im Blick",
     "beschreibung": "Wenn ein Bus oder eine Bahn, auf die du wartest, Verspätung hat oder ausfällt.",
     "standard": True},
    {"id": "verspaetungen", "name": "Verspätungen für die Garantie",
     "beschreibung": "Fahrten über 20 Minuten Verspätung in deiner Gegend, für die ÜSTRA-Garantie.",
     "standard": True},
    {"id": "verspaetungen_s5", "name": "Nur S5: Verspätungen",
     "beschreibung": "Nur die S5 über 20 Minuten Verspätung — auch wenn „Verspätungen für die Garantie“ aus ist.",
     "standard": False},
    {"id": "garantie", "name": "Garantie-Anträge",
     "beschreibung": "Antrag fertig, Bescheid da, Ticket abgelaufen.", "standard": True},
    {"id": "studio", "name": "Studio",
     "beschreibung": "Wenn ein Video fertig ist — oder nicht fertig geworden ist.", "standard": True},
    {"id": "server", "name": "Server hphome",
     "beschreibung": "Wenn die Platte fast voll ist oder ein Dienst auf hphome ausfällt.", "standard": True},
    {"id": "friday", "name": "Friday meldet sich",
     "beschreibung": "Was Friday dir im Gespräch zu sagen versprochen hat.", "standard": True},
    {"id": "kontakte", "name": "Neue Kontakte",
     "beschreibung": "Wenn jemand Friday schreibt, der noch nicht unter Kontakte angelegt ist.", "standard": True},
    {"id": "kontakt_antwort", "name": "Antworten an Friday",
     "beschreibung": "Wenn eine Kontakt-Person auf Snapchat antwortet — mit dem Inhalt.", "standard": True},
    {"id": "nachdenken", "name": "Fridays Gedanken",
     "beschreibung": "Wenn Friday beim eigenen Nachdenken etwas Wichtiges findet, z. B. einen Terminkonflikt.",
     "standard": True},
    {"id": "pruefung", "name": "Prüfungen",
     "beschreibung": "Wenn eine Machbarkeitsprüfung (Frage an den lesenden Helfer) fertig oder gescheitert ist.", "standard": True},
    {"id": "werkbank", "name": "Verbesserungen an Friday",
     "beschreibung": "Wenn eine Verbesserung an Friday fertig ist, fehlgeschlagen ist oder immer wieder dieselbe Störung auftritt.",
     "standard": True},
    {"id": "fragen", "name": "Fragen von Friday",
     "beschreibung": "Wenn Friday dich etwas fragt, wovon ihr nächster Schritt abhängt — mit Antwortvorschlägen zum Antippen.",
     "standard": True},
]
KENNT = {a["id"] for a in ARTEN}

# Diese Meldungen schreibt Friday zusätzlich in den Chat (Chat-Strang, 28.10.2026): „Ich melde mich“ ist damit wahr. Der Chat kennt keine
# Ruhezeit; ausgeschaltete Arten bleiben auch dort aus. Inhalte Dritter (`FREMDE_MELDUNGEN`) stehen in der Anzeige, im Modellkontext nur
# der Titel.
SPIEGEL_ARTEN = frozenset({"kontakte", "kontakt_antwort", "werkbank", "studio", "garantie", "friday", "pruefung"})
FREMDE_MELDUNGEN = frozenset({"kontakt_antwort", "kontakte"})
KONTEXT_MELDUNG_MAX = 300
CHAT_MELDUNG_MAX = 500

RUHE_AB = "22:30"
RUHE_BIS = "06:30"

_sperre = threading.Lock()


class UnbekannteArt(ValueError):
    """Eine Art, die es nicht gibt — lieber laut scheitern als still senden."""


def _pfad():
    return config.daten_ordner() / DATEI


def _lesen() -> dict:
    try:
        d = json.loads(_pfad().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _schreiben(daten: dict) -> None:
    ziel = _pfad()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    os.replace(neu, ziel)


def art(kennung: str) -> dict:
    for a in ARTEN:
        if a["id"] == kennung:
            return a
    raise UnbekannteArt(f"Die Benachrichtigungsart „{kennung}“ gibt es nicht.")


def an(kennung: str) -> bool:
    """Ist diese Art eingeschaltet? Standard steht in `ARTEN` — nicht einfach True."""
    vorgabe = art(kennung)["standard"]
    wert = (_lesen().get("arten") or {}).get(kennung)
    return bool(vorgabe) if wert is None else wert is True


def stellen(kennung: str, ein: bool) -> bool:
    art(kennung)                                   # unbekannt: sofort Fehler
    with _sperre:
        d = _lesen()
        d.setdefault("arten", {})[kennung] = bool(ein)
        _schreiben(d)
    return bool(ein)


def _uhr(text: str, ersatz: str) -> tageszeit:
    try:
        stunde, minute = (int(t) for t in str(text).split(":")[:2])
        return tageszeit(stunde, minute)
    except (TypeError, ValueError):
        stunde, minute = (int(t) for t in ersatz.split(":"))
        return tageszeit(stunde, minute)


def ruhe() -> dict:
    d = _lesen().get("ruhe") or {}
    return {"an": d.get("an") is True,
            "ab": f"{_uhr(d.get('ab'), RUHE_AB):%H:%M}",
            "bis": f"{_uhr(d.get('bis'), RUHE_BIS):%H:%M}"}


def ruhe_stellen(ein: bool | None = None, ab: str | None = None, bis: str | None = None) -> dict:
    jetzt = ruhe()
    with _sperre:
        d = _lesen()
        r = d.setdefault("ruhe", {})
        if ein is not None:
            r["an"] = bool(ein)
        if ab is not None:
            r["ab"] = f"{_uhr(ab, jetzt['ab']):%H:%M}"
        if bis is not None:
            r["bis"] = f"{_uhr(bis, jetzt['bis']):%H:%M}"
        _schreiben(d)
    return ruhe()


def still(jetzt: datetime | None = None) -> bool:
    """Ist gerade Ruhezeit? Über Mitternacht hinweg richtig: 22:30–06:30 ist
    EIN Zeitraum, nicht zwei — mit `ab <= h < bis` wäre er nachts leer."""
    r = ruhe()
    if not r["an"]:
        return False
    h = (jetzt or config.jetzt()).time()
    ab, bis = _uhr(r["ab"], RUHE_AB), _uhr(r["bis"], RUHE_BIS)
    if ab == bis:
        return False
    return (h >= ab or h < bis) if ab > bis else (ab <= h < bis)


def uebersicht() -> dict:
    return {"arten": [{**a, "an": an(a["id"])} for a in ARTEN],
            "ruhe": ruhe(), "geraete": len(push.geraete())}


def _in_den_chat(kennung: str, titel: str, text: str, chat_text: str | None, chat_kontext: str | None) -> None:
    from kern.chatstrang import speicher as chatstrang
    anzeige = (chat_text or "").strip() or (f"{titel}: {text}" if titel and titel not in text else text)
    if chat_kontext is not None:
        kontext = chat_kontext
    elif kennung in FREMDE_MELDUNGEN:
        kontext = f"[Meldung an Eike: {titel}]"
    else:
        kontext = anzeige[:KONTEXT_MELDUNG_MAX]
    if len(anzeige) > CHAT_MELDUNG_MAX:                          # Meldungen im Chat bleiben kurz (Eike, 29.10.2026); der Rest steht in der Übersicht
        anzeige = anzeige[:CHAT_MELDUNG_MAX].rstrip() + " …"
    chatstrang.standard().anhaengen("friday", anzeige, art="meldung", quelle="meldung",
                                    kontext_text=kontext[:KONTEXT_MELDUNG_MAX])


def senden(kennung: str, titel: str, text: str, url: str = "/", tag: str = "",
           dringend: bool = False, chat_text: str | None = None, chat_kontext: str | None = None) -> dict:
    """Der einzige Weg aufs Handy. Gibt `unterdrueckt` zurück, wenn nichts ging —
    das steht im Protokoll und lässt sich nachvollziehen, statt still zu verschwinden."""
    # Eine unbekannte Art wirft hier — `an()` schlägt die Kennung in `ARTEN` nach.
    if not an(kennung):
        log.info("Benachrichtigung „%s“ unterdrückt: Art ist aus", kennung)
        return {"gesendet": 0, "geraete": 0, "abgemeldet": 0, "unterdrueckt": "art_aus"}
    # Auch bei Ruhezeit auf die Übersicht: Eike soll es beim nächsten Öffnen sehen.
    # Eine Störung dort darf den Push nie aufhalten.
    try:
        from kern.dashboard import meldungen
        meldungen.ablegen(kennung, titel, text, url, tag)
    except Exception:                                  # noqa: BLE001
        log.exception("Meldung für die Übersicht nicht abgelegt")
    if kennung in SPIEGEL_ARTEN:
        try:
            _in_den_chat(kennung, titel, text, chat_text, chat_kontext)
        except Exception:                              # noqa: BLE001
            log.exception("Meldung nicht in den Chat geschrieben")
    if not dringend and still():
        log.info("Benachrichtigung „%s“ unterdrückt: Ruhezeit", kennung)
        return {"gesendet": 0, "geraete": 0, "abgemeldet": 0, "unterdrueckt": "ruhezeit"}
    ergebnis = push.senden(titel, text, url, tag or kennung)
    return {**ergebnis, "unterdrueckt": ""}
