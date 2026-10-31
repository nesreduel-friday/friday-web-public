"""Schulkarte: welche Stunde jetzt ist, wie lange noch, was danach kommt.

Der Kern liefert die Zeitpunkte (ISO mit Offset), die Seite zählt selbst
sekundengenau herunter. Rein rechnerischer Teil (`berechne`) und Abruf
(`karten`) sind getrennt, damit der Teil, an dem die Fehler sitzen können —
Grenzen, Doppelstunden, Ausfall —, ohne Netz testbar ist.

Zwei Entscheidungen:
- Ausgefallene Stunden zählen nicht als Unterricht: Eike ist dann frei. Sie
  werden in der Pause genannt, damit die Lücke erklärt ist.
- Zwei Stunden desselben Fachs im selben Raum mit höchstens `ZUSAMMEN_LUECKE`
  dazwischen sind EINE Doppelstunde: das 5-Minuten-Loch ist keine Pause.
"""

import logging
from datetime import date, datetime, time, timedelta

from kern import config
from kern.dienste import schulmanager
from kern.werkzeuge import schule as schulwerkzeug

log = logging.getLogger("friday.dashboard")

VORLAUF = timedelta(minutes=30)
ZUSAMMEN_LUECKE = timedelta(minutes=10)
FREISTUNDE = timedelta(minutes=20)
CACHE_S = 600
SCHULFENSTER = (6, 18)   # nur in dieser Zeit wird überhaupt beim Schulmanager gefragt

_cache: dict[str, tuple[float, list[dict]]] = {}   # Tag → (Zeitpunkt, Stunden)


def _zeit(tag: str, hhmm: str, tz) -> datetime | None:
    try:
        h, m = str(hhmm)[:5].split(":")
        return datetime.combine(date.fromisoformat(tag), time(int(h), int(m)), tzinfo=tz)
    except (ValueError, TypeError):
        return None


def _stunde(e: dict, tz) -> dict | None:
    std = e.get("classHour") or {}
    von, bis = _zeit(e.get("date", ""), std.get("from"), tz), _zeit(e.get("date", ""), std.get("until"), tz)
    if not von or not bis or bis <= von:
        return None
    quelle = e.get("actualLesson") or (e.get("originalLessons") or [{}])[0] or {}
    original = (e.get("originalLessons") or [{}])[0] or {}
    vertretung = e.get("type") == "substitution"
    hinweis = ""
    if vertretung and original and schulwerkzeug._fach(original) != schulwerkzeug._fach(quelle):
        hinweis = f"Vertretung, eigentlich {schulwerkzeug._fach(original)}"
    elif vertretung:
        hinweis = "Vertretung"
    return {"fach": schulwerkzeug._fach(quelle), "raum": (quelle.get("room") or {}).get("name", ""),
            "lehrer": schulwerkzeug._lehrkraefte(quelle), "von": von, "bis": bis,
            "vertretung": vertretung, "hinweis": hinweis, "ausfall": e.get("type") == "cancelledLesson"}


def _bloecke(stunden: list[dict]) -> list[dict]:
    bloecke: list[dict] = []
    for s in sorted(stunden, key=lambda s: s["von"]):
        letzter = bloecke[-1] if bloecke else None
        if (letzter and s["fach"] == letzter["fach"] and s["raum"] == letzter["raum"]
                and s["von"] - letzter["bis"] <= ZUSAMMEN_LUECKE and not (s["vertretung"] or letzter["vertretung"])):
            letzter["bis"] = s["bis"]
        else:
            bloecke.append(dict(s))
    return bloecke


def _aussen(b: dict | None) -> dict | None:
    if not b:
        return None
    return {"fach": b["fach"], "raum": b["raum"], "lehrer": b["lehrer"], "von": b["von"].isoformat(),
            "bis": b["bis"].isoformat(), "vertretung": b["vertretung"]}


def berechne(stunden: list[dict], jetzt: datetime) -> dict | None:
    tag = jetzt.date().isoformat()
    heute = [x for e in stunden if e.get("date") == tag and (x := _stunde(e, jetzt.tzinfo))]
    unterricht = [s for s in heute if not s["ausfall"]]
    ausfaelle = [s for s in heute if s["ausfall"]]
    bloecke = _bloecke(unterricht)
    if not bloecke or jetzt < bloecke[0]["von"] - VORLAUF or jetzt >= bloecke[-1]["bis"]:
        return None

    def karte(zustand, von, bis, aktuell, naechste, hinweis="", art_pause=""):
        return {"id": "schule", "art": "schule", "zustand": zustand, "von": von.isoformat(),
                "bis": bis.isoformat(), "aktuell": _aussen(aktuell), "naechste": _aussen(naechste),
                "hinweis": hinweis, "art_pause": art_pause}

    if jetzt < bloecke[0]["von"]:
        return karte("vor_schule", bloecke[0]["von"] - VORLAUF, bloecke[0]["von"], None, bloecke[0])
    for i, b in enumerate(bloecke):
        naechste = bloecke[i + 1] if i + 1 < len(bloecke) else None
        if b["von"] <= jetzt < b["bis"]:
            return karte("unterricht", b["von"], b["bis"], b, naechste, b["hinweis"])
        if naechste and b["bis"] <= jetzt < naechste["von"]:
            weg = [a["fach"] for a in ausfaelle if a["von"] >= b["bis"] and a["bis"] <= naechste["von"]]
            hinweis = ("Fällt aus: " + ", ".join(weg)) if weg else naechste["hinweis"]
            art = "freistunde" if naechste["von"] - b["bis"] >= FREISTUNDE else "pause"
            return karte("pause", b["bis"], naechste["von"], None, naechste, hinweis, art)
    return None


async def _plan(jetzt: datetime) -> list[dict]:
    schluessel = jetzt.date().isoformat()
    alt = _cache.get(schluessel)
    if alt and jetzt.timestamp() - alt[0] < CACHE_S:
        return alt[1]
    stunden = await schulmanager.stundenplan(jetzt.date(), jetzt.date())
    _cache.clear()
    _cache[schluessel] = (jetzt.timestamp(), stunden)
    return stunden


async def karten() -> list[dict]:
    jetzt = config.jetzt()
    if jetzt.weekday() >= 5 or not (SCHULFENSTER[0] <= jetzt.hour < SCHULFENSTER[1]):
        return []
    k = berechne(await _plan(jetzt), jetzt)
    return [k] if k else []
