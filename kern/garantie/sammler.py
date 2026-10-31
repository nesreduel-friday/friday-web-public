"""Der Sammler: sucht den ganzen Tag weiter, auch wenn Eike nicht hinsieht.

Eikes Wunsch (15.09.2026): „den ganzen Tag scannen, so einmal pro Stunde, und wenn
sie eine Verspätung in meinen Hauptgebieten findet, benachrichtigen und für die
Abgabe speichern.“

- Einmal pro Stunde: Eikes Orte (`suche.ORTE`) und die Haltestellen aus
  `data/uestra_abdeckung.json`, die zusammen alle Linien abdecken. 90 Minuten
  zurück — Echtzeit tragen nur zeitnahe Fahrten, und so rutscht zwischen zwei
  Durchgängen nichts durch.
- Fahrten über 20 Minuten werden gemerkt. Liegt sie in Eikes Orten, kommt EINE
  Benachrichtigung je Fahrt; ein Tipp darauf öffnet sie in der App.
- Alle fünf Minuten, ohne Scan: wartende Anträge nach der Ankunft nachprüfen
  (sonst verpasst Friday die Messung) und an ein abgelaufenes Ticket erinnern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as uhr
from datetime import datetime, timedelta

from kern import config, konnektoren
from kern.garantie import ablage, antrag, post, suche, verwaltung

log = logging.getLogger("friday.garantie")

KONNEKTOR = "uestra_garantie"
TAKT_S = 300                      # Nachprüfen und Erinnern
SCAN_ALLE_S = 3600                # Eike: „so einmal pro Stunde“
BETRIEB_VON, BETRIEB_BIS = 4, 1   # 4 Uhr bis 1 Uhr nachts
VORAUS = timedelta(minutes=30)
MAX_EINZELN = 3                   # mehr neue Fahrten auf einmal: eine gesammelte Nachricht
VERLAEUFE_MAX = 40                # je Durchgang höchstens so viele Verläufe sichern

_letzter_scan = 0.0


def abdeckung() -> list[dict]:
    try:
        d = json.loads((config.daten_ordner() / "uestra_abdeckung.json").read_text(encoding="utf-8"))
        return [h for h in d.get("haltestellen", []) if h.get("gid") and h.get("name")]
    except (OSError, ValueError):
        return []


def im_betrieb(stunde: int) -> bool:
    return stunde >= BETRIEB_VON or stunde < BETRIEB_BIS


async def melden(neu: list[dict]) -> int:
    """Eine Benachrichtigung je neuer Fahrt in Eikes Orten — bei vielen auf einmal
    eine gesammelte, damit das Handy nicht zehnmal klingelt."""
    if not neu:
        return 0
    from kern.dienste import benachrichtigungen
    a = ablage.standard()
    # Alle Verspätungen, oder — ist nur „Nur S5“ an — allein die S5 (eigene Art, eigener Schalter).
    art, zu_melden = "verspaetungen", neu
    if not benachrichtigungen.an("verspaetungen") and benachrichtigungen.an("verspaetungen_s5"):
        art, zu_melden = "verspaetungen_s5", [f for f in neu if suche.ist_s5(f)]
    if not zu_melden:
        for f in neu:
            a.reise_gemeldet(ablage.kurz(f["jid"]))
        return len(neu)
    if len(zu_melden) <= MAX_EINZELN:
        for f in zu_melden:
            rid = ablage.kurz(f["jid"])
            await asyncio.to_thread(
                benachrichtigungen.senden, art,
                f"+{f['verspaetung']} Min: {f['kategorie']} {f['linie']}",
                f"Richtung {f['richtung']}, gesehen in {f['halt']}. Antippen, Start und Ziel wählen — "
                "Friday bereitet den Antrag vor.", f"/?garantie={rid}#studio", f"garantie-fahrt-{rid}")
    else:
        await asyncio.to_thread(
            benachrichtigungen.senden, art,
            f"{len(zu_melden)} verspätete Fahrten in deiner Gegend",
            ", ".join(f"{f['linie']} +{f['verspaetung']}" for f in zu_melden[:6]) + " — antippen zum Beantragen.",
            "/#studio", "garantie-fahrten")
    for f in neu:
        a.reise_gemeldet(ablage.kurz(f["jid"]))
    return len(neu)


async def verlaeufe_sichern(fahrten: list[dict], hoechstens: int = VERLAEUFE_MAX) -> int:
    """Für Fahrten über 20 Minuten den Verlauf mit Echtzeit sichern, solange es sie gibt."""
    wichtig = sorted((f for f in fahrten if (f.get("verspaetung") or 0) > suche.GARANTIE_AB),
                     key=suche.relevanz)[:hoechstens]
    ampel = asyncio.Semaphore(4)

    async def eine(f):
        async with ampel:
            try:
                await antrag.verlauf(ablage.kurz(f["jid"]))
                return 1
            except Exception:
                return 0
    return sum(await asyncio.gather(*(eine(f) for f in wichtig)))


async def einmal() -> int:
    """Ein Durchgang: Eikes Orte zuerst, dann die Abdeckung. Meldet nur Eikes Orte."""
    a = ablage.standard()
    gebiet = await suche.haltestellen_im_gebiet()
    in_gebiet = await suche.abtasten(gebiet, suche.GARANTIE_AB, voraus=VORAUS)
    bekannt = {h["gid"] for h in gebiet}
    netz = await suche.abtasten([h for h in abdeckung() if h["gid"] not in bekannt], suche.GARANTIE_AB, voraus=VORAUS)
    neu = []
    for f in in_gebiet:
        rid = a.reise_merken({**f, "bereich": "deine Orte"})
        r = a.reise(rid)
        if f["verspaetung"] > suche.GARANTIE_AB and not r["gemeldet"] and not a.antrag_zur_reise(rid):
            neu.append(f)
    for f in netz:
        rid = a.reise_merken({**f, "bereich": "ganzes Netz"})
        # Die S5 meldet Eike überall, nicht nur in seinen Orten.
        if suche.ist_s5(f) and f["verspaetung"] > suche.GARANTIE_AB and not a.reise(rid)["gemeldet"] \
                and not a.antrag_zur_reise(rid):
            neu.append(f)
    await melden(neu)
    await verlaeufe_sichern(in_gebiet + netz)
    return len(in_gebiet) + len(netz)


async def ticket_erinnern() -> bool:
    """Ist das neueste Ticket abgelaufen, genau EINE Benachrichtigung: das neue hochladen."""
    a = ablage.standard()
    t = a.ticket()
    heute = config.jetzt().date().isoformat()
    if not t or not t.get("gueltig_bis") or t["gueltig_bis"] >= heute or t.get("erinnert"):
        return False
    from kern.dienste import benachrichtigungen
    bis = datetime.fromisoformat(t["gueltig_bis"])
    await asyncio.to_thread(benachrichtigungen.senden, "garantie", "Neues Ticket hochladen",
                            f"Dein {t.get('art') or 'Ticket'} galt bis {bis:%d.%m.} — lade das neue hoch, "
                            "dann bleiben deine Garantie-Anträge vollständig.", "/#studio", "garantie-ticket")
    a.ticket_merken(t["id"], erinnert=True)
    return True


async def dauerlauf() -> None:
    global _letzter_scan
    await asyncio.sleep(120)
    while True:
        try:
            if konnektoren.benutzbar(KONNEKTOR):
                if im_betrieb(config.jetzt().hour) and uhr.monotonic() - _letzter_scan >= SCAN_ALLE_S:
                    _letzter_scan = uhr.monotonic()
                    if n := await einmal():
                        log.info("Garantie: %s verspätete Fahrten gemerkt", n)
                    # Stündlich auch in Gmail nach Bescheiden der ÜSTRA sehen.
                    if post.benutzbar():
                        try:
                            await post.abgleich()
                        except Exception as exc:
                            log.warning("Gmail-Abgleich gescheitert: %s", type(exc).__name__)
                if n := await antrag.nachpruefen():
                    log.info("Garantie: %s Anträge nachgeprüft", n)
                await ticket_erinnern()
                # Verfolgung bis zum Bescheid: fällige Schritte einmal an Eike melden (nie an Dritte, nie Absenden).
                if n := await asyncio.to_thread(verwaltung.erinnern):
                    log.info("Garantie: %s fällige Schritte gemeldet", n)
                ablage.standard().aufraeumen()
        except Exception:
            log.exception("Garantie-Sammler gescheitert")
        await asyncio.sleep(TAKT_S)
