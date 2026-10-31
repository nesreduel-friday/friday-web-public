"""Lernen (Schule → Lernen): alles zu einem Fach an einem Ort.

Liest nur zusammen, was es schon gibt — gleicht nichts ab und ruft kein Gemini auf:
Noten (`noten.py`), hochgeladene Arbeiten samt ihrer Muster (`arbeiten.py`),
Kursmaterial aus dem Gruppenordner (`gruppenordner.py`) und Goodnotes-Hefte, deren
Ordner zum Fach passt (dieselbe Erkennung wie beim Einsortieren von Arbeiten,
`arbeiten.fach_zuordnen`).
"""

from __future__ import annotations

from kern.goodnotes import ablage as goodnotesablage
from kern.schule import arbeiten as arbeitenablage
from kern.schule import gruppenordner as gruppenordnerablage
from kern.schule import noten as notenablage


def _goodnotes_hefte(fach_name: str, fach_id: int, alle_faecher: list[dict]) -> list[dict]:
    a = goodnotesablage.standard()
    aus = a.ausgeschlossen()
    treffer = []
    for h in a.hefte():
        if goodnotesablage.ausgeschlossen(h["ordner"], aus):
            continue
        text = h["ordner"] or h["name"]
        if arbeitenablage.fach_zuordnen(text, alle_faecher) == fach_id:
            treffer.append(h)
    return treffer


def liste() -> list[dict]:
    """Alle Fächer mit einer Zahl, wie viel Material zu jedem vorliegt."""
    faecher = notenablage.standard().uebersicht()["faecher"]
    arbeiten_alle = arbeitenablage.standard().alle()
    muster_alle = arbeitenablage.standard().muster_liste()
    material_alle = gruppenordnerablage.standard().alle()
    aus = []
    for f in faecher:
        aus.append({
            "id": f["id"], "name": f["name"],
            "arbeiten": sum(1 for a in arbeiten_alle if a["fach_id"] == f["id"]),
            "muster": sum(1 for m in muster_alle if m["fach_id"] == f["id"]),
            "material": sum(1 for d in material_alle if d["fach_id"] == f["id"]),
            "goodnotes": len(_goodnotes_hefte(f["name"], f["id"], faecher)),
        })
    return aus


def fach(fach_id: int) -> dict | None:
    """Alles zu einem Fach: Noten (mit Semestern), eigene und fremde Arbeiten samt
    ihren Mustern, Kursmaterial aus dem Gruppenordner, passende Goodnotes-Hefte —
    dazu "aktuell": das neueste Material und die neuesten Goodnotes-Seiten (nach dem
    darauf notierten Datum), für einen schnellen Blick, was gerade dran ist."""
    faecher = notenablage.standard().uebersicht()["faecher"]
    f = next((x for x in faecher if x["id"] == fach_id), None)
    if not f:
        return None
    material = [d for d in gruppenordnerablage.standard().alle() if d["fach_id"] == fach_id]
    hefte = _goodnotes_hefte(f["name"], fach_id, faecher)
    gelesenes_material = sorted((d for d in material if d["status"] == "gelesen"),
                                key=lambda d: d["geaendert"], reverse=True)
    return {
        "fach": f,
        "arbeiten": [a for a in arbeitenablage.standard().alle() if a["fach_id"] == fach_id],
        "muster": [m for m in arbeitenablage.standard().muster_liste() if m["fach_id"] == fach_id],
        "material": material,
        "goodnotes": hefte,
        "aktuell": {
            "material": gelesenes_material[:5],
            "seiten": goodnotesablage.standard().neueste_seiten([h["id"] for h in hefte], 5),
        },
    }
