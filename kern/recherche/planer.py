"""Der Planer der Recherchen (15.10.2026): holt wartende Aufträge der Reihe nach und lässt sie laufen — immer nur einer zur Zeit.

Ein Neustart mitten im Lauf setzt den Auftrag zurück in die Warteschlange (zweimal, dann Fehler). Jeder Fehler wird gefangen: der Auftrag
steht dann auf `fehler`, Eike bekommt einen Push, und der Planer läuft weiter."""

from __future__ import annotations

import asyncio
import logging

from kern.dienste import hintergrund
from kern.recherche import ergebnis, lauf, speicher

log = logging.getLogger("friday.recherche")

TAKT_S = 20


def start_aufraeumen() -> int:
    return speicher.laufende_zuruecksetzen()


async def schritt(anfrage=None) -> str | None:
    """Ein Takt unter der gemeinsamen Hintergrund-Sperre (nie zugleich mit einem Aufgabenblatt-Lauf)."""
    async with hintergrund.SPERRE:
        return await _schritt(anfrage)


async def _schritt(anfrage=None) -> str | None:
    """Ein Takt: den ältesten wartenden Auftrag abarbeiten. Gibt den Ausgang zurück (`fertig|fehler|abgebrochen`) oder None."""
    r = speicher.naechster_wartender()
    if r is None:
        return None
    speicher.beginnen(r["id"])
    try:
        erg = await lauf.recherchieren(r["id"], anfrage=anfrage)
    except Exception as exc:  # noqa: BLE001 — ein Lauf darf den Planer nie töten
        log.warning("Recherche %s gescheitert (%s)", r["id"], type(exc).__name__)
        if not speicher.scheitern(r["id"], f"{type(exc).__name__}: {exc}"):
            return "abgebrochen"                            # Eike hat inzwischen abgebrochen: kein Fehler-Push
        await ergebnis.abliefern(r["id"])
        return "fehler"
    if erg.get("status") == "abgebrochen" or (speicher.holen(r["id"]) or {}).get("status") != "fertig":
        return "abgebrochen"
    await ergebnis.abliefern(r["id"])
    return "fertig"


async def dauerlauf() -> None:
    await asyncio.sleep(60)
    try:
        if n := start_aufraeumen():
            log.warning("Recherche: %d unterbrochene Aufträge zurückgesetzt", n)
    except Exception:  # noqa: BLE001
        log.exception("Recherche: Aufräumen beim Start gescheitert")
    while True:
        try:
            await schritt()
        except Exception as exc:  # noqa: BLE001
            log.warning("Recherche-Planer gescheitert (%s)", type(exc).__name__)
        await asyncio.sleep(TAKT_S)
