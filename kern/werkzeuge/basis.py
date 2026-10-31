"""Harmlose, nur lesende Werkzeuge — die einzigen vor Phase 3.

Vorbild ist das alte Friday (`tools/basis.py`): dort beantwortet `uhrzeit()`
die Frage nach der Zeit aus der Uhr des Rechners. Das ist robuster als eine
Zeitangabe im Prompt. Die veraltet in langen Gesprächen, und Gemini greift
sonst zu seiner eigenen Uhr in UTC (gemessen: "20 Uhr 56 UTC" statt 22:56).
Mit Werkzeug geprüft: meldet es "13:37", sagt Friday "13:37".

Handelnde Werkzeuge kommen erst mit Phase 3 — mit Default-Deny und Rückfrage.
"""

from kern import config
from kern.gehirn.persona import MONATE, WOCHENTAGE

ERKLAERUNGEN = [{
    "name": "uhrzeit",
    "description": ("Liefert die aktuelle Uhrzeit, den Wochentag und das Datum in Deutschland. "
                    "Immer benutzen, wenn nach Uhrzeit, Datum oder Wochentag gefragt wird."),
}]


def uhrzeit() -> str:
    j = config.jetzt()
    return (f"Es ist {j:%H:%M} Uhr am {WOCHENTAGE[j.weekday()]}, {j.day}. "
            f"{MONATE[j.month - 1]} {j.year} (deutsche Zeit).")


WERKZEUGE = {"uhrzeit": uhrzeit}
