"""Unbekannte Kontakte: wer Friday schreibt, ohne angelegt zu sein.

Friday antwortet einem Unbekannten nie und öffnet seine Snaps nicht (ein geöffneter Snap ist verbraucht).
Hier steht nur der Anzeigename — kein Inhalt. Eike bekommt einmal einen Push und legt die Person im Menü an
(`register.anlegen` nimmt sie dann aus dieser Liste) oder ignoriert sie."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from kern import config
from kern.personen import register

log = logging.getLogger("friday.personen")
DATEI = "personen_unbekannt.json"
_sperre = threading.RLock()


def _lesen() -> list[dict]:
    try:
        d = json.loads((config.daten_ordner() / DATEI).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return d if isinstance(d, list) else []


def _speichern(eintraege: list[dict]) -> None:
    ziel = config.daten_ordner() / DATEI
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(eintraege[-200:], f, ensure_ascii=False, indent=1)
    os.chmod(neu, 0o600)
    os.replace(neu, ziel)


def _gleich(e: dict, kanal: str, anzeige: str) -> bool:
    return e.get("kanal") == kanal and register.schluessel(e.get("anzeige", "")) == register.schluessel(anzeige)


def melden(kanal: str, anzeige: str) -> bool:
    """Vermerkt einen unbekannten Anzeigenamen. True, wenn er neu war (dann geht ein Push an Eike)."""
    anzeige = register._text(anzeige, register.ANZEIGE_MAX)
    if not anzeige or register.schluessel(anzeige) in register.RESERVIERT or register.nach_kanal(kanal, anzeige):
        return False
    with _sperre:
        eintraege = _lesen()
        if any(_gleich(e, kanal, anzeige) for e in eintraege):
            return False
        eintraege.append({"kanal": kanal, "anzeige": anzeige, "ignoriert": False,
                          "erstmals": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        _speichern(eintraege)
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden("kontakte", "Neuer Kontakt",
                                  f"{anzeige} hat Friday geschrieben. Anlegen unter Extras → Kontakte; "
                                  "bis dahin antwortet Friday nicht.", url="/#studio/kontakte")
    except Exception:  # noqa: BLE001 — ein Push-Fehler darf den Vermerk nie aufhalten
        log.exception("Push für neuen Kontakt nicht gesendet")
    return True


def liste() -> list[dict]:
    # Wer inzwischen angelegt ist (auch nachträglich über `aendern` mit Snapchat-Namen), ist nicht mehr unbekannt.
    return [dict(e) for e in _lesen()
            if not e.get("ignoriert") and not register.nach_kanal(e.get("kanal", ""), e.get("anzeige", ""))]


def ignorieren(kanal: str, anzeige: str) -> None:
    with _sperre:
        eintraege = _lesen()
        for e in eintraege:
            if _gleich(e, kanal, anzeige):
                e["ignoriert"] = True
        _speichern(eintraege)


def entfernen(kanal: str, anzeige: str) -> None:
    """Nach dem Anlegen einer Person: der Kontakt steht nicht mehr als unbekannt da."""
    if not anzeige:
        return
    with _sperre:
        eintraege = _lesen()
        rest = [e for e in eintraege if not _gleich(e, kanal, anzeige)]
        if len(rest) != len(eintraege):
            _speichern(rest)


def _kontakt_anlegen(anfrage: dict, anzeige: str) -> str:
    """Eike hat die Person selbst gewählt (Treffer-Nummer, Ja) — nach der Annahme steht sie als Kontakt da,
    mit den Standard-Rechten (nur Uhrzeit). Sonst müsste er dieselbe Person ein zweites Mal anlegen.
    Gibt den Namen zurück, oder „“ (schon da / nicht möglich → alter Weg per Hand)."""
    if anfrage.get("person") or not anfrage.get("name") \
            or register.nach_kanal("snapchat", anfrage.get("snapchat", "")):
        return ""                      # ohne Namen von Eike kein Raten: der Handle ist kein Name
    try:
        # Der Helfer erkennt Kontakte am Anzeigenamen der Chatliste: der Treffer-Titel, sonst der Nutzername.
        return register.anlegen(anfrage["name"], snapchat=anfrage.get("titel") or anfrage.get("snapchat", ""))["name"]
    except Exception:  # noqa: BLE001 — Name/Identität nicht erlaubt: dann legt Eike sie selbst an
        log.info("Kontakt nach Annahme nicht automatisch angelegt", exc_info=True)
        return ""


def _danach_schicken(anfrage: dict, dienst) -> None:
    """Eike wollte, dass Friday nach der Annahme etwas schreibt („stell dich vor“): jetzt geht es einmal raus.
    Der Wortlaut stand beim Beauftragen fest und wurde Eike vorgelesen; er gilt nur für diese eine Nachricht."""
    text = dienst.anfrage_danach_holen(anfrage.get("snapchat", ""))
    if not text:
        return
    profil = register.holen(anfrage["person"]) if anfrage.get("person") else (
        register.nach_kanal("snapchat", anfrage.get("snapchat", ""))
        or register.nach_kanal("snapchat", anfrage.get("titel", "")))     # angelegt wird unter dem Anzeigenamen
    if not profil or profil.get("pausiert"):
        return
    try:
        dienst.auftrag_legen("text", text=text, person=profil["id"])
    except Exception:  # noqa: BLE001 — Schlange voll o. ä.: Eike sagt Bescheid, die Annahme bleibt gültig
        log.exception("Nachricht nach der Annahme nicht gelegt")


def _uebernehmen(anfrage: dict, anzeige: str, satz: str) -> None:
    """Die Person ist Freund: Kontakt anlegen (wenn der Name bekannt ist), Vorstellung losschicken, ein Push an Eike."""
    from kern.dienste import snapchat as dienst
    angelegt = _kontakt_anlegen(anfrage, anzeige)
    _danach_schicken(anfrage, dienst)
    try:
        from kern.dienste import benachrichtigungen
        schon = any(register.nach_kanal("snapchat", n) for n in (anfrage.get("titel"), anfrage.get("snapchat")) if n)
        benachrichtigungen.senden("kontakte", "Anfrage angenommen",
                                  satz + (f" und ist als Kontakt „{angelegt}“ angelegt (nur die Uhrzeit erlaubt; "
                                          "Rechte unter Extras → Kontakte)." if angelegt
                                          else "; sie steht schon als Kontakt da." if schon
                                          else ". Lege die Person unter Extras → Kontakte an, "
                                               "dann kann Friday ihr schreiben."), url="/#studio/kontakte")
    except Exception:  # noqa: BLE001 — ein Push-Fehler darf den Vermerk nie aufhalten
        log.exception("Push für angenommene Anfrage nicht gesendet")


def eingehende_uebernehmen(name: str) -> str:
    """Eine eingegangene Anfrage wurde angenommen (11.10.2026): die Person ist Freund und steht als Kontakt da, mit den
    Standard-Rechten (nur Uhrzeit) — ein Push an Eike. Der Anzeigename ist der Name; Eike benennt ihn bei Bedarf um."""
    from kern.dienste import snapchat as dienst
    dienst.erwartet_erledigen(name)
    anfrage = {"snapchat": name, "name": name, "titel": name}
    schon = register.nach_kanal("snapchat", name)
    angelegt = "" if schon else _kontakt_anlegen(anfrage, name)
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden(
            "kontakte", "Anfrage angenommen",
            f"{name} hat Friday angefragt, Friday hat angenommen"
            + (f" und {name} als Kontakt angelegt (nur die Uhrzeit erlaubt; Rechte unter Extras → Kontakte)." if angelegt
               else "; sie steht schon als Kontakt da." if schon
               else ". Lege die Person unter Extras → Kontakte an, dann kann Friday ihr schreiben."),
            url="/#studio/kontakte")
    except Exception:  # noqa: BLE001 — ein Push-Fehler darf nichts aufhalten
        log.exception("Push für angenommene Anfrage nicht gesendet")
    return angelegt or (schon["name"] if schon else "")


def befreundete_uebernehmen() -> None:
    """Das Handy fand die angefragte Person schon unter „Friends & Groups“ (09.10.2026): wie eine Annahme behandeln,
    statt „Anfrage gescheitert“ zu melden."""
    from kern.dienste import snapchat as dienst
    for a in dienst.anfragen_schon_befreundet():
        _uebernehmen(a, a.get("titel") or a.get("snapchat", ""),
                     f"{a.get('titel') or a.get('snapchat', '')} ist schon mit dir befreundet")


def annahme_pruefen(kanal: str, anzeige: str) -> bool:
    """Passt ein fremder Name zu einer vermerkten Kontakt-Anfrage (03.10.2026)? Dann ist
    das keine Unbekannte, sondern die Annahme: Zustand wechseln, genau ein Push an Eike.
    True heißt übernommen — die Wache meldet den Namen NICHT zusätzlich als unbekannt.
    Auch ein zweiter Aufruf bleibt True (ohne neuen Push), damit nach einem Neustart
    kein Unbekannt-Push für die angenommene Person folgt."""
    if kanal != "snapchat":
        return False
    from kern.dienste import snapchat as dienst
    kandidaten, treffer = set(), None
    for a in dienst.anfragen():
        if a.get("zustand") not in ("ausstehend", "angenommen"):
            continue
        # Nach der Annahme zeigt die Chatliste oft den Anzeigenamen statt des Nutzernamens: auch den (und Eikes
        # Namen) gelten lassen, sonst bliebe die Person „unbekannt“ und Eike müsste sie extra freigeben.
        namen = {register.schluessel(a.get(f, "")) for f in ("snapchat", "name", "titel") if a.get(f)}
        if a.get("person"):
            profil = register.holen(a["person"])
            if profil:
                namen.add(register.schluessel(profil.get("name", "")))
        if register.schluessel(anzeige or "") in namen:
            treffer = a
            break
    if treffer is None:
        return False
    if treffer.get("zustand") == "ausstehend":
        dienst.anfrage_setzen(treffer["snapchat"], "angenommen")
        _uebernehmen(treffer, anzeige, f"{anzeige} hat deine Kontaktanfrage angenommen")
    return True
