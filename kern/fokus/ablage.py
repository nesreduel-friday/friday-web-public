"""Fokus-Personen: die markierten Personen, ihre Zeiger je Quelle und die „Neu“-Einträge (`data/fokus.json`, Rechte 600).

Gespeichert werden nur Aussagen, Zeiger (bis wohin gelesen wurde) und Zähler — nie Rohnachrichten.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

from kern import config

NEU_TAGE = 14
NEU_MAX = 20
DOPPELT_STUNDEN = 24
_sperre = threading.RLock()


def _datei():
    return config.daten_ordner() / "fokus.json"


def _heute() -> str:
    return config.jetzt().date().isoformat()


def _leer() -> dict:
    return {"version": 1, "personen": [], "tag": {"datum": "", "aufrufe": 0}}


def _lesen() -> dict:
    try:
        d = json.loads(_datei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _leer()
    if not isinstance(d, dict) or not isinstance(d.get("personen"), list):
        return _leer()
    d.setdefault("tag", {"datum": "", "aufrufe": 0})
    return d


def _schreiben(d: dict) -> None:
    datei = _datei()
    datei.parent.mkdir(parents=True, exist_ok=True)
    neu = datei.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(neu, 0o600)
    os.replace(neu, datei)


def _eintrag(uuid: str, name: str, jetzt: datetime) -> dict:
    return {"uuid": uuid, "name": name, "seit": jetzt.isoformat(timespec="seconds"),
            "quellen": {"whatsapp": {"chats": {}}, "mail": {"letzte_uid": 0}},
            "neu": [], "episoden_offen": [], "letzter_lauf": "", "fehler": {"whatsapp": "", "mail": ""}}


def _finden(d: dict, uuid: str) -> dict | None:
    return next((p for p in d["personen"] if p.get("uuid") == uuid), None)


def liste() -> list[dict]:
    with _sperre:
        return _lesen()["personen"]


def holen(uuid: str) -> dict | None:
    with _sperre:
        return _finden(_lesen(), uuid)


def namen() -> list[str]:
    return [p["name"] for p in liste() if p.get("name")]


def markieren(uuid: str, name: str, an: bool) -> bool:
    """Schaltet den Fokus. Beim Entfernen verschwinden „Neu“ und Zeiger (die Graph-Episoden bleiben)."""
    with _sperre:
        d = _lesen()
        vorhanden = _finden(d, uuid)
        if an and not vorhanden:
            d["personen"].append(_eintrag(uuid, name, config.jetzt()))
        elif not an and vorhanden:
            d["personen"] = [p for p in d["personen"] if p.get("uuid") != uuid]
        _schreiben(d)
        return an


def zeiger(uuid: str, quelle: str) -> dict:
    p = holen(uuid)
    return dict((p or {}).get("quellen", {}).get(quelle, {}))


def zeiger_setzen(uuid: str, quelle: str, wert: dict) -> None:
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if p:
            p["quellen"][quelle] = wert
            _schreiben(d)


def fehler_setzen(uuid: str, quelle: str, text: str) -> None:
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if p:
            p["fehler"][quelle] = text[:200]
            _schreiben(d)


def lauf_vermerken(uuid: str, jetzt: datetime | None = None) -> None:
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if p:
            p["letzter_lauf"] = (jetzt or config.jetzt()).isoformat(timespec="seconds")
            _schreiben(d)


def neu_ablegen(uuid: str, quelle: str, texte: list[str], jetzt: datetime | None = None) -> int:
    """Legt Aussagen als „Neu“ ab (neueste zuerst). Gleicher Text innerhalb 24 h wird nicht doppelt abgelegt;
    Einträge über 14 Tage und mehr als 20 fallen weg. Gibt die Zahl wirklich neuer Einträge zurück."""
    jetzt = jetzt or config.jetzt()
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if not p:
            return 0
        grenze_alt = (jetzt - timedelta(days=NEU_TAGE)).isoformat(timespec="seconds")
        grenze_doppelt = (jetzt - timedelta(hours=DOPPELT_STUNDEN)).isoformat(timespec="seconds")
        p["neu"] = [n for n in p["neu"] if n["zeit"] >= grenze_alt]
        neu = 0
        for text in texte:
            text = " ".join(str(text).split())
            if not text or any(n["text"] == text and n["zeit"] >= grenze_doppelt for n in p["neu"]):
                continue
            p["neu"].insert(0, {"zeit": jetzt.isoformat(timespec="seconds"), "quelle": quelle, "text": text[:400]})
            neu += 1
        p["neu"] = p["neu"][:NEU_MAX]
        _schreiben(d)
        return neu


def aufrufe_heute() -> int:
    with _sperre:
        tag = _lesen()["tag"]
        return int(tag.get("aufrufe", 0)) if tag.get("datum") == _heute() else 0


def aufruf_zaehlen() -> int:
    with _sperre:
        d = _lesen()
        if d["tag"].get("datum") != _heute():
            d["tag"] = {"datum": _heute(), "aufrufe": 0}
        d["tag"]["aufrufe"] += 1
        _schreiben(d)
        return d["tag"]["aufrufe"]


EPISODEN_MAX = 20


def episode_vormerken(uuid: str, episode: dict) -> None:
    """Merkt eine Graph-Episode vor, die noch nicht angenommen wurde (Graph pausiert, Limit, Fehler). Der Sammler schickt sie
    beim nächsten Lauf, sobald der Graph wieder annimmt. `episode`: {name, text, beschreibung, zeit}."""
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if p:
            offen = [e for e in p.setdefault("episoden_offen", []) if e["name"] != episode["name"]]
            p["episoden_offen"] = (offen + [episode])[-EPISODEN_MAX:]
            _schreiben(d)


def episoden_offen(uuid: str) -> list[dict]:
    p = holen(uuid)
    return list((p or {}).get("episoden_offen") or [])


def episode_erledigt(uuid: str, name: str) -> None:
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if p:
            p["episoden_offen"] = [e for e in p.get("episoden_offen", []) if e["name"] != name]
            _schreiben(d)


def neu_aufbauen(uuid: str) -> bool:
    """Räumt „Neu“, Zeiger, vorgemerkte Episoden und Fehler einer Person ab; die Markierung bleibt. Der nächste Lauf liest wieder die
    letzten Tage (wie beim ersten Mal). Graph-Episoden, die schon angenommen wurden, bleiben (das Gedächtnis löscht nie)."""
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if not p:
            return False
        neu = _eintrag(uuid, p["name"], config.jetzt())
        zuordnung = (p.get("quellen", {}).get("whatsapp") or {}).get("zuordnung")      # Eikes Zuordnungen bleiben
        if zuordnung:
            neu["quellen"]["whatsapp"]["zuordnung"] = zuordnung
        p.update(quellen=neu["quellen"], neu=[], episoden_offen=[], unklar=[], letzter_lauf="", fehler=neu["fehler"])
        _schreiben(d)
        return True


def zuordnung_setzen(uuid: str, chat_id: str, absender: str, an: bool) -> bool:
    """Eike ordnet einen Absender einer Gruppe dieser Person zu („Kontakt F“ in der Gruppe X ist Kontakt F Muster) — oder nimmt es zurück."""
    absender = " ".join(str(absender).split())[:80]
    if not absender:
        return False
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if not p:
            return False
        wa = p["quellen"].setdefault("whatsapp", {"chats": {}})
        liste = [a for a in wa.setdefault("zuordnung", {}).get(chat_id, []) if a.casefold() != absender.casefold()]
        if an:
            liste.append(absender)
        if liste:
            wa["zuordnung"][chat_id] = liste
        else:
            wa["zuordnung"].pop(chat_id, None)
        p["unklar"] = [u for u in p.get("unklar", []) if not (u["chat_id"] == chat_id and u["absender"].casefold() == absender.casefold())]
        _schreiben(d)
        return an


def unklar_setzen(uuid: str, unklar: list[dict]) -> None:
    """Absender in Gruppen, die wegen Namensgleichheit nicht zugeordnet werden konnten (für die Personenseite)."""
    with _sperre:
        d = _lesen()
        p = _finden(d, uuid)
        if p:
            p["unklar"] = unklar[:10]
            _schreiben(d)

