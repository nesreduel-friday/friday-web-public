"""Zeit als Auslöser einer Regel (15.10.2026): „schreib Kontakt F um 18 Uhr, dass ich nicht komme“.

Eine Zeitregel (`kanal == "zeit"`) hat statt eines Absenders eine Zeitangabe: einmalig (Datum + Uhrzeit), täglich oder an Wochentagen.
Der Planer (`schritt`, alle 30 s) fragt je aktiver Zeitregel die letzte fällige Zeit seit der Bestätigung ab. Innerhalb von 30 Minuten danach
läuft die Regel (derselbe Lauf wie bei Nachrichten, `lauf.zeit_ereignis`); war der Server länger aus, wird die Zeit NICHT nachgeholt, sondern
als „verpasst“ vermerkt. Jede Zeit löst höchstens einmal aus (UNIQUE regel+Schlüssel `zeit:<Zeit>`); eine einmalige Regel ist danach erledigt."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, time, timedelta

from kern import config
from kern.regeln import lauf, speicher

log = logging.getLogger("friday.regeln")

TAKT_S = 30
NACHHOLEN = timedelta(minutes=30)
TAGE = ("mo", "di", "mi", "do", "fr", "sa", "so")
TAGE_LANG = {"mo": "Montag", "di": "Dienstag", "mi": "Mittwoch", "do": "Donnerstag", "fr": "Freitag", "sa": "Samstag", "so": "Sonntag"}
_TAG_NAMEN = {**{t: t for t in TAGE}, **{n.casefold(): t for t, n in TAGE_LANG.items()},
              "mon": "mo", "die": "di", "mit": "mi", "don": "do", "fre": "fr", "sam": "sa", "son": "so"}
_UHR = re.compile(r"^(?P<k>.*?)\s*(?P<h>\d{1,2})[:.](?P<m>\d{2})\s*(?:uhr)?\s*$", re.I)
_EINMAL = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})$")


def _uhrzeit(h: str, m: str) -> str:
    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
        raise speicher.RegelFehler("Die Uhrzeit muss zwischen 00:00 und 23:59 liegen.")
    return f"{int(h):02d}:{int(m):02d}"


def wann_lesen(text, jetzt: datetime) -> dict:
    """Aus Eikes Zeitangabe das gespeicherte Zeitplan-Dict. Einmalig: `2026-10-15T18:00` (muss in der Zukunft liegen); täglich:
    `täglich 08:00`; Wochentage: `mo,mi 17:30` oder `Montag, Mittwoch 17:30`."""
    t = " ".join(str(text or "").split())
    if not t:
        raise speicher.RegelFehler("Wann soll die Regel auslösen? Nenne eine Uhrzeit (z. B. 2026-10-15T18:00, „täglich 08:00“ oder „mo,mi 17:30“).")
    m = _EINMAL.match(t)
    if m:
        try:
            wann = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]))
        except ValueError:
            raise speicher.RegelFehler("Dieses Datum gibt es nicht.") from None
        if wann.replace(tzinfo=jetzt.tzinfo) < jetzt - timedelta(minutes=1):
            raise speicher.RegelFehler("Dieser Zeitpunkt liegt schon in der Vergangenheit.")
        return {"typ": "einmalig", "zeit": wann.strftime("%Y-%m-%dT%H:%M")}
    m = _UHR.match(t)
    if not m:
        raise speicher.RegelFehler("Die Zeitangabe verstehe ich nicht. Nimm 2026-10-15T18:00, „täglich 08:00“ oder „mo,mi 17:30“.")
    uhr = _uhrzeit(m["h"], m["m"])
    kopf = m["k"].strip().casefold().replace("ä", "ae")
    if kopf in ("taeglich", "jeden tag", "tag"):
        return {"typ": "taeglich", "uhrzeit": uhr}
    tage = []
    for wort in re.split(r"[\s,;/&]+|\bund\b", kopf):
        if not wort:
            continue
        k = _TAG_NAMEN.get(wort) or _TAG_NAMEN.get(wort[:3])
        if k is None:
            raise speicher.RegelFehler(f"„{wort}“ ist kein Wochentag. Nimm mo, di, mi, do, fr, sa, so oder „täglich“.")
        if k not in tage:
            tage.append(k)
    if not tage:
        raise speicher.RegelFehler("Nenne „täglich“, einen Wochentag oder ein Datum.")
    return {"typ": "woechentlich", "tage": sorted(tage, key=TAGE.index), "uhrzeit": uhr}


def pruefen(w) -> dict:
    """Ein gespeichertes Zeitplan-Dict streng prüfen (z. B. beim Anlegen direkt über den Speicher)."""
    if not isinstance(w, dict):
        raise speicher.RegelFehler("Die Zeitangabe fehlt.")
    if w.get("typ") == "einmalig" and isinstance(w.get("zeit"), str) and _EINMAL.match(w["zeit"]):
        return {"typ": "einmalig", "zeit": w["zeit"]}
    uhr = w.get("uhrzeit")
    m = re.fullmatch(r"(\d{2}):(\d{2})", uhr) if isinstance(uhr, str) else None
    if m and w.get("typ") == "taeglich":
        return {"typ": "taeglich", "uhrzeit": _uhrzeit(m[1], m[2])}
    if m and w.get("typ") == "woechentlich" and isinstance(w.get("tage"), list) and w["tage"] and all(t in TAGE for t in w["tage"]):
        return {"typ": "woechentlich", "tage": sorted(set(w["tage"]), key=TAGE.index), "uhrzeit": _uhrzeit(m[1], m[2])}
    raise speicher.RegelFehler("Die Zeitangabe ist ungültig.")


def beschreibung(w: dict) -> str:
    if w.get("typ") == "einmalig":
        d = datetime.strptime(w["zeit"], "%Y-%m-%dT%H:%M")
        return f"einmal am {d:%d.%m.%Y} um {d:%H:%M} Uhr"
    if w.get("typ") == "taeglich":
        return f"jeden Tag um {w['uhrzeit']} Uhr"
    return f"jeden {', '.join(t.capitalize() for t in w.get('tage', []))} um {w.get('uhrzeit')} Uhr"


def ausloeser_text(regel: dict) -> str:
    """Eine Zeile „Wann/Wer löst aus“ für Vorlesen, Liste und Karte."""
    a = regel.get("ausloeser") or {}
    if regel.get("kanal") == "zeit":
        return beschreibung(a)
    return f"{'WhatsApp' if regel.get('kanal') == 'whatsapp' else 'Snapchat'}: {a.get('name') or '?'}"


def _um(tag: date, uhr: str, tzinfo) -> datetime:
    return datetime.combine(tag, time(int(uhr[:2]), int(uhr[3:])), tzinfo=tzinfo)


def letzte_faelligkeit(w: dict, jetzt: datetime, seit: datetime) -> datetime | None:
    """Die späteste Auslösezeit t mit seit < t ≤ jetzt (oder None)."""
    tz = jetzt.tzinfo
    if w["typ"] == "einmalig":
        t = datetime.fromisoformat(w["zeit"]).replace(tzinfo=tz)
        return t if seit < t <= jetzt else None
    tage = None if w["typ"] == "taeglich" else {TAGE.index(x) for x in w["tage"]}
    for zurueck in range(0, 8):
        tag = jetzt.date() - timedelta(days=zurueck)
        if tage is not None and tag.weekday() not in tage:
            continue
        t = _um(tag, w["uhrzeit"], tz)
        if t <= jetzt:
            return t if t > seit else None
    return None


async def schritt(anfrage=None) -> list[dict]:
    """Ein Planer-Takt: alle fälligen Zeitregeln auslösen. Gibt die neu ausgelösten Läufe zurück."""
    jetzt = config.jetzt()
    ergebnisse = []
    for regel in speicher.aktive("zeit"):
        w = regel["ausloeser"]
        t = letzte_faelligkeit(w, jetzt, lauf.bestaetigt_zeit(regel, jetzt.tzinfo))
        if t is None:
            continue
        schluessel = f"zeit:{t:%Y-%m-%dT%H:%M}"
        if jetzt - t <= NACHHOLEN:
            ergebnisse += await lauf.zeit_ereignis(regel, t, schluessel, anfrage)
        else:
            ergebnisse += lauf.verpasst(regel, schluessel)
        if w["typ"] == "einmalig":
            speicher.setzen(regel["id"], "erledigt", "Zeit erreicht")
    return ergebnisse


async def dauerlauf() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await schritt()
        except Exception as exc:  # noqa: BLE001 — ein Fehler darf den Planer nie töten
            log.warning("Regel-Planer gescheitert (%s)", type(exc).__name__)
        await asyncio.sleep(TAKT_S)
