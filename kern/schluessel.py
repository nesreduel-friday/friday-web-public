"""Welche Gemini-Schlüssel wofür gebraucht werden und welche gerade am Limit sind.

Google meldet ein Limit nur als Antwort (429) auf eine echte Anfrage. Deshalb wird
nichts extra abgefragt: `ki.senden` und der Chat melden jeden 429 und jeden
Erfolg hierher. Ein Tageslimit gilt bis zum Zurücksetzen (9:05 Uhr), ein
Minutenlimit ein paar Minuten. Das Limit zählt pro Modell, darum steht das Modell dabei.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from kern import config
from kern.dienste import schluessel_pool

log = logging.getLogger("friday.schluessel")

# Umgebungsvariable → Name in der Übersicht (in der Reihenfolge der Anzeige).
NAMEN = {
    "GEMINI_API_KEY": "Haupt",
    "RESERVE_GEMINI_API_KEY": "Reserve",
    "HINTERGRUND_GEMINI_API_KEY": "Hintergrund",
    "GRAPHITI_GEMINI_API_KEY": "Graphiti",
    "GRAPH_GEMINI_API_KEY": "Wissen",
    "HAUSAUFGABEN_GEMINI_API_KEY": "Hausaufgaben",
    "BUECHER_GEMINI_API_KEY": "Bücher",
    "LOESEN_GEMINI_API_KEY": "Lösen",
    "LOESEN_RESERVE_GEMINI_API_KEY": "Lösen (Reserve)",
    "GOODNOTES_GEMINI_API_KEY": "Goodnotes",
    "ARBEITEN_GEMINI_API_KEY": "Arbeiten",
}
MINUTENLIMIT_S = 180
RESET_STUNDE = 9          # wie in gedaechtnis/graph.py: Mitternacht pazifisch

_stand: dict | None = None      # {schlüsselname: {modell: {"seit": epoch, "art": "tag"|"minute"}}}


def _datei():
    return config.daten_ordner() / "schluessel_limits.json"


def _laden() -> dict:
    global _stand
    if _stand is None:
        try:
            _stand = json.loads(_datei().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _stand = {}
    return _stand


def _speichern() -> None:
    try:
        _datei().write_text(json.dumps(_stand or {}), encoding="utf-8")
    except OSError:
        pass


ZAEHLER_TAGE = 60         # so lange bleiben die Tageszähler in der Datei
_zaehler: dict | None = None  # {"2026-10-28": {"Haupt": {"gemini-3.6-flash": {"ok": n, "limit": n, "fehler": n}}}}


def _zaehler_datei():
    return config.daten_ordner() / "gemini_zaehler.json"


def _zaehler_laden() -> dict:
    global _zaehler
    if _zaehler is None:
        try:
            _zaehler = json.loads(_zaehler_datei().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _zaehler = {}
    return _zaehler


def zaehlen(schluessel: str, modell: str, ergebnis: str) -> None:
    """Zählt einen Gemini-Aufruf je Tag, Schlüsselname und Modell (`ok`, `limit` = 429, `fehler` = 5xx/Netz).
    Kein Inhalt, nur Zahlen; ein Fehler beim Schreiben darf nie einen Aufruf stören."""
    try:
        tag = config.jetzt().strftime("%Y-%m-%d")
        alle = _zaehler_laden()
        zeile = alle.setdefault(tag, {}).setdefault(name_von(schluessel) or "unbekannt", {}).setdefault(modell, {})
        zeile[ergebnis] = zeile.get(ergebnis, 0) + 1
        for alt in sorted(alle)[:-ZAEHLER_TAGE]:
            del alle[alt]
        _zaehler_datei().write_text(json.dumps(alle, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("Gemini-Zähler konnte nicht schreiben")


def zaehler_tage(tage: int = 7) -> dict:
    """Die letzten `tage` Tage, neueste zuerst."""
    alle = _zaehler_laden()
    return {t: alle[t] for t in sorted(alle, reverse=True)[:max(1, tage)]}


def name_von(schluessel: str) -> str | None:
    if not schluessel:
        return None
    for var, name in NAMEN.items():
        if os.getenv(var, "").strip() == schluessel:
            return name
    for eintrag in schluessel_pool.eintraege():
        if eintrag["wert"] == schluessel:
            return eintrag["name"]
    return None


def art_aus(antworttext: str) -> str:
    """Tageslimit oder nur zu viele Anfragen pro Minute?"""
    return "tag" if "perday" in antworttext.lower().replace(" ", "") else "minute"


def frei(schluessel: str) -> bool:
    """Ist dieser Schlüssel gerade für kein Modell limitiert? Ein unbekannter (nie
    gemeldeter) Schlüssel gilt als frei."""
    name = name_von(schluessel)
    if not name:
        return True
    return not any(_aktiv(e) for e in _laden().get(name, {}).values())


def _pool_frei_anzahl() -> int:
    return sum(1 for eintrag in schluessel_pool.eintraege() if frei(eintrag["wert"]))


def limit_melden(schluessel: str, modell: str, antworttext: str = "") -> None:
    zaehlen(schluessel, modell, "limit")
    name = name_von(schluessel)
    if not name:
        return
    art = art_aus(antworttext)
    _laden().setdefault(name, {})[modell] = {"seit": time.time(), "art": art}
    _speichern()
    log.info("Schlüssel %s: Limit (%s) bei %s", name, art, modell)
    try:
        schluessel_pool.pruefen_und_anstossen(_pool_frei_anzahl())
    except Exception:
        log.exception("Automatik für den Schlüssel-Pool ist fehlgeschlagen")


def fehler_melden(schluessel: str, modell: str) -> None:
    """Überlastet (5xx) oder nicht erreichbar: nur zählen, kein Limit."""
    zaehlen(schluessel, modell, "fehler")


def erfolg_melden(schluessel: str, modell: str) -> None:
    zaehlen(schluessel, modell, "ok")
    name = name_von(schluessel)
    if name and _laden().get(name, {}).pop(modell, None) is not None:
        if not _stand[name]:
            del _stand[name]
        _speichern()


def _reset_nach(epoch: float):
    zeit = datetime.fromtimestamp(epoch, ZoneInfo(config.ZEITZONE))
    reset = zeit.replace(hour=RESET_STUNDE, minute=5, second=0, microsecond=0)
    return reset if reset > zeit else reset + timedelta(days=1)


def _aktiv(eintrag: dict):
    """Bis wann das Limit noch gilt (datetime) — oder None, wenn es vorbei ist."""
    jetzt = config.jetzt()
    if eintrag["art"] == "tag":
        bis = _reset_nach(eintrag["seit"])
    else:
        bis = datetime.fromtimestamp(eintrag["seit"] + MINUTENLIMIT_S, jetzt.tzinfo)
    return bis if bis > jetzt else None


def _zwecke() -> list[tuple[str, list[str]]]:
    """Wofür welche Schlüssel gebraucht werden, jeweils in der Reihenfolge, in der sie drankommen."""
    def namen(liste):
        return [n for n in (name_von(s) for s in liste) if n]
    return [
        ("Gespräch und Chat", namen(config.gespraech_schluessel_liste())),
        ("Briefe, Mails, Chats auswerten", namen(config.hintergrund_schluessel_liste())),
        ("Hausaufgaben einlesen", namen(config.hausaufgaben_schluessel_liste())),
        ("Bücher lesen", namen(config.buecher_schluessel_liste())),
        ("Hausaufgaben lösen", namen(config.loesen_schluessel_liste())),
        ("Goodnotes", namen(config.goodnotes_schluessel_liste())),
        ("Klassenarbeiten lesen", namen(config.arbeiten_schluessel_liste())),
        ("Wissensgraph", namen(config.graph_schluessel_liste())),
    ]


def uebersicht() -> dict:
    stand = _laden()
    zwecke = _zwecke()
    try:
        from kern.gedaechtnis import graph
        graph_pause = graph.pause_bis()
    except Exception:
        graph_pause = ""
    schluessel = []
    for var, name in NAMEN.items():
        if not os.getenv(var, "").strip():
            continue
        limits = []
        for modell, e in stand.get(name, {}).items():
            bis = _aktiv(e)
            if bis:
                limits.append({"modell": modell, "art": e["art"], "bis": bis.strftime("%H:%M")})
        if name == "Wissen" and graph_pause:
            limits.append({"modell": "Wissensgraph", "art": "tag", "bis": graph_pause[11:16]})
        eingesetzt = []
        for zweck, reihe in zwecke:
            if name in reihe:
                rang = reihe.index(name) + 1
                eingesetzt.append({"zweck": zweck, "rang": rang, "von": len(reihe)})
        schluessel.append({"name": name, "limits": limits, "zwecke": eingesetzt})
    for eintrag in schluessel_pool.eintraege():
        name = eintrag["name"]
        limits = []
        for modell, e in stand.get(name, {}).items():
            bis = _aktiv(e)
            if bis:
                limits.append({"modell": modell, "art": e["art"], "bis": bis.strftime("%H:%M")})
        eingesetzt = [{"zweck": zweck, "rang": reihe.index(name) + 1, "von": len(reihe)}
                      for zweck, reihe in zwecke if name in reihe]
        schluessel.append({"name": name, "limits": limits, "zwecke": eingesetzt})
    frei = {s["name"]: not s["limits"] for s in schluessel}
    zweck_liste = [{"zweck": zweck, "verfuegbar": any(frei.get(n, False) for n in reihe), "schluessel": reihe}
                   for zweck, reihe in zwecke]
    helfer = schluessel_pool.helfer_zustand()
    return {"zwecke": zweck_liste, "schluessel": schluessel,
            "am_limit": sum(1 for z in zweck_liste if not z["verfuegbar"]),
            "zaehler": zaehler_tage(7),
            "pool": {"anzahl": len(schluessel_pool.eintraege()), "automatik": schluessel_pool.automatik_an(),
                     "helfer": helfer["helfer"], "fehler": helfer["fehler"]}}
