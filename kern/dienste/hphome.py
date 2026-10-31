"""hphome — den Server ansehen und verwalten (Extras → hphome). Die Friday-Seite.

Friday steckt gehärtet im Container und kann selbst nichts auf dem Host tun. Dieses Modul liest, was der
Helfer auf hphome (`host-helfer/hphome_helfer.py`) nach `data/hphome/zustand.json` schreibt, und legt
Aufträge als Datei ab (`auftrag.json`); der Helfer führt sie aus und antwortet im Zustand. Läuft der
Helfer nicht, veraltet der Zustand — dann sagt die Seite das, statt zu warten.

**Kein Werkzeug fürs Modell** für Neustart, Stopp, Start, Logs, Host-Neustart und Platz schaffen:
Hier führt nur ein Tipp von Eike in der App etwas aus. Das Modell darf nur lesen (`werkzeuge/hphome.py`).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from kern import config

NAME_OK = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
# Älter heißt: der Helfer läuft nicht mehr. Eine Runde des Helfers kann bei hängendem Docker ~50 s dauern
# (docker ps 20 s + inspect 30 s + tailscale 15 s) — darunter flackerte „Helfer aus“.
FRISCH_S = 90
TAKT_S = 0.25
# So lange warten wir auf die Antwort des Helfers — je nach Aktion. Platz schaffen räumt Hunderte Cache-Einträge;
# der Host-Neustart muss eine langsame Runde abwarten können, sonst gäbe es ein 504 und der Neustart liefe trotzdem.
ZEITLIMIT_S = {"logs": 30.0, "neustart": 90.0, "stopp": 90.0, "start": 60.0,
               "host_neustart": 90.0, "platz_schaffen": 330.0, "handy_start": 60.0, "bot_modell": 150.0}

# Bekannte Dienste: Containername → Titel und Weboberfläche. Reihenfolge = Reihenfolge auf der Seite.
DIENSTE: dict[str, dict] = {
    "jellyfin": {"titel": "Jellyfin", "port": 8096, "info": "Filme und Serien ansehen"},
    "sonarr": {"titel": "Sonarr", "port": 8989, "info": "Serien verwalten"},
    "radarr": {"titel": "Radarr", "port": 7878, "info": "Filme verwalten"},
    "prowlarr": {"titel": "Prowlarr", "port": 9696, "info": "Suchquellen für Sonarr und Radarr"},
    "qbittorrent": {"titel": "qBittorrent", "port": 8080, "info": "Downloads"},
    "portainer": {"titel": "Portainer", "port": 9443, "schema": "https", "info": "Docker-Oberfläche (Zertifikatswarnung ist normal)"},
    "adguardhome": {"titel": "AdGuard Home", "port": 80, "info": "DNS-Filter im Heimnetz"},
    "immich_server": {"titel": "Immich", "port": 2283, "info": "Fotos und Videos ansehen und sichern"},
    "homeassistant": {"titel": "Home Assistant", "port": 8123, "info": "Smarthome-Zentrale (nur im Tailnet erreichbar)"},
    "heimdall": {"titel": "Heimdall", "port": 8090, "info": "Startseite mit Verknüpfungen zu den Diensten"},
}
# Kein Container, aber ein Dienst auf hphome: Cockpit verwaltet den Server im Browser (Terminal, Dienste, Updates).
EXTRA: list[dict] = [
    {"titel": "Cockpit", "port": 9090, "info": "Server im Browser verwalten, mit Terminal (Anmeldung mit dem Linux-Benutzer)"},
]
NICHT_STOPPEN = {"friday"}


class HphomeFehler(Exception):
    """Der Text ist für Eike gedacht; `status` wird zum HTTP-Status der Route."""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def _ordner() -> Path:
    return config.daten_ordner() / "hphome"


def _lesen() -> dict | None:
    try:
        d = json.loads((_ordner() / "zustand.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _alter(d: dict) -> float:
    return time.time() - float(d.get("aktualisiert") or 0)


def zustand_roh() -> dict | None:
    """Der frische Zustand des Helfers — oder `None`, wenn er fehlt oder veraltet ist (dann weiß Friday nichts)."""
    d = _lesen()
    return d if d is not None and _alter(d) <= FRISCH_S else None


def _link(d: dict) -> str:
    host = urlparse(config.basis_url()).hostname or "localhost"
    return f"{d.get('schema', 'http')}://{host}:{d['port']}{d.get('pfad', '/')}"


def _anreichern(c: dict) -> dict:
    bekannt = DIENSTE.get(c.get("name"))
    return {**c, "titel": bekannt["titel"] if bekannt else c.get("name", ""), "info": bekannt["info"] if bekannt else "",
            "link": _link(bekannt) if bekannt else None, "bekannt": bool(bekannt),
            "darf_stoppen": c.get("name") not in NICHT_STOPPEN}


def _reihenfolge(c: dict):
    namen = list(DIENSTE)
    n = c.get("name", "")
    return (0, namen.index(n), n) if n in DIENSTE else (1, 0, n)


def _extra() -> list[dict]:
    return [{**e, "link": _link(e)} for e in EXTRA]


def zustand() -> dict:
    """Was die Seite braucht. Ohne `letzte_antwort` (dort können Logs stehen)."""
    d = _lesen()
    if d is None:
        return {"helfer": False, "text": "Der Helfer auf hphome läuft nicht (host-helfer/hphome_helfer.py).",
                "container": [], "extra": _extra()}
    frisch = _alter(d) <= FRISCH_S
    z = {k: v for k, v in d.items() if k != "letzte_antwort"}
    z["helfer"] = frisch
    if not frisch:
        z["text"] = f"Der Helfer meldet sich seit {int(_alter(d))} s nicht mehr."
    z["container"] = [_anreichern(c) for c in sorted(d.get("container") or [], key=_reihenfolge)]
    z["extra"] = _extra()
    return z


# ---------------------------------------------------------------- Aufträge

_sperre = threading.Lock()


def _antwort_abwarten(auftrag_id: str, limit: float) -> dict:
    ende = time.time() + limit
    while time.time() < ende:
        time.sleep(TAKT_S)
        d = _lesen() or {}
        antwort = d.get("letzte_antwort") or {}
        if antwort.get("id") == auftrag_id:
            return {"ok": bool(antwort.get("ok")), "text": str(antwort.get("text") or "")}
    # Den Auftrag zurückziehen, damit ihn kein (später startender) Helfer noch ausführt. unlink ist atomar gegenüber
    # dem Umbenennen des Helfers: entweder liegt er noch (dann ist er jetzt weg) oder der Helfer hat ihn schon.
    try:
        os.unlink(_ordner() / "auftrag.json")
    except OSError:                           # meist FileNotFoundError: schon abgeholt
        raise HphomeFehler("Der Helfer arbeitet vielleicht noch — schau gleich nach, was passiert ist.", 504) from None
    raise HphomeFehler("Nicht ausgeführt — der Helfer hat nicht reagiert.", 504)


def _auftrag(aktion: str, name: str = "", wert: str = "") -> dict:
    if zustand_roh() is None:
        raise HphomeFehler("Der Helfer auf hphome läuft nicht — ohne ihn geht das nicht.", 503)
    if not _sperre.acquire(blocking=False):
        raise HphomeFehler("Es läuft schon eine Aktion — einen Moment.", 409)
    try:
        ordner = _ordner()
        ordner.mkdir(parents=True, exist_ok=True)
        datei = ordner / "auftrag.json"
        if datei.exists():
            raise HphomeFehler("Der Helfer ist noch beschäftigt — einen Moment.", 409)
        auftrag_id = secrets.token_hex(8)
        tmp = ordner / "auftrag.tmp"
        tmp.write_text(json.dumps({"id": auftrag_id, "aktion": aktion, "name": name, "wert": wert},
                                  ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(datei)
        return _antwort_abwarten(auftrag_id, ZEITLIMIT_S[aktion])
    finally:
        _sperre.release()


def _bekannt(name: str) -> None:
    if not isinstance(name, str) or not NAME_OK.fullmatch(name):
        raise HphomeFehler("Ungültiger Containername.", 400)
    d = _lesen() or {}
    if name not in {c.get("name") for c in d.get("container") or []}:
        raise HphomeFehler(f"Einen Container „{name}“ gibt es nicht.", 404)


def container_aktion(name: str, aktion: str) -> dict:
    if aktion not in ("neustart", "stopp", "start"):
        raise HphomeFehler("Unbekannte Aktion.", 400)
    _bekannt(name)
    if aktion == "stopp" and name in NICHT_STOPPEN:
        raise HphomeFehler(f"„{name}“ lässt sich hier nicht stoppen — sonst gäbe es keine Oberfläche mehr, "
                           "um es zu starten.", 400)
    return _auftrag(aktion, name)


def logs(name: str) -> dict:
    _bekannt(name)
    return _auftrag("logs", name)


def host_neustart() -> dict:
    return _auftrag("host_neustart")


def platz_schaffen() -> dict:
    return _auftrag("platz_schaffen")


def handy_start() -> dict:
    return _auftrag("handy_start")


def bot_modell(modell: str) -> dict:
    """Das Modell des Minecraft-Bots umstellen (nur gemini/deepseek). Der Schlüssel geht nie mit: den liest
    der Helfer selbst aus Eikes Datei auf dem Host."""
    if modell not in ("gemini", "deepseek"):
        raise HphomeFehler("Unbekanntes Modell.", 400)
    return _auftrag("bot_modell", "", modell)
