"""Notizbuch verdichten — Doppeltes zusammenziehen, ohne etwas zu erfinden.

Das `merken`-Werkzeug legt bei jedem neuen Thema einen eigenen Fakt an. So
stand Eikes Wohnort dreimal da ("Adresse", "Identität/Wohnort", "Persönliche
Daten"). Hier liest Gemini alle gültigen Fakten und schlägt vor, welche
dasselbe meinen und zu EINEM zusammengehören.

Streng gehalten, weil Eike sich über erfundene Fakten beschwert hat:
- Zusammengeführt wird nur, was mindestens ZWEI vorhandene Themen betrifft —
  einzelne Fakten bleiben unangetastet.
- Der neue Text darf nur aus den zusammengeführten Fakten stammen, nichts Neues.
- Nichts wird gelöscht: die alten Fassungen bleiben im Verlauf nachlesbar.

Läuft mit dem Hintergrund-Schlüssel; die alten Fakten bleiben als Beleg.
"""

from __future__ import annotations

import logging

from kern.gedaechtnis import speicher as gspeicher
from kern.schule import briefe

log = logging.getLogger("friday.verdichten")

SCHEMA = {"type": "OBJECT", "properties": {
    "gruppen": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "thema": {"type": "STRING"},
        "inhalt": {"type": "STRING"},
        "ersetzt": {"type": "ARRAY", "items": {"type": "STRING"}},
    }, "required": ["thema", "inhalt", "ersetzt"]}},
}, "required": ["gruppen"]}

ANWEISUNG = (
    "Unten steht Fridays Notizbuch über den Nutzer Eike — je Zeile ein Thema und was dazu gilt. "
    "Finde Fakten, die DASSELBE beschreiben (z.B. Wohnort/Adresse/Identität) und zu einem einzigen "
    "zusammengehören. Antworte auf Deutsch im JSON:\n"
    "- gruppen: je Gruppe ein zusammengeführter Fakt mit thema (ein klarer, kurzer Name), inhalt "
    "(alle Angaben der Gruppe knapp vereint) und ersetzt (die genauen alten Themennamen der Gruppe).\n"
    "STRENGE REGELN: Führe nur zusammen, was WIRKLICH dasselbe meint, und nur, wenn ersetzt MINDESTENS "
    "ZWEI Themen enthält. Übernimm ausschließlich Angaben, die in den genannten Fakten stehen — erfinde "
    "nichts, ergänze nichts, rate nicht. Lass alles Übrige weg. Gibt es nichts zusammenzuführen, gib eine "
    "leere Liste zurück."
)


async def vorschlag() -> list[dict]:
    """Fragt Gemini nach Zusammenführungen. Gibt nur gültige, sichere Gruppen zurück."""
    g = gspeicher.standard()
    fakten = g.fakten()
    if len(fakten) < 3:
        return []
    vorhanden = {f["thema"].strip().lower(): f["thema"] for f in fakten}
    text = "\n".join(f"- {f['thema']}: {f['inhalt']}" for f in fakten)
    try:
        antwort, _ = await briefe._gemini([{"text": text}], anweisung=ANWEISUNG, schema=SCHEMA)
    except Exception as exc:
        log.warning("Verdichtung nicht möglich: %s", type(exc).__name__)
        return []
    gruppen = []
    for gr in antwort.get("gruppen", []):
        ersetzt = [t for t in gr.get("ersetzt", []) if t.strip().lower() in vorhanden]
        # Nur echte Zusammenführungen von mindestens zwei bekannten Themen.
        if len(set(t.lower() for t in ersetzt)) >= 2 and gr.get("thema", "").strip() and gr.get("inhalt", "").strip():
            gruppen.append({"thema": gr["thema"].strip(), "inhalt": gr["inhalt"].strip(),
                            "ersetzt": [vorhanden[t.strip().lower()] for t in ersetzt]})
    return gruppen


async def verdichten() -> int:
    """Verdichtung ausführen. Gibt die Zahl der zusammengeführten Gruppen zurück."""
    g = gspeicher.standard()
    gruppen = await vorschlag()
    for gr in gruppen:
        g.zusammenfuehren(gr["thema"], gr["inhalt"], gr["ersetzt"])
        log.info("Verdichtet: %s ← %s", gr["thema"], ", ".join(gr["ersetzt"]))
    return len(gruppen)
