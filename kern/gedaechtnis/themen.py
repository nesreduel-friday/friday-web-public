"""Die Themen-Karten der Wissen-Seite: was Friday über Eike weiß, nach Wichtigkeit und Relevanz sortiert.

Ein Thema ist ein Knoten vom Typ Thema, Ereignis, Organisation, Projekt oder Ort (Personen haben ihre eigene Ansicht).
Wichtig ist, woran viele Fakten hängen, was in vielen Gesprächen vorkam und was frisch ist. Der Satz auf der Karte kommt aus
dem Graphen (Zusammenfassung, sonst der neueste gültige Fakt) — ohne Modellaufruf.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone

from kern.gedaechtnis import wissensgraph

log = logging.getLogger("friday.themen")

THEMEN_TYPEN = ("Topic", "Event", "Organization", "Project", "Location")
MAX_KARTEN = 60
KURZ_ZEICHEN = 140
HALBWERTSZEIT_TAGE = 60.0


def wichtigkeit(fakten: int, gespraeche: int, tage: float | None) -> float:
    """Je mehr Fakten und Gespräche und je frischer, desto wichtiger. Ein unbekanntes Datum gilt wie „mittel alt“."""
    grundwert = 2.0 * math.sqrt(max(fakten, 0)) + 1.5 * max(gespraeche, 0)
    frische = 0.5 + 0.5 * math.exp(-tage / HALBWERTSZEIT_TAGE) if tage is not None else 0.75
    return round(grundwert * frische, 3)


def kurz(zusammenfassung: str, bester_fakt: str) -> str:
    """Ein Satz für die Karte: der erste Satz der Zusammenfassung, sonst der beste Fakt, höchstens ~140 Zeichen."""
    text = " ".join((zusammenfassung or "").split()) or " ".join((bester_fakt or "").split())
    if not text:
        return ""
    # Satzende = Punkt/!/? NACH einem Nicht-Ziffernzeichen und vor einem Großbuchstaben („dem 14. September“ trennt nicht).
    erster = re.split(r"(?<=[^0-9\s][.!?])\s+(?=[A-ZÄÖÜ])", text, maxsplit=1)[0]
    if len(erster) > KURZ_ZEICHEN:
        erster = erster[:KURZ_ZEICHEN].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return erster


def _tage(zeitstempel) -> int | None:
    try:
        t = datetime.fromisoformat(str(zeitstempel).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - t).days, 0)


async def themen() -> dict:
    if not wissensgraph.eingerichtet():
        return {"eingerichtet": False, "erreichbar": True, "themen": []}
    markierung = " OR ".join(f"n:{t}" for t in THEMEN_TYPEN)
    try:
        zeilen = await wissensgraph._abfrage(
            f"MATCH (n:Entity) WHERE {markierung} "
            "OPTIONAL MATCH (n)-[r:RELATES_TO]-() WITH n, count(r) AS k "
            "OPTIONAL MATCH (e:Episodic)-[:MENTIONS]->(n) "
            "RETURN n.uuid, n.name, labels(n), n.summary, k, count(DISTINCT e), max(e.valid_at), n.group_id")
        fakten = await wissensgraph._abfrage(
            "MATCH (n:Entity)-[r:RELATES_TO]-() WHERE r.invalid_at IS NULL "
            "RETURN n.uuid, r.fact ORDER BY r.valid_at DESC")
    except Exception as exc:  # noqa: BLE001
        log.warning("Themen nicht lesbar: %s", type(exc).__name__)
        return {"eingerichtet": True, "erreichbar": False, "themen": []}

    bester: dict[str, str] = {}
    for uuid, fakt in fakten:
        bester.setdefault(str(uuid), str(fakt or ""))     # die Zeilen sind nach Datum absteigend sortiert: erste = neueste

    karten = []
    for uuid, name, labels, zusammenfassung, k, gespraeche, zuletzt, gruppe in zeilen:
        name = str(name or "")
        if not name or name.lower() in ("friday", "eike") or gruppe == "friday-test" or not k:
            continue
        tage = _tage(zuletzt)
        typ = next((t for t in THEMEN_TYPEN if t in (labels or [])), "Topic")
        karten.append({"uuid": str(uuid), "name": name, "typ": typ, "fakten": int(k), "gespraeche": int(gespraeche or 0),
                       "tage": tage, "kurz": kurz(str(zusammenfassung or ""), bester.get(str(uuid), "")),
                       "wichtigkeit": wichtigkeit(int(k), int(gespraeche or 0), tage)})
    karten.sort(key=lambda c: (-c["wichtigkeit"], c["name"].lower()))
    return {"eingerichtet": True, "erreichbar": True, "themen": karten[:MAX_KARTEN]}
