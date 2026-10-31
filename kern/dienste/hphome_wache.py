"""Wache für hphome — Friday sagt Bescheid, wenn die Platte fast voll ist oder ein Dienst ausfällt.

Am 26.09.2026 stand die Platte bei 97 %, ohne dass es jemand merkte. Diese Wache liest den Zustand des
Helfers (`hphome.zustand_roh`) und meldet über `benachrichtigungen` (Art `server`, abschaltbar; in der Ruhezeit
zurückgehalten und beim ersten Lauf danach gesendet):

- **Platte** ab 90 %; erst nach einem Rückgang unter 85 % wieder.
- **Container**, der `unhealthy` ist, oder der seit mindestens drei Minuten nicht läuft — aber nur, wenn er
  vorher lief (`gesehen`), nicht absichtlich in der App gestoppt wurde und nicht gerade der Host neu startet
  (`wartung_bis`). Ein Container, der ganz verschwindet, gilt als bewusst entfernt (`compose down`).
- Derselbe Befund höchstens alle 6 Stunden — ein Container im Neustart-Dauerlauf soll nicht das Handy fluten.

Meldet sich der Helfer nicht, weiß Friday nichts: dann kommt keine Meldung (und keine Entwarnung).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from kern import config
from kern.dienste import benachrichtigungen, hphome

log = logging.getLogger("friday.hphome_wache")

ART = "server"
TAKT_S = 60
PLATTE_MELDEN = 90.0
PLATTE_ENTWARNUNG = 85.0
DOWN_S = 180
SPERRE_S = 6 * 3600


def _datei():
    return config.daten_ordner() / "hphome" / "wache.json"


def stand_lesen() -> dict:
    try:
        d = json.loads(_datei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    return {"platte": bool(d.get("platte")), "gesehen": list(d.get("gesehen") or []),
            "down": dict(d.get("down") or {}), "gemeldet": dict(d.get("gemeldet") or {})}


def stand_schreiben(st: dict) -> None:
    pfad = _datei()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(pfad)


def pruefen(z: dict | None, jetzt: float, st: dict) -> list[tuple[str, str, str]]:
    """(Schlüssel, Titel, Text) dessen, was JETZT zu melden ist. Verändert `st` (Hysterese, Sperren)."""
    if not z or not z.get("helfer") or (z.get("wartung_bis") or 0) > jetzt:
        return []
    gefunden: list[tuple[str, str, str]] = []
    for p in (z.get("host") or {}).get("platten") or []:
        prozent = p.get("prozent")
        if prozent is None:
            continue
        if prozent >= PLATTE_MELDEN and not st["platte"]:
            st["platte"] = True
            # Nutzbarer Platz vom Helfer (ohne root-Reserve, wie df) — nie gesamt − belegt; fehlt er: „?“.
            frei = p.get("frei_gb")
            frei_text = (("?" if not isinstance(frei, (int, float))
                          else f"{frei:.0f} GB" if frei >= 10 else f"{frei:.1f} GB"))
            gefunden.append(("platte", "Platte fast voll",
                             f"{p.get('pfad', '/')} ist zu {prozent:.0f} % belegt — nur noch {frei_text} frei."))
        elif prozent < PLATTE_ENTWARNUNG:
            st["platte"] = False
            st["gemeldet"].pop("platte", None)          # Entwarnung: eine neue Überschreitung darf sofort wieder melden
    if z.get("container_ok", True):
        vorhanden, laufend, weg = set(), set(), set()
        for c in z.get("container") or []:
            n = c.get("name")
            vorhanden.add(n)
            if c.get("zustand") == "running":
                laufend.add(n)
                st["down"].pop(n, None)
                if c.get("gesundheit") == "unhealthy":
                    gefunden.append((f"unhealthy:{n}", f"{n} ist nicht gesund",
                                     f"Der Dienst „{n}“ läuft, besteht aber seine Gesundheitsprüfung nicht."))
            elif n in st["gesehen"] and not c.get("absichtlich_gestoppt"):
                weg.add(n)
            else:
                st["down"].pop(n, None)
        for n in list(st["gesehen"]):
            if n not in vorhanden:                         # bewusst entfernt
                st["gesehen"].remove(n)
                st["down"].pop(n, None)
        for n in weg:
            seit = st["down"].setdefault(n, jetzt)
            if jetzt - seit >= DOWN_S:
                gefunden.append((f"down:{n}", f"{n} ist ausgefallen",
                                 f"Der Dienst „{n}“ läuft seit {int((jetzt - seit) // 60)} Minuten nicht mehr."))
        st["gesehen"] = sorted(set(st["gesehen"]) | laufend)
    heraus = []
    for schluessel, titel, text in gefunden:
        zuletzt = st["gemeldet"].get(schluessel)
        if zuletzt is None or jetzt - float(zuletzt) >= SPERRE_S:      # noch nie gemeldet oder Sperre um
            st["gemeldet"][schluessel] = jetzt
            heraus.append((schluessel, titel, text))
    return heraus


async def einmal(jetzt: float | None = None) -> list[tuple[str, str, str]]:
    jetzt = time.time() if jetzt is None else jetzt
    z = await asyncio.to_thread(hphome.zustand_roh)
    st = stand_lesen()
    meldungen = pruefen(z, jetzt, st)
    if benachrichtigungen.an(ART):
        for schluessel, titel, text in meldungen:
            try:
                # Eigenes Tag je Befund: sonst ersetzt auf dem Handy jede Server-Meldung die vorige.
                ergebnis = await asyncio.to_thread(benachrichtigungen.senden, ART, titel, text, "/#studio",
                                                   tag=f"server-{schluessel.replace(':', '-')}")
            except Exception as exc:                       # ein Push darf die anderen nicht aufhalten
                log.warning("hphome-Wache: Meldung „%s“ nicht gesendet (%s)", schluessel, type(exc).__name__)
                ergebnis = None
            # Ruhezeit oder Fehler: nicht als gemeldet merken — der nächste Lauf danach sendet es.
            # („art_aus“ bleibt gemeldet: abgeschaltet heißt nicht nachholen.)
            if ergebnis is None or (isinstance(ergebnis, dict) and ergebnis.get("unterdrueckt") == "ruhezeit"):
                st["gemeldet"].pop(schluessel, None)
                if schluessel == "platte":
                    st["platte"] = False
    stand_schreiben(st)                                    # erst nach dem Senden: was zurückgenommen wurde, zählt
    return meldungen


async def dauerlauf() -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await einmal()
        except Exception as exc:                  # ein Fehler darf den Lauf nie töten
            log.warning("hphome-Wache gescheitert (%s)", type(exc).__name__)
        await asyncio.sleep(TAKT_S)
