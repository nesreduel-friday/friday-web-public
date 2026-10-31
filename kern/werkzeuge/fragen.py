"""Werkzeuge `eike_fragen` und `frage_beantworten` (Plan 2026-10-28-fragen-an-eike).

Friday stellt Eike eine Frage mit Push, wenn die Wahl von seiner Vorliebe abhängt (Denkrunde und Chat). Eike antwortet in der App
oder im Chat. `frage_beantworten` nimmt seine Antwort aus dem Chat an. Beides nur für Eike (Personen sehen die Werkzeuge nicht),
beides nach Fremdtext gesperrt; die Denkrunde darf fragen, aber nie beantworten.
"""

from __future__ import annotations

import logging
import re

from kern.dienste import benachrichtigungen
from kern.fragen import fach

log = logging.getLogger("friday.fragen")

ERKLAERUNGEN = [
    {
        "name": "eike_fragen",
        "description": ("Stellt Eike eine Frage und schickt ihm eine Push-Nachricht. NUR wenn zwei vernünftige Wege bestehen und die Wahl "
                        "von seiner Vorliebe abhängt — nie, wenn eine Vorgabe oder das Gedächtnis es beantwortet. Bis zu 4 kurze "
                        "Optionen (je höchstens 40 Zeichen); er kann auch frei antworten. Danach tust du NICHTS, was von der Antwort "
                        "abhängt. Höchstens 3 offene Fragen, 3 neue am Tag."),
        "parameters": {"type": "OBJECT", "properties": {
            "titel": {"type": "STRING", "description": "Die Frage in einem Satz, höchstens 80 Zeichen"},
            "text": {"type": "STRING", "description": "Warum du fragst und was davon abhängt, höchstens 300 Zeichen"},
            "optionen": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "0 bis 4 kurze Antwortvorschläge"},
            "bezug": {"type": "STRING", "description": "faden:<Nummer> oder vorhaben:<Nummer>, wenn die Frage dazu gehört; sonst leer"},
        }, "required": ["titel", "text"]},
    },
    {
        "name": "frage_beantworten",
        "description": ("Hält Eikes Antwort auf eine offene Frage fest (Nummer aus dem [FRAGEN]-Block). Genau eins von beidem: option "
                        "(1 = erste Option) oder text (seine eigene Antwort, in seinen Worten). Nur wenn Eike in diesem Zug selbst "
                        "geantwortet hat, z. B. „nimm Option 2“."),
        "parameters": {"type": "OBJECT", "properties": {
            "id": {"type": "INTEGER", "description": "Die Nummer der Frage"},
            "option": {"type": "INTEGER", "description": "Welche Option, 1-basiert"},
            "text": {"type": "STRING", "description": "Seine eigene Antwort"},
        }, "required": ["id"]},
    },
]


_ORDINAL = {1: "erste", 2: "zweite", 3: "dritte", 4: "vierte"}


def _eigener_bezug(bezug: str) -> bool:
    """In der Denkrunde nur zu einem eigenen, nicht aus Fremdtext stammenden Faden oder Vorhaben: Der Überblick enthält Gesprächs-
    zusammenfassungen, in denen Mailinhalt stecken kann — ohne diesen Bezug könnte so ein Text einen Push mit Antwortkanal auslösen."""
    art, _, nr = (bezug or "").strip().partition(":")
    if not nr.isdigit():
        return False
    if art == "faden":
        from kern.denken import buch
        f = buch.standard().faden(int(nr))
        return bool(f) and not f["fremd"]
    if art == "vorhaben":
        from kern.werkbank import speicher
        v = speicher.standard().eintrag(int(nr))
        return bool(v) and not v["fremd"]
    return False


def _in_eikes_worten(eingabe: str, option, text: str, optionen: list[str]) -> bool:
    """Wortlaut und Wahl stehen in Eikes Äußerung, nicht im Kopf des Modells."""
    e = " ".join(str(eingabe or "").lower().split())
    if option is not None:
        try:
            nr = int(option)
        except (TypeError, ValueError):
            return False
        if not 1 <= nr <= len(optionen):
            return True                               # die Zahl prüft das Fach mit klarer Absage
        wort = _ORDINAL.get(nr, "")
        return bool(re.search(rf"(?<!\d){nr}(?!\d)", e)) or optionen[nr - 1].lower() in e or (bool(wort) and wort in e)
    return " ".join(str(text or "").lower().split()) in e


def eike_fragen(titel: str, text: str, optionen: list | None = None, bezug: str = "", runde: int | None = None,
                fremde_inhalte: bool = False) -> str:
    # Die Quelle bestimmt der Kern über die Runde, nie das Modell.
    quelle = "denkrunde" if runde is not None else "chat"
    if runde is not None and not _eigener_bezug(bezug):
        return ("NICHTS gefragt. In der Denkrunde nur mit bezug auf einen eigenen Faden oder ein eigenes Vorhaben "
                "(faden:<Nummer> oder vorhaben:<Nummer>), das nicht aus gelesenem Fremdtext stammt.")
    if not isinstance(optionen, list):
        optionen = [] if optionen in (None, "") else [str(optionen)]
    r = fach.standard().stellen(quelle, titel, text, [str(o) for o in optionen], bezug, fremd=bool(fremde_inhalte))
    if not r["ok"]:
        return f"NICHTS gefragt. {r['grund']}"
    f = fach.standard().eintrag(r["id"])
    try:
        benachrichtigungen.senden("fragen", f["titel"], " · ".join(f["optionen"]) or "Tippe zum Antworten.",
                                  url="/#gedaechtnis/gedanken", tag=f"frage-{r['id']}")
    except Exception:                                  # noqa: BLE001
        log.exception("Frage %s: Push fehlgeschlagen", r["id"])
    return (f"Gestellt (Nummer {r['id']}). Eike bekommt eine Push-Nachricht. Tu bis zu seiner Antwort nichts, "
            "was davon abhängt.")


def frage_beantworten(id, option=None, text: str = "", eingabe: str = "") -> str:     # noqa: A002 (Name kommt vom Modell)
    if not str(eingabe or "").strip():
        return "NICHTS festgehalten. Eike hat in diesem Zug nichts geantwortet."
    try:
        fid = int(id)
    except (TypeError, ValueError):
        return "NICHTS festgehalten. Welche Nummer hat die Frage?"
    if option in ("", 0):
        option = None
    frage = fach.standard().eintrag(fid)
    if frage and not _in_eikes_worten(eingabe, option, text, frage["optionen"]):
        return ("NICHTS festgehalten. Das steht so nicht in Eikes Äußerung. Bitte ihn, die Option (Nummer oder Wortlaut) zu nennen "
                "oder die Antwort selbst zu schreiben.")
    r = fach.standard().beantworten(fid, option=option, text=text)
    if not r["ok"]:
        return f"NICHTS festgehalten. {r['grund']}"
    return f"Festgehalten: „{r['antwort']}“ ({'Option' if r['art'] == 'option' else 'eigene Antwort'})."


eike_fragen.kennt_runde = True
eike_fragen.kennt_fremde_inhalte = True
frage_beantworten.kennt_eingabe = True

# Ändern etwas und verlangen kein „ja“: nach Fremdtext im selben Zug gesperrt (katalog.ausfuehren).
NICHT_NACH_FREMDTEXT = {"eike_fragen", "frage_beantworten"}

WERKZEUGE = {"eike_fragen": eike_fragen, "frage_beantworten": frage_beantworten}
