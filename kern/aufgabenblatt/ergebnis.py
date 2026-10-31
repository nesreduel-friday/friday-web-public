"""Ein fertiger Aufgabenblatt-Auftrag wird gemeldet (15.10.2026): Push und Karte in der Übersicht. KEIN Gedanke im Gedankenbuch — Schulaufgaben
gehören nicht ins Langzeitgedächtnis. Die volle Lösung bleibt im Speicher (Friday liest sie im Chat vor: `aufgaben_lesen`). Jede Zugabe ist
abgesichert: ein Fehler bei der Karte darf den Push nie kippen und umgekehrt."""

from __future__ import annotations

import logging

from kern.aufgabenblatt import speicher
from kern.dashboard import friday_karten
from kern.dienste import benachrichtigungen

log = logging.getLogger("friday.aufgabenblatt")

KARTE_MINUTEN = 720


def _kurz(t: str, laenge: int) -> str:
    t = " ".join(str(t).split())
    return t if len(t) <= laenge else t[:laenge - 2].rsplit(" ", 1)[0].rstrip(",;:- ")[:laenge - 2] + " …"


async def abliefern(aid: int) -> None:
    a = speicher.holen(aid)
    if not a or a["status"] in ("abgebrochen", "wartend", "laeuft"):
        return
    name = (speicher.blatt_holen(a["blatt"]) or {}).get("name", "Aufgabenblatt")
    if a["status"] == "fehler":
        try:
            benachrichtigungen.senden("friday", "Aufgaben gescheitert", f"„{_kurz(name, 60)}“ hat nicht geklappt: {_kurz(a['fehler'] or 'unbekannter Fehler', 60)}", url="/#chat")
        except Exception:  # noqa: BLE001
            log.exception("Aufgabenblatt %s: Fehler-Push nicht gesendet", aid)
        return
    try:
        benachrichtigungen.senden("friday", "Aufgaben fertig", _kurz(f"„{_kurz(name, 70)}“ ist bearbeitet — frag mich nach der Lösung.", 160), url="/#chat")
    except Exception:  # noqa: BLE001
        log.exception("Aufgabenblatt %s: Push nicht gesendet", aid)
    try:
        unsicher = sum(1 for l in a["loesungen"] if not l["sicher"])
        zusatz = f" ({unsicher} unsicher)" if unsicher else ""
        text = _kurz(" · ".join(f"{l['nummer']}: {l['loesung']}" for l in a["loesungen"]), 360)
        await friday_karten.legen_datei(f"Aufgaben: {_kurz(name, 55)}", f"{text}{zusatz}", f"/api/aufgabenblatt/{aid}/ausarbeitung",
                                        f"Ausarbeitung {_kurz(name, 50)}", minuten=KARTE_MINUTEN)
    except Exception:  # noqa: BLE001
        log.exception("Aufgabenblatt %s: Karte nicht gelegt", aid)
