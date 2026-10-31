"""Standort — wo Eike gerade ist, wenn er es erlaubt hat.

Der Server hat selbst kein GPS. Die App (Handy oder PC) fragt beim Browser nach
der Position — aber NUR AUF ANFRAGE: Fragt Eike „Wo bin ich?“, stellt das
Werkzeug eine Anfrage (`frisch_holen`), die App hört per Long-Poll darauf
(`auf_anfrage_warten`), holt die Position und meldet sie. Nichts davon läuft,
solange der Konnektor `standort` aus ist.

Warum auf Anfrage und nicht laufend (20.09.2026): iOS behält die Standort-
Erlaubnis der installierten App nicht und fragt bei jedem Öffnen neu. Meldete
die App beim Öffnen und danach im Takt, käme dieser Dialog jedes Mal — auch wenn
Eike nie nach seinem Standort fragt. So kommt er nur, wenn er gebraucht wird,
und dann genau nach seiner Frage. Nebenbei ist die Position beim Antworten frisch,
es entsteht keine Spur im Takt, und der Akku wird nicht belastet.

Was hier gilt (und warum):

- **Nur im Arbeitsspeicher.** Wie `kern/umgebung.py`: Koordinaten werden nie auf
  die Platte geschrieben. Nach einem Neustart meldet sich die App beim nächsten
  Öffnen ohnehin neu. So entsteht aus dem Dienst selbst kein Bewegungsprotokoll.
- **Der Browser ist eine fremde Quelle.** Zahlen werden geprüft und begrenzt,
  Fehlergründe stehen in einer festen Liste. Es wird kein Freitext übernommen.
- **Der Ortsname kommt erst auf Nachfrage** (nicht bei jeder Meldung) von
  OpenStreetMap (Nominatim), gemerkt für eine Stunde, höchstens eine Anfrage pro
  Sekunde — das verlangt deren Nutzungsregel. Dorthin gehen also Koordinaten;
  das steht auf der Freigaben-Seite.
- **Straße, keine Hausnummer.** Nominatim liefert auf Zoom 18 die Hausnummer
  (gemessen 20.09.2026). Das Gespräch wird protokolliert, und eine Hausnummer
  darin ist mehr, als „wo bin ich?“ braucht.
"""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from datetime import datetime, timezone

import httpx

from kern import umgebung
from kern.dienste import wetter

KONNEKTOR = "standort"
OWNTRACKS_NAME = "eike"     # so heißt Eike in der OwnTracks-Verwaltung (`friday-personenstandort`)
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_SUCHE = "https://nominatim.openstreetmap.org/search"
KENNUNG = "Friday/0.4 (privater Assistent, selbst gehostet)"
ZEITLIMIT = 10
ZOOM = 17                   # Straße; 18 wäre die Hausnummer
MIN_ABSTAND_S = 1.1         # Nutzungsregel von Nominatim: höchstens 1 Anfrage pro Sekunde
NAME_MERKEN_S = 3600
MAX_NAMEN = 50
MAX_CLIENTS = 10
VERFALL_S = 7200            # nach zwei Stunden ohne Meldung ist der Wert weg
AKTUELL_S = 900             # bis hierhin gilt ein Standort als „jetzt“, danach als alt
FRISCH_S = 120              # jünger als das: kein neuer Dialog nötig, der Wert genügt
WARTEN_S = 25               # so lange wartet das Werkzeug auf die App (Zeit, den Dialog zu lesen und zu tippen)
LAUSCHEN_S = 25             # so lange hält der Long-Poll der App offen, bevor er neu ansetzt
SCHRITT_S = 0.25            # Abtastung beim Warten — bewusst ohne asyncio-Bedingung, siehe frisch_holen
WOHNORT_NAH_M = 1000        # innerhalb davon: „in deinem Wohnort“ — der Wert ist die Ortsmitte, keine Adresse
UNGENAU_M = 500             # darüber ist es eher WLAN-/IP-Ortung als GPS
HEIM_NAH_M = 75             # so nah am Haus gilt als „zu Hause“ …
HEIM_GENAU_M = 100          # … aber nur, wenn die Ortung selbst mindestens so genau ist
HEIM_NEUER_VERSUCH_S = 600  # nach einem Fehlschlag der Adresssuche so lange nicht noch einmal fragen

# Warum keine Position kam — feste Liste, kein Freitext vom Browser.
FEHLER = {
    "verweigert": "Der Zugriff auf den Standort ist im Browser nicht erlaubt.",
    "nicht_verfuegbar": "Das Gerät kann seinen Standort gerade nicht bestimmen.",
    "zeitueberschreitung": "Die Standortbestimmung hat zu lange gedauert.",
    "nicht_unterstuetzt": "Dieser Browser kann keinen Standort abfragen.",
}

_sperre = threading.Lock()
_stand: dict[str, dict] = {}
_nr = 0                     # Nummer der letzten Anfrage; die App merkt sich die zuletzt gesehene
_offen_bis = 0.0            # monotone Zeit, bis zu der die letzte Anfrage noch beantwortet werden soll


def _zahl(wert, unten: float, oben: float) -> float | None:
    try:
        x = float(wert)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and unten <= x <= oben else None


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def melden(daten: dict) -> dict:
    """Nimmt eine Meldung der App an. Gibt zurück, was davon gilt."""
    kennung = umgebung._kennung(daten.get("kennung"))
    eintrag: dict = {"kennung": kennung, "zuletzt": _jetzt().isoformat()}
    fehler = str(daten.get("fehler") or "")
    if fehler:
        eintrag["fehler"] = fehler if fehler in FEHLER else "nicht_verfuegbar"
    else:
        breite = _zahl(daten.get("breite"), -90, 90)
        laenge = _zahl(daten.get("laenge"), -180, 180)
        if breite is None or laenge is None:
            raise ValueError("Ungültige Koordinaten.")
        genau = _zahl(daten.get("genauigkeit"), 0, 100000)
        # „0 m“ ist keine Messung: iOS lieferte es bei zwischengespeicherten Werten
        # (Eikes Log, 20.09.2026 — „Genauigkeit rund 0 m“). Als unbekannt behandeln,
        # dann klingt Friday vorsichtig statt millimetergenau.
        eintrag.update({"breite": breite, "laenge": laenge,
                        "genauigkeit": round(genau) if genau else None})
    with _sperre:
        _aufraeumen()
        _stand[kennung] = eintrag
        if len(_stand) > MAX_CLIENTS:
            for k in sorted(_stand, key=lambda k: _stand[k]["zuletzt"])[:-MAX_CLIENTS]:
                _stand.pop(k, None)
    return {"ok": True}


def _alter_s(eintrag: dict) -> float:
    try:
        return (_jetzt() - datetime.fromisoformat(eintrag["zuletzt"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return float("inf")


def _aufraeumen() -> None:
    """Nur innerhalb von `_sperre` aufrufen."""
    for k, e in list(_stand.items()):
        if _alter_s(e) > VERFALL_S:
            del _stand[k]


def vergessen() -> None:
    """Alles verwerfen — auch für Tests."""
    global _nr, _offen_bis, _heim, _heim_versuch
    with _sperre:
        _stand.clear()
    _namen.clear()
    _nr, _offen_bis = 0, 0.0
    _heim, _heim_versuch = None, 0.0


def aktuell() -> dict | None:
    """Die beste bekannte Position, mit Alter und Herkunft — oder None.

    Ein Handy geht vor: Es ist bei Eike, ein PC steht meist irgendwo, und seine
    Ortung (WLAN/IP) ist viel gröber."""
    with _sperre:
        _aufraeumen()
        mit_ort = [dict(e) for e in _stand.values() if "breite" in e]
    if not mit_ort:
        return None

    def bewerten(e: dict):
        geraet = (umgebung.fuer(e["kennung"]) or {}).get("geraet", umgebung.UNBEKANNT)
        e["geraet"] = geraet
        frisch = _alter_s(e) <= AKTUELL_S
        return (not frisch, geraet != "Handy", _alter_s(e))

    besser = min(mit_ort, key=bewerten)
    besser["alter_s"] = int(_alter_s(besser))
    return besser


async def eigene_kandidaten(frisch: bool = True) -> list[dict]:
    """Eikes Position aus allen Quellen: die App (Browser-Ortung), OwnTracks (GPS im Hintergrund,
    `friday-personenstandort`) und Home Assistant (nur lesend). Jeder Kandidat trägt Quelle, Genauigkeit
    und Alter, damit der Aufrufer selbst entscheiden kann, ob er damit arbeiten darf.

    `frisch=False`: das Handy nicht fragen — für Hintergrundläufe, die Eike nicht mit einem
    Standort-Dialog überraschen dürfen (iOS fragt bei jedem Aufruf neu). Home Assistant und OwnTracks
    melden von selbst; sie werden auch dann gelesen.
    """
    from kern import konnektoren
    from kern.dienste import homeassistant, personenstandort
    kandidaten = []
    if konnektoren.benutzbar(KONNEKTOR):
        pos = aktuell()
        if frisch and (not pos or pos["alter_s"] > FRISCH_S):
            try:
                pos = await frisch_holen() or pos
            except Exception:
                pass
        if pos:
            kandidaten.append({"quelle": "App", "breite": pos["breite"], "laenge": pos["laenge"], "genau": pos.get("genauigkeit"),
                               "alter_s": pos["alter_s"], "geraet": pos.get("geraet", "")})
    if konnektoren.benutzbar(personenstandort.KONNEKTOR):
        try:
            eike = personenstandort.finden(OWNTRACKS_NAME, await personenstandort.personen())
        except Exception:
            eike = []                                   # ein Fehler im Container darf den Aufrufer nicht kosten
        for p in eike[:1]:
            if p.get("zustand") == "ok" and "breite" in p:
                kandidaten.append({"quelle": "OwnTracks", "breite": p["breite"], "laenge": p["laenge"], "genau": p.get("genauigkeit_m"),
                                   "alter_s": p["alter_s"], "geraet": "Handy"})
    if konnektoren.benutzbar(homeassistant.KONNEKTOR):
        try:
            pos = await homeassistant.position()
        except Exception:
            pos = None                                  # Home Assistant ist Beiwerk — nie den Aufrufer kosten
        if pos:
            kandidaten.append(pos)
    return kandidaten


def grund() -> str:
    """Warum es keine Position gibt, soweit die App das gemeldet hat."""
    with _sperre:
        _aufraeumen()
        fehler = sorted((e for e in _stand.values() if e.get("fehler")), key=lambda e: e["zuletzt"])
    return FEHLER.get(fehler[-1]["fehler"], "") if fehler else ""


# ---------- Anfrage an die App ----------

async def auf_anfrage_warten(seit: int, max_s: float | None = None) -> dict:
    """Long-Poll der App: kehrt zurück, sobald es eine neue Anfrage gibt (oder nach
    `LAUSCHEN_S`). `offen` sagt, ob die letzte noch beantwortet werden soll — eine
    alte, längst verfallene löst beim Öffnen der Seite keinen Dialog aus.

    Verglichen wird auf UNGLEICH, nicht auf „größer“: Nach einem Neustart des
    Servers zählt `_nr` wieder bei 0, und eine App, die 5 gemerkt hat, würde sonst
    auf 6 warten und jede Anfrage verpassen.
    """
    ende = time.monotonic() + (LAUSCHEN_S if max_s is None else max_s)
    while _nr == seit and time.monotonic() < ende:
        await asyncio.sleep(SCHRITT_S)
    return {"nr": _nr, "offen": time.monotonic() < _offen_bis}


def _neuer_als(start: datetime) -> list[dict]:
    with _sperre:
        return [dict(e) for e in _stand.values() if datetime.fromisoformat(e["zuletzt"]) > start]


async def frisch_holen() -> dict | None:
    """Die App nach der Position fragen und auf die Antwort warten.

    Gibt die beste Position zurück, oder None, wenn nichts ankam. Hört auf, sobald
    eine Position da ist, oder wenn jede offene Oberfläche geantwortet hat, dass sie
    keine liefern kann (z. B. Erlaubnis verweigert) — dann nützt Warten nichts.

    Mit Abtasten statt `asyncio.Condition`: Die Bedingung bindet sich an die
    Ereignisschleife des ersten Aufrufers, und Anfrage (Chat-Zug) und Long-Poll
    (eigene Anfrage) laufen nicht zwingend in derselben.
    """
    global _nr, _offen_bis
    start = _jetzt()
    _nr += 1
    _offen_bis = time.monotonic() + WARTEN_S
    ende = time.monotonic() + WARTEN_S
    try:
        while time.monotonic() < ende:
            await asyncio.sleep(SCHRITT_S)
            neu = _neuer_als(start)
            if any("breite" in e for e in neu):
                return aktuell()
            offen = max(1, len(umgebung.offene()))
            if sum(1 for e in neu if e.get("fehler")) >= offen:
                return None
        return None
    finally:
        _offen_bis = 0.0                      # danach soll keine Seite mehr einen Dialog zeigen


# ---------- Rechnen ----------

def abstand_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Luftlinie in Metern (Haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ---------- Ortsname ----------

_namen: dict[tuple[float, float], tuple[float, str]] = {}
_letzte_anfrage = 0.0
_gate = asyncio.Lock()


def _bezeichnen(d: dict) -> str:
    """Aus der Nominatim-Antwort einen kurzen Ort machen: Straße, Ortsteil, Stadt.
    Ohne Hausnummer und ohne Postleitzahl."""
    a = d.get("address") or {}
    strasse = next((a[k] for k in ("road", "pedestrian", "footway", "path", "cycleway") if a.get(k)), "")
    teil = next((a[k] for k in ("suburb", "village", "hamlet", "neighbourhood", "quarter") if a.get(k)), "")
    stadt = next((a[k] for k in ("city", "town", "municipality") if a.get(k)), "")
    stuecke: list[str] = []
    for s in (strasse, teil, stadt):
        if s and s not in stuecke:
            stuecke.append(s)
    if stuecke:
        return ", ".join(stuecke)
    # Ohne Adresse (Feld, Wald): die ersten beiden Teile des Anzeigenamens.
    return ", ".join((d.get("display_name") or "").split(", ")[:2])


async def _nominatim(url: str, params: dict) -> httpx.Response | None:
    """Ein Aufruf bei Nominatim — nacheinander und mit Abstand (deren Nutzungsregel)."""
    global _letzte_anfrage
    async with _gate:
        warte = MIN_ABSTAND_S - (time.monotonic() - _letzte_anfrage)
        if warte > 0:
            await asyncio.sleep(warte)
        try:
            async with httpx.AsyncClient(timeout=ZEITLIMIT, headers={
                    "User-Agent": KENNUNG, "Accept-Language": "de"}) as client:
                return await client.get(url, params=params)
        except httpx.HTTPError:
            return None
        finally:
            _letzte_anfrage = time.monotonic()


async def ortsname(breite: float, laenge: float) -> str:
    """Wie heißt der Ort? Leer, wenn er sich nicht ermitteln lässt."""
    schluessel = (round(breite, 3), round(laenge, 3))      # ~100 m — reicht zum Zwischenspeichern
    gemerkt = _namen.get(schluessel)
    if gemerkt and time.monotonic() - gemerkt[0] < NAME_MERKEN_S:
        return gemerkt[1]
    antwort = await _nominatim(NOMINATIM, {
        "lat": breite, "lon": laenge, "format": "jsonv2", "zoom": ZOOM, "addressdetails": 1})
    if antwort is None or antwort.status_code != 200:
        return ""
    try:
        name = _bezeichnen(antwort.json())
    except ValueError:
        return ""
    if name:
        if len(_namen) >= MAX_NAMEN:
            _namen.pop(next(iter(_namen)))
        _namen[schluessel] = (time.monotonic(), name)
    return name


UMKREIS_GRAD = 0.15         # Suchfenster um Eike: grob 17 km
NAH_TREFFER = 20            # so viele holen und SELBST nach Nähe sortieren


def _ort_aus(t: dict) -> dict:
    """Ein Nominatim-Treffer in Fridays Form. Der Name bleibt kurz — der volle
    `display_name` endet auf „…, Niedersachsen, 31832, Deutschland“."""
    a = t.get("address") or {}
    # Der eigentliche Name steht oben, nicht in der Adresse (gemessen 20.09.2026):
    # „Berlin Hauptbahnhof“ hieß sonst „Washingtonplatz, Moabit“.
    wie = (t.get("name") or "").strip()
    if not wie:
        wie = next((a[k] for k in ("shop", "amenity", "road", "pedestrian") if a.get(k)), "")
        if wie and a.get("house_number"):
            wie = f"{wie} {a['house_number']}"
    wo = next((a[k] for k in ("suburb", "village", "hamlet", "quarter", "town", "city") if a.get(k)), "")
    teile = [x for x in (wie, wo) if x]
    # „Berlin Hauptbahnhof, Berlin“ wäre doppelt gemoppelt.
    if len(teile) == 2 and teile[1].casefold() in teile[0].casefold():
        teile.pop()
    name = ", ".join(teile) or (t.get("display_name") or "").split(",")[0]
    return {"name": name, "breite": float(t["lat"]), "laenge": float(t["lon"]),
            "voll": (t.get("display_name") or "")[:200]}


async def ort_finden(wunsch: str, nahe: tuple[float, float] | None = None) -> list[dict]:
    """Orte und Adressen zu einem Wunsch („Penny“, „Bahnhof Musterhausen“), nächste zuerst.

    Mit `nahe` bevorzugt Nominatim das Fenster um diesen Punkt (`viewbox`, nicht
    `bounded`: Treffer außerhalb kommen noch, nur weiter hinten). Ohne das lieferte
    „Penny“ irgendeine Filiale in Deutschland.

    Sortiert wird am Ende selbst nach echter Entfernung — nicht blind der erste
    Treffer (dieselbe Falle wie bei Spotify und den Playlists).
    """
    wunsch = (wunsch or "").strip()
    if not wunsch:
        return []
    grund = {"q": wunsch, "format": "jsonv2", "addressdetails": 1, "countrycodes": "de"}
    versuche = []
    if nahe:
        b, l = nahe
        # `bounded` statt nur `viewbox`: ein bloßes Vorzugsfenster reicht NICHT. Gemessen
        # am 20.09.2026 lieferte „Penny“ damit Wettbergen (9 km) und Ricklingen (11 km),
        # während die Filiale in Bennigsen (2 km) gar nicht auftauchte — Nominatim ordnet
        # nach eigener Wichtigkeit, nicht nach Nähe. Mit `bounded` und mehr Treffern ist
        # sie dabei, und sortiert wird hier selbst.
        versuche.append({**grund, "limit": NAH_TREFFER, "bounded": 1,
                         "viewbox": (f"{l - UMKREIS_GRAD},{b + UMKREIS_GRAD},"
                                     f"{l + UMKREIS_GRAD},{b - UMKREIS_GRAD}")})
    # Ohne Standort — und als Rückfall für Ziele außerhalb des Fensters („bis Berlin“).
    versuche.append({**grund, "limit": 8})

    for params in versuche:
        antwort = await _nominatim(NOMINATIM_SUCHE, params)
        if antwort is None or antwort.status_code != 200:
            continue
        try:
            treffer = [_ort_aus(t) for t in (antwort.json() or []) if t.get("lat") and t.get("lon")]
        except (ValueError, KeyError, TypeError):
            continue
        if not treffer:
            continue
        if nahe:
            for t in treffer:
                t["meter"] = abstand_m(nahe, (t["breite"], t["laenge"]))
            treffer.sort(key=lambda t: t["meter"])
        return treffer
    return []


_wohnort: dict | None = None
_heim: dict | None = None
_heim_versuch = 0.0


async def heim() -> dict | None:
    """Der Punkt des Hauses aus `FRIDAY_HEIMADRESSE` — oder None.

    Ohne diesen Punkt kennt Friday nur die Ortsmitte (`wohnort`) und darf deshalb nie
    „du bist zu Hause“ sagen: Musterdorf ist größer als ein Haus. Eike bat darum, genau das
    zu hören (20.09.2026, „Sag mir, dass ich zu Hause bin“), und Friday versprach es,
    konnte es aber nicht einlösen.

    Die Adresse wird EINMAL bei Nominatim nachgeschlagen und im Speicher gehalten (nicht
    auf der Platte). Gilt nur, wenn Nominatim eine Hausnummer trifft — sonst wäre es
    wieder nur eine Ortsmitte, und die Genauigkeit wäre vorgetäuscht.
    """
    global _heim, _heim_versuch
    adresse = os.getenv("FRIDAY_HEIMADRESSE", "").strip()
    if not adresse:
        return None
    if _heim and _heim.get("adresse") == adresse:
        return _heim
    if _heim_versuch and time.monotonic() - _heim_versuch < HEIM_NEUER_VERSUCH_S:
        return None                                        # gerade erst gescheitert
    _heim_versuch = time.monotonic()
    antwort = await _nominatim(NOMINATIM_SUCHE, {
        "q": adresse, "format": "jsonv2", "limit": 1, "addressdetails": 1, "countrycodes": "de"})
    if antwort is None or antwort.status_code != 200:
        return None
    try:
        treffer = (antwort.json() or [None])[0]
        if not treffer or not (treffer.get("address") or {}).get("house_number"):
            return None
        _heim = {"adresse": adresse, "breite": float(treffer["lat"]), "laenge": float(treffer["lon"])}
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    return _heim


async def wohnort() -> dict | None:
    """Ortsmitte des Wohnorts (aus `FRIDAY_ORT`) — nur für „wie weit von zu Hause“.
    Das ist die Mitte des Ortes, KEINE Hausadresse; deshalb sagt Friday nie
    „du bist zu Hause“, sondern „du bist in deinem Wohnort“."""
    global _wohnort
    if _wohnort is None:
        try:
            _wohnort = await wetter.ort_finden(wetter.heimatort())
        except Exception:
            return None
    return _wohnort
