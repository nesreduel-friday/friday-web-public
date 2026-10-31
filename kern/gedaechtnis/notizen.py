"""Eikes eigene Angaben zu einem Eintrag („Kontakt Bs Mutter heißt Kontakt H“).

Eike ist hier die direkte Quelle, und es gibt nur EINE Wahrheit: Die Angabe wird nicht in einer eigenen Schicht neben dem Graphen
abgelegt, sondern geht den normalen Weg — als Notizbuch-Fakt (sofort wirksam: die Wissensseite liest sie als „Von Eike selbst gesagt“
mit Vorrang) und als Graph-Episode mit dem Vermerk „direkte Angabe von Eike“, die vor allem anderen verarbeitet wird (`durchgang`
läuft im Rückblick zuerst). Was noch nicht im Graphen angekommen ist, zeigt die Seite als „wartet auf Verarbeitung“.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from kern import config
from kern.gedaechtnis import graph
from kern.gedaechtnis import speicher as gspeicher

log = logging.getLogger("friday.notizen")

MAX_VERSUCHE = 3
PRUEFEN_NACH_MIN = 10            # so lange nach dem Senden wird geprüft, ob die Episode angekommen ist
NOCHMAL_NACH_MIN = 45            # nicht angekommen: nach so langer Zeit noch einmal senden
MAX_NOTIZEN = 300
_sperre = threading.RLock()


def _datei():
    return config.daten_ordner() / "notizen.json"


def _lesen() -> list[dict]:
    try:
        d = json.loads(_datei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return d if isinstance(d, list) else []


def _schreiben(liste: list[dict]) -> None:
    datei = _datei()
    datei.parent.mkdir(parents=True, exist_ok=True)
    neu = datei.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(liste[-MAX_NOTIZEN:], f, ensure_ascii=False)
    os.chmod(neu, 0o600)
    os.replace(neu, datei)


def _sauber(text: str, maximal: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    return " ".join(text.split())[:maximal]


def satz(art: str, name: str, wert: str = "", label: str = "", text: str = "") -> str:
    """Der Satz, der gespeichert wird. `name` ist der Name des Eintrags im Graphen („Kontakt Bs Mutter“)."""
    name = _sauber(name, 120)
    if art == "name":
        wert = _sauber(wert, 120)
        if not wert:
            raise ValueError("Der Name fehlt.")
        return f"{name} heißt {wert}."
    if art == "angabe":
        label, wert = _sauber(label, 40), _sauber(wert, 200)
        if not label or not wert:
            raise ValueError("Bezeichnung und Wert fehlen.")
        return f"{label} von {name}: {wert}."
    if art == "frei":
        text = _sauber(text, 600)
        if not text:
            raise ValueError("Der Text fehlt.")
        return f"Zu {name}: {text}"
    raise ValueError("Unbekannte Art.")


def anweisungen(name: str) -> str:
    return (f"Das ist eine direkte Angabe von Eike selbst über „{name}“. Eike ist die zuverlässigste Quelle: Übernimm die Aussage als "
            "Fakt genau so, wie sie dasteht — auch wenn sie andere Menschen betrifft. Sie ersetzt widersprechende frühere Aussagen. "
            "Formuliere Fakten auf Deutsch.")


def anlegen(uuid: str, name: str, art: str, **felder) -> dict:
    """Legt eine Angabe an: Notizbuch (sofort wirksam) und vorgemerkte Graph-Episode (wird von `durchgang` gesendet)."""
    text = satz(art, name, **felder)
    jetzt = config.jetzt()
    nummer = _uuid.uuid4().hex[:8]
    gspeicher.standard().merken(f"Notiz {name} {jetzt:%Y-%m-%dT%H:%M:%S}", text, quelle="eike")
    notiz = {"id": nummer, "uuid": uuid, "name": name, "art": art, "text": text, "zeit": jetzt.isoformat(timespec="seconds"),
             "episode": f"notiz-{uuid}-{jetzt:%Y%m%d-%H%M%S}-{nummer}", "status": "offen", "versuche": 0, "gesendet": ""}
    with _sperre:
        liste = _lesen()
        liste.append(notiz)
        _schreiben(liste)
    return notiz


def fuer(uuid: str) -> list[dict]:
    """Die Angaben zu einem Eintrag, neueste zuerst (für die Seite)."""
    with _sperre:
        return [n for n in reversed(_lesen()) if n.get("uuid") == uuid]


def _setzen(nummer: str, **felder) -> None:
    with _sperre:
        liste = _lesen()
        for n in liste:
            if n["id"] == nummer:
                n.update(felder)
        _schreiben(liste)


def _vor_min(iso: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except (ValueError, TypeError):
        return 1e9


async def durchgang() -> int:
    """Schickt offene Angaben an den Graphen (nur wenn er nicht pausiert), prüft Gesendetes auf Ankunft und sendet bei Bedarf noch
    einmal. Gibt die Zahl der gesendeten Episoden zurück."""
    if graph.pausiert():
        return 0
    gesendet = 0
    for n in list(_lesen()):
        if n["status"] in ("angekommen", "aufgegeben"):
            continue
        if n["status"] == "gesendet":
            if _vor_min(n["gesendet"]) < PRUEFEN_NACH_MIN:
                continue
            da = await graph.angekommen(n["episode"])
            if da:
                _setzen(n["id"], status="angekommen")
                continue
            if da is None or _vor_min(n["gesendet"]) < NOCHMAL_NACH_MIN:
                continue                                    # Graphiti antwortet nicht / die Schlange ist noch lang
        if n["versuche"] >= MAX_VERSUCHE:
            _setzen(n["id"], status="aufgegeben")
            log.warning("Angabe zu %s kam nach %s Versuchen nicht im Graphen an", n["name"], MAX_VERSUCHE)
            continue
        text = f"[Direkte Angabe von Eike — hat Vorrang vor allem anderen]\n{n['text']}"
        if await graph.einspeisen(n["episode"], text, f"Angabe von Eike zu {n['name']}", n["zeit"],
                                  anweisungen=anweisungen(n["name"]), gruppe=graph.GRUPPE):
            _setzen(n["id"], status="gesendet", versuche=n["versuche"] + 1, gesendet=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            gesendet += 1
        else:
            break                                           # Graph nimmt gerade nichts an: beim nächsten Takt wieder
    return gesendet
