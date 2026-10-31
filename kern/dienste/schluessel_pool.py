"""Der wachsende Vorrat zusätzlicher Gemini-Schlüssel — je einer aus einem eigenen
Google-Cloud-Projekt, damit sich keine zwei ein Tageslimit teilen.

Friday selbst legt kein Google-Cloud-Projekt an: Es fehlt der Login, und der Container
soll ihn nicht halten. Wie bei den Claude-Sitzungen (`claude_sitzung.py`) legt dieses
Modul nur einen Auftrag in `data/schluessel_auftraege/auftrag.json` ab; ausgeführt wird
er vom Helfer auf hphome (`host-helfer/schluessel_helfer.py`), der seinerseits
`data/schluessel_auftraege/zustand.json` schreibt.

Der Pool selbst liegt in `data/gemini_pool.json` (Rechte 600): eine Liste angelegter
Schlüssel plus der Schalter „automatisch anlegen". `eintraege()`/`werte()` lesen bei
jedem Aufruf frisch von der Platte (kein Zwischenspeicher — `config.py` ruft sie bei
jeder Zweck-Liste ab) und übernehmen dabei nebenbei eine frisch fertige Antwort des
Helfers in den Pool.

Details und Begründung: docs/superpowers/specs/2026-09-22-gemini-schluessel-pool-design.md
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from kern import config

ABKUEHLUNG_S = 4 * 3600
HELFER_FRISCH_S = 20


def _pool_datei() -> Path:
    return config.daten_ordner() / "gemini_pool.json"


def _auftrag_ordner() -> Path:
    return config.daten_ordner() / "schluessel_auftraege"


def _lesen(pfad: Path) -> dict:
    try:
        d = json.loads(pfad.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _schreiben(pfad: Path, daten: dict) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(pfad)


def _pool_stand() -> dict:
    d = _lesen(_pool_datei())
    d.setdefault("schluessel", [])
    d.setdefault("automatik", False)
    d.setdefault("letzte_uebernahme", 0)
    return d


def _zustand() -> dict:
    return _lesen(_auftrag_ordner() / "zustand.json")


def _uebernehmen(stand: dict) -> dict:
    """Eine neue, noch nicht übernommene Erfolgsantwort des Helfers in den Pool holen.
    Nur Antworten vom Anlegen eines neuen Schlüssels tragen "wert" — die Antwort eines
    Umschaltens (siehe `graph_umschalten_anstossen`) legt nichts in den Pool."""
    antwort = _zustand().get("letzte_antwort") or {}
    zeit = antwort.get("zeit") or 0
    if antwort.get("ok") and "wert" in antwort and zeit > stand.get("letzte_uebernahme", 0):
        stand["schluessel"].append({"name": f"Pool {len(stand['schluessel']) + 1}",
                                     "wert": antwort["wert"], "projekt_id": antwort["projekt_id"],
                                     "angelegt_am": config.jetzt().isoformat()})
        stand["letzte_uebernahme"] = zeit
        _schreiben(_pool_datei(), stand)
    return stand


def eintraege() -> list[dict]:
    return _uebernehmen(_pool_stand())["schluessel"]


def werte() -> list[str]:
    return [e["wert"] for e in eintraege()]


def automatik_an() -> bool:
    return bool(_pool_stand()["automatik"])


def automatik_setzen(an: bool) -> None:
    stand = _pool_stand()
    stand["automatik"] = bool(an)
    _schreiben(_pool_datei(), stand)


def helfer_zustand() -> dict:
    """Läuft der Helfer, und was hat sein letzter Auftrag ergeben — nie der Schlüsselwert."""
    d = _zustand()
    alter = time.time() - float(d.get("aktualisiert") or 0) if d else float("inf")
    laeuft = alter <= HELFER_FRISCH_S
    antwort = d.get("letzte_antwort") or {}
    fehler = None if antwort.get("ok", True) else antwort.get("text")
    return {"helfer": laeuft, "fehler": fehler}


def _auftrag_offen() -> bool:
    return (_auftrag_ordner() / "auftrag.json").exists()


def _in_abkuehlung() -> bool:
    antwort = _zustand().get("letzte_antwort") or {}
    if antwort.get("ok", True):
        return False
    return time.time() - float(antwort.get("zeit") or 0) < ABKUEHLUNG_S


def anlegen_anstossen(erzwingen: bool = False) -> tuple[bool, str]:
    """Einen Auftrag ablegen. `erzwingen=True` (Knopf in den Einstellungen) übergeht
    Automatik-Schalter und Abkühlzeit — nie aber einen schon laufenden Auftrag oder
    einen fehlenden Helfer."""
    if not helfer_zustand()["helfer"]:
        return False, "Der Helfer auf hphome läuft nicht (host-helfer/schluessel_helfer.py)."
    if _auftrag_offen():
        return False, "Es läuft schon ein Auftrag."
    if not erzwingen and not automatik_an():
        return False, "Automatik ist aus."
    if not erzwingen and _in_abkuehlung():
        return False, "Nach dem letzten Fehlschlag wird gerade abgewartet (Abkühlzeit)."
    _auftrag_ablegen({"aktion": "anlegen"})
    return True, "Auftrag liegt bereit."


def graph_umschalten_anstossen(schluessel: str) -> tuple[bool, str]:
    """Den Graphiti-Container auf einen anderen, noch freien Gemini-Schlüssel umschalten (neu gestartet über den
    Host-Helfer, `GRAPH_GEMINI_API_KEY` = `schluessel`). Kein neues Google-Cloud-Projekt, deshalb ohne
    Automatik-Schalter und Abkühlzeit — nur der Helfer muss laufen und kein anderer Auftrag darf offen sein."""
    if not helfer_zustand()["helfer"]:
        return False, "Der Helfer auf hphome läuft nicht (host-helfer/schluessel_helfer.py)."
    if _auftrag_offen():
        return False, "Es läuft schon ein Auftrag."
    _auftrag_ablegen({"aktion": "graphiti_umschalten", "schluessel": schluessel})
    return True, "Umschalten liegt bereit."


def _auftrag_ablegen(daten: dict) -> None:
    ordner = _auftrag_ordner()
    ordner.mkdir(parents=True, exist_ok=True)
    datei, tmp = ordner / "auftrag.json", ordner / "auftrag.tmp"
    tmp.write_text(json.dumps({"id": secrets.token_hex(8), **daten}), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(datei)


def pruefen_und_anstossen(anzahl_frei: int, reserve: int = 2) -> None:
    """Nach einem gemeldeten Limit aufgerufen: reicht die Reserve nicht mehr, automatisch
    einen Auftrag anstoßen (wenn erlaubt — sonst passiert einfach nichts)."""
    if anzahl_frei <= reserve:
        anlegen_anstossen(erzwingen=False)
