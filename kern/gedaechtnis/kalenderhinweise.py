"""Kalender-Hinweise für die Wissensseiten: Welche Termine gehören zu einem Eintrag, und welchen Rhythmus haben sie?

Eikes iCloud-Kalender kennt, was der Graph nicht kennt: „Montags 18:30 im Feuerwehrhaus, alle 14 Tage“, „JF mittwochs 18:00“,
die Therapie mit Ort und Takt. Die Wissensseite bekommt davon eine knappe, GEZÄHLTE Zusammenfassung (Wochentag, Uhrzeit, Ort,
typischer Abstand) statt roher Termine — das Rechnen übernimmt der Code, nicht das Modell. Nur lesen; ohne Freigabe des
Konnektors `icloud_kalender` oder bei einem Fehler kommt eine leere Liste.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from datetime import datetime, timedelta
from statistics import median

from kern import config, konnektoren

log = logging.getLogger("friday.kalenderhinweise")

TAGE_VORAUS = 75
CACHE_S = 1800
MAX_ZEILEN = 10
MAX_TERMINE = 18
WOCHENTAG = ["Montags", "Dienstags", "Mittwochs", "Donnerstags", "Freitags", "Samstags", "Sonntags"]
_cache: dict = {"zeit": 0.0, "termine": None}


def _lokal(iso: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return t.astimezone(config.jetzt().tzinfo)


def _passt(t: dict, begriffe: list[str]) -> bool:
    heuhaufen = f"{t.get('kalender', '')} {t.get('titel', '')} {t.get('ort', '')}".lower().replace("-", "")
    return any(b and b.replace("-", "") in heuhaufen for b in begriffe)


def _abstand(daten: list[datetime]) -> str:
    """Typischer Abstand zwischen Terminen („alle 14 Tage“), wenn er erkennbar ist (mindestens zwei Abstände)."""
    tage = [(b.date() - a.date()).days for a, b in zip(daten, daten[1:]) if (b.date() - a.date()).days > 0]
    if len(tage) < 2:
        return ""
    haeufigster, anzahl = Counter(tage).most_common(1)[0]
    if anzahl >= max(2, len(tage) // 2):
        return f"Abstand meist {haeufigster} Tage"
    return f"Abstand unregelmäßig (Median {int(median(tage))} Tage)"


def rhythmen(termine: list[dict], begriffe: list[str]) -> list[str]:
    """Zeilen für den Prompt. Gruppiert wird (1) je Titel (ab 3 Terminen) und (2) je Wochentag mit gleicher Uhrzeit (ab 3 Terminen,
    mit verschiedenen Titeln), danach kommen die nächsten Einzeltermine."""
    passend = []
    for t in termine:
        beginn = _lokal(t.get("beginn", ""))
        if beginn and _passt(t, begriffe):
            passend.append((beginn, t))
    passend.sort(key=lambda x: x[0])
    if not passend:
        return []

    def zeitraum(daten: list[datetime]) -> str:
        return f"{len(daten)} Termine zwischen {daten[0]:%d.%m.} und {daten[-1]:%d.%m.}"

    zeilen: list[str] = []
    je_titel: dict[str, list] = {}
    for beginn, t in passend:
        je_titel.setdefault(str(t["titel"]).strip(), []).append((beginn, t))
    for titel, eintraege in sorted(je_titel.items(), key=lambda kv: -len(kv[1])):
        if len(eintraege) < 3:
            continue
        daten = [b for b, _ in eintraege]
        tag = Counter(WOCHENTAG[b.weekday()] for b in daten).most_common(1)[0][0]
        uhr = Counter(f"{b:%H:%M}" for b in daten).most_common(1)[0][0]
        zeilen.append(f"„{titel}“: {zeitraum(daten)}, meist {tag.lower()} um {uhr} Uhr; {_abstand(daten)}".rstrip("; ").rstrip())

    je_zeit: dict[tuple[int, str], list] = {}
    for beginn, t in passend:
        je_zeit.setdefault((beginn.weekday(), f"{beginn:%H:%M}"), []).append((beginn, t))
    for (wochentag, uhr), eintraege in sorted(je_zeit.items(), key=lambda kv: -len(kv[1])):
        titel = list(dict.fromkeys(str(t["titel"]).strip() for _, t in eintraege))
        if len(eintraege) < 3 or len(titel) < 2:
            continue                                    # ein einziger Titel steht schon in der Titel-Gruppe oben
        orte = [t.get("ort") for _, t in eintraege if t.get("ort")]
        ort = f" ({Counter(orte).most_common(1)[0][0]})" if orte else ""
        daten = [b for b, _ in eintraege]
        zeilen.append(f"{WOCHENTAG[wochentag]} um {uhr} Uhr{ort}: {zeitraum(daten)} ({', '.join(titel[:5])}); {_abstand(daten)}".rstrip("; "))

    naechste = []
    for beginn, t in passend[:MAX_TERMINE]:
        ort = f", {t['ort']}" if t.get("ort") else ""
        naechste.append(f"{WOCHENTAG[beginn.weekday()][:-1]} {beginn:%d.%m. %H:%M} {t['titel']}{ort}")
    if naechste:
        zeilen.append("Nächste Termine: " + "; ".join(naechste))
    return zeilen[:MAX_ZEILEN]


async def _alle_termine() -> list[dict]:
    if _cache["termine"] is not None and time.monotonic() - _cache["zeit"] < CACHE_S:
        return _cache["termine"]
    from kern.dienste import icloud  # erst hier: der Kalender-Client wird nur gebraucht, wenn der Konnektor an ist
    zone = config.jetzt().tzinfo
    von = datetime.now(zone)
    termine = await asyncio.to_thread(icloud.termine, von, von + timedelta(days=TAGE_VORAUS))
    _cache.update(zeit=time.monotonic(), termine=termine)
    return termine


ABKUERZUNGEN = {"JF": "Jugendfeuerwehr", "FF": "Freiwillige Feuerwehr", "THL": "Technische Hilfeleistung"}


def mit_legende(zeilen: list[str]) -> list[str]:
    """Steht „JF“ oder „FF“ in den Zeilen, kommt eine Legende davor — das Modell kennt Eikes Kürzel sonst nicht."""
    text = " ".join(zeilen)
    benutzt = [f"{k} = {v}" for k, v in ABKUERZUNGEN.items() if f"„{k}“" in text or f" {k} " in f" {text} " or f"({k}" in text]
    return ([f"Abkürzungen im Kalender: {', '.join(benutzt)}"] + zeilen) if benutzt and zeilen else zeilen


async def hinweise(name: str, stichwoerter: list[str]) -> list[str]:
    """Kalender-Zeilen zu einem Eintrag (leer ohne Freigabe oder bei Fehlern)."""
    if not konnektoren.aktiv("icloud_kalender"):
        return []
    begriffe = [name.lower()] + [w for w in stichwoerter if len(w) >= 5]
    try:
        return mit_legende(rhythmen(await _alle_termine(), begriffe))
    except Exception as exc:  # noqa: BLE001 — der Kalender ist Beiwerk: ohne ihn steht die Seite trotzdem
        log.warning("Kalender-Hinweise nicht möglich: %s", type(exc).__name__)
        return []
