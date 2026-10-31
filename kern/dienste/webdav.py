"""IServ-WebDAV — Eikes Dateien auf dem Schulserver, nur lesend.

Adresse: `<Schule>/webdav/` (aus ISERV_URL ohne `/iserv`), Anmeldung mit den
IServ-Zugangsdaten (Basic Auth über HTTPS). Gemessen am 16.09.2026: PROPFIND
mit Depth 1 liefert `getlastmodified` und `getcontentlength`, aber KEIN
ETag — geändert heißt deshalb: anderes Datum oder andere Größe.

Nur PROPFIND und GET. Weist IServ die Daten ab (401), probiert Friday sie
nicht noch einmal, bis sie sich ändern — sonst sperrt IServ das Konto.
"""

from __future__ import annotations

import hashlib
import os
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from kern import konnektoren

KONNEKTOR = "goodnotes"
ZEITLIMIT = 120.0
MAX_TIEFE = 8
DAV = "{DAV:}"

_abgewiesen: str | None = None


class AnmeldeFehler(RuntimeError):
    pass


def basis() -> str:
    """https://schule.de/iserv → https://schule.de"""
    url = os.getenv("ISERV_URL", "https://schule.example/iserv").rstrip("/")
    teile = urlsplit(url)
    return f"{teile.scheme}://{teile.netloc}"


def _zugang() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise AnmeldeFehler("Für IServ sind noch keine Zugangsdaten eingetragen (Freigabe „Schulmanager“).")
    fp = hashlib.sha256(f"{z[0]}\0{z[1]}".encode()).hexdigest()
    if fp == _abgewiesen:
        raise AnmeldeFehler("IServ hat diese Zugangsdaten für WebDAV abgewiesen. Ich probiere sie nicht noch "
                            "einmal, bis sie auf der Freigaben-Seite neu eingetragen sind.")
    return z


def _pruefen(antwort: httpx.Response, zugang: tuple[str, str]) -> None:
    global _abgewiesen
    if antwort.status_code == 401:
        _abgewiesen = hashlib.sha256(f"{zugang[0]}\0{zugang[1]}".encode()).hexdigest()
        raise AnmeldeFehler("IServ hat die WebDAV-Anmeldung abgewiesen. Ich versuche es bewusst nicht noch einmal.")
    if antwort.status_code >= 400:
        raise RuntimeError(f"WebDAV antwortet {antwort.status_code}")


def vergessen() -> None:
    global _abgewiesen
    _abgewiesen = None


def pfad_url(pfad: str) -> str:
    """„GoodNotes/Biologie eA/x.pdf“ → volle URL, sauber kodiert."""
    return f"{basis()}/webdav/" + quote(pfad.strip("/"), safe="/")


def _zeit(text: str | None) -> str:
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


def eintraege(xml: bytes, eigener: str) -> list[dict]:
    """Eine PROPFIND-Antwort → [{pfad, ordner, groesse, geaendert}] ohne den Ordner selbst.
    `pfad` ist relativ zu /webdav/ und dekodiert."""
    aus = []
    for antwort in ET.fromstring(xml).findall(DAV + "response"):
        href = unquote(antwort.findtext(DAV + "href") or "")
        rel = href.split("/webdav/", 1)[-1].strip("/")
        if rel == eigener.strip("/"):
            continue
        prop = antwort.find(f"{DAV}propstat/{DAV}prop")
        if prop is None:
            continue
        aus.append({"pfad": rel, "ordner": prop.find(f"{DAV}resourcetype/{DAV}collection") is not None,
                    "groesse": int(prop.findtext(DAV + "getcontentlength") or 0),
                    "geaendert": _zeit(prop.findtext(DAV + "getlastmodified"))})
    return aus


async def auflisten(wurzel: str) -> list[dict]:
    """Alle Dateien unter `wurzel`, rekursiv (Ordner für Ordner, Depth 1)."""
    zugang = _zugang()
    dateien = []
    async with httpx.AsyncClient(auth=zugang, timeout=ZEITLIMIT) as client:
        offen = [(wurzel.strip("/"), 0)]
        while offen:
            ordner, tiefe = offen.pop()
            antwort = await client.request("PROPFIND", pfad_url(ordner) + "/", headers={"Depth": "1"})
            _pruefen(antwort, zugang)
            for e in eintraege(antwort.content, ordner):
                if e["ordner"]:
                    if tiefe < MAX_TIEFE:
                        offen.append((e["pfad"], tiefe + 1))
                else:
                    dateien.append(e)
    return sorted(dateien, key=lambda e: e["pfad"])


async def unterordner(wurzel: str) -> list[str]:
    """Nur die Ordner direkt unter `wurzel` (eine Abfrage, Depth 1) — Pfade relativ zu /webdav/."""
    zugang = _zugang()
    async with httpx.AsyncClient(auth=zugang, timeout=ZEITLIMIT) as client:
        antwort = await client.request("PROPFIND", pfad_url(wurzel) + "/", headers={"Depth": "1"})
    _pruefen(antwort, zugang)
    return sorted(e["pfad"] for e in eintraege(antwort.content, wurzel) if e["ordner"])


async def laden(pfad: str, ziel: Path) -> int:
    """Datei nach `ziel` laden (erst .neu, dann umbenennen). Gibt die Größe zurück."""
    zugang = _zugang()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(ziel.suffix + ".neu")
    groesse = 0
    async with httpx.AsyncClient(auth=zugang, timeout=ZEITLIMIT) as client:
        async with client.stream("GET", pfad_url(pfad)) as antwort:
            _pruefen(antwort, zugang)
            with open(neu, "wb") as f:
                async for stueck in antwort.aiter_bytes(1024 * 1024):
                    f.write(stueck)
                    groesse += len(stueck)
    os.replace(neu, ziel)
    return groesse


async def pruefen(wurzel: str) -> str:
    """Eine einzige Abfrage: gibt es den Ordner, und was liegt darin?"""
    zugang = _zugang()
    async with httpx.AsyncClient(auth=zugang, timeout=ZEITLIMIT) as client:
        antwort = await client.request("PROPFIND", pfad_url(wurzel) + "/", headers={"Depth": "1"})
    if antwort.status_code == 404:
        raise RuntimeError(f"Den Ordner „{wurzel}“ gibt es im IServ-WebDAV nicht.")
    _pruefen(antwort, zugang)
    inhalt = eintraege(antwort.content, wurzel)
    ordner = sum(1 for e in inhalt if e["ordner"])
    return f"Angemeldet. Im Ordner „{wurzel}“ liegen {ordner} Unterordner und {len(inhalt) - ordner} Dateien."
