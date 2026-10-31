"""Ticket-Werkzeuge für Personen (14.10.2026, Spec 2026-10-14-garantie-annahme-design.md, Abschnitt 4).

`ticket_lesen` liest das Bild, das die Person in DIESEM Zug geschickt hat (`zug.bild()` — nie ein Argument des Modells), und merkt das
Ergebnis nur VORLÄUFIG (30 Minuten, im Speicher). `ticket_bestaetigen` speichert es erst, wenn die Person die Nummern bestätigt oder
korrigiert hat; ihre Bestätigung ersetzt Eikes „Nummern verglichen“. Lesewerte aus dem Bild sind Fremdtext: Zeichensatz-begrenzt,
nie Freitext (Bezeichnung, Zweifel, Inhabername) an das Modell."""

from __future__ import annotations

import base64
import binascii
import logging
import re
import threading
import time

from kern import config
from kern.ehrlichkeit.wirkung import wirkt
from kern.garantie import ablage, annahme, uebernahme
from kern.garantie import ticket as garantieticket
from kern.personen import register
from kern.skills import primitive, zug as skillzug, zugriff

log = logging.getLogger("friday.ticket_annahme")

KONNEKTOR = "uestra_garantie"        # ohne Freigabe der Garantie gibt es die Werkzeuge nicht (Default-Deny)
GUELTIG_S = 30 * 60
BILD_MAX = 8 * 1024 * 1024
TYPEN = {"image/jpeg", "image/png", "image/webp"}
_VORLAEUFIG: dict[str, dict] = {}
_sperre = threading.Lock()
NICHT_ERLAUBT = "Das ist hier nicht erlaubt. Sag schlicht in einem Satz, dass du dabei nicht helfen kannst."
KEIN_PROFIL = "Dein Ticket kann ich noch nicht aufnehmen — Eike kümmert sich darum. Melde dich danach noch einmal."

ERKLAERUNGEN = [
    {"name": "ticket_lesen",
     "description": ("Liest das Ticketfoto, das die Person in DIESER Nachricht geschickt hat, und nennt dir die gelesenen Nummern zum "
                     "Vorlesen. Speichert nichts — erst ticket_bestaetigen nach der Bestätigung der Person."),
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "ticket_bestaetigen",
     "description": ("Speichert das gelesene Ticket, nachdem die Person die Nummern bestätigt oder die richtigen genannt hat. Gib die "
                     "Nummern GENAU so an, wie die Person sie bestätigt oder korrigiert hat."),
     "parameters": {"type": "OBJECT", "properties": {
         "ticketnummer": {"type": "STRING", "description": "Ticketnummer, wie von der Person bestätigt"},
         "kundenkarte": {"type": "STRING", "description": "Kundenkartennummer (6 Ziffern), wie von der Person bestätigt"},
         "abo_nummer": {"type": "STRING", "description": "optional: Abo-Nummer, falls auf dem Ticket"}}}},
]


def _registrieren() -> None:
    primitive.registrieren(
        "ticket_lesen", "Liest das Ticketfoto der Person aus dieser Nachricht (speichert nichts)",
        gates=["nur das Bild dieses Zuges", "nur Bildtypen jpeg/png/webp bis 8 MB", "nur der Standard-Ablauf der Garantie",
               "Lesewerte nur als Ziffern und Buchstaben"])
    primitive.registrieren(
        "ticket_bestaetigen", "Speichert das gelesene Ticket nach der Bestätigung der Person",
        gates=["nur nach ticket_lesen derselben Person (30 Minuten)", "Formate geprüft", "Name, Gültigkeit und Herausgeber geprüft",
               "abweichende Kundenkarte wird nie überschrieben"])


_registrieren()


def _eike_anliegen(person: dict, grund: str) -> None:
    annahme._anliegen_fuer_eike(person, grund)


def _zeichen(w, n: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9 \-./]", "", str(w or ""))[:n].strip()


def _tag(iso: str) -> str:
    return iso[8:10] + "." + iso[5:7] + "." + iso[:4] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso or "") else "?"


def _person_und_profil(kontakt_id: str, werkzeug: str):
    """(Person, Profil-Id, Profil) — alles None: nicht erlaubt; Profil None: Person ok, aber kein Garantie-Profil."""
    if not isinstance(kontakt_id, str) or not kontakt_id or not zugriff.erlaubt(kontakt_id, werkzeug):
        return None, None, None
    person = register.holen(kontakt_id)
    if not person or person.get("pausiert"):
        return None, None, None
    pid = (person.get("quellen") or {}).get("garantie")
    profil = ablage.standard().profil(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
    if not profil or profil.get("archiviert"):
        _eike_anliegen(person, "kein_profil")
        return person, None, None
    return person, pid, profil


@wirkt("akte", ok=lambda t: t.startswith("Ticket gelesen"), detail=lambda a, t: "Ticketfoto gelesen")
async def ticket_lesen(kontakt_id: str = "") -> str:
    person, pid, profil = _person_und_profil(kontakt_id, "ticket_lesen")
    if person is None:
        return NICHT_ERLAUBT
    if pid is None:
        return KEIN_PROFIL
    bild = skillzug.bild()
    if not bild:
        return "In dieser Nachricht ist kein Bild. Bitte die Person, ein Foto ihres Tickets zu schicken."
    if bild["mime"] not in TYPEN:
        return "Das ist kein Foto (jpeg, png oder webp). Bitte die Person, ihr Ticket als Foto zu schicken."
    try:
        daten = base64.b64decode(bild["data"], validate=False)
    except (binascii.Error, ValueError):
        return "Das Bild ist nicht angekommen. Bitte die Person, es noch einmal zu schicken."
    if len(daten) > BILD_MAX:
        return "Das Bild ist zu groß. Bitte die Person, es kleiner oder als Screenshot noch einmal zu schicken."
    try:
        erkannt = await garantieticket.auslesen(bild["mime"], daten)
    except Exception as exc:               # noqa: BLE001
        log.warning("Ticket nicht lesbar (%s)", type(exc).__name__)
        return "Das Bild konnte gerade nicht gelesen werden. Bitte die Person, es in ein paar Minuten noch einmal zu schicken."
    b = annahme.bewerten(erkannt, annahme.vollname(profil, ablage.standard().angaben(pid)), config.jetzt().date())
    ergebnis, grund = b["ergebnis"], b["grund"]
    # `bewerten` prüft Papier vor dem Herausgeber: Ein Papier-Deutschlandticket eines fremden Herausgebers darf nicht durch die
    # Bestätigung der Person laufen — da schaut Eike selbst hin.
    if ergebnis == "eike_pruefen" and grund == "papier" and erkannt.get("art") == "Deutschlandticket" \
            and garantieticket.ist_uestra(erkannt.get("herausgeber") or "") is False:
        grund = "herausgeber"
    if ergebnis == "nachfragen":
        return ("Das Ticket konnte nicht übernommen werden: " + annahme.GRUND_TEXT.get(grund, "unklar")
                + ". Bitte die Person um ein neues, vollständiges Foto ihres aktuellen Tickets.")
    if ergebnis == "eike_pruefen" and grund != "papier":
        _eike_anliegen(person, grund)
        return "Eike schaut kurz selbst auf das Ticket. Sag der Person nur das — nenne keinen Grund."
    with _sperre:
        _VORLAEUFIG[kontakt_id] = {"erkannt": erkannt, "mime": bild["mime"], "daten": daten, "zeit": time.time(),
                                   "zug": skillzug.zug_id()}
    zeilen = [f"Ticketnummer {_zeichen(erkannt.get('ticketnummer')) or 'nicht lesbar'}",
              f"Kundenkartennummer {_zeichen(erkannt.get('kundenkarte')) or 'nicht lesbar'}"]
    if erkannt.get("abo_nummer"):
        zeilen.append(f"Abo-Nummer {_zeichen(erkannt.get('abo_nummer'))}")
    zeilen.append(f"gültig {_tag(erkannt.get('gueltig_von', ''))} bis {_tag(erkannt.get('gueltig_bis', ''))}")
    return ("Ticket gelesen (noch nichts gespeichert). Lies der Person diese Werte genau vor und frag, ob sie stimmen: "
            + "; ".join(zeilen) + ". Bestätigt sie oder nennt die richtigen Nummern, ruf ticket_bestaetigen auf.")


def _holen(kontakt_id: str) -> dict | None:
    with _sperre:
        v = _VORLAEUFIG.get(kontakt_id)
        if v and time.time() - v["zeit"] > GUELTIG_S:
            _VORLAEUFIG.pop(kontakt_id, None)
            return None
        return dict(v) if v else None


@wirkt("akte", ok=lambda t: t.startswith("Gespeichert"), detail=lambda a, t: "Ticket gespeichert")
def ticket_bestaetigen(kontakt_id: str = "", ticketnummer: str = "", kundenkarte: str = "", abo_nummer: str = "") -> str:
    person, pid, profil = _person_und_profil(kontakt_id, "ticket_bestaetigen")
    if person is None:
        return NICHT_ERLAUBT
    if pid is None:
        return KEIN_PROFIL
    v = _holen(kontakt_id)
    if v is None:
        return "Dazu liegt gerade kein gelesenes Ticket vor. Lies es zuerst mit ticket_lesen (die Person schickt dafür ein Foto)."
    if not skillzug.zug_id() or v.get("zug") == skillzug.zug_id():
        return ("Die Nummern sind gerade erst vorgelesen worden. Warte auf die Antwort der Person (ihre Bestätigung oder die "
                "richtigen Nummern), bevor du das Ticket speicherst.")
    kk = re.sub(r"\s", "", str(kundenkarte or ""))
    tn = garantieticket.nummer(ticketnummer)
    abo = re.sub(r"\s", "", str(abo_nummer or ""))
    if kk and not garantieticket.KUNDENKARTE.match(kk):
        return "Die Kundenkartennummer hat genau 6 Ziffern. Frag die Person noch einmal danach."
    if tn and not garantieticket.TICKETNUMMER.match(tn):
        return "Die Ticketnummer darf nur Buchstaben und Ziffern enthalten. Frag die Person noch einmal danach."
    if abo and not re.fullmatch(r"\d{4,12}", abo):
        return "Die Abo-Nummer besteht aus 4 bis 12 Ziffern. Frag die Person noch einmal danach."
    erkannt = dict(v["erkannt"])
    # Was das Formular verlangt (Jugend-/Ausbildungsnetzkarte), muss die Person SELBST nennen oder bestätigen — nie stillschweigend das Gelesene.
    sorte = garantieticket.sorte(erkannt)
    gegeben = {"kundenkarte": kk, "ticketnummer": tn}
    for f in (sorte["braucht"] if sorte else ()):
        if not gegeben[f]:
            return f"Es fehlt noch: {garantieticket.FELDNAMEN[f]}. Frag die Person danach."
    neu = {"kundenkarte": kk or erkannt.get("kundenkarte") or "", "ticketnummer": tn or erkannt.get("ticketnummer") or "",
           "abo_nummer": abo or erkannt.get("abo_nummer") or ""}
    korrigiert = (neu["kundenkarte"], neu["ticketnummer"], neu["abo_nummer"]) != (
        erkannt.get("kundenkarte") or "", erkannt.get("ticketnummer") or "", erkannt.get("abo_nummer") or "")
    erkannt.update(neu)
    a = ablage.standard()
    info = a.ticket_speichern("snapchat.jpg", v["mime"], v["daten"], erkannt, pid)
    gespeichert_kk = a.angaben(pid).get("kundenkarte") or ""
    uebernahme.uebernehmen(a, info)
    a.ticket_merken(info["id"], nummern_geprueft=True, pruefung_quelle="person", person_korrigiert=bool(korrigiert),
                    pruefung_zeit=config.jetzt().isoformat(timespec="seconds"))
    with _sperre:
        _VORLAEUFIG.pop(kontakt_id, None)
    if gespeichert_kk and neu["kundenkarte"] and gespeichert_kk != neu["kundenkarte"]:
        _eike_anliegen(person, "kundenkarte_weicht_ab")
        return ("Gespeichert: Das Ticket ist angekommen. Die Kundenkartennummer weicht von der bei Eike ab — Eike schaut sie kurz "
                "an, bevor der Antrag rausgeht. Sag der Person nur das.")
    return "Gespeichert: Das Ticket ist angekommen und die Nummern sind bestätigt. Mach mit der Zusammenfassung weiter."


ticket_lesen.kennt_kontakt = True
ticket_bestaetigen.kennt_kontakt = True
WERKZEUGE = {"ticket_lesen": ticket_lesen, "ticket_bestaetigen": ticket_bestaetigen}
