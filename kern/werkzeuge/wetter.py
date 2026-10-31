"""Wetter-Werkzeug — Open-Meteo, am Konnektor „Wetter“.

Kein Konto, keine Zugangsdaten, rein lesend — aber eine eigene Freigabe
(Eikes Entscheidung, 14.09.2026), also standardmäßig aus. Ohne Ortsangabe gilt
Eikes Wohnort (`FRIDAY_ORT`). Die Antwort nennt den gefundenen Ort samt
Gemeinde — bei fünf „Holtensen“ in Niedersachsen muss ein Fehlgriff auffallen
können (siehe `kern/dienste/wetter.py`).
"""

from __future__ import annotations

from datetime import date

from kern import config
from kern.dienste import wetter as dienst
from kern.gehirn.persona import WOCHENTAGE
from kern.werkzeuge.schule import tag_deuten

KONNEKTOR = "wetter"

ERKLAERUNGEN = [{
    "name": "wetter",
    "description": ("Wettervorhersage (Open-Meteo) für heute oder einen der nächsten sechs Tage. "
                    "Ohne Ort gilt Eikes Wohnort. Benutzen bei Fragen nach Wetter, Temperatur, Regen, "
                    "Jacke oder Schirm. Nenne den Ort, den das Werkzeug zurückgibt, und erfinde keine Werte."),
    "parameters": {"type": "OBJECT", "properties": {
        "ort": {"type": "STRING",
                "description": "Ort, bei Mehrdeutigkeit mit Gemeinde ('Holtensen, Musterhausen'). Leer = Wohnort."},
        "tag": {"type": "STRING",
                "description": "'heute', 'morgen', 'übermorgen', ein Wochentag, ein Datum (JJJJ-MM-TT) oder 'Woche'"},
    }},
}]


def _zeile(t: dict) -> str:
    d = date.fromisoformat(t["datum"])
    return f"{WOCHENTAGE[d.weekday()]}, {d:%d.%m.}: {dienst.kurz(t)}"


async def wetter(ort: str = "", tag: str = "heute") -> str:
    heute = config.jetzt().date()
    if "woche" in (tag or "").lower():
        ab, anzahl = 0, dienst.MAX_TAGE
    else:
        ab = (tag_deuten(tag) - heute).days
        if ab < 0:
            return "Für vergangene Tage gibt es keine Vorhersage."
        if ab >= dienst.MAX_TAGE:
            return "Die Vorhersage reicht nur sieben Tage weit."
        anzahl = ab + 1
    try:
        v = await dienst.vorhersage(ort or dienst.heimatort(), anzahl)
    except dienst.WetterFehler as exc:
        return str(exc)
    tage = v["tage"][ab:]
    if not tage:
        return "Der Wetterdienst hat für diesen Tag nichts geliefert."
    return f"Vorhersage für {v['ort']} (Open-Meteo):\n" + "\n".join(_zeile(t) for t in tage)


WERKZEUGE = {"wetter": wetter}
