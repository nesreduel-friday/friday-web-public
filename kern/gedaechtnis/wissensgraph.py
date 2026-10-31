"""Der Wissensgraph zum Ansehen — Personen, Themen, Orte … und was Friday über sie weiß.

Liest Graphitis Daten direkt aus FalkorDB, nur lesend (`ro_query`). Die Suche
über MCP (`graph.py`) liefert die semantisch besten Treffer zu einer Frage;
zum Durchblättern nach Typ braucht es aber ALLE Entitäten, und das kann nur
die Datenbank selbst.

Graphiti legt pro Gruppe einen eigenen Graphen an — gemessen am 12.09.2026:
Eikes Daten stehen im Graphen "eike", nicht in "friday" aus der Konfiguration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from kern.gedaechtnis import bilder, graph

log = logging.getLogger("friday.wissensgraph")

# Reihenfolge = Reihenfolge der Reiter auf der Seite.
TYPEN = [("Person", "Personen"), ("Project", "Projekte"), ("Topic", "Themen"), ("Location", "Orte"),
         ("Event", "Ereignisse"), ("Organization", "Organisationen"), ("Preference", "Vorlieben"),
         ("Document", "Dokumente"), ("Procedure", "Abläufe"), ("Requirement", "Anforderungen")]
SONSTIGES = ("Sonstiges", "Sonstiges")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _host() -> str:
    return os.getenv("FALKORDB_HOST", "").strip()


def eingerichtet() -> bool:
    return bool(_host())


def _abfrage_sync(cypher: str, werte: dict | None = None) -> list:
    from falkordb import FalkorDB  # erst hier: Tests ohne Datenbank brauchen das Paket nicht

    db = FalkorDB(host=_host(), port=int(os.getenv("FALKORDB_PORT", "6379")))
    return db.select_graph(graph.GRUPPE).ro_query(cypher, werte or {}).result_set


async def _abfrage(cypher: str, werte: dict | None = None) -> list:
    return await asyncio.to_thread(_abfrage_sync, cypher, werte)


def _typ(labels) -> str:
    labels = set(labels or [])
    for typ, _ in TYPEN:
        if typ in labels:
            return typ
    return SONSTIGES[0]


def _text(wert) -> str:
    return "" if wert is None else str(wert)


async def uebersicht() -> dict:
    """Alle Entitäten, nach Typ gezählt. Eike selbst steht als "Über dich" obenan,
    Friday wird ausgeblendet — über sich selbst muss sie nichts nachschlagen."""
    if not eingerichtet():
        return {"eingerichtet": False, "typen": [], "entitaeten": [], "du": None}
    try:
        zeilen = await _abfrage(
            "MATCH (n:Entity) OPTIONAL MATCH (n)-[r:RELATES_TO]-() "
            "RETURN n.uuid, n.name, labels(n), n.summary, count(r), n.group_id ORDER BY n.name")
    except Exception as exc:
        log.warning("Wissensgraph nicht lesbar: %s", type(exc).__name__)
        return {"eingerichtet": True, "erreichbar": False, "typen": [], "entitaeten": [], "du": None}

    entitaeten, du = [], None
    for uuid, name, labels, zusammenfassung, anzahl, gruppe in zeilen:
        name = _text(name)
        if name.lower() == "friday":
            continue
        # Die Testgruppe gehört nie in Eikes Ansicht (03.10.2026: eine Test-Episode
        # hatte eine zweite "Eike"-Entität angelegt und die "Über dich"-Karte auf
        # 1 Fakt gedrückt, weil bei Namensgleichstand der letzte Treffer gewann).
        if gruppe == "friday-test":
            continue
        eintrag = {"uuid": _text(uuid), "name": name, "typ": _typ(labels),
                   "zusammenfassung": _text(zusammenfassung), "fakten": int(anzahl or 0)}
        if name.lower() == "eike":
            if du is None or gruppe == "eike":
                du = eintrag
            continue
        entitaeten.append(eintrag)

    namen = dict(TYPEN + [SONSTIGES])
    zaehlung = {}
    for e in entitaeten:
        zaehlung[e["typ"]] = zaehlung.get(e["typ"], 0) + 1
    reihenfolge = [t for t, _ in TYPEN] + [SONSTIGES[0]]
    typen = [{"typ": t, "name": namen[t], "anzahl": zaehlung[t]} for t in reihenfolge if t in zaehlung]
    return {"eingerichtet": True, "erreichbar": True, "typen": typen, "entitaeten": entitaeten, "du": du}


async def entitaet(uuid: str) -> dict | None:
    """Eine Entität mit allen Fakten (gültig und überholt), den Gesprächen, in
    denen sie vorkam, und den Bildern, die ihren Namen tragen."""
    from kern.werkzeuge.gedaechtnis import datum

    if not UUID.match(uuid or "") or not eingerichtet():
        return None
    knoten = await _abfrage("MATCH (n:Entity {uuid: $u}) RETURN n.name, labels(n), n.summary", {"u": uuid})
    if not knoten:
        return None
    name, labels, zusammenfassung = knoten[0]
    kanten = await _abfrage(
        "MATCH (n:Entity {uuid: $u})-[r:RELATES_TO]-(m:Entity) "
        "RETURN r.fact, r.valid_at, r.invalid_at, m.name, m.uuid ORDER BY r.valid_at", {"u": uuid})
    erwaehnt = await _abfrage(
        "MATCH (e:Episodic)-[:MENTIONS]->(n:Entity {uuid: $u}) RETURN e.name", {"u": uuid})

    fakten, gesehen = [], set()
    for fakt, seit, bis, mit_name, mit_uuid in kanten:
        schluessel = (_text(fakt).strip().lower(), bool(bis))
        if not fakt or schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        fakten.append({"fakt": _text(fakt), "seit": datum(_text(seit)), "bis": datum(_text(bis)) if bis else "",
                       "mit": _text(mit_name), "mit_uuid": _text(mit_uuid)})
    gespraeche = sorted({int(n.split("-", 1)[1]) for (n,) in erwaehnt
                         if _text(n).startswith("gespraech-") and _text(n).split("-", 1)[1].isdigit()})
    return {"uuid": uuid, "name": _text(name), "typ": _typ(labels), "zusammenfassung": _text(zusammenfassung),
            "fakten": fakten, "gespraeche": gespraeche, "bilder": bilder.mit_namen(_text(name))}
