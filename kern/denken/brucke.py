"""Brücke zu Graphiti: Fridays Gedanken werden als Episode (eine je Runde) ins Langzeitgedächtnis gegeben.

Gedanken sind Fridays Schlüsse, keine Aussagen von Eike — die Extraktionsanweisung sagt das ausdrücklich, damit
daraus keine „Fakten, die Eike gesagt hat“ werden. Nachsehen wie beim Rückblick: Graphitis Warteschlange verwirft
Episoden still; nach 10 Minuten wird geprüft und höchstens dreimal geschickt.
"""

from __future__ import annotations

import logging
from datetime import datetime

from kern import config
from kern.denken import buch as gedankenbuch
from kern.gedaechtnis import graph

log = logging.getLogger("friday.denken")

MAX_VERSUCHE = 3
ANWEISUNGEN = (
    "Das sind eigene Gedanken der KI-Assistentin Friday beim Nachdenken, keine Aussagen von Eike. "
    "Halte sie als Überlegungen, Verbindungen und offene Fragen von Friday fest. Mache daraus nie Fakten, die Eike "
    "gesagt oder bestätigt hätte. Gedanken, die als vermutet oder als aus fremdem Text gekennzeichnet sind, sind unbestätigt. "
    "Formuliere auf Deutsch."
)


def _name(rid: int) -> str:
    return f"gedanken-runde-{rid}"


def _text(r: dict, gedanken: list[dict]) -> str:
    t = datetime.fromisoformat(r["start"]).astimezone(config.jetzt().tzinfo)
    zeilen = [f"Fridays Gedanken beim Nachdenken am {t:%d.%m.%Y um %H:%M}:"]
    for g in gedanken:
        herkunft = ", aus fremdem Text" if g["fremd"] else ""
        quellen = f" [Quellen: {'; '.join(g['quellen'])}]" if g["quellen"] else ""
        zeilen.append(f"- ({g['art']}, {g['sicherheit']}{herkunft}) {g['text']}{quellen}")
    return "\n".join(zeilen)


def _graph_beschaeftigt() -> bool:
    """Warten noch Gespräche auf Graphiti oder sind welche in Arbeit? Dann haben sie Vorrang: Graphitis Tageslimit ist
    knapp (08.10.2026: 161 wartende Gespräche), Gedanken dürfen sie nicht überholen."""
    from kern.protokoll import speicher as pspeicher
    p = pspeicher.standard()
    return bool(p.graph_offen()) or p.graph_in_arbeit() > 0


async def einspeisen_offene(b: gedankenbuch.Buch | None = None) -> None:
    b = b or gedankenbuch.standard()
    if graph.pausiert() or _graph_beschaeftigt():
        return
    for r in b.runden_fuer_graph(1):
        ok = await graph.einspeisen(_name(r["id"]), _text(r, b.gedanken_der_runde(r["id"], nur_graph=True)),
                                    "Fridays eigenes Nachdenken (keine Aussage von Eike)", r["start"],
                                    anweisungen=ANWEISUNGEN)
        if ok:
            b.graph_setzen(r["id"], "gesendet")


async def nachsehen(b: gedankenbuch.Buch | None = None) -> None:
    b = b or gedankenbuch.standard()
    for r in b.runden_zu_pruefen(10):
        da = await graph.angekommen(_name(r["id"]), r["graph_gesendet"])
        if da:
            b.graph_setzen(r["id"], "angekommen")
        elif da is False:
            b.graph_setzen(r["id"], "aufgegeben" if r["graph_versuche"] >= MAX_VERSUCHE else None)
