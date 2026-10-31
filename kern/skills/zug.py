"""Zugkontext für Personen-Werkzeuge (14.10.2026): eine Kennung je Eingang, das letzte Bild der Person und vorgemerkte Zusammenfassungen.

Alles setzt der Kern, nie das Modell. Ein Werkzeug sieht nur, was in DIESEM Zug wirklich ankam (ein Bild aus einem früheren Zug ist nie
erreichbar). Die Zug-Kennung hängt an den EINGEHENDEN Nachrichten (nicht an einer Zufallszahl je Aufruf): Verarbeitet die Wache nach einem
Fehler dieselben Nachrichten noch einmal, ist das derselbe Zug — erst eine neue Nachricht der Person macht einen neuen.

Eine Zusammenfassung zählt erst als GEZEIGT, wenn die Antwort des Zuges sie wirklich enthält (`festschreiben`): vorgemerkt wird sie beim
Bauen, in die Akte geschrieben erst, nachdem `chat.antworten` mit einer Antwort zurückkam, die die Merkstücke nennt."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_zug: ContextVar[str] = ContextVar("friday_zug_id", default="")
_bild: ContextVar[dict | None] = ContextVar("friday_zug_bild", default=None)
_vorgemerkt: ContextVar[list | None] = ContextVar("friday_zug_vorgemerkt", default=None)


def zug_id() -> str:
    return _zug.get()


def bild() -> dict | None:
    return _bild.get()


def _kennung(nachrichten: list[dict] | None) -> str:
    """Ohne Nachrichten eine Zufallskennung; sonst ein Fingerabdruck von Anzahl und Inhalt der letzten Nachrichten (gleiche Eingabe,
    gleiche Kennung — ein Neuversuch ist kein neuer Zug)."""
    if not nachrichten:
        return uuid.uuid4().hex
    letzte = [[str(n.get("rolle") or ""), str(n.get("text") or ""),
               hashlib.sha1(str((n.get("bild") or {}).get("data") or "").encode()).hexdigest()[:12] if isinstance(n.get("bild"), dict) else ""]
              for n in nachrichten[-4:] if isinstance(n, dict)]
    return hashlib.md5(json.dumps([len(nachrichten), letzte], ensure_ascii=False).encode()).hexdigest()


def vormerken(akte_id: int, stand: str, stuecke: list[str], text: str = "") -> None:
    """Merkt eine gebaute Zusammenfassung vor. Gezeigt gilt sie erst nach `festschreiben`: Die Antwort an die Person nennt alle
    `stuecke` — oder der Server hängt `text` an (Friday umformuliert oder lässt Teile weg)."""
    liste = _vorgemerkt.get()
    if liste is not None:
        liste.append({"akte": int(akte_id), "stand": str(stand), "stuecke": [str(s) for s in stuecke if s], "text": str(text or "")})


def festschreiben(antwort) -> str:
    """Macht aus den vorgemerkten Zusammenfassungen gezeigte: Nennt die Antwort alle Merkstücke, bleibt sie unverändert; sonst
    hängt der Server den festen Text der Zusammenfassung an. Danach steht der Vermerk in der Akte. Gibt die (ergänzte) Antwort zurück."""
    liste = _vorgemerkt.get()
    text = str(antwort or "")
    if not liste:
        return text
    from kern.skills import akten
    for v in list(liste):
        if not (v["stuecke"] and all(s.casefold() in text.casefold() for s in v["stuecke"])):
            if not v["text"]:
                continue                                   # nichts anzuhängen und nicht genannt: nicht als gezeigt werten
            text = (text.rstrip() + "\n\n" if text.strip() else "") + v["text"] + "\nStimmt das so?"
        akten.standard().zusammenfassung_merken(v["akte"], v["stand"], zug_id())
    liste.clear()
    return text


@contextmanager
def zug(nachrichten: list[dict] | None = None):
    """Neuer Zug; `bild()` ist das letzte Bild einer Nachricht der Person (`rolle == "du"`) in `nachrichten`."""
    letztes = None
    for n in nachrichten or []:
        b = n.get("bild") if isinstance(n, dict) and n.get("rolle") == "du" else None
        if isinstance(b, dict) and b.get("data") and b.get("mime"):
            letztes = {"mime": str(b["mime"]), "data": str(b["data"])}
    t1 = _zug.set(_kennung(nachrichten))
    t2 = _bild.set(letztes)
    t3 = _vorgemerkt.set([])
    try:
        yield
    finally:
        _vorgemerkt.reset(t3)
        _bild.reset(t2)
        _zug.reset(t1)
