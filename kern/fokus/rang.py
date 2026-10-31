"""Fokus-Personen: Vorrang bei der Verarbeitung — Fokus (0), Eikes eigene Gespräche (1), der Rest (2).

Die Reihenfolge innerhalb eines Rangs bleibt (stabile Sortierung); ohne markierte Personen ändert sich nichts.
"""

from __future__ import annotations

import re

from kern.fokus import ablage
from kern.personen import register


def _woerter(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß0-9]+", (text or "").casefold())


def kommt_vor(text: str, name: str) -> bool:
    """Alle Wörter des Namens (ab 2 Zeichen) kommen als GANZE Wörter im Text vor."""
    gesucht = [w for w in _woerter(name) if len(w) >= 2]
    if not gesucht:
        return False
    vorhanden = set(_woerter(text))
    return all(w in vorhanden for w in gesucht)


def ist_fokus(name: str) -> bool:
    return any(kommt_vor(name, n) and kommt_vor(n, name) for n in ablage.namen())


def rang_von(zusammenfassung: str, person_id: str | None, fokusnamen: list[str]) -> int:
    if any(kommt_vor(zusammenfassung, n) for n in fokusnamen):
        return 0
    return 1 if person_id is None else 2


def _personenname(person_id: str) -> str:
    p = register.holen(person_id) or {}
    return p.get("graph_name") or p.get("name") or ""


def sortiert(ids: list[int], protokoll) -> list[int]:
    """`ids` nach Rang (stabil). Gespräche, die eine markierte Person führt, zählen wie Erwähnungen in der Zusammenfassung."""
    fokus = ablage.namen()
    if not fokus:
        return list(ids)
    raenge = []
    for pos, i in enumerate(ids):
        kopf = protokoll.kopf(i) or {}
        person = kopf.get("person")
        r = rang_von(kopf.get("zusammenfassung") or "", person, fokus)
        if r != 0 and person and any(kommt_vor(_personenname(person), n) and kommt_vor(n, _personenname(person)) for n in fokus):
            r = 0
        raenge.append((r, pos, i))
    return [i for _, _, i in sorted(raenge)]


def personen_sortiert(personen: list[dict]) -> list[dict]:
    """Personen aus der Chat-Ablage: markierte zuerst, sonst unverändert."""
    return sorted(personen, key=lambda p: 0 if ist_fokus(str(p.get("name") or "")) else 1)
