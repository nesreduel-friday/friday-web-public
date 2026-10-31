"""Die Karte „Regeln“ in der Übersicht (15.10.2026): zeigt, was Friday für Eikes Regeln getan hat.

Es gibt immer genau eine Karte (auch ohne Regeln), sie läuft nie ab. Jeder Lauf hat „gesehen“ (`regeln:<lauf_id>`): Eike
blendet einzelne Einträge aus, die Karte bleibt. Gezeigt werden nur Kurztexte (Auslöser höchstens 120 Zeichen)."""

from __future__ import annotations

import re

from kern.regeln import speicher, zeit

ID = "regeln"
ANZAHL = 4


async def karten() -> list[dict]:
    regeln = speicher.liste()
    laeufe = [{"id": f"{ID}:{l['id']}", "zeit": l["zeit"], "regel": l["regel"], "ausloeser": l["ausloeser_kurz"], "status": l["status"],
               "aktionen": [a.get("kurz", "") for a in l["aktionen"] if a.get("kurz")], "gesehen": l["gesehen"]}
              for l in speicher.laeufe(ANZAHL)]
    return [{"id": ID, "art": "regeln", "titel": "Regeln",
             "aktiv": sum(1 for r in regeln if r["status"] == "aktiv"),
             "pausiert": sum(1 for r in regeln if r["status"] == "pausiert"),
             "pausiert_namen": [r["name"] for r in regeln if r["status"] == "pausiert"],
             "regeln": [{"id": r["id"], "name": r["name"], "status": r["status"], "wann": zeit.ausloeser_text(r)}
                        for r in regeln if r["status"] in ("entwurf", "aktiv", "pausiert")],
             "laeufe": laeufe}]


def gesehen(karten_id: str) -> bool:
    m = re.fullmatch(rf"{ID}:([0-9]{{1,9}})", karten_id or "")
    return bool(m) and speicher.gesehen_setzen(int(m.group(1)))
