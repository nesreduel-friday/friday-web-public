"""Bekannte Personen: alles, wen Friday schon aus irgendeiner Quelle kennt — zur Auswahl beim Anlegen eines Kontakts.

Quellen: Wissensgraph (Person-Einträge), Familie (Standort), ÜSTRA-Garantie-Profile, WhatsApp-Verläufe, benannte
Bilder. Zusammengeführt ohne Groß-/Kleinschreibung; Eike und Friday und schon angelegte Kontakte fehlen. Fällt eine
Quelle aus, fehlt nur sie (`ausgefallen`); ausgeschaltete Konnektoren zählen nicht als Ausfall. Nur Namen — nie Inhalte."""

from __future__ import annotations

import logging

from kern import konnektoren
from kern.chats import ablage as chatsablage
from kern.dienste import personenstandort as personendienst
from kern.garantie import ablage as garantieablage
from kern.gedaechtnis import bilder, wissensgraph
from kern.personen import register

log = logging.getLogger("friday.personen")


def _garantie_namen() -> list[str]:
    return [p.get("name", "") for p in garantieablage.standard().profile() if not p.get("archiviert")]


def _chat_namen() -> list[str]:
    return [p.get("name", "") for p in chatsablage.standard().personen() if not p.get("ich")]


def _bild_namen() -> list[str]:
    return [b.get("name") or "" for b in bilder.liste(500)]


async def liste() -> dict:
    """`{"personen": [{"name", "quellen": [...]}], "ausgefallen": [Quellennamen]}`, nach Namen sortiert."""
    gefunden: list[tuple[str, str]] = []      # (Quelle, Name) in der Reihenfolge der Quellen
    ausgefallen: list[str] = []

    try:
        w = await wissensgraph.uebersicht()
        if w.get("eingerichtet") and not w.get("erreichbar", True):
            ausgefallen.append("Wissen")
        else:
            gefunden += [("Wissen", e["name"]) for e in w.get("entitaeten", []) if e.get("typ") == "Person"]
    except Exception:  # noqa: BLE001 — eine Quelle darf die Liste nie kippen
        log.warning("Bekannte Personen: Wissensgraph nicht lesbar")
        ausgefallen.append("Wissen")

    if konnektoren.benutzbar("personenstandort"):
        try:
            gefunden += [("Familie", p.get("name", "")) for p in await personendienst.personen()]
        except Exception:  # noqa: BLE001
            log.warning("Bekannte Personen: Familie nicht lesbar")
            ausgefallen.append("Familie")

    for quelle, funktion in (("Garantie", _garantie_namen), ("WhatsApp", _chat_namen), ("Bilder", _bild_namen)):
        try:
            gefunden += [(quelle, n) for n in funktion()]
        except Exception:  # noqa: BLE001
            log.warning("Bekannte Personen: %s nicht lesbar", quelle)
            ausgefallen.append(quelle)

    vergeben = {register.schluessel(p.get("graph_name") or p["name"]) for p in register.liste()}
    personen: dict[str, dict] = {}
    for quelle, roh in gefunden:
        name = register._text(roh, register.NAME_MAX)
        k = register.schluessel(name)
        if not k or k in register.RESERVIERT or k in vergeben:
            continue
        p = personen.setdefault(k, {"name": name, "quellen": []})
        if quelle not in p["quellen"]:
            p["quellen"].append(quelle)
    return {"personen": sorted(personen.values(), key=lambda p: register.schluessel(p["name"])),
            "ausgefallen": ausgefallen}
