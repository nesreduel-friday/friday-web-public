"""Werkzeug für Personen: ein Bild als Snap wünschen (14.10.2026, Modul `snap_wuensche`).

Der Snap geht NUR an die schreibende Person — ihre Id kommt vom Kern (`kontakt_id`), nie aus dem Text. Grenzen: höchstens 5 am
Tag, nie in der Ruhezeit (22:30–06:30), nie ein schon gesendetes Bild, nie heikler
Inhalt, ein Snap nach dem anderen (der Helfer steuert EIN Handy). Findet sich nichts Passendes, wird nichts gesendet und das
ehrlich gesagt.

Der Katalog sperrt dieses Werkzeug NICHT nach Fremdtext: Bei einer Person ist ihr Text immer „fremd“, und ihr Wunsch IST der Auslöser.
Geschützt ist es durch den festen Empfänger, das Tageslimit, die Bildprüfung und das Snapchat-Schreibrecht (Review 14.10.2026)."""

from __future__ import annotations

import json
import logging
import os
import threading

from kern import config, konnektoren
from kern.dienste import snapbild, snapchat, spassbild
from kern.ehrlichkeit.wirkung import wirkt
from kern.erinnerungen import PERSON_RUHE_AB, PERSON_RUHE_BIS
from kern.personen import module, register
from kern.werkzeuge import sendepruefung

log = logging.getLogger("friday.snapwunsch")
LIMIT_TAG = 5
_sperre = threading.Lock()                       # Zähler lesen/ändern/schreiben nie gleichzeitig (Wache bedient Personen parallel)
DATEI = "snap_wuensche.json"
BILDER_MERKEN = 60
KEIN = "NICHTS gesendet. {grund} Sag der Person ehrlich, dass nichts geschickt wurde, und behaupte nie „geschickt“ oder „ist raus“."

ERKLAERUNGEN = [
    {"name": "snap_wunsch",
     "description": ("Schickt der Person, mit der du schreibst, auf ihren Wunsch ein Bild als Snap. Beschreib kurz, was drauf sein soll "
                     "(auch „etwas Lustiges“). Der Snap geht nur an sie, höchstens 5 am Tag."),
     "parameters": {"type": "OBJECT", "properties": {
         "beschreibung": {"type": "STRING", "description": "Was auf dem Bild sein soll, in wenigen Worten"}},
         "required": ["beschreibung"]}},
]


def _pfad():
    return config.daten_ordner() / DATEI


def _lesen() -> dict:
    try:
        d = json.loads(_pfad().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _schreiben(d: dict) -> None:
    ziel = _pfad()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(neu, ziel)


def _ruhezeit(t) -> bool:
    return t >= PERSON_RUHE_AB or t < PERSON_RUHE_BIS


def _platz_nehmen(pid: str, heute: str) -> tuple[bool, list[str]]:
    """Einen der Tagesplätze RESERVIEREN, bevor das Bild gesucht wird (zwei gleichzeitige Wünsche überschreiten das Limit nie)."""
    with _sperre:
        d = _lesen()
        z = d.get(pid) or {}
        n = int(z.get("n") or 0) if z.get("tag") == heute else 0
        if n >= LIMIT_TAG:
            return False, []
        bisher = list(z.get("bilder") or [])
        d[pid] = {"tag": heute, "n": n + 1, "bilder": bisher}
        _schreiben(d)
        return True, bisher


def _platz_zurueck(pid: str, heute: str) -> None:
    with _sperre:
        d = _lesen()
        z = d.get(pid)
        if z and z.get("tag") == heute and int(z.get("n") or 0) > 0:
            z["n"] = int(z["n"]) - 1
            _schreiben(d)


def _bild_merken(pid: str, url: str) -> None:
    with _sperre:
        d = _lesen()
        z = d.setdefault(pid, {"tag": "", "n": 0, "bilder": []})
        z["bilder"] = ([url] + list(z.get("bilder") or []))[:BILDER_MERKEN]
        _schreiben(d)


@wirkt("versand", ok=("Gesendet",), nicht_praefix="NICHTS gesendet", detail=lambda a, t: "Snap-Wunsch")
async def snap_wunsch(beschreibung: str = "", kontakt_id: str = "") -> str:
    p = register.holen(kontakt_id) if isinstance(kontakt_id, str) and kontakt_id else None
    if p is None or p.get("pausiert") or not module.oeffnet(p, "snap_wunsch"):
        return KEIN.format(grund="Das geht für diese Person gerade nicht.")
    if not ((p.get("kanaele") or {}).get("snapchat") or "").strip():
        return KEIN.format(grund="Ohne Snapchat-Namen kann ich nichts schicken.")
    if not (konnektoren.benutzbar("snapchat") and konnektoren.schreiben_erlaubt("snapchat")):
        return KEIN.format(grund="Snapchat ist gerade nicht freigegeben.")
    jetzt = config.jetzt()
    if _ruhezeit(jetzt.time()):
        return KEIN.format(grund="Nachts schicke ich keine Snaps — morgen wieder.")
    wunsch = " ".join(str(beschreibung or "").split())[:100]
    if not wunsch:
        return KEIN.format(grund="Was soll auf dem Bild sein?")
    if sendepruefung.heikel(wunsch):
        return KEIN.format(grund="Dazu schicke ich nichts.")
    if snapchat.snap_wartet():
        return KEIN.format(grund="Ein anderer Snap ist noch auf dem Weg. Versuch es in ein, zwei Minuten noch einmal.")
    heute = jetzt.date().isoformat()
    ok, bisher = _platz_nehmen(p["id"], heute)
    if not ok:
        return KEIN.format(grund=f"Heute waren es schon {LIMIT_TAG} Wunsch-Snaps — morgen wieder.")
    try:
        gefunden = await spassbild.finden(wunsch, ausser=set(bisher))
        if not gefunden:
            _platz_zurueck(p["id"], heute)
            return KEIN.format(grund=f"Ich habe nichts Passendes zu „{wunsch}“ gefunden.")
        if snapchat.snap_wartet():          # während der Suche kann ein anderer Snap (Flamme, andere Person) in die Schlange gekommen sein
            _platz_zurueck(p["id"], heute)
            return KEIN.format(grund="Ein anderer Snap ist noch auf dem Weg. Versuch es in ein, zwei Minuten noch einmal.")
        daten, url, _titel = gefunden
        pfad = snapbild.ablegen(snapbild.vorbereiten(daten, "", hochformat=True))
        auftrag = snapchat.auftrag_legen("snap", bild_pfad=pfad, person=p["id"])
    except (snapbild.SnapbildFehler, snapchat.SnapchatFehler) as exc:
        _platz_zurueck(p["id"], heute)
        return KEIN.format(grund=str(exc))
    except Exception:  # noqa: BLE001 — Technik (Pfade, Systemfehler) bleibt im Log, nicht beim Modell
        log.exception("Snap-Wunsch für %s gescheitert", p["id"])
        _platz_zurueck(p["id"], heute)
        return KEIN.format(grund="Das Handy ist gerade nicht erreichbar.")
    _bild_merken(p["id"], url)
    from kern.personen import flammen
    flammen.merken_auftrag(p["id"], auftrag)
    return f"Gesendet: ein Bild zu „{wunsch}“ ist auf dem Weg zu {p['name']}."


WERKZEUGE = {"snap_wunsch": snap_wunsch}
snap_wunsch.kennt_kontakt = True
