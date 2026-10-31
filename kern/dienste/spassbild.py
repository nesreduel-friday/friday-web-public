"""Bilder für Snaps an Personen: lustig oder auf Wunsch (14.10.2026). Dünne Schicht über `bildsuche` — Kandidaten holen,
prüfen lassen, das beste nehmen, nie ein schon gesendetes (`ausser`)."""

from __future__ import annotations

import asyncio
import random

from kern.dienste import bildsuche

LUSTIG = ("lustige Katze", "lustiger Hund", "lustiges Tier Foto", "lustiges Meerschweinchen", "lustiger Pinguin",
          "lustiges Otter Foto", "lustiges Kaninchen", "lustige Ente", "lustiger Papagei", "lustiges Faultier")


def _ist_hochkant(t: dict) -> bool:
    """Deutlich höher als breit (mindestens 1,2 : 1); unbekannte Maße zählen nicht."""
    try:
        breite, hoehe = int(t.get("breite") or 0), int(t.get("hoehe") or 0)
    except (TypeError, ValueError):
        return False
    return breite > 0 and hoehe >= 1.2 * breite


async def _versuch(begriff: str, ausser: set[str], wunsch: str = "", hochkant: bool = False) -> tuple[bytes, str, str] | None:
    treffer = [t for t in await bildsuche.kandidaten(begriff) if t.get("bild") not in ausser]
    if not treffer:
        return None
    kennungen = await asyncio.gather(*(bildsuche.holen(t["bild"]) for t in treffer))
    geholt = [(t, k) for t, k in zip(treffer, kennungen) if k]
    if not geholt:
        return None
    # Ein Snap an eine Person geht nur raus, wenn das Bild geprüft ist: es passt zum WUNSCH (nicht nur zum Suchbegriff) und ist
    # unbedenklich. Ohne Prüfung (Modell weg, Kontingent leer) geht nichts raus.
    urteil = await bildsuche.pruefen(wunsch or begriff, [(k, t["titel"]) for t, k in geholt], harmlos=True)
    if urteil is None:
        return None
    geholt = sorted((x for x in geholt if urteil.get(x[1], {}).get("passt") and urteil[x[1]].get("harmlos")),
                    # Hochkant gewünscht (automatische Snaps): geprüfte Hochkant-Bilder zuerst, sonst das beste Querformat
                    key=lambda x: (not (hochkant and _ist_hochkant(x[0])), -urteil[x[1]]["qualitaet"]))
    kopie = bildsuche.bild(geholt[0][1]) if geholt else None
    if not kopie:
        return None
    t = geholt[0][0]
    bildsuche.gezeigt_merken(t["bild"])
    return kopie[0], t["bild"], " ".join(str(t.get("titel") or "").split())[:100]


async def finden(wunsch: str = "", ausser: set[str] | None = None, hochkant: bool = False) -> tuple[bytes, str, str] | None:
    """(Bildbytes, Quell-URL, Titel) oder None. `wunsch` leer = irgendetwas Lustiges. Die Suchbegriffe macht `suchplan`;
    sie werden der Reihe nach probiert, bis einer ein geprüftes Bild liefert. `hochkant=True`: Hochkant-Bilder bevorzugen
    (das Handy-Format der automatischen Snaps), wenn es keines gibt, gilt das Querformat."""
    ausser = ausser or set()
    wunsch = " ".join(str(wunsch or "").split())[:100]
    begriffe = await bildsuche.suchplan(wunsch, lustig=not wunsch) or [wunsch or random.choice(LUSTIG)]
    for begriff in begriffe:
        gefunden = await _versuch(begriff, ausser, wunsch, hochkant)
        if gefunden:
            return gefunden
    return None
