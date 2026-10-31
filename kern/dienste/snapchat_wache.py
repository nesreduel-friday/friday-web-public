"""Wache für Snapchat — erkennt Eikes Nachrichten und Snaps und lässt Friday normal weiterreden.

Liest ungelesene Einträge aus `snapchat.eingang_neu()` (geschrieben vom Host-Helfer: Texte und
Snaps als Bilddatei) und legt Fridays Antwort als reinen Text-Sendeauftrag zurück (eine
Gesprächsantwort ist in Snapchat ein Chat-Text, kein Snap).

Antwortmotor ist seit dem 29.09.2026 derselbe wie im Web-Chat: `chat.antworten()` mit voller Persona,
Gedächtnis, Werkzeugen und Bildern, dazu der Kanal-Zusatz `persona.SNAPCHAT_KANAL`. Der Verlauf kommt aus
dem Gesprächsprotokoll und verfällt nach `RUHE_MINUTEN`. Was innerhalb von `BUENDEL_S` Sekunden
ankommt, wird ein Zug — Text und Snap gemeinsam ergeben genau eine Antwort.

Jeder beantwortete Zug landet im Gesprächsprotokoll (`_faden()`, `_protokollieren()`); Snap-Bilder gehen
wie beim Web-Chat ins Bildgedächtnis (`gedaechtnis/bilder.py`) mit Fridays Antwort als Beschreibung. Eikes
Snapchat-Text sind eigene Worte (Rolle `du`); Bildinhalt gilt für `chat.antworten` als fremder Text.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from datetime import datetime, timezone

from kern import config, konnektoren
from kern.dienste import friday_selfie, snapchat
from kern.gedaechtnis import bilder, bildauswertung, bildpersonen
from kern.garantie import annahme
from kern.gehirn import persona
from kern.personen import flammen, register, rueckfragen, unbekannt
from kern.personen import module as modul
from kern.protokoll import speicher as pspeicher
from kern.regeln import lauf as regeln_lauf
from kern.sitzung import chat

log = logging.getLogger("friday.snapchat_wache")

TAKT_S = 3  # nur eine Dateiprüfung; Gemini läuft erst bei neuer Nachricht (29.09.2026: 300 s ließen Antworten minutenlang liegen)
ART = "snapchat"
# Gemini kann minutenlang ausfallen. Eine Nachricht, auf die so lange keine Antwort kam, bekommt
# lieber eine ehrliche Kurzantwort als Stille (Eike hatte den ganzen Tag nichts zurückbekommen).
NOTFALL_NACH_S = 240
NOTFALL_ANTWORT = "Ich bin gerade zu langsam und komme nicht durch. Schreib mir in ein paar Minuten nochmal."
# Der Helfer meldet sich in den Wachstunden mindestens alle 15 Minuten (7–23 Uhr).
HELFER_STILL_NACH_S = 1800
WACH_STUNDEN = range(8, 23)
# Wie im Web-Chat: Ein Faden bleibt, solange Eike schreibt, und beginnt nach Ruhe neu. Nur der
# Verlauf verfällt — das Langzeitgedächtnis bleibt.
RUHE_MINUTEN = snapchat.RUHE_MINUTEN
BUENDEL_S = 8      # so lange nach dem letzten Eintrag warten, damit Text und Snap ein Zug werden
# Scheitert eine Gruppe (Motor-Fehler, leere Antwort, kein Auftrag möglich), kommt sie erst nach dieser Frist wieder dran —
# sonst blockiert sie alle anderen und ruft im 3-s-Takt endlos Gemini auf (Review 30.09.2026).
RUECKSTELLUNG_S = 30
PARALLEL = 3          # so viele Personen gleichzeitig an Gemini (je Person höchstens eine Anfrage), 07.10.2026
RUHIG_BIS_HOCH = 3    # nach so vielen fehlerfreien Ticks steigt die Parallelität wieder um 1 (nach einem Fehler: 1)
_parallel = {"grenze": PARALLEL, "ruhig": 0}
RUECKFRAGE_MAX_TAGE = 2  # eine Rückfrage-Antwort, die so lange nicht raus konnte (Konnektor aus, Handy weg), kommt nicht mehr
_unbekannt_gemeldet: set[str] = set()   # Namen aus `unbekannt_eingang.json`, die schon an `unbekannt.melden` gingen
_antwort_gemeldet: set[str] = set()     # Eingangseintrags-Ids, deren Personen-Antwort schon per Push an Eike ging
_gesehen_texte: dict = {}    # Gruppe -> [(Zeit, Text)] der letzten Eingänge (Schleifenbremse)
_wiederholung_gemeldet: set = set()      # (Gruppe, Text), zu denen Eike schon Bescheid bekam
WIEDERHOLT_AB = 5            # ab dem so vielten gleichen Text im Fenster antwortet Friday nicht mehr
WIEDERHOLT_FENSTER_S = 1800
_zurueck: dict = {}      # Gruppe (Register-Id, None = Eike) oder ("rf", Rückfrage-Id) -> nicht vor dieser Zeit erneut versuchen
QUELLE = "Snapchat · Eike"  # Herkunft der Snap-Bilder im Bildgedächtnis (und damit im Langzeitspeicher)


def _zeit(e: dict) -> float:
    """Zeitstempel eines Eingangseintrags; ein kaputter Wert gilt als uralt (bearbeiten statt die Wache lahmzulegen)."""
    z = e.get("zeit")
    return float(z) if isinstance(z, (int, float)) and not isinstance(z, bool) else 0.0


def _hat_snapchat(person: dict) -> bool:
    return bool((person.get("kanaele") or {}).get("snapchat"))


def _quelle(person: dict | None) -> str:
    """Herkunft der Snap-Bilder im Bildgedächtnis: „Snapchat · Eike“ oder „Snapchat · <Name der Person>“."""
    return QUELLE if person is None else f"Snapchat · {person['name']}"


def _verlauf(person: dict | None = None) -> list[dict]:
    """Die letzten Beiträge des laufenden Snapchat-Fadens dieser Person im Format von `chat.antworten` — ohne sie
    beantwortete Friday jede Nachricht wie die erste („Und Muster?“ ohne Bezug, 29.09.2026)."""
    try:
        return snapchat.verlauf(person["id"] if person else None)
    except Exception as exc:
        log.warning("Snapchat-Wache: Verlauf nicht lesbar (%s)", type(exc).__name__)
        return []


def _jpeg(png: bytes | None) -> bytes | None:
    """Das Snap-PNG als JPEG (klein genug für Gemini und das Bildgedächtnis); None, wenn es nicht geht."""
    if png is None:
        return None
    try:
        return snapchat.bild_als_jpeg(png)
    except Exception as exc:
        log.warning("Snapchat-Wache: Bild nicht umwandelbar (%s)", type(exc).__name__)
        return None


def _bekannt(jpeg: bytes, person: dict | None = None) -> str:
    """Kennt Friday das Bild schon? Dann steht der alte Eintrag (ID, Name, Beschreibung, Notizen) dabei, damit sie Erinnerungen
    verbindet und `bild_benennen` auf die alte ID setzen kann. Bei einer Person nur aus IHREN Bildern (nie Eikes Notizen)."""
    alt = bilder.gleiches(jpeg, person=person["id"] if person else None)
    if not alt:
        return ""
    teile = [f"Bild-ID {alt['id']}"]
    for feld, marke in (("name", "Name"), ("beschreibung", "Beschreibung"), ("notizen", "Notizen")):
        if (alt.get(feld) or "").strip():
            teile.append(f"{marke}: {alt[feld].strip()[:200]}")
    return f" [Das Bild kennst du schon: {'; '.join(teile)}.]"


def _zug_bauen(eintraege: list[dict], person: dict | None = None) -> tuple[list[dict], list[str]]:
    """Alle Einträge einer Runde als `chat.antworten`-Nachrichten: erst ein Text mit allen Texten, dann
    je Snap eine Nachricht mit dem Bild. Der Snap steht zuletzt: Bildinhalt gilt damit als fremder Text."""
    wer = person["name"] if person else "Eike"
    texte = [e["text"] for e in eintraege if e.get("art", "text") == "text" and e.get("text")]
    nachrichten, namen = [], []
    if texte:
        nachrichten.append({"rolle": "du", "text": "\n".join(texte)})
    for e in eintraege:
        if e.get("art") not in ("snap", "chatbild"):
            continue
        was = "Snap" if e["art"] == "snap" else "Bild im Chat"
        daten = _jpeg(snapchat.bild_lesen(e.get("bild") or ""))
        if daten is None:
            nachrichten.append({"rolle": "du",
                                "text": f"[{wer} hat einen {was} geschickt, den ich nicht lesen konnte.]"})
            continue
        namen.append(e["bild"])
        nachrichten.append({"rolle": "du", "text": f"[{was} von {wer}, das Bild hängt an.]" + _bekannt(daten, person),
                            "bild": {"mime": "image/jpeg", "data": base64.b64encode(daten).decode("ascii")}})
    return nachrichten, namen


def _faden_vorhanden(person: dict | None = None) -> int | None:
    return snapchat.faden_vorhanden(person["id"] if person else None)


async def _antworten(eintraege: list[dict], person: dict | None = None) -> dict:
    """Derselbe Antwortmotor wie im Web (Persona, Gedächtnis, Werkzeuge, Bilder). Eike bekommt den Kanal-Zusatz samt seiner
    gesendeten Snaps; eine Person bekommt nur den Kanalnamen (der Motor baut ihren Kanaltext selbst, nie mit Eikes Snaps).
    Aktionen für die App gibt es in Snapchat nicht — sie werden verworfen."""
    nachrichten, _ = _zug_bauen(eintraege, person)
    if person is None:
        # Mit dem Faden kennen Werkzeuge wie `bild_benennen` („das ist Torge“) das zuletzt gezeigte Bild.
        ergebnis = await chat.antworten(_verlauf() + nachrichten, gespraech=_faden_vorhanden(),
                                        kanal=persona.SNAPCHAT_KANAL + snapchat.gesendet_kontext() + flammen.eike_kontext())
    else:
        ergebnis = await chat.antworten(_verlauf(person) + nachrichten, gespraech=_faden_vorhanden(person),
                                        kanal="Snapchat", person=person)
    ergebnis["aktionen"] = []
    return ergebnis


def _faden(person: dict | None = None) -> int:
    return snapchat.faden(person["id"] if person else None)


def _protokollieren(zug_text: str, bild_namen: list[str], ergebnis: dict,
                   person: dict | None = None) -> list[tuple[str, str]]:
    """Zug, Werkzeuge und Antwort als Gespräch ins Protokoll, Snap-Bilder ins Bildgedächtnis — wie
    `/api/chat`, aber mit Quelle und OHNE Fridays Antwort als Beschreibung (die kommt aus der
    Bildauswertung, siehe `einmal`). Erst NACH `auftrag_legen()`: protokolliert wird nur, was wirklich
    rausging. Ein Fehler hier darf die Wache nicht töten und den Auftrag nicht zurückhalten.
    Gibt `(bild_id, daten_b64)` der gespeicherten Bilder zurück."""
    gespeichert: list[tuple[str, str]] = []
    try:
        p = pspeicher.standard()
        faden = _faden(person)
        p.beitrag(faden, "du", zug_text, "bild" if bild_namen else "text")
        for w in ergebnis.get("werkzeug_ergebnisse") or []:
            text = f"Werkzeug {w['name']}: {w['text']}" if w.get("text") else f"Werkzeug benutzt: {w['name']}"
            p.beitrag(faden, "system", text, "werkzeug")
        p.beitrag(faden, "friday", ergebnis["antwort"])
        for name in bild_namen:
            daten = _jpeg(snapchat.bild_lesen(name))
            if daten is None:
                continue
            b64 = base64.b64encode(daten).decode("ascii")
            bild_id = bilder.speichern(faden, "image/jpeg", b64, zug_text, quelle=_quelle(person),
                                       person=person["id"] if person else None)
            if bild_id:
                gespeichert.append((bild_id, b64))
            else:
                log.warning("Snapchat-Wache: Snap-Bild wurde nicht ins Bildgedächtnis übernommen (%d Byte)",
                            len(daten))
    except Exception as exc:
        log.warning("Snapchat-Wache: Dialog nicht im Gesprächsprotokoll (%s)", type(exc).__name__)
    return gespeichert


_vermerkt: dict = {}
# Textantworten, die auf einen freien Auftragsplatz warten: Ein Snap des Werkzeugs `schicke_snap` belegt den einen
# Platz schon während der Antwortrunde. Ohne diese Warteschlange scheiterte die Textantwort, der Zug blieb ungelesen
# und lief im 3-s-Takt erneut — mit jedem Lauf ein weiterer Snap (29.09.2026).
_nachzulegen: list[str] = []


NEUTRALE_ABSAGE = "Ich habe bei Eike nachgefragt: Dazu kann ich dir leider nichts sagen."


ANTWORT_MAX = 280   # Rückfrage-Antworten bleiben kurz (nur Eikes Freigabetext)
TEIL_MAX = 700      # eine lange Antwort geht in Stücken dieser Größe raus (an Satzgrenzen) — nie mehr still gekürzt
MAX_TEILE = 3       # mehr als so viele Nachrichten hintereinander schickt Friday nie auf einmal
ZU_LANG = "Ich habe bei Eike nachgefragt. Die Antwort passt nicht in eine Nachricht — frag mich bitte direkt danach."


def _antwort_legen(text: str, person: str | None, ausloeser: float | None) -> None:
    """Fridays Antwort als Auftrag/Aufträge legen. Passt sie nicht in eine Nachricht, geht sie in Stücken an Satzgrenzen raus (höchstens
    `MAX_TEILE`). Scheitert schon das ERSTE Stück (z. B. Schlange voll), wirft es wie bisher — dann wird der Zug später wiederholt. Scheitert
    ein späteres, bleibt es dabei: Der Zug ist beantwortet, ein Wiederholen würde das erste Stück doppelt schicken."""
    teile = snapchat.teilen(text, TEIL_MAX)[:MAX_TEILE]
    for i, teil in enumerate(teile):
        zusatz = {"person": person} if person is not None else {}
        if ausloeser is not None:
            zusatz["ausloeser"] = ausloeser
        try:
            snapchat.auftrag_legen("text", text=teil, **zusatz)
        except snapchat.SnapchatFehler as exc:
            if i == 0:
                raise
            log.warning("Snapchat-Wache: Teil %d von %d der Antwort nicht gelegt (%s)", i + 1, len(teile), exc)
            return


def _rueckfrage_text(a: dict) -> str:
    """Nur was Eike ausdrücklich formuliert hat (Freigabetext einer AKTIVEN Freigabe), die Zusage zu einem
    eingetragenen Termin oder eine neutrale Absage — nichts über die Sache selbst. Eine widerrufene
    Freigabe sagt nichts mehr."""
    from datetime import datetime as _dt
    if a.get("status") == "eingetragen" and isinstance(a.get("termin"), dict):
        t = a["termin"]
        try:
            wann = _dt.fromisoformat(t["beginn"]).strftime("%a %d.%m. %H:%M")
        except (TypeError, ValueError, KeyError):
            wann = "zum vorgeschlagenen Termin"
        ort = f" ({t['ort']})" if t.get("ort") else ""
        return f"Eike hat zugesagt: {t.get('titel', 'Termin')} {wann} Uhr{ort}."
    if a.get("status") == "freigegeben":
        f = next((x for x in register.freigaben(a["person"]) if x["id"] == a.get("freigabe")), None)
        if f and f.get("status") == "aktiv" and f.get("text"):
            text = f"Ich habe bei Eike nachgefragt: {f['text']}"
            return text if len(text) <= ANTWORT_MAX else ZU_LANG
    return NEUTRALE_ABSAGE


def _rueckfrage_alt(a: dict) -> bool:
    try:
        alter = datetime.now(timezone.utc) - datetime.fromisoformat(a["zeit"])
    except (KeyError, TypeError, ValueError):
        return False
    return alter.days >= RUECKFRAGE_MAX_TAGE


def _rueckfragen_melden() -> None:
    """Höchstens eine Antwort je Tick an die fragende Person (nie an jemand anderen, nie doppelt). Ist die Person nicht mehr da,
    pausiert oder ohne Snapchat-Namen, oder die Rückfrage uralt, gilt sie als erledigt; ist die Schlange voll, wird es später
    wiederholt — und eine, die nicht geht, hält die übrigen nicht auf."""
    for a in rueckfragen.zu_melden():
        verfallen = a.get("status") == "offen"
        status = "verfallen" if verfallen else None
        person = register.holen(a["person"])
        if not person or person.get("pausiert") or not _hat_snapchat(person) or _rueckfrage_alt(a):
            rueckfragen.gemeldet_markieren(a["id"], status=status)
            continue
        schluessel = ("rf", a["id"])
        if _zurueck.get(schluessel, 0) > time.time():
            continue
        text = _rueckfrage_text(a)
        try:
            snapchat.auftrag_legen("text", text=text, person=a["person"])
        except snapchat.SnapchatFehler as exc:
            log.warning("Snapchat-Wache: Rückfrage-Antwort wartet auf einen freien Platz (%s)", exc)
            _zurueck[schluessel] = time.time() + RUECKSTELLUNG_S
            continue
        try:
            pspeicher.standard().beitrag(snapchat.faden(a["person"]), "friday", text)
        except Exception as exc:  # noqa: BLE001 — Protokoll ist Zugabe, der Auftrag ist raus
            log.warning("Snapchat-Wache: Rückfrage-Antwort nicht im Protokoll (%s)", type(exc).__name__)
        rueckfragen.gemeldet_markieren(a["id"], status=status)
        _zurueck.pop(schluessel, None)
        return


def _anfragen_spiegeln_melden() -> None:
    """Gescheiterte Kontakt-Anfragen ehrlich an Eike (03.10.2026): Der Spiegel stellt um,
    der Push geht genau einmal — sonst wartet Eike auf etwas, das nie kommt."""
    from kern.dienste import benachrichtigungen
    unbekannt.befreundete_uebernehmen()   # „schon Freund“ ist keine Panne, sondern eine Annahme
    for name, grund in snapchat.anfrage_ergebnisse_spiegeln():
        try:
            benachrichtigungen.senden(
                "kontakte", "Anfrage gescheitert",
                f"Kontaktanfrage an {name} kam nicht an ({grund}). "
                "Einfach erneut beauftragen, dann versucht es das Handy neu.", url="/#studio/kontakte")
        except Exception:  # noqa: BLE001 — ein Push-Fehler ändert nichts am Vermerk
            log.exception("Push für gescheiterte Anfrage nicht gesendet")


_suchen_gemeldet: set[tuple[str, float]] = set()   # Notbremse im RAM; Quelle ist das Datei-Flag (neustartfest)


def _suchergebnisse_melden() -> None:
    """Fertige Handy-Suchen an Eike (04.10.2026): Push genau einmal je Suche —
    Eike antwortet mit 1–5 (`anfrage_auswaehlen`). Die Liste selbst holt das
    Modell per `anfrage_treffer` in den Verlauf, nicht der Push."""
    from kern.dienste import benachrichtigungen
    for e in snapchat.suchergebnisse():
        begriff = str(e.get("begriff", "")).strip()
        try:
            zeit = float(e.get("zeit", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not begriff or e.get("gemeldet") is True or (begriff, zeit) in _suchen_gemeldet:
            continue
        _suchen_gemeldet.add((begriff, zeit))
        anzahl = len(e.get("kandidaten", []) or [])
        try:
            if anzahl:
                benachrichtigungen.senden(
                    "kontakte", "Suche fertig",
                    f"{anzahl} Treffer für „{begriff}“ — antworte mit 1 bis {anzahl}.", url="/#chat")
            else:
                benachrichtigungen.senden(
                    "kontakte", "Nichts gefunden",
                    f"Zu „{begriff}“ habe ich auf dem Handy nichts gefunden.", url="/#studio/kontakte")
        except Exception:  # noqa: BLE001 — ein Push-Fehler ändert nichts an der Ablage
            log.exception("Push für Suchergebnis nicht gesendet")
        snapchat.suchergebnis_gemeldet(begriff)


_annehmen_lauf = {"stunde": None}


def _eingehende_annehmen() -> None:
    """Erwartete Anfragen annehmen (11.10.2026): Für jeden Namen auf der Erwartungsliste lässt die Wache das Handy alle
    `ERWARTET_TAKT_S` einmal unter „Added Me“ nachsehen — nur wenn der Helfer frei ist, das Schreibrecht da ist und es
    tagsüber ist. Ein Fund wird Freund und Kontakt; „nicht gefunden“ heißt weiter warten (nach 48 h verfällt der Name)."""
    if not konnektoren.schreiben_erlaubt("snapchat"):
        return
    liste = snapchat.erwartet_liste()
    # 1. Ergebnisse von vorhin einsammeln
    for e in liste:
        auftrag = str(e.get("auftrag", "") or "")
        if not auftrag:
            continue
        ergebnis = snapchat.ergebnis_fuer(auftrag)
        if ergebnis is None:
            continue
        snapchat.erwartet_vermerken(e["name"], auftrag="")
        grund = str(ergebnis.get("grund", "") or "")
        if ergebnis.get("ergebnis") == "gesendet" or grund.startswith(snapchat.SCHON_BEFREUNDET):
            unbekannt.eingehende_uebernehmen(e["name"])
    # 2. Den nächsten fälligen Namen losschicken (einer je Tick, nur bei freiem Helfer, nur tagsüber)
    if config.jetzt().hour not in WACH_STUNDEN or snapchat.auftrag_lesen() is not None:
        return
    jetzt = time.time()
    for e in snapchat.erwartet_liste():
        if e.get("auftrag") or jetzt - float(e.get("zuletzt", 0) or 0) < snapchat.ERWARTET_TAKT_S:
            continue
        try:
            auftrag = snapchat.auftrag_legen("annehmen", snapchat=e["name"])
        except snapchat.SnapchatFehler:
            return
        snapchat.erwartet_vermerken(e["name"], zuletzt=jetzt, auftrag=auftrag)
        return


async def einmal() -> None:
    if not konnektoren.benutzbar("snapchat"):
        return
    try:
        snapchat.kontakte_schreiben()   # die Liste des Helfers folgt dem Register (Pausieren/Umbenennen gilt sofort)
    except Exception:  # noqa: BLE001 — ein Fehler hier darf die Wache nicht anhalten
        log.exception("Snapchat-Kontaktliste nicht geschrieben")
    try:
        _anfragen_spiegeln_melden()   # gescheiterte Anfragen ehrlich melden (03.10.2026)
    except Exception:  # noqa: BLE001 — ein Fehler hier darf die Wache nicht anhalten
        log.exception("Anfrage-Ergebnisse nicht gespiegelt")
    try:
        _eingehende_annehmen()   # erwartete Anfragen annehmen (11.10.2026)
    except Exception:  # noqa: BLE001 — ein Fehler hier darf die Wache nicht anhalten
        log.exception("Eingehende Anfragen nicht bearbeitet")
    try:
        _suchergebnisse_melden()   # fertige Handy-Suchen per Push an Eike (04.10.2026)
    except Exception:  # noqa: BLE001 — ein Fehler hier darf die Wache nicht anhalten
        log.exception("Suchergebnisse nicht gemeldet")
    try:
        for anzeige in snapchat.unbekannte_lesen():
            if anzeige not in _unbekannt_gemeldet:     # der Helfer hängt nur an; die Wache löscht dort nichts, merkt sich aber, was sie meldete
                if not unbekannt.annahme_pruefen("snapchat", anzeige):   # angenommene Anfrage: eigener Push, kein Unbekannt-Push
                    unbekannt.melden("snapchat", anzeige)   # einmal Push je Name; bekannte/reservierte Namen meldet `melden` nicht
                _unbekannt_gemeldet.add(anzeige)
    except Exception:  # noqa: BLE001 — eine Meldung darf die Wache nicht anhalten
        log.exception("Unbekannte Snapchat-Kontakte nicht gemeldet")
    try:
        _rueckfragen_melden()
    except Exception:  # noqa: BLE001 — eine Antwort auf eine Rückfrage darf die Wache nicht anhalten
        log.exception("Rückfrage-Antwort nicht gesendet")
    z = snapchat.zustand()
    verbunden, meldung = bool(z.get("verbunden")), z.get("letzter_fehler") or "Verbunden."
    if verbunden and z.get("zeit") and time.time() - z["zeit"] > HELFER_STILL_NACH_S \
            and config.jetzt().hour in WACH_STUNDEN:
        verbunden, meldung = False, "Der Helfer auf hphome meldet sich nicht mehr (läuft er noch?)."
    # Der Lauf tickt alle paar Sekunden — die Freigaben-Seite braucht die Meldung nur bei Änderung
    # oder alle 30 s, nicht bei jedem Tick eine Schreibaktion auf der Platte.
    if _vermerkt.get("stand") != (verbunden, meldung) or time.time() - _vermerkt.get("zeit", 0) > 30:
        konnektoren.vermerken("snapchat", verbunden, meldung)
        _vermerkt.update(stand=(verbunden, meldung), zeit=time.time())
    if _nachzulegen and snapchat.auftraege_anzahl() <= snapchat.QUEUE_MAX:
        try:
            _antwort_legen(_nachzulegen[0], None, None)
            _nachzulegen.pop(0)
        except snapchat.SnapchatFehler as exc:
            log.warning("Snapchat-Wache: wartende Antwort konnte nicht gelegt werden (%s)", exc)
    eintraege = snapchat.eingang_neu()
    if not eintraege:
        return
    gruppen, ungueltig = snapchat.gruppen(eintraege)
    if ungueltig:
        # Ein `person`-Wert, der keine Register-Id ist, wird NIE als Eike gedeutet — und nie beantwortet.
        log.warning("Snapchat-Wache: %d Eingangseinträge mit ungültiger Person verworfen", len(ungueltig))
        snapchat.eingang_gelesen_markieren([e["id"] for e in ungueltig])
    if not gruppen:
        return
    # Je Tick bis zu `_parallel["grenze"]` Gruppen gleichzeitig (Eike und jede Person einzeln, nie gemischt, je Person eine): Eike zuerst,
    # dann die mit dem ältesten Eintrag unter denen, die jetzt dran sind — nicht zurückgestellt und mit abgelaufenem Bündelfenster.
    # So hält weder eine scheiternde noch eine dauernd schreibende Gruppe die anderen auf.
    jetzt = time.time()
    # Ein Platz bleibt frei: Eikes „schick X …“ (Werkzeug) darf nicht an einer von vielen Kontakten gefüllten Schlange scheitern.
    frei = snapchat.QUEUE_MAX - snapchat.auftraege_anzahl()
    if frei < 1 or snapchat.sende_zaehler() >= snapchat.SENDE_GRENZE:
        return  # Die Schlange vor dem Helfer ist (fast) voll — der nächste Lauf sieht wieder nach
    platz = max(1, min(_parallel["grenze"], frei))
    gewaehlt: list[tuple] = []
    for k in sorted(gruppen, key=lambda k: (k is not None, min(_zeit(e) for e in gruppen[k]))):
        if len(gewaehlt) >= platz:
            break
        eintraege = gruppen[k]
        person = None
        if k is not None:
            person = register.holen(k)     # frisch: pausiert, gelöscht oder ohne Snapchat-Namen heißt keine Antwort
            if not person or person.get("pausiert") or not _hat_snapchat(person):
                log.info("Snapchat-Wache: Person %s ist unbekannt, pausiert oder ohne Snapchat-Namen — keine Antwort", k)
                snapchat.eingang_gelesen_markieren([e["id"] for e in eintraege])
                continue
        if _zurueck.get(k, 0) > jetzt:
            continue
        if jetzt - max(_zeit(e) for e in eintraege) < BUENDEL_S:
            continue  # es kommt vielleicht noch etwas dazu: Text und Snap sollen ein Zug werden
        gewaehlt.append((k, person, eintraege))
    if not gewaehlt:
        return
    ergebnisse = await asyncio.gather(*[_bearbeiten(pid, person, eintraege) for pid, person, eintraege in gewaehlt],
                                      return_exceptions=True)
    if any(isinstance(r, BaseException) or r == "fehler" for r in ergebnisse):
        _parallel.update(grenze=1, ruhig=0)      # nach einem Fehler vorsichtig: eine nach der anderen
    else:
        _parallel["ruhig"] += 1
        if _parallel["ruhig"] >= RUHIG_BIS_HOCH and _parallel["grenze"] < PARALLEL:
            _parallel.update(grenze=_parallel["grenze"] + 1, ruhig=0)


async def _tickets_annehmen(person: dict | None, eintraege: list[dict]) -> list[dict]:
    """Ticketbilder aus dem Eingang abfangen (Spec 2026-10-09): im Probelauf lesen und bewerten, aber nichts speichern und nichts
    senden; mit „an“ prüft der Server und Friday bekommt nur den festen Hinweisblock statt des Bildes. Bei „aus“ — und bei
    Eike, der nur im Probelauf annehmen darf — bleibt es ein gewöhnliches Chatbild."""
    modus = snapchat.ticket_modus()
    rest = []
    for e in eintraege:
        if e.get("art") != "ticketbild":
            rest.append(e)
            continue
        if modus == "aus" or (person is None and modus != "trocken"):
            rest.append({**e, "art": "chatbild"})
            continue
        daten = _jpeg(snapchat.bild_lesen(e.get("bild") or ""))
        if daten is None:
            hinweis = annahme.HINWEIS_NICHT_LESBAR
        else:
            try:
                hinweis = (await annahme.annehmen(person, daten, modus))["hinweis"]
            except Exception as exc:       # noqa: BLE001 — die Wache darf nie an einem Ticket sterben
                log.warning("Snapchat-Wache: Ticketannahme gescheitert (%s)", type(exc).__name__)
                hinweis = annahme.HINWEIS_FEHLER
        snapchat.bild_entfernen(e.get("bild") or "")
        if modus == "trocken":
            snapchat.eingang_gelesen_markieren([e["id"]])
            continue
        rest.append({"id": e["id"], "zeit": e.get("zeit"), "art": "text", "text": hinweis})
    return rest


_TICKET_WOERTER = ("fahrkarte", "ticket", "netzkarte", "monatskarte", "deutschlandticket", "kundenkarte", "abo-karte", "semesterticket")


def _sieht_nach_ticket_aus(beschreibung: str) -> bool:
    t = (beschreibung or "").casefold()
    return any(w in t for w in _TICKET_WOERTER)


async def _ticket_nachtraeglich(person: dict | None, bild_id: str, b64: str, beschreibung: str) -> None:
    """Ein Bild einer Person als Ticket erkannt, NACHDEM es angekommen ist (11.10.2026, Kontakt O-Fall): Snaps tragen kein
    „Ticketbild“-Merkmal, und ein geöffneter Snap ist weg — gespeichert wird das reine Bild, das schon im Gedächtnis liegt.
    Nur mit Modus „an“ und nur bei Personen mit Garantie-Profil; gespeichert wird im Profil (für die Anträge), nicht noch
    einmal anderswo: die Kopie im Bildgedächtnis wird danach entfernt. Eike bekommt einen Push mit dem Ergebnis."""
    if person is None or snapchat.ticket_modus() != "an" or not _sieht_nach_ticket_aus(beschreibung):
        return
    pid = (person.get("quellen") or {}).get("garantie")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return
    from kern.skills import standard
    if standard.fuer(person) is not None:
        return                               # der Garantie-Ablauf dieser Person liest und bestätigt das Ticket selbst (mit ihr)
    try:
        daten = base64.b64decode(b64)       # schon JPEG (so liegt es im Gedächtnis)
        erg = await annahme.annehmen(person, daten, "an")
        if erg.get("gespeichert"):
            bilder.entfernen(bild_id)       # Eike (11.10.2026): Tickets gehören nicht ins Bildgedächtnis, kein Bild doppelt
        from kern.dienste import benachrichtigungen
        satz = {"angenommen": "gelesen und im Garantie-Profil gespeichert.",
                "eike_pruefen": ("liegt im Garantie-Profil, bitte kurz ansehen: Papier- oder unklares Ticket." if erg.get("gespeichert")
                                 else "gelesen, aber nicht gespeichert — bitte kurz ansehen."),
                "nachfragen": "unklar oder nicht lesbar — die Person sollte es noch einmal schicken."}.get(erg.get("ergebnis"), "geprüft.")
        benachrichtigungen.senden("garantie", f"Ticket von {person['name']}", f"Das Bild von {person['name']} ist ein Ticket: {satz}", url="/#studio/garantie")
    except Exception as exc:       # noqa: BLE001 — die Wache darf nie an einem Ticket sterben
        log.warning("Snapchat-Wache: nachträgliche Ticketannahme gescheitert (%s)", type(exc).__name__)


def _wiederholt(pid, text: str, jetzt: float | None = None) -> bool:
    """Kommt derselbe Text immer wieder (Lesefehler im Helfer, Vorfall 14./15.10.2026: 25× „Ja“ → 25× „Sir?“), ist es ab dem fünften Mal
    ein Fehler und kein Gespräch. Gezählt wird je Gruppe (Eike oder eine Person), ohne Satzzeichen und Großschreibung."""
    jetzt = time.time() if jetzt is None else jetzt
    norm = " ".join(re.sub(r"[\W_]+", " ", text or "").casefold().split())
    liste = [(z, t) for z, t in _gesehen_texte.get(pid, []) if jetzt - z < WIEDERHOLT_FENSTER_S]
    liste.append((jetzt, norm))
    _gesehen_texte[pid] = liste[-20:]
    return bool(norm) and sum(1 for _, t in liste if t == norm) >= WIEDERHOLT_AB


SELFIE_PROTOKOLL = "[Ich habe ein Selfie von mir zurückgeschickt.]"


async def _selfie_vorab(eintraege: list[dict], person: dict | None) -> tuple[dict, bool]:
    """Ein reiner Snap-Zug (ohne Text, ohne Chatbild): Ist der Snap ein Selfie und geht Fridays Selfie raus, ist DAS die Antwort —
    kein zusätzlicher Text. Sonst bleibt es bei der Textantwort. Gibt die Bildauswertungen (nach Bild-Base64, für den Nachlauf)
    und zurück, ob das Selfie rausging."""
    if not eintraege or any(e.get("art", "text") != "snap" for e in eintraege):
        return {}, False
    nachrichten, _ = _zug_bauen(eintraege, person)
    vorab: dict = {}
    for n in nachrichten:
        b64 = (n.get("bild") or {}).get("data")
        if not b64 or b64 in vorab:
            continue
        try:
            auswertung = await bildauswertung.auswerten(b64, "image/jpeg", _quelle(person), "")
        except Exception:  # noqa: BLE001 — ohne Auswertung bleibt es bei der Textantwort
            log.exception("Snapchat-Wache: Vorab-Auswertung gescheitert")
            continue
        if not auswertung:
            continue
        vorab[b64] = auswertung
        if not auswertung.get("selfie"):
            continue
        try:
            status = await friday_selfie.antworten(person, base64.b64decode(b64), auswertung["beschreibung"])
        except Exception:  # noqa: BLE001 — das Selfie ist Zugabe: im Zweifel antwortet Friday mit Text
            log.exception("Snapchat-Wache: Selfie-Antwort gescheitert")
            continue
        log.info("Snapchat-Wache: Selfie-Antwort für %s: %s", "Eike" if person is None else person["id"], status)
        if status == "gesendet":
            return vorab, True       # höchstens ein Selfie je Zug
    return vorab, False


async def _bearbeiten(pid, person, eintraege) -> str:
    """Eine Gruppe beantworten: Gemini, Auftrag legen, protokollieren. „ok“ oder „fehler“ (dann ist die Gruppe zurückgestellt)."""
    eintraege = await _tickets_annehmen(person, eintraege)
    if not eintraege:
        return "ok"          # Probelauf: alles verbraucht, keine Antwort
    _, bild_namen = _zug_bauen(eintraege, person)
    zug_text = "\n".join(e["text"] for e in eintraege if e.get("art", "text") == "text") or "[Snap]"
    if not bild_namen and zug_text != "[Snap]" and _wiederholt(pid, zug_text):
        # Dieselbe Nachricht zum wiederholten Mal: nicht noch einmal antworten, als erledigt markieren, Eike einmal Bescheid sagen.
        log.warning("Snapchat-Wache: gleiche Nachricht von %s zum wiederholten Mal — keine Antwort mehr", "Eike" if person is None else pid)
        if (pid, zug_text) not in _wiederholung_gemeldet:
            _wiederholung_gemeldet.add((pid, zug_text))
            try:
                from kern.dienste import benachrichtigungen
                wer = "dir" if person is None else person["name"]
                benachrichtigungen.senden("friday", "Snapchat: gleiche Nachricht immer wieder",
                                          f"„{zug_text[:60]}“ kam von {wer} mehrfach hintereinander an — ich antworte nicht mehr darauf. "
                                          "Vermutlich liest der Handy-Helfer eine alte Nachricht neu.")
            except Exception:  # noqa: BLE001 — die Meldung ist Zugabe
                log.exception("Snapchat-Wache: Wiederholungs-Hinweis nicht gesendet")
        snapchat.eingang_gelesen_markieren([e["id"] for e in eintraege])
        return "ok"
    vorab, selfie_raus = await _selfie_vorab(eintraege, person)
    try:
        ergebnis = ({"antwort": SELFIE_PROTOKOLL, "werkzeug_ergebnisse": []} if selfie_raus
                    else await _antworten(eintraege, person))
    except Exception as exc:  # ein Fehler darf die Wache nie töten, der nächste Lauf versucht's erneut
        log.warning("Snapchat-Wache: Antwort gescheitert (%s)", type(exc).__name__)
        if time.time() - min(_zeit(e) or time.time() for e in eintraege) < NOTFALL_NACH_S:
            _zurueck[pid] = time.time() + RUECKSTELLUNG_S
            return "fehler"
        ergebnis = {"antwort": NOTFALL_ANTWORT, "werkzeug_ergebnisse": []}
    if not ergebnis.get("antwort"):
        _zurueck[pid] = time.time() + RUECKSTELLUNG_S
        return "fehler"  # leere Antwort: nicht als leeren Auftrag verschicken, nicht als gelesen markieren
    try:
        if not selfie_raus:        # ein Selfie ist schon die Antwort: nicht zusätzlich noch ein Text
            _antwort_legen(ergebnis["antwort"], None if person is None else pid, min(_zeit(e) for e in eintraege))
    except snapchat.SnapchatFehler as exc:
        if person is not None or "schicke_snap" not in (ergebnis.get("werkzeuge") or []):
            log.warning("Snapchat-Wache: Antwort konnte nicht als Auftrag gelegt werden (%s)", exc)
            _zurueck[pid] = time.time() + RUECKSTELLUNG_S
            return "fehler"
        # Ein Snap des Werkzeugs ist unterwegs und die Schlange voll: Der Zug ist erledigt (nicht wiederholen!), die
        # Textantwort geht raus, sobald wieder Platz ist. (Nur Eike hat `schicke_snap`.)
        _nachzulegen.append(ergebnis["antwort"])
    gespeichert = _protokollieren(zug_text, bild_namen, ergebnis, person)
    snapchat.eingang_gelesen_markieren([e["id"] for e in eintraege])
    if person is not None:
        ids = [e.get("id") for e in eintraege if e.get("id")]
        # Der Push „X antwortet“ kommt nur bei aktiver Überwachung dieser Person (Kontakte → Module, Standard aus; 15.10.2026).
        if modul.von_person(person)["ueberwachung"] and any(i not in _antwort_gemeldet for i in ids):
            from kern.dienste import benachrichtigungen
            try:
                benachrichtigungen.senden("kontakt_antwort", f"{person['name']} antwortet", zug_text[:280], url="/#studio/kontakte")
            except Exception:  # noqa: BLE001 — die Meldung ist Zugabe; ein Fehler darf den Zug nicht wiederholen lassen
                log.exception("Snapchat-Wache: Antwort-Push nicht gesendet")
            _antwort_gemeldet.update(ids)
    _zurueck.pop(pid, None)
    if person is not None:
        # Regeln (Eike, 15.10.2026): ein Zug = ein Ereignis. Ein Fehler hier ändert nichts an der Antwort, die schon raus ist.
        try:
            await regeln_lauf.ereignis("snapchat", pid, person["name"], zug_text, ",".join(str(e.get("id")) for e in eintraege)[:200])
        except Exception:  # noqa: BLE001
            log.exception("Snapchat-Wache: Regel-Lauf gescheitert")
    # Die Antwort ist raus. Jetzt erst die sachliche Beschreibung fürs Gedächtnis (eigene Runde).
    for bild_id, b64 in gespeichert:
        # Nur Eikes eigene Worte dürfen ein Bild benennen; der Text einer anderen Person ist fremd (kein Beleg, kein „Nachricht von Eike“).
        nachricht = "" if person is not None or zug_text == "[Snap]" else zug_text
        auswertung = vorab.get(b64) or await bildauswertung.auswerten(b64, "image/jpeg", _quelle(person), nachricht)
        if auswertung:
            bilder.beschreiben(bild_id, auswertung["beschreibung"])
            # Eine Person als Absender: genau eine Person auf dem Bild heißt wie sie (Selfie-Annahme, „automatisch“ vermerkt).
            bildpersonen.zuordnen(bild_id, auswertung, nachricht, absender=(person.get("graph_name") or person["name"]) if person else "")
            await _ticket_nachtraeglich(person, bild_id, b64, auswertung["beschreibung"])
    for name in bild_namen:
        snapchat.bild_entfernen(name)
    return "ok"


async def dauerlauf() -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await einmal()
        except Exception as exc:
            log.warning("Snapchat-Wache gescheitert (%s)", type(exc).__name__)
        await asyncio.sleep(TAKT_S)
