"""Gemini für Bücher und Hausaufgaben — mit dem eigenen Schlüssel
(`HAUSAUFGABEN_GEMINI_API_KEY`), sonst den Hintergrund-Schlüsseln.

Ein Modell nach dem anderen, erst auf dem ersten Schlüssel, dann auf dem
nächsten. Eine kaputte Anfrage (400) bricht sofort ab — da hilft kein anderes
Modell.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re

import httpx

from kern import config, schluessel as schluesselstand
from kern.verbrauch import tokens as tokenzaehler

log = logging.getLogger(__name__)
ZEITLIMIT = 180.0
# 503 = Modell überlastet, meist nur kurz: zweimal nach einer Pause noch einmal versuchen.
PAUSEN_503 = (3, 8)
# Messung 16.09.2026: 3.8-flash las Buchseiten und rief Werkzeuge mit Bild-Antwort korrekt auf.
MODELLE = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash"]
BILDMODELLE = ["gemini-3.1-flash-image", "gemini-2.5-flash-image"]


class KiFehler(RuntimeError):
    pass


def verstaendlich(exc: Exception) -> str:
    """Fehlertext für die Oberfläche: erst was los ist, dann der technische Rest."""
    text = str(exc)
    hinweis = None
    if isinstance(exc, KiFehler):
        if re.search(r"\b503\b", text):
            hinweis = "Google ist gerade überlastet — später noch einmal versuchen."
        elif re.search(r"\b429\b", text):
            hinweis = "Das Google-Kontingent ist gerade aufgebraucht — später oder morgen ab 9:05 Uhr noch einmal versuchen."
    return f"{hinweis} ({text})" if hinweis else f"{type(exc).__name__}: {text}"


def schluessel(liste: list[str] | None = None) -> list[str]:
    liste = config.hausaufgaben_schluessel_liste() if liste is None else liste
    if not liste:
        raise KiFehler("Kein Gemini-Schlüssel hinterlegt")
    return liste


async def senden(client: httpx.AsyncClient, modell: str, schl: str, inhalt: dict) -> dict:
    """Eine Anfrage. Gibt den ersten Kandidaten zurück; wirft KiFehler mit Status."""
    for pause in (*PAUSEN_503, None):
        try:
            a = await client.post(f"{config.gemini_rest()}/models/{modell}:generateContent",
                                  json=inhalt, headers={"x-goog-api-key": schl})
        except httpx.HTTPError as exc:
            raise KiFehler(f"{modell}: {type(exc).__name__}") from exc
        if a.status_code != 503 or pause is None:
            break
        await asyncio.sleep(pause)
    if a.status_code == 429:
        schluesselstand.limit_melden(schl, modell, a.text)
    if a.status_code != 200:
        log.warning("Gemini %s auf Schlüssel %s: %s %s", modell, schluesselstand.name_von(schl) or "?",
                    a.status_code, config.schwaerzen(" ".join(a.text.split()))[:300])
        fehler = KiFehler(f"{modell}: {a.status_code}")
        fehler.status = a.status_code
        raise fehler
    schluesselstand.erfolg_melden(schl, modell)
    tokenzaehler.zaehlen(modell, a.json(), "schule")
    kandidat = (a.json().get("candidates") or [{}])[0]
    if not (kandidat.get("content") or {}).get("parts"):
        raise KiFehler(f"{modell}: leere Antwort ({kandidat.get('finishReason', '?')})")
    return kandidat


def text_aus(kandidat: dict) -> str:
    return "".join(p.get("text", "") for p in kandidat["content"]["parts"] if not p.get("thought"))


async def json_anfrage(teile: list[dict], anweisung: str, schema: dict,
                       modelle: list[str] | None = None,
                       schluessel_liste: list[str] | None = None) -> tuple[dict, str]:
    inhalt = {"systemInstruction": {"parts": [{"text": anweisung}]},
              "contents": [{"role": "user", "parts": teile}],
              "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema,
                                   "temperature": 0.1}}
    letzter = "kein Modell"
    async with httpx.AsyncClient(timeout=ZEITLIMIT) as client:
        for schl in schluessel(schluessel_liste):
            for modell in modelle or MODELLE:
                try:
                    kandidat = await senden(client, modell, schl, inhalt)
                    return json.loads(text_aus(kandidat)), modell
                except KiFehler as exc:
                    letzter = str(exc)
                    if getattr(exc, "status", None) == 400:
                        raise
                except ValueError:
                    letzter = f"{modell}: kein JSON"
    raise KiFehler(config.schwaerzen(f"Gemini hat nicht geantwortet ({letzter})"))


async def bild(beschreibung: str, schluessel_liste: list[str] | None = None) -> tuple[bytes, str]:
    """Ein Bild erzeugen → (Bytes, MIME-Typ). Wirft KiFehler, wenn kein Modell kann."""
    inhalt = {"contents": [{"role": "user", "parts": [{"text": beschreibung}]}],
              "generationConfig": {"responseModalities": ["IMAGE"]}}
    letzter = "kein Modell"
    async with httpx.AsyncClient(timeout=ZEITLIMIT) as client:
        for schl in schluessel(schluessel_liste):
            for modell in BILDMODELLE:
                try:
                    kandidat = await senden(client, modell, schl, inhalt)
                except KiFehler as exc:
                    letzter = str(exc)
                    continue
                for p in kandidat["content"]["parts"]:
                    daten = p.get("inlineData") or {}
                    if daten.get("data"):
                        return base64.b64decode(daten["data"]), daten.get("mimeType") or "image/png"
                letzter = f"{modell}: kein Bild in der Antwort"
    if letzter.endswith(": 429"):
        letzter += " — für Bilder hat dieser Schlüssel kein Kontingent"
    raise KiFehler(config.schwaerzen(f"Bild nicht erzeugt ({letzter})"))
