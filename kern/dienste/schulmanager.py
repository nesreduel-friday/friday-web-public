"""Schulmanager Online über die IServ-Anmeldung — ohne Browser.

Übernommen aus dem alten Friday (`dienste/schulmanager.py`, dort geprüft am
04.09.2026 mit echten Daten), hier auf httpx und async umgestellt. Die Kette:

  1. POST aufs IServ-Formular (`_username`, `_password`, kein CSRF-Token)
  2. `<meta refresh>` folgen — httpx folgt nur HTTP-Weiterleitungen
  3. SSO-Einstieg im IServ-Menü SUCHEN (`/oidc/<Nummer der Schule>`) und folgen
  4. GET `/oidc/get-jwt` → 204, der Token steht im Kopf `X-New-Bearer-Token`
     (das Sitzungs-Cookie allein reicht der API nicht)
  5. POST `/api/calls` mit `Authorization: Bearer …`

Zwei Regeln schützen das Schulkonto:
  - Genau EIN Anmeldeversuch, keine Schleife. Wiederholte Fehlschläge sperren es.
  - Wurden Zugangsdaten abgewiesen, probiert Friday sie nicht noch einmal,
    bis Eike neue einträgt (`vergessen()`).

Zugangsdaten kommen nur aus `kern.konnektoren` — nie als Werkzeug-Angabe.
Hier stehen nur Rohdaten; vorlesbar macht sie `kern/werkzeuge/schule.py`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import time
from datetime import date
from urllib.parse import urlsplit

import httpx

from kern import konnektoren
from kern.dienste import netz

log = logging.getLogger("friday.schulmanager")

KONNEKTOR = "schulmanager"
ISERV_BASIS = os.getenv("ISERV_URL", "https://schule.example/iserv").rstrip("/")
SCHULMANAGER_BASIS = "https://login.schulmanager-online.de"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ZEITLIMIT = 30
# Schickt die Weboberfläche mit; ob der Server ihn prüft, ist ungeklärt.
# Bei einem Schulmanager-Update der erste Verdächtige.
BUNDLE_VERSION = os.getenv("SCHULMANAGER_BUNDLE", "cec66cfa9e")
# Token wiederverwenden: die volle Kette dauert Sekunden und belastet den Schulserver.
TOKEN_GILT_S = 45 * 60
ANF = "\"'"

MAX_ANHANG_BYTES = 15 * 1024 * 1024

_sitzung: dict | None = None
_speicher_url: str | None = None
_abgewiesen: str | None = None      # Fingerabdruck abgewiesener Zugangsdaten
_sperre: asyncio.Lock | None = None


class AnmeldeFehler(RuntimeError):
    """Anmeldung nicht möglich. Die Nachricht sagt, woran es lag — vorlesbar."""


def _lock() -> asyncio.Lock:
    global _sperre
    if _sperre is None:
        _sperre = asyncio.Lock()
    return _sperre


def _fingerabdruck(benutzer: str, passwort: str) -> str:
    return hashlib.sha256(f"{benutzer}\0{passwort}".encode()).hexdigest()


def zugang() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise AnmeldeFehler("Für den Schulmanager sind noch keine Zugangsdaten eingetragen.")
    return z


def vergessen() -> None:
    """Neue Zugangsdaten: alte Sitzung weg, Sperre aufheben."""
    global _sitzung, _abgewiesen, _stundenzeiten
    _sitzung, _abgewiesen = None, None
    _stundenzeiten = None      # könnte zu einer anderen Schule gehören


def _formularfelder(html: str) -> dict[str, str]:
    """Versteckte Felder mitschicken — falls IServ doch einmal ein CSRF-Token einführt."""
    felder = {}
    for treffer in re.finditer(r"<input[^>]+>", html, re.I):
        tag = treffer.group(0)
        if not re.search(r'type=["\']hidden["\']', tag, re.I):
            continue
        name = re.search(r'name=["\']([^"\']+)["\']', tag, re.I)
        wert = re.search(r'value=["\']([^"\']*)["\']', tag, re.I)
        if name:
            felder[name.group(1)] = wert.group(1) if wert else ""
    return felder


def _sieht_nach_login_aus(html: str, url: str) -> bool:
    """Nur der PFAD zählt: nach Erfolg steht "/login" im Query-Teil
    (`?_iserv_app_url=/iserv/login`) — darauf fiel das alte Friday zuerst herein."""
    pfad = urlsplit(url or "").path.lower()
    if pfad.endswith("/login") or pfad.endswith("/auth/login"):
        return True
    return 'name="_password"' in (html or "").lower()


def iserv_meldung(html: str) -> str:
    """Der Grund, den IServ auf die Anmeldeseite schreibt ("Falsches Passwort",
    "Account gesperrt" …) — damit Eike sieht, ob es an den Daten liegt."""
    for m in re.finditer(r'<(div|p|span|li)[^>]*class=["\'][^"\']*(alert|error|invalid-feedback|text-danger)'
                         r'[^"\']*["\'][^>]*>(.*?)</\1>', html or "", re.I | re.S):
        text = " ".join(re.sub(r"<[^>]+>", " ", m.group(3)).split())
        if text:
            return text[:200]
    return ""


async def _meta_refresh_folgen(client: httpx.AsyncClient, antwort: httpx.Response, runden: int = 5):
    for _ in range(runden):
        m = re.search(rf"http-equiv=[{ANF}]refresh[{ANF}][^>]*content=[{ANF}][^{ANF}]*url=([^{ANF}]+)",
                      antwort.text, re.I)
        if not m:
            break
        antwort = await client.get(antwort.url.join(m.group(1).replace("&amp;", "&")))
    return antwort


def _sso_verweis(html: str) -> str:
    for ziel in re.findall(rf"href=[{ANF}]([^{ANF}]+)[{ANF}]", html, re.I):
        if "schulmanager-online.de/oidc/" in ziel.lower():
            return ziel.replace("&amp;", "&")
    return ""


async def _anmelden() -> dict:
    global _abgewiesen
    benutzer, passwort = zugang()
    fp = _fingerabdruck(benutzer, passwort)
    if _abgewiesen == fp:
        raise AnmeldeFehler("IServ hat diese Zugangsdaten abgewiesen. Bitte auf der Freigaben-Seite neu "
                            "eintragen — ich probiere sie nicht noch einmal, sonst sperrt IServ das Konto.")
    client = netz.client(follow_redirects=True, timeout=ZEITLIMIT, headers={"User-Agent": USER_AGENT})
    try:
        seite = await client.get(f"{ISERV_BASIS}/login")
        if seite.status_code >= 400:
            raise AnmeldeFehler(f"Die IServ-Anmeldeseite antwortet nicht ({seite.status_code}).")
        daten = _formularfelder(seite.text)
        daten.update({"_username": benutzer, "_password": passwort, "_remember_me": "on"})
        antwort = await client.post(str(seite.url), data=daten)
        if _sieht_nach_login_aus(antwort.text, str(antwort.url)):
            _abgewiesen = fp
            grund = iserv_meldung(antwort.text)
            log.warning("IServ-Anmeldung abgewiesen (%s, %s)", antwort.status_code, grund or "ohne Meldung")
            raise AnmeldeFehler("IServ hat die Anmeldung abgewiesen"
                                + (f" und sagt: „{grund}“." if grund else " — Benutzername oder Passwort stimmen nicht.")
                                + " Ich versuche es bewusst nicht noch einmal.")

        start = await _meta_refresh_folgen(client, await client.get(f"{ISERV_BASIS}/"))
        verweis = _sso_verweis(start.text)
        if not verweis:
            nummer = os.getenv("SCHULMANAGER_INSTITUTION", "").strip()
            if not nummer:
                raise AnmeldeFehler("Den Schulmanager-Eintrag im IServ-Menü finde ich nicht.")
            verweis = f"{SCHULMANAGER_BASIS}/oidc/{nummer}"
        await _meta_refresh_folgen(client, await client.get(verweis))

        jwt = await client.get(f"{SCHULMANAGER_BASIS}/oidc/get-jwt")
        token = jwt.headers.get("X-New-Bearer-Token", "").strip()
        if not token:
            raise AnmeldeFehler(f"Schulmanager gibt keinen Zugang heraus ({jwt.status_code}).")
        status = (await client.post(f"{SCHULMANAGER_BASIS}/api/login-status", json={},
                                    headers={"Authorization": f"Bearer {token}"})).json()
        if not status.get("isAuthenticated"):
            raise AnmeldeFehler("Schulmanager hat die Anmeldung nicht angenommen.")
        log.info("Beim Schulmanager angemeldet")
        return {"client": client, "token": token, "benutzer": status.get("user") or {},
                "zeit": time.monotonic(), "fp": fp}
    except BaseException:
        await client.aclose()
        raise


async def anmeldung(erneuern: bool = False) -> dict:
    """Sitzung mit Token und Benutzer — zwischengespeichert, nie parallel angemeldet."""
    global _sitzung
    async with _lock():
        benutzer, passwort = zugang()
        s = _sitzung
        if (s and not erneuern and s["fp"] == _fingerabdruck(benutzer, passwort)
                and time.monotonic() - s["zeit"] < TOKEN_GILT_S):
            return s
        if s:
            _sitzung = None
            try:
                await s["client"].aclose()
            except Exception:
                pass
        _sitzung = await _anmelden()
        return _sitzung


async def api(anfragen: list[dict]) -> list[dict]:
    """`/api/calls` — mehrere Anfragen in einem Aufruf, wie die Weboberfläche.
    Läuft der Token ab (401), wird GENAU EINMAL neu angemeldet."""
    for versuch in (1, 2):
        s = await anmeldung(erneuern=versuch == 2)
        a = await s["client"].post(
            f"{SCHULMANAGER_BASIS}/api/calls",
            json={"bundleVersion": BUNDLE_VERSION, "requests": anfragen},
            headers={"Authorization": f"Bearer {s['token']}", "Accept": "application/json, text/plain, */*"})
        if a.status_code == 401:
            if versuch == 1:
                continue
            raise AnmeldeFehler("Schulmanager nimmt den Zugang nicht mehr an.")
        a.raise_for_status()
        d = a.json()
        return d.get("results", d) if isinstance(d, dict) else d
    return []


async def schueler() -> dict:
    s = await anmeldung()
    st = s["benutzer"].get("associatedStudent")
    if not st:
        raise AnmeldeFehler("Am Konto hängt kein Schülerprofil.")
    return st


async def abfrage(modul: str, endpunkt: str, parameter: dict | None = None):
    """Eine Anfrage; `/api/calls` meldet pro Anfrage einen eigenen Status."""
    ergebnis = await api([{"moduleName": modul, "endpointName": endpunkt, "parameters": parameter or {}}])
    erstes = (ergebnis or [{}])[0] or {}
    status = erstes.get("status", 200)
    if status != 200:
        raise RuntimeError(f"Schulmanager {modul}/{endpunkt} antwortet mit {status}")
    return erstes.get("data")


async def stundenplan(start: date, ende: date) -> list[dict]:
    st = await schueler()
    stunden = await abfrage("schedules", "get-actual-lessons",
                            {"student": st, "start": start.isoformat(), "end": ende.isoformat()}) or []
    # Uhrzeiten nachtragen: Der Stundenplan liefert je Stunde nur Nummer und id,
    # die Zeiten stehen in einem eigenen Endpunkt. `schule.stunde_zeile` zeigt
    # `from`/`until` längst an — es fehlten nur die Daten, und Friday schloss
    # daraus, sie könne die Zeiten nicht auslesen (14.09.2026).
    try:
        zeiten = await stundenzeiten()
    except Exception:                       # ohne Zeiten lieber Plan als gar nichts
        zeiten = []
    nach_id = {z["id"]: z for z in zeiten if z.get("id") is not None}
    nach_nummer = {z["nummer"]: z for z in zeiten}
    for e in stunden:
        std = e.get("classHour") or {}
        z = nach_id.get(std.get("id")) or nach_nummer.get(str(std.get("number") or "").strip())
        if z and not std.get("from"):
            std["from"], std["until"] = z["von"], z["bis"]
            e["classHour"] = std
    return sorted(stunden, key=lambda e: (e.get("date", ""),
                                          int((e.get("classHour") or {}).get("number", 0) or 0)))


_stundenzeiten: list[dict] | None = None


async def stundenzeiten() -> list[dict]:
    """Anfang und Ende jeder Schulstunde.

    Der Stundenplan liefert je Stunde nur `classHour: {id, number}`. Daraus
    schloss Friday am 14.09.2026, die Uhrzeiten seien nicht auslesbar — und
    erfand daraufhin welche. Sie sind sehr wohl auslesbar:
    `schedules/get-class-hours` gibt `from` und `until` je Stunde (gemessen:
    6. Stunde 12:45–13:30, genau das, was Eike selbst gesagt hatte).

    Ändert sich praktisch nie, deshalb einmal je Prozess geholt.
    """
    global _stundenzeiten
    if _stundenzeiten is None:
        roh = await abfrage("schedules", "get-class-hours") or []
        _stundenzeiten = sorted(
            [{"id": s.get("id"), "nummer": str(s.get("number") or "").strip(),
              "von": str(s.get("from") or "")[:5], "bis": str(s.get("until") or "")[:5]}
             for s in roh if s.get("from") and s.get("until")],
            key=lambda s: int(s["nummer"]) if s["nummer"].isdigit() else 99)
    return _stundenzeiten


async def elternbriefe() -> list[dict]:
    """Titel, Datum und Lesestatus — ohne Text. Den holt `elternbrief()`."""
    st = await schueler()
    return await abfrage("letters", "get-letters", {"student": st}) or []


async def anhang_laden(datei: str) -> bytes:
    """Anhang herunterladen — so wie die Oberfläche (`getDownloadUrl`, 12.09.2026):
    Speicheradresse von `get-remote-storage-url`, dann `/download-file/` plus das
    `file`-Feld in Base64. Kommt schon entschlüsselt zurück (geprüft: PDF, 500 kB)."""
    global _speicher_url
    if not datei:
        raise RuntimeError("Anhang ohne Datei")
    if not _speicher_url:
        _speicher_url = str(await abfrage("main", "get-remote-storage-url") or SCHULMANAGER_BASIS).rstrip("/")
    s = await anmeldung()
    a = await s["client"].get(f"{_speicher_url}/download-file/" + base64.b64encode(datei.encode()).decode())
    if a.status_code != 200:
        raise RuntimeError(f"Anhang nicht ladbar ({a.status_code})")
    if len(a.content) > MAX_ANHANG_BYTES:
        raise RuntimeError("Anhang zu groß")
    return a.content


async def elternbrief(brief_id: int, mit_dateien: bool = False) -> dict | None:
    """Ein Brief mit Text (HTML) und Anhangliste.

    Gefunden am 12.09.2026 im JavaScript der Oberfläche: die Schüleransicht
    fragt das Modell über den allgemeinen Endpunkt `poqa` ab — Modellname
    `modules/letters/letter` (Präfix + kebab-case, siehe Models Service),
    Aktion `findByPk`. Geratene Endpunktnamen waren alle 404. Der Filter auf
    die eigene Schüler-ID sorgt dafür, dass nur eigene Briefe kommen. Nur
    lesen: als gelesen markiert die Oberfläche mit einem eigenen Aufruf, den
    Friday nicht macht.
    """
    st = await schueler()
    optionen = {"include": [
        {"association": "attachments", "required": False,
         "attributes": ["id", "filename", "contentType", "inline", "letterId"] + (["file"] if mit_dateien else [])},
        {"association": "studentStatuses", "required": True, "where": {"studentId": {"$in": [st["id"]]}}}]}
    return await abfrage("letters", "poqa", {
        "action": {"model": "modules/letters/letter", "action": "findByPk", "parameters": [int(brief_id), optionen]},
        "uiState": "main.modules.letters.view.details"})


async def hausaufgaben() -> list[dict]:
    """Der Server ignoriert start/end und liefert alles — gefiltert wird im Werkzeug.
    `date` ist der Tag, an dem die Aufgabe GESTELLT wurde; einen Abgabetermin gibt es nicht."""
    st = await schueler()
    return await abfrage("classbook", "get-homework", {"student": st}) or []
