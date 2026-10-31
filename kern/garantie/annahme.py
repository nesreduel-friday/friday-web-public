"""Ticketbild von einer Person per Snapchat annehmen (09.10.2026, Spec `2026-10-09-ticket-annahme-design.md`).

Hier: bewerten (ohne Modell, nur aus den gelesenen Feldern) — und in `annehmen` lesen, speichern, Hinweis bauen.
Was in den Hinweis für Friday kommt, ist ausschließlich Ergebniscode, ISO-Datum und feste Sätze: Freitext aus dem
Bild (`bezeichnung`, `zweifel`, Inhabername) ist fremder Text und gelangt nie in Fridays Prompt.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from datetime import date, datetime

from kern import config
from kern.garantie import ablage, uebernahme
from kern.garantie import ticket as garantieticket

log = logging.getLogger("friday.annahme")

GRUND_TEXT = {
    "keine_fahrkarte": "auf dem Bild ist keine Fahrkarte zu erkennen",
    "nur_kundenkarte": "das Bild zeigt nur die Kundenkarte, nicht das Ticket",
    "datum_unlesbar": "die Gültigkeit war nicht lesbar",
    "abgelaufen": "das Ticket ist schon abgelaufen",
    "zweifel": "das Bild war nicht eindeutig lesbar",
    "lesefehler": "das Bild konnte nicht gelesen werden",
}


def _teile(text: str) -> list[str]:
    t = (text or "").lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    t = unicodedata.normalize("NFKD", t)
    return re.findall(r"[a-z]+", "".join(c for c in t if not unicodedata.combining(c)))


def name_passt(inhaber: str, vollname: str) -> bool:
    """Stehen alle Namensteile des Profils auf dem Ticket? Zweiter Vorname, Reihenfolge, Umlaute und Bindestrich sind egal.
    Steht auf dem Ticket kein Name, gibt es nichts zu vergleichen (True); hat das Profil keinen, ist es unklar (False)."""
    drauf, soll = set(_teile(inhaber)), _teile(vollname)
    if not drauf:
        return True
    return bool(soll) and all(teil in drauf for teil in soll)


def vollname(profil: dict | None, angaben: dict) -> str:
    profil, angaben = profil or {}, angaben or {}
    vor = profil.get("vorname") or angaben.get("vorname") or ""
    nach = profil.get("nachname") or angaben.get("nachname") or ""
    return " ".join(x for x in (vor, nach) if x) or profil.get("name") or ""


def bewerten(erkannt: dict, vollname: str, heute: date) -> dict:
    def er(ergebnis: str, grund: str) -> dict:
        return {"ergebnis": ergebnis, "grund": grund}

    if not erkannt.get("ist_fahrkarte"):
        return er("nachfragen", "keine_fahrkarte")
    if erkannt.get("ist_kundenkarte"):
        return er("nachfragen", "nur_kundenkarte")
    von, bis = erkannt.get("gueltig_von") or "", erkannt.get("gueltig_bis") or ""
    if not (von and bis):
        return er("nachfragen", "datum_unlesbar")
    if bis < heute.isoformat():
        return er("nachfragen", "abgelaufen")
    if not name_passt(erkannt.get("inhaber") or "", vollname):
        return er("eike_pruefen", "inhaber")
    if erkannt.get("form") == "papier":
        return er("eike_pruefen", "papier")
    if erkannt.get("form") != "digital":
        return er("eike_pruefen", "form_unklar")
    if erkannt.get("art") == "Deutschlandticket" and garantieticket.ist_uestra(erkannt.get("herausgeber") or "") is False:
        return er("eike_pruefen", "herausgeber")
    if erkannt.get("zweifel"):
        return er("nachfragen", "zweifel")
    return er("angenommen", "ok")


HINWEIS_KOPF = "[TICKET-PRÜFUNG vom Server]"
HINWEIS_FEHLER = (f"{HINWEIS_KOPF} Das Ticketbild konnte gerade nicht geprüft werden. Bitte die Person freundlich, "
                  "das Bild in ein paar Minuten noch einmal zu schicken.")
HINWEIS_NICHT_LESBAR = (f"{HINWEIS_KOPF} Das Bild ist nicht angekommen. Bitte die Person freundlich, "
                        "ihr Ticket noch einmal als Bild zu schicken.")

_ANLIEGEN = {
    "papier": "Papierticket: Kundenkarten- und Ticketnummer mit dem Original vergleichen und das Häkchen setzen.",
    "inhaber": "Der Name auf dem Ticket passt nicht zum Profil. Das Ticket wurde nicht übernommen.",
    "form_unklar": "Friday konnte nicht erkennen, ob es ein digitales oder ein Papierticket ist. Bitte ansehen.",
    "herausgeber": "Deutschlandticket nicht von der ÜSTRA: Die Garantie gilt dafür nicht. Das Ticket wurde nicht übernommen.",
    "kundenkarte_weicht_ab": "Die von der Person bestätigte Kundenkartennummer weicht von der gespeicherten ab. Bitte prüfen, welche stimmt; vorher schickt Friday nicht ab.",
    "kein_profil": "Für diese Person gibt es kein Garantie-Profil, ein Ticketbild kam trotzdem an. Bitte Profil anlegen und verknüpfen.",
}


def _tag(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d.%m.%Y")


def _hinweis(ergebnis: str, grund: str, erkannt: dict, modus: str) -> str:
    kopf = HINWEIS_KOPF + (" Probelauf, nichts gespeichert." if modus == "trocken" else "")
    if ergebnis == "angenommen":
        return (f"{kopf} Ergebnis: angenommen, gültig {_tag(erkannt['gueltig_von'])} bis {_tag(erkannt['gueltig_bis'])}. "
                "Bestätige der Person kurz, dass das Ticket angekommen ist.")
    if ergebnis == "nachfragen":
        return (f"{kopf} Ergebnis: nicht angenommen, {GRUND_TEXT[grund]}. Bitte die Person freundlich um ein neues, "
                "vollständiges Bild ihres aktuellen Tickets.")
    return (f"{kopf} Ergebnis: Eike schaut selbst drauf. Sag der Person nur, dass Eike kurz drauf schaut — "
            "nenne keinen Grund.")


def _probe_vermerken(person: dict | None, ergebnis: str, grund: str, erkannt: dict) -> None:
    zeile = {"zeit": time.time(), "person": person["id"] if person else "eike", "ergebnis": ergebnis, "grund": grund,
             "von": erkannt.get("gueltig_von", ""), "bis": erkannt.get("gueltig_bis", ""),
             "art": erkannt.get("art", ""), "form": erkannt.get("form", "")}
    ordner = config.daten_ordner() / "snapchat"
    ordner.mkdir(parents=True, exist_ok=True)
    with open(ordner / "ticket_probe.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def _anliegen_fuer_eike(person: dict | None, grund: str) -> None:
    try:
        from kern.anliegen import fach
        wer = person["name"] if person else "Eike"
        fach.standard().anlegen("eike", "ticket-annahme", f"Ticket von {wer} prüfen", _ANLIEGEN[grund], "normal")
    except Exception:                      # noqa: BLE001 — ein Anliegen darf die Annahme nie kippen
        log.exception("Anliegen zur Ticketannahme nicht angelegt")


async def annehmen(person: dict | None, jpeg: bytes, modus: str) -> dict:
    """Ein Ticketbild lesen, bewerten und (nur `modus == "an"`) im Profil der absendenden Person speichern."""
    if modus not in ("trocken", "an"):
        raise ValueError("Die Ticketannahme ist ausgeschaltet.")
    if person is None and modus != "trocken":
        raise ValueError("Eikes eigener Chat nimmt Tickets nur im Probelauf an.")
    a = ablage.standard()
    pid = 0
    profil = None
    if person is not None:
        pid = (person.get("quellen") or {}).get("garantie")
        profil = a.profil(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
        if not profil or profil.get("archiviert"):
            if modus == "an":
                _anliegen_fuer_eike(person, "kein_profil")
            return {"ergebnis": "kein_profil", "grund": "kein_profil", "gespeichert": False, "profil": None,
                    "hinweis": (f"{HINWEIS_KOPF} Ergebnis: Eike kümmert sich darum. Sag der Person nur, dass Eike "
                                "kurz drauf schaut — nenne keinen Grund.")}
    try:
        erkannt = await garantieticket.auslesen("image/jpeg", jpeg)
    except Exception as exc:               # noqa: BLE001
        log.warning("Ticketannahme: Bild nicht lesbar (%s)", type(exc).__name__)
        ergebnis, grund, erkannt = "nachfragen", "lesefehler", {}
        if modus == "trocken":
            _probe_vermerken(person, ergebnis, grund, erkannt)
        return {"ergebnis": ergebnis, "grund": grund, "gespeichert": False, "profil": pid or None,
                "hinweis": _hinweis(ergebnis, grund, erkannt, modus)}
    b = bewerten(erkannt, vollname(profil, a.angaben(pid)), config.jetzt().date())
    ergebnis, grund = b["ergebnis"], b["grund"]
    gespeichert = False
    if modus == "an":
        if ergebnis == "angenommen" or (ergebnis == "eike_pruefen" and grund == "papier"):
            info = a.ticket_speichern("snapchat.jpg", "image/jpeg", jpeg, erkannt, pid)
            uebernahme.uebernehmen(a, info)
            gespeichert = True
        if ergebnis == "eike_pruefen":
            _anliegen_fuer_eike(person, grund)
    else:
        _probe_vermerken(person, ergebnis, grund, erkannt)
    return {"ergebnis": ergebnis, "grund": grund, "gespeichert": gespeichert, "profil": pid if person else None,
            "hinweis": _hinweis(ergebnis, grund, erkannt, modus)}
