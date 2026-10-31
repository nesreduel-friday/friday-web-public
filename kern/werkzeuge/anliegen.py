"""Werkzeug `anliegen_erledigt` — Friday hakt ein erwähntes Anliegen ab. Nur für Eike."""

from __future__ import annotations

from kern.anliegen import fach

ERKLAERUNGEN = [
    {
        "name": "anliegen_erledigt",
        "description": ("Hakt das Anliegen aus dem [ANLIEGEN]-Block ab. wie=erwähnt: du hast es Eike gesagt. wie=nicht_mehr: er will "
                        "es nicht (mehr) hören, z. B. 'kenn ich schon'. wie=später: es passt heute nicht, frühestens in 4 Stunden "
                        "wieder. Ohne Aufruf kommt es in einem späteren Gespräch noch einmal (höchstens dreimal)."),
        "parameters": {"type": "OBJECT", "properties": {
            "id": {"type": "INTEGER", "description": "Die Nummer aus dem [ANLIEGEN]-Block"},
            "wie": {"type": "STRING", "description": "erwähnt, später oder nicht_mehr"},
        }, "required": ["id"]},
    },
]

_WIE = {"erwähnt": "erwaehnt", "erwaehnt": "erwaehnt", "später": "spaeter", "spaeter": "spaeter", "nicht_mehr": "nicht_mehr"}
_SATZ = {"erwaehnt": "Ist als erwähnt erledigt.", "spaeter": "Ich erinnere mich später daran (frühestens in 4 Stunden).",
         "nicht_mehr": "Gut, das erwähne ich nicht mehr."}


def anliegen_erledigt(id, wie: str = "erwähnt") -> str:     # noqa: A002 (Name kommt vom Modell)
    art = _WIE.get(str(wie or "erwähnt").strip().lower())
    if art is None:
        return "wie muss erwähnt, später oder nicht_mehr sein."
    try:
        aid = int(id)
    except (TypeError, ValueError):
        return "Welche Nummer hat das Anliegen?"
    e = fach.standard().abhaken(aid, fach.EIKE, art)
    return _SATZ[art] if e["ok"] else f"Nicht abgehakt: {e['grund']}"


# Ändert etwas und verlangt kein „ja“: nach Fremdtext im selben Zug gesperrt (katalog.ausfuehren).
NICHT_NACH_FREMDTEXT = {"anliegen_erledigt"}

WERKZEUGE = {"anliegen_erledigt": anliegen_erledigt}
