"""ÜSTRA-Garantie — die Schnittstelle hinter dem Online-Formular (elma.gvh.de/ten).

Untersucht am 15.09.2026, Einzelheiten in docs/LEHREN.md. Kurz:
- Anmeldung ohne Konto: `GET /token` gibt in den Kopfzeilen
  `x-transity-authorization` und `x-transity-sessionkey`. Ohne sie: 401.
- Die Stationssuche antwortet DEUTSCHLANDWEIT („Bennigsen" → zuerst „Alt
  Bennebek"). Hier bleiben nur Haltestellen der Region Hannover übrig — dieselben
  Kennungen (`de:03241:…`) wie in der ÜSTRA-Abfahrtstafel.
- Die Fahrtsuche braucht `creatorType=TenMinutes` (`ONLINE_APP` ergab 400) und
  liefert geplante UND tatsächliche Zeiten — der GVH kennt die Verspätung selbst.

Lesende Aufrufe, dazu zwei schreibende: `hochladen` (Ticketbild als
Zwischenanhang) und `einreichen` (der Antrag). Beide ruft NUR
kern/garantie/abschicken.py auf — nach allen Prüfungen, mit eigener Freigabe und
auf Eikes Knopfdruck (von Eike ausdrücklich erlaubt am 16.09.2026). Ein Test wacht
darüber, dass der Abschick-Pfad nirgends sonst auftaucht.

Aufbau der Anfrage: mitgeschnitten am 16.09.2026 mit Testdaten und Sperre im
Browser, Einzelheiten in docs/LEHREN.md.
"""

from __future__ import annotations

import time as uhr
from datetime import datetime, timezone

import httpx

BASIS = "https://elma.gvh.de/ten/api/public"
KOEPFE = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*",
          "Referer": "https://www.uestra.de/", "Origin": "https://www.uestra.de",
          "locale": "de", "Accept-Language": "de"}
ZEITLIMIT = 20
TOKEN_GILT_S = 600
REGION = "de:03241:"          # Region Hannover — Kennungen der ÜSTRA-Haltestellen
FAHRKARTEN = ["Einzelkarte", "Monatskarte", "Jobticket", "Sonstige", "Deutschlandticket"]


class GarantieFehler(Exception):
    """Klartext für Eike und Friday."""


_sitzung: dict = {"kopf": {}, "bis": 0.0}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(ZEITLIMIT, connect=5), headers=KOEPFE,
                             transport=httpx.AsyncHTTPTransport(retries=2))


async def _anmelden(client: httpx.AsyncClient) -> dict:
    antwort = await client.get(f"{BASIS}/token")
    kennung = antwort.headers.get("x-transity-authorization")
    if antwort.status_code != 200 or not kennung:
        raise GarantieFehler("Das Garantie-Formular lässt sich gerade nicht öffnen.")
    _sitzung["kopf"] = {"x-transity-authorization": kennung,
                        "x-transity-sessionkey": antwort.headers.get("x-transity-sessionkey", "")}
    _sitzung["bis"] = uhr.monotonic() + TOKEN_GILT_S
    return _sitzung["kopf"]


async def _holen(pfad: str, parameter: dict):
    try:
        async with _client() as client:
            kopf = _sitzung["kopf"] if uhr.monotonic() < _sitzung["bis"] else await _anmelden(client)
            antwort = await client.get(f"{BASIS}/{pfad}", params=parameter, headers=kopf)
            if antwort.status_code == 401:                 # abgelaufen: genau einmal neu
                antwort = await client.get(f"{BASIS}/{pfad}", params=parameter, headers=await _anmelden(client))
    except httpx.HTTPError as exc:
        raise GarantieFehler(f"Das Garantie-Formular antwortet gerade nicht ({type(exc).__name__}).") from None
    if antwort.status_code != 200:
        raise GarantieFehler(f"Das Garantie-Formular antwortet mit {antwort.status_code}.")
    try:
        return antwort.json()
    except ValueError:
        raise GarantieFehler("Das Garantie-Formular hat unverständlich geantwortet.") from None


async def stationen(name: str) -> list[dict]:
    """Haltestellen der Region Hannover zu einem Namen: {name, reference}."""
    name = (name or "").strip()
    if len(name) < 3:
        return []
    daten = await _holen("search/station", {"stationName": name})
    return [{"name": str(s["name"]), "reference": str(s["reference"])} for s in daten or []
            if isinstance(s, dict) and s.get("name") and str(s.get("reference") or "").startswith(REGION)]


def iso(zeit: datetime) -> str:
    return zeit.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00.000Z")


async def fahrten(start: dict, ziel: dict, zeitpunkt: datetime, ankunft: bool = False) -> list[dict]:
    """Fahrten von start nach ziel ab (bzw. bis) zeitpunkt — so, wie das Formular sucht."""
    daten = await _holen("search/trips", {
        "creatorType": "TenMinutes", "startStationRef": start["reference"], "startStationName": start["name"],
        "endStationRef": ziel["reference"], "endStationName": ziel["name"], "date": iso(zeitpunkt),
        "viaStationsReference": "", "arrival": "true" if ankunft else "false", "scrollingContext": ""})
    return daten if isinstance(daten, list) else []


async def fahrkartenarten(zeitpunkt: datetime) -> list[str]:
    daten = await _holen("ticket/ticket-kinds", {"dateOfEvent": iso(zeitpunkt)})
    return [str(x) for x in daten] if isinstance(daten, list) else FAHRKARTEN


async def fahrkarten_typen(zeitpunkt: datetime, art: str) -> list[str]:
    daten = await _holen("ticket/ticket-types", {"dateOfEvent": iso(zeitpunkt), "ticketKind": art})
    return [str(x) for x in daten] if isinstance(daten, list) else []


async def fahrkarten_details(zeitpunkt: datetime, art: str, typ: str) -> list[str]:
    daten = await _holen("ticket/ticket-details", {"dateOfEvent": iso(zeitpunkt), "ticketKind": art, "ticketType": typ})
    return [str(x) for x in daten] if isinstance(daten, list) else []


async def erstattungsarten(zeitpunkt: datetime, art: str, typ: str, detail: str) -> list[dict]:
    """Je Fahrkarte die Regeln des Formulars samt signierter Kennung (`refundIdSignature`)."""
    daten = await _holen("ticket/price-categories", {"dateOfEvent": iso(zeitpunkt), "ticketKind": art,
                                                     "ticketType": typ, "ticketDetail": detail})
    return [x for x in daten if isinstance(x, dict)] if isinstance(daten, list) else []


# ---------- Schreibend: nur über kern/garantie/abschicken.py ----------

async def _post(pfad: str, **kwargs) -> httpx.Response:
    """Ohne automatische Wiederholung: Ein Antrag darf nie doppelt ankommen."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=5), headers=KOEPFE) as client:
        kopf = _sitzung["kopf"] if uhr.monotonic() < _sitzung["bis"] else await _anmelden(client)
        antwort = await client.post(f"{BASIS}/{pfad}", headers=kopf, **kwargs)
        if antwort.status_code == 401:          # abgewiesen, also nicht angenommen: einmal neu anmelden
            antwort = await client.post(f"{BASIS}/{pfad}", headers=await _anmelden(client), **kwargs)
        return antwort


async def hochladen(name: str, mime: str, inhalt: bytes) -> str:
    """Ein Ticketbild als Zwischenanhang; zurück kommt die Kennung für den Antrag."""
    try:
        antwort = await _post("tmp-attachments", files={"file": (name, inhalt, mime)})
    except httpx.HTTPError as exc:
        raise GarantieFehler(f"Das Ticket ließ sich nicht hochladen ({type(exc).__name__}).") from None
    if antwort.status_code != 200:
        raise GarantieFehler(f"Das Ticket ließ sich nicht hochladen ({antwort.status_code}).")
    # Antwort: {"data": "<Kennung>"} (gemessen 16.09.2026 — vorher wurde das ganze
    # Objekt weitergegeben, und das Formular meldete „Validierung der Anhänge gescheitert“).
    try:
        daten = antwort.json()
    except ValueError:
        daten = None
    kennung = daten.get("data") if isinstance(daten, dict) else daten
    kennung = kennung.strip() if isinstance(kennung, str) else ""
    if not kennung or len(kennung) > 1000:
        raise GarantieFehler("Das Formular hat den Upload nicht bestätigt.")
    return kennung


class UnklarerAusgang(GarantieFehler):
    """Gesendet, aber ohne klare Antwort — vielleicht ist der Antrag angekommen."""


async def einreichen(anfrage: dict) -> dict:
    """Der Antrag. {"id": …} bei Erfolg; bei 400 {"fehler": [...]} (nicht angenommen)."""
    try:
        antwort = await _post("complaint/create", json=anfrage)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise GarantieFehler(f"Das Formular war nicht erreichbar ({type(exc).__name__}) — nichts gesendet.") from None
    except httpx.HTTPError as exc:
        raise UnklarerAusgang(f"Keine Antwort vom Formular ({type(exc).__name__}).") from None
    try:
        daten = antwort.json()
    except ValueError:
        daten = {}
    if antwort.status_code == 200 and isinstance(daten, dict) and daten.get("id"):
        # `template` ist die Eingangsbestätigung, die das Formular nach dem Absenden zeigt.
        return {"id": str(daten["id"]), "signiert": str(daten.get("signedId") or ""),
                "bestaetigung": str(daten.get("template") or "")}
    if antwort.status_code == 400:
        fehler = daten.get("errors") if isinstance(daten, dict) else None
        texte = [str(f.get("errorMessage") or f.get("error") or f)[:200] for f in (fehler or [])
                 if isinstance(f, dict)] or ["Das Formular hat den Antrag abgelehnt."]
        return {"fehler": texte}
    raise UnklarerAusgang(f"Das Formular antwortete mit {antwort.status_code}.")
