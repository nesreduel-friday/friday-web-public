"""Wege von A nach B: Bus und Bahn (Tür zu Tür), zu Fuß, mit dem Auto — für die Verbindungskarte.

Quellen, alle kostenlos, gemessen am 25.09.2026:
- **Bus/Bahn:** GVH-HAFAS mit Koordinaten (`gvh.verbindungen_koordinaten`) — Fußweg zur Haltestelle, Fahrten, Umstiege, Fußweg zum
  Ziel, mit Echtzeit, wo der GVH sie hat.
- **Zu Fuß und Auto:** Valhalla (OpenStreetMap Deutschland), Profile `pedestrian` und `auto`. NICHT der OSRM-Demoserver: Er ignoriert das
  Profil und liefert für „zu Fuß“ eine Autoroute (`docs/LEHREN.md`, 20.09.2026). Das Auto rechnet ohne aktuellen Verkehr — das steht dabei.

Jede Quelle fällt für sich aus: `alle` gibt zurück, was ging, und nennt, was nicht ging.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

import httpx

from kern import config
from kern.dienste import gvh

VALHALLA = "https://valhalla1.openstreetmap.de/route"
KENNUNG = "Friday/0.1 (persoenlicher Assistent)"
ZEITLIMIT = 10.0
WEIT_ZU_FUSS_M = 10_000
MAX_OEPNV = 2
ALLE_MODI = ("oepnv", "fuss", "auto")
TITEL = {"oepnv": "Bus & Bahn", "fuss": "Zu Fuß", "auto": "Auto"}
_PROFIL = {"fuss": "pedestrian", "auto": "auto"}


class RoutenFehler(Exception):
    """Eine Quelle antwortet nicht oder findet keinen Weg — der Text ist für Eike gedacht."""


def _hm(text: str) -> int | None:
    m = re.match(r"\s*(\d{1,2}):(\d{2})", str(text or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _diff_min(ab: str, an: str) -> int | None:
    a, b = _hm(ab), _hm(an)
    if a is None or b is None:
        return None
    return (b - a) % (24 * 60)


async def _valhalla(start: tuple[float, float], ziel: tuple[float, float], modus: str) -> dict:
    rumpf = {"locations": [{"lat": start[0], "lon": start[1]}, {"lat": ziel[0], "lon": ziel[1]}],
             "costing": _PROFIL[modus], "directions_options": {"units": "kilometers", "language": "de-DE"}}
    name = TITEL[modus]
    try:
        async with httpx.AsyncClient(timeout=ZEITLIMIT) as c:
            r = await c.post(VALHALLA, json=rumpf, headers={"User-Agent": KENNUNG})
    except httpx.HTTPError:
        raise RoutenFehler(f"{name}: Die Routenauskunft antwortet gerade nicht.") from None
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code != 200 or not isinstance(d, dict) or "trip" not in d:
        grund = str(d.get("error") or "") if isinstance(d, dict) else ""
        raise RoutenFehler(f"{name}: " + (f"Dafür finde ich keinen Weg ({grund[:80]})." if r.status_code == 400 and grund
                                          else "Die Routenauskunft meldet gerade einen Fehler."))
    try:
        s = d["trip"]["summary"]
        return {"sekunden": float(s["time"]), "meter": int(round(float(s["length"]) * 1000))}
    except (KeyError, TypeError, ValueError):
        raise RoutenFehler(f"{name}: Die Routenauskunft hat unverständlich geantwortet.") from None


def _uhr(t: datetime) -> str:
    return t.strftime("%H:%M")


async def _strasse(start, ziel, wann: datetime, modus: str, jetzt: datetime | None = None) -> dict:
    v = await _valhalla(start, ziel, modus)
    dauer = max(1, round(v["sekunden"] / 60))
    an = wann + timedelta(minutes=dauer)
    bis_start = max(0, round((wann - (jetzt or wann)).total_seconds() / 60))
    hinweis = ""
    if modus == "auto":
        hinweis = "Ohne aktuellen Verkehr gerechnet."
    elif v["meter"] > WEIT_ZU_FUSS_M:
        hinweis = f"Das ist weit zu Fuß ({v['meter'] / 1000:.1f} km)."
    return {"modus": modus, "titel": TITEL[modus], "dauer_min": dauer, "distanz_m": v["meter"], "ab": _uhr(wann), "an": _uhr(an),
            "hinweis": hinweis, "_an_min": bis_start + dauer}


async def zu_fuss(start, ziel, wann: datetime, jetzt: datetime | None = None) -> dict:
    return await _strasse(start, ziel, wann, "fuss", jetzt)


async def auto(start, ziel, wann: datetime, jetzt: datetime | None = None) -> dict:
    return await _strasse(start, ziel, wann, "auto", jetzt)


def _abschnitt(a: dict) -> dict:
    ab, an = a.get("ab_echt") or a.get("ab", ""), a.get("an_echt") or a.get("an", "")
    minuten = _diff_min(ab, an)
    return {"art": a.get("verkehrsmittel") or ("fuss" if a.get("zu_fuss") else "bahn"), "linie": "" if a.get("zu_fuss") else a.get("linie", ""),
            "richtung": a.get("richtung", ""), "von": a.get("von", ""), "nach": a.get("nach", ""), "ab": ab[:5], "an": an[:5],
            "min": minuten if minuten is not None else 0, "meter": a.get("meter", 0), "verspaetung_min": a.get("ab_verspaetung")}


async def oepnv(start, ziel, datum: str, zeit: str, jetzt: datetime) -> list[dict]:
    """Bus und Bahn von Tür zu Tür: die ersten `MAX_OEPNV` Verbindungen, die nicht ausfallen."""
    try:
        verbindungen = await gvh.verbindungen_koordinaten(start, ziel, datum, zeit, anzahl=4)
    except gvh.GvhFehler as exc:
        raise RoutenFehler(f"Bus & Bahn: {exc}") from None
    ergebnis = []
    for v in verbindungen:
        if any(a.get("faellt_aus") for a in v["abschnitte"]):
            continue
        ab_dt, an_dt = gvh.zeitpunkt(v.get("datum"), v.get("ab_roh")), gvh.zeitpunkt(v.get("datum"), v.get("an_roh"))
        ab, an = v.get("ab_echt") or v["ab"], v.get("an_echt") or v["an"]
        dauer = round((an_dt - ab_dt).total_seconds() / 60) if ab_dt and an_dt else (_diff_min(ab, an) or 0)
        echt = any(x.get("ab_verspaetung") is not None or x.get("an_verspaetung") is not None for x in [v, *v["abschnitte"]])
        ergebnis.append({
            "modus": "oepnv", "titel": TITEL["oepnv"], "dauer_min": dauer, "ab": ab[:5], "an": an[:5], "umstiege": v["umstiege"],
            "echtzeit": echt, "verspaetung_min": v.get("an_verspaetung"), "hinweis": "" if echt else "Fahrplanzeiten, keine Echtzeit.",
            "ab_in_min": max(0, round((ab_dt - jetzt).total_seconds() / 60)) if ab_dt else None,
            "abschnitte": [_abschnitt(a) for a in v["abschnitte"]],
            "_an_min": round((an_dt - jetzt).total_seconds() / 60) if an_dt else None,
        })
        if len(ergebnis) >= MAX_OEPNV:
            break
    if not ergebnis:
        raise RoutenFehler("Bus & Bahn: Ich finde gerade keine Verbindung, die fährt.")
    return ergebnis


async def alle(start: tuple[float, float], ziel: tuple[float, float], datum: str = "", zeit: str = "",
               verkehrsmittel=ALLE_MODI, wann: datetime | None = None, jetzt: datetime | None = None) -> dict:
    """Die gewünschten Verkehrsmittel gleichzeitig. `schnellste` = früheste Ankunft (Index in `optionen`)."""
    jetzt = jetzt or config.jetzt()
    start_zeit = wann or jetzt
    if wann and not zeit:
        datum, zeit = wann.strftime("%Y%m%d"), wann.strftime("%H:%M")
    aufgaben = {}
    if "oepnv" in verkehrsmittel:
        aufgaben["oepnv"] = oepnv(start, ziel, datum, zeit, jetzt)
    if "fuss" in verkehrsmittel:
        aufgaben["fuss"] = zu_fuss(start, ziel, start_zeit, jetzt)
    if "auto" in verkehrsmittel:
        aufgaben["auto"] = auto(start, ziel, start_zeit, jetzt)
    antworten = await asyncio.gather(*aufgaben.values(), return_exceptions=True)
    optionen, ausfaelle = [], []
    for modus, antwort in zip(aufgaben, antworten):
        if isinstance(antwort, (RoutenFehler, gvh.GvhFehler)):
            ausfaelle.append({"modus": modus, "text": str(antwort)})
        elif isinstance(antwort, BaseException):
            ausfaelle.append({"modus": modus, "text": f"{TITEL[modus]}: gerade nicht verfügbar ({type(antwort).__name__})."})
        else:
            optionen.extend(antwort if isinstance(antwort, list) else [antwort])
    zeiten = [(o.get("_an_min"), i) for i, o in enumerate(optionen) if o.get("_an_min") is not None]
    return {"optionen": optionen, "schnellste": min(zeiten)[1] if zeiten else None, "ausfaelle": ausfaelle}
