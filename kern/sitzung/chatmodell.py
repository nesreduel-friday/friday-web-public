"""Welches Modell Eikes Chat beantwortet (29.10.2026): Gemini (Standard) oder DeepSeek, per Schalter in der App.

Gilt nur für Eikes eigenen Chat (nie für Personen) und nur für Züge ohne Bild/PDF — DeepSeek sieht keine Dateien. Fällt DeepSeek aus
(Schlüssel fehlt, Fehler), antwortet Gemini; der Zug geht nie verloren. Die Einstellung liegt in `einstellungen.json`, geschrieben
nur von der Route (Eikes Knopf), kein Werkzeug."""

from __future__ import annotations

import logging

log = logging.getLogger("friday.chat")

BACKENDS = ("gemini", "deepseek")


def backend() -> str:
    from kern.planer import briefing
    wert = briefing._lesen(briefing._einstellungsdatei()).get("chat_backend")
    return wert if wert in BACKENDS else "gemini"


def einstellen(wahl: str) -> str:
    if wahl not in BACKENDS:
        raise ValueError(f"Unbekanntes Backend: {wahl}")
    from kern.planer import briefing
    with briefing._sperre:
        d = briefing._lesen(briefing._einstellungsdatei())
        d["chat_backend"] = wahl
        briefing._schreiben(briefing._einstellungsdatei(), d)
    return backend()


def _hat_datei(inhalt: dict) -> bool:
    return any("inlineData" in t or "fileData" in t
               for z in inhalt.get("contents", []) for t in z.get("parts", []) if isinstance(t, dict))


def deepseek_dran(inhalt: dict, person: dict | None) -> bool:
    return person is None and backend() == "deepseek" and not _hat_datei(inhalt)


async def anfrage(gemini, inhalt: dict, bevorzugt: str | None, person: dict | None) -> tuple[dict, str]:
    """`gemini` = die Gemini-Anfragefunktion von chat.py. Gibt (Antwort im Gemini-Format, Modellname) zurück."""
    if deepseek_dran(inhalt, person):
        from kern.denken import deepseek
        from kern.sitzung.chat import ChatFehler
        try:
            return await deepseek.anfrage(inhalt)
        except ChatFehler as e:
            log.warning("DeepSeek im Chat ging nicht, Gemini antwortet: %s", e)
            bevorzugt = None
    return await gemini(inhalt, bevorzugt=bevorzugt)
