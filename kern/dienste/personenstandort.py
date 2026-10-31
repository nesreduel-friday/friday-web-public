"""Personenstandort — wo Familienmitglieder zuletzt waren, wenn SIE es melden.

Die Daten liegen nicht in Friday, sondern im eigenen Container
`friday-personenstandort` (Stack `~/docker/friday-personenstandort`). Die
Familie meldet selbst per OwnTracks-App; dort steht je Person nur der letzte
Punkt. Friday liest ihn über das interne Docker-Netz, mit eigenem Schlüssel.

Warum nicht Apple „Wo ist?“: Es gibt keinen veröffentlichten, wartbaren
Linux-Client für Personenstandorte (25.09.2026) — nur einen Blogpost-Versuch, der
Apples Geräteprüfung nachahmt und Eikes Apple-ID gefährdet.

Hier wird nichts gespeichert; ein Fehler im Container darf das Gespräch nicht
stören (Beiwerk).
"""

from __future__ import annotations

import base64
import json
import os
import re
from urllib.parse import quote

import httpx

KONNEKTOR = "personenstandort"
ZEITLIMIT = 5
VERALTET_S = 3 * 3600        # älter: „zuletzt“, nicht „ist“


def _url() -> str:
    return os.getenv("PERSONENSTANDORT_URL", "").strip() or "http://friday-personenstandort:8131"


def _schluessel() -> str:
    return os.getenv("PERSONENSTANDORT_SCHLUESSEL", "").strip()


async def personen() -> list[dict]:
    """Alle Personen mit letztem Stand. Wirft bei Fehlern (der Aufrufer sagt es in einem Satz)."""
    if not _schluessel():
        raise RuntimeError("PERSONENSTANDORT_SCHLUESSEL fehlt in .env")
    async with httpx.AsyncClient(timeout=ZEITLIMIT) as c:
        r = await c.get(_url() + "/api/personen", headers={"X-Api-Key": _schluessel()})
        r.raise_for_status()
        return list(r.json().get("personen", []))


def finden(name: str, liste: list[dict]) -> list[dict]:
    """Erst genau (Name oder Kennung), dann Anfang, dann Teilwort — jeweils ohne Groß/Klein."""
    q = (name or "").strip().casefold()
    if not q:
        return []
    for passt in (lambda p: q in (p["name"].casefold(), p["kennung"].casefold()),
                  lambda p: p["name"].casefold().startswith(q),
                  lambda p: q in p["name"].casefold()):
        treffer = [p for p in liste if passt(p)]
        if treffer:
            return treffer
    return []


# ---------- Verwalten: Personen für OwnTracks anlegen (Freigaben → Standort der Familie) ----------
#
# Der Container hat dafür eine eigene Tür mit EIGENEM Schlüssel (`VERWALTUNGS_SCHLUESSEL`): Der Lese-Schlüssel darf nichts
# verwalten. Das Passwort einer Person gibt der Container nur beim Anlegen/Erneuern heraus — hier wird es weder gespeichert
# noch geloggt, es geht nur in die Antwort an Eikes App.


class PersonenFehler(Exception):
    """Der Container lehnt ab oder antwortet nicht — der Text ist für Eike gedacht."""


def _verwaltungsschluessel() -> str:
    return os.getenv("PERSONENSTANDORT_VERWALTUNGS_SCHLUESSEL", "").strip()


async def _verwalten(methode: str, pfad: str, daten: dict | None = None) -> dict:
    schluessel = _verwaltungsschluessel()
    if not schluessel:
        raise PersonenFehler("Die Verwaltung ist nicht eingerichtet (PERSONENSTANDORT_VERWALTUNGS_SCHLUESSEL fehlt in .env).")
    try:
        async with httpx.AsyncClient(timeout=ZEITLIMIT) as c:
            r = await c.request(methode, _url() + pfad, headers={"X-Api-Key": schluessel}, json=daten)
    except httpx.HTTPError:
        raise PersonenFehler("Der Personenstandort-Container antwortet nicht (läuft er?).") from None
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code in (200, 201) and isinstance(d, dict):
        return d
    fehler = str(d.get("fehler") or "") if isinstance(d, dict) else ""
    if r.status_code == 401:
        raise PersonenFehler("Der Verwaltungsschlüssel passt nicht zum Container — beide .env gleich setzen und den Container neu starten.")
    raise PersonenFehler(fehler or f"Der Container meldet einen Fehler ({r.status_code}).")


def kennung_aus_namen(name: str) -> str:
    """„Kontakt P Müller“ → „mama_mueller“: Kleinbuchstaben, Ziffern, - und _, mit Buchstabe am Anfang, 2–24 Zeichen."""
    t = (name or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9_-]+", "_", t).strip("_-")
    t = re.sub(r"^[^a-z]+", "", t)[:24].rstrip("_-")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,23}", t):
        raise ValueError("Aus diesem Namen lässt sich keine Kennung bilden — bitte eine angeben (Kleinbuchstaben, 2–24 Zeichen).")
    return t


async def anlegen(kennung: str, name: str) -> dict:
    return await _verwalten("POST", "/api/verwaltung/personen", {"kennung": kennung, "name": name})


async def neues_passwort(kennung: str) -> dict:
    return await _verwalten("POST", f"/api/verwaltung/personen/{kennung}/token", {})


async def pausieren(kennung: str, pause: bool) -> dict:
    return await _verwalten("POST", f"/api/verwaltung/personen/{kennung}/pause", {"pause": bool(pause)})


async def entfernen(kennung: str) -> dict:
    return await _verwalten("DELETE", f"/api/verwaltung/personen/{kennung}")


def owntracks_link(einrichtung: dict) -> str:
    """Ein Tipp richtet OwnTracks ein: `owntracks:///config?inline=<Base64 der Einstellungen>` (HTTP-Modus, Zugang, Bewegungsmodus).
    Der Link trägt das Passwort — er geht nur an Eikes App und wird nirgends gespeichert."""
    kennung = str(einrichtung.get("kennung") or "")
    cfg = {"_type": "configuration", "mode": 3, "url": einrichtung.get("empfangs_url", ""), "auth": True, "username": kennung,
           "password": einrichtung.get("passwort", ""), "deviceId": "handy", "tid": (kennung[:2] or "ph").upper(),
           "monitoring": 2, "locatorDisplacement": 50, "locatorInterval": 180}
    roh = base64.b64encode(json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return "owntracks:///config?inline=" + quote(roh, safe="")


async def verwaltungsliste() -> list[dict]:
    """Die Personen für die Verwaltung: Name, Zustand, wie lange her, wie genau — NIE Koordinaten."""
    return [{"kennung": p.get("kennung", ""), "name": p.get("name", ""), "zustand": p.get("zustand", ""),
             "alter_s": p.get("alter_s"), "genauigkeit_m": p.get("genauigkeit_m"), "akku_prozent": p.get("akku_prozent")}
            for p in await personen()]

