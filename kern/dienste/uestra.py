"""ÜSTRA-Abfahrtstafel — Echtzeit für Bus, Stadtbahn und S-Bahn in der Region Hannover.

Grundlage ist Eikes Ansatz aus Jarvis (`1/ÜSTRA API`, 14.09.2026): die offene
Schnittstelle hinter https://abfahrten.uestra.de/. Kein Schlüssel, kein Konto.

Gemessen am 14.09.2026, nicht aus dem Ansatz abgeschrieben:
- `/stops`: 1948 Haltestellen (id, gid, name, lat, lon, platforms), gut 600 KB —
  einmal pro Stunde geladen, nicht bei jeder Frage.
- `/proxy2/efa/XML_DM_REQUEST` liefert `{stop, coordinates, departures}`; je
  Linie `number`, `line`, `destination`, `events[{plannedTime, estimated_time}]`
  und `infos`. Zeiten in UTC. Das Feld heißt wirklich `estimated_time`.
- ECHTE Prognosen, auch für DB-S-Bahnen: Bennigsen Bahnhof S5 +29 und +39 Min,
  Hauptbahnhof S2 +16 Min (14.09., 23 Uhr). In 60 Ereignissen fehlte keine Prognose.
- Ein Ausfall-Feld kam in der Messung NICHT vor. `cancelled` wird ausgewertet,
  wenn es kommt — sonst behauptet Friday keinen Ausfall.
- Der Haltestellenname in der Antwort ist verkürzt („Bahnhof“); genannt wird
  deshalb der Name aus der Haltestellenliste.

Aus dem Ansatz bewusst NICHT übernommen:
- `requests` (synchron) — hier läuft alles async.
- Fehlende Zeit = „jetzt“ (`_ensure_datetime`): Das hätte aus einer fehlenden
  Prognose eine erfundene Verspätung gemacht. Hier heißt fehlend „keine Prognose“.
- Blind der erste Suchtreffer (`_resolve_stop_id`) — derselbe Fehler, der Eike am
  13.09. eine Verbindung ab Ludwigshafen bescherte. Hier wird streng gewählt und
  bei Gleichstand nachgefragt.
"""

from __future__ import annotations

import time as uhr
from datetime import datetime, timedelta

import httpx

from kern import config
from kern.dienste.gvh import _worte

BASIS = "https://abfahrten.uestra.de"
HALTESTELLEN_URL = f"{BASIS}/stops"
ABFAHRTEN_URL = f"{BASIS}/proxy2/efa/XML_DM_REQUEST"
KOEPFE = {"Accept": "application/json", "User-Agent": "Mozilla/5.0", "Referer": f"{BASIS}/"}
ZEITLIMIT = 15
VERBINDEN_S = 3
WIEDERHOLEN = 2
LISTE_GILT_S = 3600
MAX_ABFAHRTEN = 8
MAX_MELDUNGEN = 3
# Störungen und Umleitungen ja — kaputte Aufzüge und Rolltreppen („infrastructure“) nicht.
MELDUNGEN = {"lineInfo", "routeBlocking"}
BAHNHOF = {"bahnhof", "hauptbahnhof"}


class UestraFehler(Exception):
    """Klartext für Friday."""


class Unbekannt(UestraFehler):
    """Die Haltestelle gibt es in der ÜSTRA-Liste nicht (etwa außerhalb der Region)."""


class Mehrdeutig(UestraFehler):
    """Mehrere Haltestellen passen gleich gut — nachfragen statt raten."""


_liste: list[dict] = []
_geladen = 0.0


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(ZEITLIMIT, connect=VERBINDEN_S), headers=KOEPFE,
                             transport=httpx.AsyncHTTPTransport(retries=WIEDERHOLEN))


async def haltestellen() -> list[dict]:
    """Alle Haltestellen, eine Stunde lang zwischengespeichert. Ist die Auskunft
    kurz weg, gilt die alte Liste weiter — besser als gar keine."""
    global _liste, _geladen
    if _liste and uhr.monotonic() - _geladen < LISTE_GILT_S:
        return _liste
    try:
        async with _client() as client:
            antwort = await client.get(HALTESTELLEN_URL)
        antwort.raise_for_status()
        roh = antwort.json()
    except (httpx.HTTPError, ValueError) as exc:
        if _liste:
            return _liste
        raise UestraFehler(f"Die ÜSTRA-Auskunft antwortet gerade nicht ({type(exc).__name__}).") from None
    # `gid` ist dieselbe Kennung, die das Garantie-Formular als `reference` nutzt.
    _liste = [{"id": str(h["id"]), "name": str(h["name"]), "gid": str(h.get("gid") or "")} for h in roh
              if isinstance(h, dict) and h.get("id") and h.get("name")]
    _geladen = uhr.monotonic()
    return _liste


def auswaehlen(wunsch: str, liste: list[dict]) -> dict:
    """Die gemeinte Haltestelle — oder ein Fehler, der Friday nachfragen lässt.

    1. exakt gleicher Name;
    2. sonst müssen ALLE tragenden Wörter vorkommen (wie beim GVH, `gvh._passt`);
    3. unter den Treffern gewinnt der Bahnhof des Orts („Bennigsen“ → Bennigsen
       Bahnhof), sonst der Name mit den wenigsten Zusatzwörtern („Kröpcke“ →
       Hannover Kröpcke statt Kröpcke/Theaterstraße);
    4. bleibt ein Gleichstand („Musterdorf“: Denkmal oder Wolfsberg?), wird gefragt.
    """
    wunsch = (wunsch or "").strip()
    worte = _worte(wunsch)
    if not worte:
        raise Unbekannt("Welche Haltestelle meinst du?")
    for h in liste:
        if h["name"].casefold() == wunsch.casefold():
            return h
    passend = [(h, _worte(h["name"]) - worte) for h in liste if worte <= _worte(h["name"])]
    if not passend:
        raise Unbekannt(f"Eine Haltestelle „{wunsch}“ kennt die ÜSTRA-Auskunft nicht.")
    bahnhoefe = [(h, extra) for h, extra in passend if extra and extra <= BAHNHOF]
    kandidaten = bahnhoefe or passend
    wenigste = min(len(extra) for _, extra in kandidaten)
    beste = [h for h, extra in kandidaten if len(extra) == wenigste]
    if len(beste) == 1:
        return beste[0]
    namen = ", ".join(sorted(h["name"] for h in beste)[:4])
    raise Mehrdeutig(f"Zu „{wunsch}“ passen mehrere Haltestellen: {namen}. Welche meinst du?")


def _zeit(wert) -> datetime | None:
    try:
        return datetime.fromisoformat(str(wert).replace("Z", "+00:00")).astimezone(config.jetzt().tzinfo)
    except (TypeError, ValueError):
        return None


def gleiche_linie(uestra_nummer: str, gvh_name: str) -> bool:
    """„S5“ ↔ „S5“, „500“ ↔ „Bus 500“ — die beiden Auskünfte schreiben Linien verschieden."""
    u, g = (uestra_nummer or "").strip().casefold(), (gvh_name or "").strip().casefold()
    return bool(u) and (u == g or g.split()[-1:] == [u])


async def abfahrten(wunsch: str, anzahl: int = MAX_ABFAHRTEN) -> dict:
    """Nächste Abfahrten mit Plan, Prognose und Verspätung in Minuten."""
    return await tafel(auswaehlen(wunsch, await haltestellen()), anzahl)


async def tafel(halt: dict, anzahl: int = MAX_ABFAHRTEN) -> dict:
    """Die Tafel einer schon bekannten Haltestelle ({id, name}) — auch für den Garantie-Sammler."""
    anfrage = {"outputFormat": "rapidJSON", "useRealtime": "1", "name_dm": halt["id"], "type_dm": "any",
               "mode": "direct", "depType": "stopEvents", "itdDateTimeDepArr": "dep", "maxTimeLoop": "1",
               "mergeDep": "1", "depSequence": "30", "useAllStops": "1", "includedMeans": "checkbox",
               "version": "10.5.17.3", "language": "de", **{f"inclMOT_{i}": "true" for i in range(1, 20)}}
    try:
        async with _client() as client:
            antwort = await client.get(ABFAHRTEN_URL, params=anfrage)
        antwort.raise_for_status()
        linien = antwort.json()["departures"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise UestraFehler(f"Die ÜSTRA-Auskunft antwortet gerade nicht ({type(exc).__name__}).") from None

    jetzt = config.jetzt()
    liste, meldungen = [], []
    for linie in linien or []:
        for ereignis in linie.get("events") or []:
            plan = _zeit(ereignis.get("plannedTime"))
            if plan is None:
                continue
            prognose = _zeit(ereignis.get("estimated_time") or ereignis.get("estimatedTime"))
            if (prognose or plan) < jetzt - timedelta(minutes=1):
                continue                                   # schon weg
            liste.append({
                "linie": str(linie.get("number") or linie.get("line") or "?"),
                "art": str(linie.get("line") or ""),       # „Stadtbahn 7“, „Bus 360“, „S-Bahn S5“
                "richtung": str(linie.get("destination") or ""),
                "plan": plan, "prognose": prognose,
                # Keine Prognose heißt: keine Aussage — nie „pünktlich“ und nie eine Zahl.
                "verspaetung": round((prognose - plan).total_seconds() / 60) if prognose else None,
                "faellt_aus": bool(ereignis.get("cancelled") or linie.get("cancelled")),
            })
        for info in linie.get("infos") or []:
            titel = " ".join(str(info.get("titel") or info.get("title") or "").split())
            if info.get("type") in MELDUNGEN and titel and titel not in meldungen:
                meldungen.append(titel[:200])
    liste.sort(key=lambda a: a["prognose"] or a["plan"])
    return {"haltestelle": halt["name"], "abfahrten": liste[:max(1, anzahl)], "meldungen": meldungen[:MAX_MELDUNGEN]}
