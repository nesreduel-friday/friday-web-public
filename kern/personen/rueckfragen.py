"""Rückfragen an Eike: eine Person fragt etwas, das nicht freigegeben ist — Friday fragt ihn.

Friday sagt der Person neutral „das kann ich dir nicht sagen“ und legt hier eine Rückfrage ab (`data/freigabe_anfragen.json`,
600). Eike bekommt einen Push (Art `kontakte`) und entscheidet im Menü: Freigeben (er formuliert selbst, was die Person
erfahren darf — das wird eine Freigabe, siehe `freigaben.py`) oder Ablehnen. Sein Ja gilt für genau diese Sache. Das Modell
kann nichts davon entscheiden; Thema und Wunsch sind Text der Person (fremd) und werden bereinigt und gekürzt."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from kern import config
from kern.personen import PersonenFehler, freigaben, register

log = logging.getLogger("friday.personen")
DATEI = "freigabe_anfragen.json"
MAX_OFFEN = 5
VERFALL_STUNDEN = 24
THEMA_MAX, WUNSCH_MAX = 120, 400
_sperre = threading.RLock()


def _lesen() -> dict:
    try:
        d = json.loads((config.daten_ordner() / DATEI).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"naechste": 1, "anfragen": []}
    if not isinstance(d, dict) or not isinstance(d.get("anfragen"), list):
        return {"naechste": 1, "anfragen": []}
    d.setdefault("naechste", len(d["anfragen"]) + 1)
    return d


def _speichern(d: dict) -> None:
    ziel = config.daten_ordner() / DATEI
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    d["anfragen"] = d["anfragen"][-300:]
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.chmod(neu, 0o600)
    os.replace(neu, ziel)


def _verfallen(a: dict) -> bool:
    try:
        alter = datetime.now(timezone.utc) - datetime.fromisoformat(a["zeit"])
    except (KeyError, ValueError):
        return True
    return alter > timedelta(hours=VERFALL_STUNDEN)


def _offen(a: dict) -> bool:
    return a.get("status") == "offen" and not _verfallen(a)


def stellen(pid: str, thema: str, wunsch: str = "") -> dict:
    person = register.holen(pid)
    if not person or person.get("pausiert"):
        raise PersonenFehler("Diese Person gibt es nicht oder sie ist pausiert.")
    thema = register._text(thema, THEMA_MAX)
    if not thema:
        raise PersonenFehler("Das Thema der Rückfrage fehlt.")
    wunsch = register._text(wunsch, WUNSCH_MAX)
    with _sperre:
        d = _lesen()
        eigene = [a for a in d["anfragen"] if a.get("person") == pid and _offen(a)]
        for a in eigene:
            if register.schluessel(a["thema"]) == register.schluessel(thema):
                return dict(a)                              # dieselbe Frage nur einmal, kein zweiter Push
        if len(eigene) >= MAX_OFFEN:
            raise PersonenFehler("Zu viele offene Rückfragen von dieser Person — Eike hat sie noch nicht beantwortet.")
        a = {"id": f"a{int(d['naechste'])}", "person": pid, "thema": thema, "wunsch": wunsch,
             "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "offen", "gemeldet": False}
        d["naechste"] = int(d["naechste"]) + 1
        d["anfragen"].append(a)
        _speichern(d)
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden("kontakte", f"Rückfrage von {person['name']}",
                                  f"{person['name']} fragt nach: {thema}. Freigeben oder ablehnen unter Extras → Kontakte.",
                                  url="/#studio/kontakte")
    except Exception:  # noqa: BLE001 — ein Push-Fehler darf die Rückfrage nie verlieren
        log.exception("Push für Rückfrage nicht gesendet")
    return dict(a)


def offene() -> list[dict]:
    namen = {p["id"]: p["name"] for p in register.liste()}
    return [dict(a, person_name=namen.get(a["person"], a["person"])) for a in _lesen()["anfragen"] if _offen(a)]


def _finden(d: dict, aid: str) -> dict:
    a = next((x for x in d["anfragen"] if x["id"] == aid), None)
    if not a or not _offen(a):
        raise PersonenFehler("Diese Rückfrage gibt es nicht (mehr) oder ist schon beantwortet.")
    return a


async def freigeben(aid: str, text: str) -> dict:
    """Eike formuliert, was die Person erfahren darf: Es wird eine Freigabe (Kopie in ihre Graph-Gruppe). Während sie
    läuft, steht die Rückfrage auf `wird_freigegeben`: Ablehnen und ein zweites Freigeben (Doppeltipp) werden abgewiesen —
    sonst erführe die Person etwas, das Eike zuletzt abgelehnt hat. Scheitert die Freigabe, ist die Rückfrage wieder offen."""
    with _sperre:
        d = _lesen()
        a = _finden(d, aid)
        a["status"] = "wird_freigegeben"
        _speichern(d)
        pid, thema = a["person"], a["thema"]
    try:
        f = await freigaben.freigeben(pid, text, f"Rückfrage: {thema}")
    except BaseException:
        with _sperre:
            d = _lesen()
            for x in d["anfragen"]:
                if x["id"] == aid and x["status"] == "wird_freigegeben":
                    x["status"] = "offen"
            _speichern(d)
        raise
    with _sperre:
        d = _lesen()
        for x in d["anfragen"]:
            if x["id"] == aid:
                x.update(status="freigegeben", freigabe=f["id"])
                a = dict(x)
        _speichern(d)
    return a


def ablehnen(aid: str) -> dict:
    with _sperre:
        d = _lesen()
        a = _finden(d, aid)
        a["status"] = "abgelehnt"
        _speichern(d)
        return dict(a)


# ---------- Termine (04.10.2026, Eikes Auftrag) ----------

TERMIN_DAUER_STANDARD = 60
TERMIN_DAUER_MAX = 480


def _termin_deuten(titel: str, beginn: str, dauer_minuten) -> tuple[str, datetime, int]:
    """Titel, Beginn (Datum UND Uhrzeit, Zukunft) und Dauer — alles andere ist kein Terminwunsch."""
    titel = register._text(titel, 120)
    if not titel:
        raise PersonenFehler("Worum soll es bei dem Termin gehen?")
    try:
        wann = datetime.fromisoformat((beginn or "").strip())
    except (TypeError, ValueError, AttributeError):
        raise PersonenFehler("Wann genau? Sag Datum und Uhrzeit, z.B. Freitag 15 Uhr.")
    if wann.tzinfo is None:
        wann = wann.replace(tzinfo=config.jetzt().tzinfo)
    if wann <= config.jetzt():
        raise PersonenFehler("Der Termin liegt in der Vergangenheit — sag mir einen kommenden.")
    try:
        dauer = int(dauer_minuten or TERMIN_DAUER_STANDARD)
    except (TypeError, ValueError):
        dauer = TERMIN_DAUER_STANDARD
    if not 5 <= dauer <= TERMIN_DAUER_MAX:
        raise PersonenFehler("Die Dauer muss zwischen 5 Minuten und 8 Stunden liegen.")
    return titel, wann, dauer


def termin_wunsch_stellen(pid: str, titel: str, beginn: str, dauer_minuten: int = 60, ort: str = "") -> dict:
    """Eine Person schlägt Eike einen Termin vor — er entscheidet im Menü (`annehmen`). Legt nichts an,
    trägt nichts ein. Push an Eike wie bei Rückfragen, höchstens so viele offen wie sonst."""
    person = register.holen(pid)
    if not person or person.get("pausiert"):
        raise PersonenFehler("Diese Person gibt es nicht oder sie ist pausiert.")
    titel, wann, dauer = _termin_deuten(titel, beginn, dauer_minuten)
    ort = register._text(ort or "", 120)
    with _sperre:
        d = _lesen()
        eigene = [a for a in d["anfragen"] if a.get("person") == pid and _offen(a)]
        for a in eigene:
            t = a.get("termin") or {}
            if a.get("art") == "termin" and t.get("beginn") == wann.isoformat(timespec="seconds"):
                return dict(a)                              # derselbe Wunsch nur einmal, kein zweiter Push
        if len(eigene) >= MAX_OFFEN:
            raise PersonenFehler("Zu viele offene Rückfragen von dieser Person — Eike hat sie noch nicht beantwortet.")
        a = {"id": f"a{int(d['naechste'])}", "person": pid, "art": "termin",
             "thema": f"Termin: {titel}", "wunsch": "",
             "termin": {"titel": titel, "beginn": wann.isoformat(timespec="seconds"),
                        "dauer_minuten": dauer, "ort": ort},
             "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "offen", "gemeldet": False}
        d["naechste"] = int(d["naechste"]) + 1
        d["anfragen"].append(a)
        _speichern(d)
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden("kontakte", f"Terminanfrage von {person['name']}",
                                  f"{person['name']} schlägt vor: {titel}, {wann:%a %d.%m. %H:%M} Uhr"
                                  f"{f' ({ort})' if ort else ''}. Annehmen oder ablehnen unter Extras → Kontakte.",
                                  url="/#studio/kontakte")
    except Exception:  # noqa: BLE001 — ein Push-Fehler darf den Wunsch nie verlieren
        log.exception("Push für Terminanfrage nicht gesendet")
    return dict(a)


def _kollidiert(wann: datetime, dauer_minuten: int) -> bool:
    """Steht im Weg (Best-Effort fürs Menü)? Fehler heißen kein Nein — Eike entscheidet."""
    try:
        from kern.dienste import icloud
        ende = wann + timedelta(minutes=dauer_minuten)
        zone = wann.tzinfo
        termine = icloud.termine(datetime.combine(wann.date(), datetime.min.time(), zone),
                                 datetime.combine(wann.date(), datetime.max.time(), zone))
    except Exception:  # noqa: BLE001 — kein Kalender = keine Warnung, nie ein Absturz
        return False
    for t in termine:
        try:
            beginn = datetime.fromisoformat(t["beginn"]).astimezone(zone)
            schluss = datetime.fromisoformat(t["ende"]).astimezone(zone) if t.get("ende") else None
        except (TypeError, ValueError, KeyError):
            return True
        if t.get("ganztags") or schluss is None or (beginn < ende and wann < schluss):
            return True
    return False


def offene_mit_kollision() -> list[dict]:
    """Offene Rückfragen fürs Menü — Terminwünsche mit Kollisionswarnung."""
    aus = []
    for a in offene():
        a = dict(a)
        if a.get("art") == "termin" and isinstance(a.get("termin"), dict):
            try:
                wann = datetime.fromisoformat(a["termin"]["beginn"])
                a["kollision"] = _kollidiert(wann, int(a["termin"].get("dauer_minuten", TERMIN_DAUER_STANDARD)))
            except (TypeError, ValueError, KeyError):
                a["kollision"] = False
        aus.append(a)
    return aus


def annehmen(aid: str) -> dict:
    """Eikes Ja im Menü: trägt den Termin SOFORT mit Vermerk ein (sein Tipp IST die Zustimmung).
    Kollision egal — die Warnung sah er vorher, er entscheidet. Danach meldet die Wache
    der Person die Zusage genau einmal."""
    with _sperre:
        d = _lesen()
        a = _finden(d, aid)
        if a.get("art") != "termin" or not isinstance(a.get("termin"), dict):
            raise PersonenFehler("Das ist keine Terminanfrage.")
        a["status"] = "wird_eingetragen"
        _speichern(d)
        pid, termin = a["person"], a["termin"]
    try:
        from kern.dienste import icloud
        person = register.holen(pid)
        name = person["name"] if person else "einer Person"
        beginn = datetime.fromisoformat(termin["beginn"])
        icloud.anlegen(termin["titel"], beginn,
                       beginn + timedelta(minutes=int(termin.get("dauer_minuten", TERMIN_DAUER_STANDARD))),
                       ort=termin.get("ort", ""),
                       notiz=f"Über Friday mit {name} vereinbart.")
    except BaseException as exc:
        with _sperre:
            d = _lesen()
            for x in d["anfragen"]:
                if x["id"] == aid and x["status"] == "wird_eingetragen":
                    x["status"] = "offen"
            _speichern(d)
        raise PersonenFehler(f"Nicht eingetragen ({exc}). Versuch es noch einmal.") from exc
    with _sperre:
        d = _lesen()
        for x in d["anfragen"]:
            if x["id"] == aid:
                x.update(status="eingetragen")
                a = dict(x)
        _speichern(d)
    return a


def zu_melden() -> list[dict]:
    """Rückfragen, auf die die Person noch keine Antwort bekam: von Eike freigegeben oder abgelehnt, eingetragene
    Termine oder nach Verfall unbeantwortet. Ältestes zuerst; höchstens einmal je Rückfrage (`gemeldet_markieren`)."""
    aus = []
    for a in _lesen()["anfragen"]:
        if a.get("gemeldet") is True:
            continue
        if a.get("status") in ("freigegeben", "abgelehnt", "eingetragen") \
                or (a.get("status") == "offen" and _verfallen(a)):
            aus.append(dict(a))
    return aus


def gemeldet_markieren(aid: str, status: str | None = None) -> None:
    """Die Person hat ihre Antwort bekommen (oder bekommt keine, weil sie nicht mehr existiert). `status="verfallen"` führt eine
    unbeantwortet abgelaufene Rückfrage sauber als verfallen."""
    with _sperre:
        d = _lesen()
        for a in d["anfragen"]:
            if a["id"] == aid:
                a["gemeldet"] = True
                if status:
                    a["status"] = status
        _speichern(d)
