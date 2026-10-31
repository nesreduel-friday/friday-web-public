"""Token-Verbrauch von Gemini, nur lesend (28.10.2026). Die Zahlen führt `kern/verbrauch/tokens.py`."""

from __future__ import annotations

ERKLAERUNGEN = [{
    "name": "token_verbrauch",
    "description": ("Wie viele Gemini-Tokens Friday verbraucht hat, nur lesend: heute oder die letzten Tage, aufgeteilt nach "
                    "Bereich (Chat, Denkrunde, Schule, Live …) und Modell. Benutzen bei „Wie viel verbrauchst du?“ oder "
                    "„Was kostet das?“. Es sind Zahlen, keine Preise."),
    "parameters": {"type": "OBJECT", "properties": {
        "tage": {"type": "INTEGER", "description": "1 = heute (Standard), 7 = letzte Woche, höchstens 90"},
    }},
}]


def token_verbrauch(tage: int = 1) -> str:
    from kern.verbrauch import tokens
    try:
        tage = int(tage or 1)
    except (TypeError, ValueError):
        tage = 1
    return tokens.als_text(tage)


WERKZEUGE = {"token_verbrauch": token_verbrauch}
