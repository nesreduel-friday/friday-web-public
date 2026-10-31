"""Hochgeladene Schulbücher (Schule → HA): welche es gibt und was davon lesbar ist.

Nur die Übersicht — die Seiten selbst blättert `kern/schule/loesen.py` beim Bearbeiten von Hausaufgaben.
"""

from __future__ import annotations

from kern.schule import buecher as ablage

KONNEKTOR = "hausaufgaben"

ERKLAERUNGEN = [
    {
        "name": "schulbuecher",
        "description": ("Listet die Schulbücher, die Eike im Hausaufgabenmodul hochgeladen hat: Titel, Fach, "
                        "wie viele Buchseiten Friday lesen kann und ob das Einlesen fertig ist. Benutzen bei "
                        "„welche Bücher hast du“, „wie viele Seiten hat das Buch, das ich hochgeladen habe“."),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def schulbuecher() -> str:
    liste = [b for b in ablage.standard().alle() if not b.get("entfernt")]
    if not liste:
        return "Es sind keine Schulbücher hochgeladen."
    zeilen = []
    for b in liste:
        u = ablage.uebersicht(b)
        seiten = (f"Buchseiten {u['bereich'][0]}–{u['bereich'][1]} ({u['buchseiten']} lesbar)"
                  if u["bereich"] else f"{u['gesamt']} Scans")
        stand = "fertig eingelesen" if u["status"] == "fertig" else f"Stand: {u['status']} ({u['fortschritt']}/{u['gesamt']})"
        zeilen.append(f"- {u['titel']} ({u['fach']}): {seiten}, {stand}")
    return "\n".join(zeilen)


WERKZEUGE = {"schulbuecher": schulbuecher}
