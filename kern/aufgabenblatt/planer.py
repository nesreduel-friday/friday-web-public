"""Der Planer der Aufgabenblätter (15.10.2026): holt wartende Aufträge der Reihe nach — immer nur einer zur Zeit, und nie zugleich mit einer Recherche
(gemeinsame Sperre `hintergrund.SPERRE`). Ein Neustart mitten im Lauf setzt den Auftrag zurück in die Warteschlange (zweimal, dann Fehler)."""

from __future__ import annotations

import asyncio
import logging

from kern.aufgabenblatt import ergebnis, lauf, speicher
from kern.dienste import hintergrund

log = logging.getLogger("friday.aufgabenblatt")

TAKT_S = 20


def start_aufraeumen() -> int:
    return speicher.laufende_zuruecksetzen()


async def schritt(anfrage=None) -> str | None:
    """Ein Takt: den ältesten wartenden Auftrag abarbeiten. Gibt den Ausgang zurück (`fertig|fehler|abgebrochen`) oder None."""
    async with hintergrund.SPERRE:
        a = speicher.naechster_wartender()
        if a is None:
            return None
        speicher.beginnen(a["id"])
        try:
            erg = await lauf.aufgaben_loesen(a["id"], anfrage=anfrage)
        except Exception as exc:  # noqa: BLE001 — ein Lauf darf den Planer nie töten
            log.warning("Aufgabenblatt %s gescheitert (%s)", a["id"], type(exc).__name__)
            if not speicher.scheitern(a["id"], f"{type(exc).__name__}: {exc}"):
                return "abgebrochen"                        # Eike hat inzwischen abgebrochen: kein Fehler-Push
            await ergebnis.abliefern(a["id"])
            return "fehler"
        if erg.get("status") == "abgebrochen" or (speicher.holen(a["id"]) or {}).get("status") != "fertig":
            return "abgebrochen"
        await ergebnis.abliefern(a["id"])
        return "fertig"


async def dauerlauf() -> None:
    await asyncio.sleep(75)
    try:
        if n := start_aufraeumen():
            log.warning("Aufgabenblatt: %d unterbrochene Aufträge zurückgesetzt", n)
    except Exception:  # noqa: BLE001
        log.exception("Aufgabenblatt: Aufräumen beim Start gescheitert")
    while True:
        try:
            await schritt()
        except Exception as exc:  # noqa: BLE001
            log.warning("Aufgabenblatt-Planer gescheitert (%s)", type(exc).__name__)
        await asyncio.sleep(TAKT_S)
