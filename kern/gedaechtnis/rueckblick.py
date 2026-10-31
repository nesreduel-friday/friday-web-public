"""Rückblick — was nach einem Gespräch passiert, im Hintergrund.

Pro Durchgang (alle fünf Minuten) höchstens EIN Schritt jeder Art, weil die
kostenlose Stufe ein Minutenkontingent hat:

1. Zusammenfassen: ein fertiges Gespräch (beendet, oder 30 Minuten still)
   bekommt einen Satz Zusammenfassung — die sieht Eike auf der
   Gedächtnis-Seite, und das Werkzeug `rueckblick` sucht darin.
2. Ins Langzeitgedächtnis: das Gespräch geht als Episode an Graphiti.
3. Nachsehen: Graphitis Warteschlange verwirft gescheiterte Episoden still
   (queue_service.py, nur ein Log-Eintrag). Friday prüft deshalb nach zehn
   Minuten per get_episodes, ob die Episode da ist, und schickt sie sonst
   erneut — höchstens drei Versuche.
4. Sichern: einmal pro Nacht eine Kopie beider Datenbanken, 14 Tage lang.
   Der Graph selbst lässt sich aus dem Protokoll neu aufbauen.

Nichts hiervon darf ein Gespräch stören: jeder Schritt fängt seine Fehler.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from kern import config
from kern.fokus import rang as fokusrang
from kern.gedaechtnis import bilder, graph
from kern.gedaechtnis import notizen as wissensnotizen
from kern.personen import register
from kern.protokoll import speicher as pspeicher

log = logging.getLogger("friday.rueckblick")

TAKT_SEKUNDEN = 300
MAX_VERSUCHE = 3
# Je Takt mehrere Gespräche an den Graphen (05.10.2026): vorher je Schritt nur eines = höchstens 12 je Stunde, während
# Graphiti ≈ 60 je Stunde schafft. Graphitis Schlange liegt im Arbeitsspeicher und wird je Gespräch ≈ 1 min abgearbeitet:
# Liegen mehr als IN_ARBEIT_MAX junge „gesendet“ darin, wird nichts Neues geschickt — sonst sähe `nachsehen` nach 10 min
# ein bloß wartendes Gespräch als verloren an, schickte es doppelt und gäbe es nach MAX_VERSUCHE zu Unrecht auf.
EINSPEISEN_STAPEL = 5
NACHSEHEN_STAPEL = 8
IN_ARBEIT_MAX = 6
SICHERUNGEN_BEHALTEN = 14
# Hintergrund nimmt das leichte Modell zuerst — es hat ein eigenes
# Minutenlimit und frisst Eikes Chat nichts weg.
HINTERGRUND_MODELL = "gemini-3.5-flash-lite"

ANWEISUNG = ("Fasse das folgende Gespräch zwischen Eike und seiner Assistentin Friday in EINEM deutschen "
             "Satz zusammen, höchstens 25 Wörter. Nur worum es ging, keine Einleitung, keine Anführungszeichen.")


async def zusammenfassen(gespraech: int) -> bool:
    from kern.sitzung import chat  # hier, sonst Kreisimport über main

    p = pspeicher.standard()
    k = p.kopf(gespraech) or {}
    anweisung, sprecher = ANWEISUNG, "Eike"
    if _ist_fremd(k):
        # Ein Gespräch mit einer anderen Person: Sprecher und Anweisung nennen sie, nicht Eike — sonst stünde in der
        # Zusammenfassung „Eike erzählt …“ über etwas, das sie gesagt hat.
        person = _person(k)
        sprecher = _sprecher(person) if person else "eine andere Person"
        anweisung = ANWEISUNG.replace("zwischen Eike und seiner Assistentin Friday",
                                      f"zwischen {sprecher} (einer Bekannten von Eike) und Eikes Assistentin Friday")
    abschrift = p.abschrift(gespraech, sprecher=sprecher)
    if not abschrift.strip():
        p.zusammenfassen(gespraech, "(ohne Inhalt)")
        return True
    try:
        antwort, _ = await chat._gemini_anfrage({
            "systemInstruction": {"parts": [{"text": anweisung}]},
            "contents": [{"role": "user", "parts": [{"text": abschrift[:20000]}]}],
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "low"}},
        }, bevorzugt=HINTERGRUND_MODELL, schluessel_liste=config.hintergrund_schluessel_liste())
        text = "".join(t.get("text", "") for t in antwort["candidates"][0]["content"]["parts"]).strip()
    except Exception as exc:
        log.warning("Zusammenfassung von #%s fehlgeschlagen: %s", gespraech, exc)
        return False
    p.zusammenfassen(gespraech, text or "(ohne Zusammenfassung)")
    return True


def _episodenname(gespraech: int) -> str:
    return f"gespraech-{gespraech}"


def _person(k: dict) -> dict | None:
    """Die Person eines Gesprächs aus dem Register; None bei Eikes eigenen Gesprächen UND bei einer Person, die es
    nicht gibt — dann wird nichts in irgendeine Gruppe geschickt (`_ist_fremd` unterscheidet die beiden)."""
    return register.holen(k["person"]) if k.get("person") is not None else None


def _sprecher(person: dict) -> str:
    """Der Name, unter dem die Person im Graphen steht (`graph_name`) — nur so verbindet der Graph ihre Aussagen mit
    dem, was er über sie schon weiß."""
    return person.get("graph_name") or person["name"]


def _ist_fremd(k: dict) -> bool:
    return k.get("person") is not None


def _episodentext(p, gespraech: int, k: dict, sprecher: str = "Eike") -> str:
    art = {"sprache": "Sprachgespräch", "snapchat": "Snapchat-Chat"}.get(k["art"], "Chat")
    text = p.abschrift(gespraech, sprecher=sprecher)
    for b in bilder.fuer_gespraech(gespraech):
        wer = f" von {b['name']}" if b["name"] else ""
        if b.get("quelle"):
            # Bild von außerhalb der App (Snapchat): die Beschreibung ist sachlich und kommt von der
            # Bildauswertung, nicht aus Fridays Antwort; Quelle und Ergänzungen gehören dazu.
            text += (f"\nBild{wer} (Quelle: {b['quelle']}): {(b['beschreibung'] or 'nicht beschrieben')[:400]}")
            if b.get("notizen"):
                text += f" Ergänzung: {b['notizen'][:300]}"
        else:
            text += f"\n{sprecher} zeigte ein Bild{wer}. Friday beschrieb es: {(b['beschreibung'] or '')[:300]}"
            if b.get("notizen"):
                text += f" Ergänzung: {b['notizen'][:300]}"
    return text


async def einspeisen(gespraech: int) -> None:
    p = pspeicher.standard()
    k = p.kopf(gespraech)
    if not k:
        return
    art = {"sprache": "Sprachgespräch", "snapchat": "Snapchat-Chat"}.get(k["art"], "Chat")
    if not _ist_fremd(k):
        ok = await graph.einspeisen(_episodenname(gespraech), _episodentext(p, gespraech, k), f"{art} mit Friday",
                                    k["beginn"])
    else:
        person = _person(k)
        if not person:
            log.warning("Gespräch #%s gehört zu einer Person, die es nicht gibt — nichts an Graphiti geschickt", gespraech)
            p.graph_markieren(gespraech, "aufgegeben")  # sonst bliebe es für immer das erste in der Warteschlange
            return
        ok = await graph.einspeisen(
            _episodenname(gespraech), _episodentext(p, gespraech, k, _sprecher(person)),
            f"{art} von {_sprecher(person)} mit Friday", k["beginn"],
            anweisungen=graph.anweisungen_fuer_person(_sprecher(person)), gruppe=person["id"])
    if ok:
        p.graph_markieren(gespraech, "gesendet", neuer_versuch=True)


async def nachsehen(gespraech: int) -> None:
    p = pspeicher.standard()
    k = p.kopf(gespraech) or {}
    if _ist_fremd(k):
        da = await graph.angekommen(_episodenname(gespraech), (k or {}).get("graph_gesendet"), gruppe=k["person"])
    else:
        da = await graph.angekommen(_episodenname(gespraech), (p.kopf(gespraech) or {}).get("graph_gesendet"))
    if da is None:
        return  # Graphiti antwortet nicht — später nochmal
    if da:
        p.graph_markieren(gespraech, "angekommen")
    elif (p.kopf(gespraech) or {}).get("graph_versuche", 0) >= MAX_VERSUCHE:
        p.graph_markieren(gespraech, "aufgegeben")
        log.warning("Gespräch #%s nach %s Versuchen nicht im Langzeitgedächtnis", gespraech, MAX_VERSUCHE)
        # Woran lag es? Beim Tageslimit hat weiteres Probieren keinen Sinn.
        if await graph.pause_pruefen():
            log.info("Weitere Versuche ausgesetzt bis zum Zurücksetzen des Tageslimits")
    elif p.graph_in_arbeit() >= IN_ARBEIT_MAX:
        return  # Graphitis Schlange ist voll — beim nächsten Takt erneut nachsehen
    else:
        await einspeisen(gespraech)


def _kopiename(gespraech: int, person: dict) -> str:
    return f"{_episodenname(gespraech)}-von-{person['id']}"


async def kopie_an_eike(gespraech: int) -> None:
    """Gespräch einer anderen Person zusätzlich in Eikes Gruppe — mit Vermerk, von wem es kommt (der Graph verbindet
    es dort mit der Person). Erst, wenn die Episode in ihrer eigenen Gruppe angekommen ist. Wie jede Episode wird auch
    die Kopie danach auf Ankunft geprüft (`kopie_nachsehen`); ein Fehler zählt einen Versuch, nach MAX_VERSUCHE gibt
    es auf — sonst bliebe ein Gespräch für immer vorn in der Warteschlange."""
    p = pspeicher.standard()
    k = p.kopf(gespraech)
    if not k or not _ist_fremd(k) or k.get("graph_eike_kopie") != 0 or k.get("graph_status") != "angekommen":
        return
    person = _person(k)
    if not person:
        p.eike_kopie_setzen(gespraech, 3)  # die Person gibt es nicht (mehr): nichts in Eikes Gruppe raten
        return
    name = _sprecher(person)
    art = {"sprache": "Sprachgespräch", "snapchat": "Snapchat-Chat"}.get(k["art"], "Chat")
    kopf = f"[Gespräch von {name} mit Friday, Kanal {k['art']} — Herkunft: {name}, nicht Eike]\n"
    ok = await graph.einspeisen(_kopiename(gespraech, person), kopf + _episodentext(p, gespraech, k, name),
                                f"{art} von {name} mit Friday", k["beginn"],
                                anweisungen=graph.anweisungen_fuer_person(name))
    if ok:
        p.eike_kopie_markieren(gespraech)  # gesendet
    else:
        p.eike_kopie_setzen(gespraech, 3 if k["graph_eike_versuche"] + 1 >= MAX_VERSUCHE else 0, versuch=True)


async def kopie_nachsehen(gespraech: int) -> None:
    """Ist die Kopie in Eikes Gruppe angekommen? Sonst zurück auf „offen“ (erneut senden) oder aufgeben."""
    p = pspeicher.standard()
    k = p.kopf(gespraech)
    if not k or not _ist_fremd(k) or k.get("graph_eike_kopie") != 1:
        return
    person = _person(k)
    if not person:
        p.eike_kopie_setzen(gespraech, 3)
        return
    da = await graph.angekommen(_kopiename(gespraech, person), None)
    if da is None:
        return  # Graphiti antwortet nicht — später nochmal
    if da:
        p.eike_kopie_setzen(gespraech, 2)
    else:
        p.eike_kopie_setzen(gespraech, 3 if k["graph_eike_versuche"] >= MAX_VERSUCHE else 0)


def sichern() -> None:
    """Einmal pro Tag, frühestens um 3 Uhr: Kopie über die SQLite-Backup-API
    (konsistent auch während geschrieben wird), älteste zuerst weg."""
    jetzt = config.jetzt()
    ordner = config.daten_ordner() / "sicherung"
    ordner.mkdir(parents=True, exist_ok=True)
    tag = jetzt.strftime("%Y-%m-%d")
    if jetzt.hour < 3 or any(ordner.glob(f"*-{tag}.db")):
        return
    for datei in ("friday.db", "gedaechtnis.db", "schule.db", "mail.db", "chats.db", "erinnerungen.db",
                  "garantie.db", "goodnotes.db", "noten.db"):
        quelle = config.daten_ordner() / datei
        if not quelle.exists():
            continue
        ziel = ordner / f"{quelle.stem}-{tag}.db"
        with sqlite3.connect(quelle) as von:
            try:
                von.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass
            with sqlite3.connect(ziel) as nach:
                von.backup(nach)
        for alt in sorted(ordner.glob(f"{quelle.stem}-*.db"))[:-SICHERUNGEN_BEHALTEN]:
            alt.unlink()
    # Wichtige Angaben, Konfigurationen und Zugangsdaten (600 wie das Original)
    for json_datei in ("garantie_angaben.json", "garantie_profile.json", "zugaenge.json", "konnektoren.json", "einstellungen.json",
                       "personen.json"):
        angaben = config.daten_ordner() / json_datei
        if angaben.exists():
            kopie = ordner / f"{angaben.stem}-{tag}.json"
            kopie.write_bytes(angaben.read_bytes())
            os.chmod(kopie, 0o600)
            for alt in sorted(ordner.glob(f"{angaben.stem}-*.json"))[:-SICHERUNGEN_BEHALTEN]:
                alt.unlink()
    log.info("Sicherung %s geschrieben", tag)


async def durchgang() -> None:
    p = pspeicher.standard()
    # Ist der Wissensgraph wegen des Tageslimits ausgesetzt, wird nichts an ihn
    # geschickt — Zusammenfassen, Verdichten und Sichern laufen weiter.
    pausiert = graph.pausiert()
    if pausiert and await graph.pause_vorzeitig_beenden():
        pausiert = False
    if not pausiert:
        try:
            # Nach dem Ende einer Pause sofort nachholen, nicht erst nach 12 Stunden.
            stunden = 0 if graph.pause_bis() else 12
            if graph.pause_bis():
                graph.pause_aufheben()
            if n := p.graph_wieder_versuchen(stunden=stunden):
                log.info("%s aufgegebene Gespräche neu angestellt", n)
        except Exception:
            log.exception("Neu anstellen fehlgeschlagen")
    # (Schritt, Auswahl, wie viele je Takt, begrenzt durch Graphitis Schlange)
    if not pausiert:
        try:
            await wissensnotizen.durchgang()        # Eikes eigene Angaben gehen vor allem anderen an den Graphen
        except Exception:
            log.exception("Eikes Angaben konnten nicht gesendet werden")
    schritte = [(zusammenfassen, p.offene_rueckblicke, 1, False)]
    if not pausiert:
        schritte += [(nachsehen, p.graph_zu_pruefen, NACHSEHEN_STAPEL, False),
                     (einspeisen, p.graph_offen, EINSPEISEN_STAPEL, True)]
    for schritt, auswahl, anzahl, begrenzt in schritte:
        try:
            ids = fokusrang.sortiert(auswahl(), p)[:anzahl]      # markierte Personen zuerst, dann Eike, dann der Rest
        except Exception:
            log.exception("Rückblick-Schritt %s fehlgeschlagen", schritt.__name__)
            continue
        for gespraech in ids:
            if begrenzt and p.graph_in_arbeit() >= IN_ARBEIT_MAX:
                break
            try:
                await schritt(gespraech)
            except Exception:
                log.exception("Rückblick-Schritt %s fehlgeschlagen", schritt.__name__)
    if not pausiert:
        try:
            ids = p.eike_kopie_offen()
            if ids:
                await kopie_an_eike(ids[0])
            ids = p.eike_kopie_zu_pruefen()
            if ids:
                await kopie_nachsehen(ids[0])
        except Exception:
            log.exception("Kopie an Eikes Gruppe fehlgeschlagen")
    if not pausiert:
        try:
            from kern.personen import freigaben
            if n := await freigaben.nachlauf():
                log.info("%s widerrufene Freigaben im Graphen gelöscht", n)
        except Exception:
            log.exception("Freigaben-Nachlauf fehlgeschlagen")
    try:
        await _gelegentlich_verdichten()
    except Exception:
        log.exception("Verdichtung fehlgeschlagen")
    try:
        sichern()
    except Exception:
        log.exception("Sicherung fehlgeschlagen")


_zuletzt_verdichtet = 0.0
VERDICHTEN_ABSTAND_S = 6 * 3600


async def _gelegentlich_verdichten() -> None:
    """Höchstens alle sechs Stunden das Notizbuch verdichten — Doppeltes zusammenziehen."""
    global _zuletzt_verdichtet
    import time
    from kern.gedaechtnis import verdichten
    if time.monotonic() - _zuletzt_verdichtet < VERDICHTEN_ABSTAND_S:
        return
    _zuletzt_verdichtet = time.monotonic()
    n = await verdichten.verdichten()
    if n:
        log.info("Notizbuch verdichtet: %s Gruppen zusammengeführt", n)


async def dauerlauf() -> None:
    await asyncio.sleep(20)   # erst den Start abwarten
    while True:
        await durchgang()
        await asyncio.sleep(TAKT_SEKUNDEN)
