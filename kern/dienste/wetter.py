"""Wetter über Open-Meteo — kein Schlüssel, kein Konto.

Zwei Aufrufe: den Ort in Koordinaten übersetzen (Geocoding) und die
Vorhersage holen. Beide Antwortformen am 14.09.2026 gemessen, nicht geraten.

Der Ort ist die Falle, nicht das Wetter: „Holtensen“ ergibt in Niedersachsen
FÜNF Orte (Barsinghausen, Wennigsen, Musterhausen, Hameln, Einbeck). Wie bei den
GVH-Haltestellen wird deshalb nicht blind der erste Treffer genommen. Eine
Angabe wie „Holtensen, Musterhausen“ grenzt über Gemeinde oder Kreis ein, und die
Antwort nennt IMMER, welcher Ort gefunden wurde — ein Fehlgriff ist so
wenigstens sichtbar.
"""

from __future__ import annotations

import os

import httpx

from kern import config

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
VORHERSAGE_URL = "https://api.open-meteo.com/v1/forecast"
ZEITLIMIT = 10
# Gemessen am 14.09.2026: Jeder dritte bis zehnte Verbindungsaufbau zu Open-Meteo
# hängt — vom Host (3/10) wie aus dem Container (1/10) —, die übrigen antworten
# in 0,1 s. Also nicht länger warten, sondern früh aufgeben und neu verbinden.
# httpx wiederholt dabei nur gescheiterte VERBINDUNGEN, nie eine angekommene Anfrage.
VERBINDEN_S = 3
WIEDERHOLEN = 2
MAX_TAGE = 7
REGEN_AB_PROZENT = 50          # ab hier gilt eine Stunde als „wahrscheinlich Regen“

# WMO-Wettercodes, wie Open-Meteo sie liefert.
CODES = {
    0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt",
    45: "Nebel", 48: "Nebel mit Reif",
    51: "leichter Niesel", 53: "Niesel", 55: "starker Niesel",
    56: "gefrierender Niesel", 57: "gefrierender Niesel",
    61: "leichter Regen", 63: "Regen", 65: "starker Regen",
    66: "gefrierender Regen", 67: "gefrierender Regen",
    71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall", 77: "Schneegriesel",
    80: "leichte Regenschauer", 81: "Regenschauer", 82: "heftige Regenschauer",
    85: "Schneeschauer", 86: "starke Schneeschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel",
}


STANDARD_ORT = "Hannover"


class WetterFehler(Exception):
    """Klartext für Friday — kein Stacktrace."""


def heimatort() -> str:
    """Eikes Wohnort aus `FRIDAY_ORT` — steht in der .env, nicht im Code."""
    return os.getenv("FRIDAY_ORT", "").strip() or STANDARD_ORT


def kurz(t: dict) -> str:
    """Ein Tag in einer Zeile: „bedeckt, 11 bis 20 °C, Regen wahrscheinlich ab 15 Uhr“."""
    zeile = f"{t['wetter']}, {round(t['min'])} bis {round(t['max'])} °C"
    if t["regen_ab"]:
        zeile += f", Regen wahrscheinlich ab {t['regen_ab']}"
    elif (t["regen_prozent"] or 0) >= 20:
        zeile += f", Regenrisiko {t['regen_prozent']} %"
    if (t["regen_mm"] or 0) >= 1:
        zeile += f" ({t['regen_mm']:.0f} mm)"
    return zeile


_orte: dict[str, dict] = {}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(ZEITLIMIT, connect=VERBINDEN_S),
                             transport=httpx.AsyncHTTPTransport(retries=WIEDERHOLEN))


def _falten(text: str) -> str:
    return " ".join((text or "").casefold().replace("-", " ").split())


def _bezeichnung(r: dict) -> str:
    """„Musterdorf, Musterhausen“ — der Name samt Gemeinde, damit Verwechslungen auffallen.
    Mit Komma statt Klammer: Gemeinden heißen selbst „Wennigsen (Deister)“."""
    gemeinde = r.get("admin4") or r.get("admin3") or r.get("admin2") or ""
    return f"{r['name']}, {gemeinde}" if gemeinde and gemeinde != r["name"] else r["name"]


def auswaehlen(wunsch: str, treffer: list[dict]) -> dict | None:
    """Den passenden Treffer suchen. „Ort, Gemeinde“ grenzt zuerst ein, danach
    gewinnt ein exakter Namenstreffer vor bloßer Ähnlichkeit. Die Reihenfolge
    zählt: „Holtensen, Wennigsen“ heißt bei Open-Meteo „Holtensen bei Weetzen“
    und fiele durch, würde zuerst nach exaktem Namen gefiltert."""
    name, _, zusatz = wunsch.partition(",")
    name, zusatz = _falten(name), _falten(zusatz)
    if zusatz:
        treffer = [r for r in treffer
                   if any(zusatz in _falten(r.get(f) or "") for f in ("admin4", "admin3", "admin2", "admin1"))]
    exakt = [r for r in treffer if _falten(r.get("name", "")) == name]
    return (exakt or treffer or [None])[0]


async def ort_finden(wunsch: str) -> dict:
    wunsch = (wunsch or "").strip()
    if not wunsch:
        raise WetterFehler("Für welchen Ort?")
    if wunsch in _orte:
        return _orte[wunsch]
    name = wunsch.partition(",")[0].strip()
    try:
        async with _client() as client:
            antwort = await client.get(GEO_URL, params={"name": name, "count": 10, "language": "de",
                                                        "countryCode": "DE"})
        antwort.raise_for_status()
        treffer = antwort.json().get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        raise WetterFehler(f"Die Ortssuche antwortet gerade nicht ({type(exc).__name__}).") from None
    gewaehlt = auswaehlen(wunsch, treffer)
    if not gewaehlt:
        raise WetterFehler(f"Den Ort „{wunsch}“ finde ich nicht.")
    ort = {"name": _bezeichnung(gewaehlt), "breite": gewaehlt["latitude"], "laenge": gewaehlt["longitude"]}
    _orte[wunsch] = ort
    return ort


def _regen_ab(tag: str, zeiten: list[str], wahrscheinlich: list) -> str:
    """Erste Stunde zwischen 6 und 22 Uhr mit mindestens REGEN_AB_PROZENT."""
    for zeit, prozent in zip(zeiten, wahrscheinlich):
        if zeit.startswith(tag) and 6 <= int(zeit[11:13]) <= 22 and (prozent or 0) >= REGEN_AB_PROZENT:
            return f"{int(zeit[11:13])} Uhr"
    return ""


async def vorhersage(wunsch: str, tage: int = 2) -> dict:
    """Tageswerte für die nächsten `tage` Tage, heute eingeschlossen."""
    ort = await ort_finden(wunsch)
    tage = max(1, min(int(tage), MAX_TAGE))
    try:
        async with _client() as client:
            antwort = await client.get(VORHERSAGE_URL, params={
                "latitude": ort["breite"], "longitude": ort["laenge"], "timezone": config.ZEITZONE,
                "forecast_days": tage, "hourly": "precipitation_probability",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max,precipitation_sum"})
        antwort.raise_for_status()
        d = antwort.json()
        tageswerte, stunden = d["daily"], d.get("hourly") or {}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise WetterFehler(f"Der Wetterdienst antwortet gerade nicht ({type(exc).__name__}).") from None
    zeiten, wkt = stunden.get("time") or [], stunden.get("precipitation_probability") or []
    liste = []
    for i, tag in enumerate(tageswerte.get("time") or []):
        code = tageswerte["weather_code"][i]
        liste.append({
            "datum": tag,
            "wetter": CODES.get(code, "unbekannt"),
            "min": tageswerte["temperature_2m_min"][i],
            "max": tageswerte["temperature_2m_max"][i],
            "regen_prozent": tageswerte["precipitation_probability_max"][i],
            "regen_mm": tageswerte["precipitation_sum"][i],
            "regen_ab": _regen_ab(tag, zeiten, wkt),
        })
    return {"ort": ort["name"], "tage": liste}
