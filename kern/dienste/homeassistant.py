"""Home Assistant als Standortquelle — nur lesen.

Home Assistant kennt Eikes Position, wenn sein Handy dort als Gerät oder Person
geführt wird (HA-App, iCloud, OwnTracks …). Friday liest dafür genau EINE
Entität (`GET /api/states/<entität>`) und schreibt nichts, fragt keinen Verlauf
ab und schickt kein Kommando.

Adresse und Token stehen im Konnektor `homeassistant` auf der Freigaben-Seite —
die Adresse im Feld „Adresse“ (der Zugriffsweg aus dem Friday-Container heraus),
das langlebige Zugriffstoken im Feld „Passwort“. Das Token geht nur als
`Authorization: Bearer …` mit, nie in die Adresse, und taucht in keiner
Fehlermeldung auf.

Welche Entität gilt, sagt `HOMEASSISTANT_ENTITAET` (Standard `person.eike`;
ein kurzer Name wie `eike` wird zu `person.eike`). Fehler hier dürfen den
Aufrufer nicht kosten: Für die Ankunftserkennung ist Home Assistant nur eine
Quelle unter mehreren, und ein stiller Ausfall ist besser als ein Absturz.
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone

import httpx

from kern import konnektoren

KONNEKTOR = "homeassistant"
ZEITLIMIT = 5
STANDARD_ENTITAET = "person.eike"
MAX_GENAU_M = 100000
_ENTITAET = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


class HomeAssistantFehler(Exception):
    """Home Assistant antwortet nicht oder lehnt ab — der Text ist für Eike gedacht."""


def entitaet() -> str:
    """Die gelesene Entität: `HOMEASSISTANT_ENTITAET`, Standard `person.eike`."""
    roh = (os.getenv("HOMEASSISTANT_ENTITAET", "").strip().lower() or STANDARD_ENTITAET)
    eid = roh if "." in roh else f"person.{roh}"
    if not _ENTITAET.match(eid):
        raise HomeAssistantFehler(
            f"„{eid}“ ist keine gültige Home-Assistant-Entität — HOMEASSISTANT_ENTITAET z. B. "
            "auf person.eike oder device_tracker.eikes_iphone setzen.")
    return eid


def _ziel() -> tuple[str, str]:
    """(Adresse ohne Schrägstrich am Ende, Token) aus dem Konnektor."""
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise HomeAssistantFehler("Für Home Assistant fehlen Adresse und Zugriffstoken — Freigaben → Home Assistant.")
    adresse = z[0].strip().rstrip("/")
    if not adresse:
        raise HomeAssistantFehler("Für Home Assistant fehlt die Adresse — Freigaben → Home Assistant.")
    return (adresse if "://" in adresse else "http://" + adresse), z[1]


def _zahl(wert, unten: float, oben: float) -> float | None:
    try:
        x = float(wert)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and unten <= x <= oben else None


def _alter_s(wert) -> int | None:
    """Sekunden seit `last_updated` (UTC, ISO mit +00:00 oder Z) — None, wenn unlesbar."""
    if not isinstance(wert, str) or not wert.strip():
        return None
    try:
        t = datetime.fromisoformat(wert.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - t).total_seconds()))


def _aus_state(d: dict) -> dict | None:
    """Ein HA-Zustand als Kandidat für `standort.eigene_kandidaten` — oder None."""
    if not isinstance(d, dict) or d.get("state") in (None, "", "unknown", "unavailable"):
        return None
    a = d.get("attributes") or {}
    breite = _zahl(a.get("latitude"), -90, 90)
    laenge = _zahl(a.get("longitude"), -180, 180)
    if breite is None or laenge is None:
        return None
    alter = _alter_s(d.get("last_updated"))
    if alter is None:
        return None
    # „0 m“ ist keine Messung (dieselbe Regel wie bei der App-Ortung).
    genau = _zahl(a.get("gps_accuracy"), 0, MAX_GENAU_M)
    return {"quelle": "Home Assistant", "breite": breite, "laenge": laenge,
            "genau": round(genau) if genau else None, "alter_s": alter, "geraet": ""}


async def _zustand() -> dict:
    """Den Zustand der eingestellten Entität holen. Wirft `HomeAssistantFehler`."""
    adresse, token = _ziel()
    eid = entitaet()
    try:
        async with httpx.AsyncClient(timeout=ZEITLIMIT) as c:
            r = await c.get(f"{adresse}/api/states/{eid}", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HomeAssistantFehler(f"Home Assistant antwortet nicht ({type(exc).__name__}).") from None
    if r.status_code in (401, 403):
        raise HomeAssistantFehler("Home Assistant lehnt das Token ab — ein neues unter Profil → "
                                  "Sicherheit → Langlebige Zugriffstoken anlegen.")
    if r.status_code == 404:
        raise HomeAssistantFehler(f"Die Entität {eid} gibt es in Home Assistant nicht — "
                                  "HOMEASSISTANT_ENTITAET richtig gesetzt?")
    if r.status_code >= 400:
        raise HomeAssistantFehler(f"Home Assistant meldet einen Fehler ({r.status_code}).")
    try:
        d = r.json()
    except ValueError:
        raise HomeAssistantFehler("Home Assistant antwortete nicht mit JSON.") from None
    if not isinstance(d, dict):
        raise HomeAssistantFehler("Home Assistant antwortete unerwartet.")
    return d


async def position() -> dict | None:
    """Eikes Position aus Home Assistant — oder None, wenn es keine brauchbare gibt.

    Das Ergebnis hat dieselbe Form wie die übrigen Kandidaten von
    `standort.eigene_kandidaten`: Quelle, Koordinaten, Genauigkeit in Metern und
    Alter in Sekunden. Ohne Genauigkeit bleibt `genau` None; die Ankunftserkennung
    wertet das dann bewusst nicht aus.
    """
    return _aus_state(await _zustand())


async def pruefen() -> tuple[bool, str]:
    """(ok, Text) für „Anmeldung prüfen“ auf der Freigaben-Seite — ohne Koordinaten."""
    d = await _zustand()
    p = _aus_state(d)
    eid = entitaet()
    if p is None:
        return False, (f"{eid} meldet gerade keine Position (Zustand „{d.get('state')}“) — "
                       "steht das Handy in Home Assistant auf „zu Hause“ oder „unterwegs“?")
    genau = f"±{p['genau']} m" if p["genau"] is not None else "Genauigkeit unbekannt"
    alter = _vor(p["alter_s"])
    zuletzt = "gerade eben" if alter == "gerade eben" else f"vor {alter}"
    return True, f"Verbunden: {eid} meldet „{d.get('state')}“, zuletzt {zuletzt}, {genau}."


def _vor(sekunden: int) -> str:
    if sekunden < 90:
        return "gerade eben"
    minuten = round(sekunden / 60)
    if minuten < 90:
        return f"{minuten} Minuten"
    return f"{round(minuten / 60)} Stunden"
