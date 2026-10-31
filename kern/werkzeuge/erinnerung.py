"""Erinnerungs-Werkzeuge — Friday meldet sich später von selbst.

Bewusst OHNE Rückfrage, anders als beim Kalender: eine Erinnerung ist
harmlos, gilt nur Eike selbst und lässt sich absagen. Nur aus fremdem Text
(Mail, Elternbrief, Webseite) wird nichts angelegt — sonst könnte eine
Webseite Benachrichtigungen auslösen.

Zeitpunkt, Überschrift und Text wählt Friday selbst; die Regeln dafür stehen
in der Persona. Hier wird nur geprüft, dass der Zeitpunkt brauchbar ist.
„Erinnere mich, wenn ich nach Hause komme“ legt dagegen eine Erinnerung mit
`ausloeser=ankunft` an — die misst den Standort (siehe `erinnerungen.py`).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, time, timedelta

from kern import config, erinnerungen
from kern.ehrlichkeit.wirkung import wirkt
from kern.gehirn.persona import WOCHENTAGE

MAX_VORAUS_TAGE = 730

ERKLAERUNGEN = [
    {
        "name": "erinnerung",
        "description": ("Legt eine Erinnerung an, die Eike zur genannten Zeit — oder sobald er zu Hause ankommt — als "
                        "Benachrichtigung aufs Handy bekommt. Benutzen, wenn er sagt 'erinner mich an ...' oder 'erinnere "
                        "mich, wenn ich nach Hause komme' (dann ausloeser=ankunft, ohne Zeitpunkt). Auch per Snapchat "
                        "(kanal=snapchat) und für andere (an=Name; dann Snapchat oder gespraech): der Text ist FEST und "
                        "wird so verschickt, schreib ihn also fertig. Zeitpunkt, Überschrift und Text wählst DU sinnvoll: "
                        "der Zeitpunkt so, dass er noch handeln kann (etwas mitnehmen: am Abend davor), die Überschrift "
                        "kurz, der Text konkret und in der Du-Form."),
        "parameters": {"type": "OBJECT", "properties": {
            "titel": {"type": "STRING", "description": "Kurze Überschrift, z.B. 'Wasserpest für Bio'"},
            "text": {"type": "STRING", "description": "Was zu tun ist, z.B. 'Denk dran, die Wasserpest einzupacken.'"},
            "ausloeser": {"type": "STRING", "description": "zeit (Standard: fester Zeitpunkt) oder ankunft (sobald Eike zu "
                                                           "Hause ankommt; dann braucht es keinen Zeitpunkt)"},
            "zeitpunkt": {"type": "STRING", "description": "Wann, als 2026-09-15T19:00 (Datum UND Uhrzeit, deutsche Zeit); "
                                                           "Pflicht bei ausloeser=zeit, bei ankunft leer lassen"},
            "kanal": {"type": "STRING", "description": "push (Standard, Benachrichtigung), snapchat (Snapchat-Nachricht), "
                                                       "beides (Push und Snapchat, nur für Eike) oder gespraech (beim nächsten Gespräch erwähnen)"},
            "an": {"type": "STRING", "description": "Name einer Person aus den Kontakten; leer = Eike selbst (nur bei zeit)"},
        }, "required": ["titel", "text"]},
    },
    {
        "name": "erinnerungen",
        "description": "Zeigt die noch offenen Erinnerungen mit Nummer und Zeitpunkt.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "erinnerung_absagen",
        "description": "Sagt eine offene Erinnerung ab. Die Nummer kommt aus erinnerungen.",
        "parameters": {"type": "OBJECT", "properties": {
            "nummer": {"type": "INTEGER", "description": "Die Nummer der Erinnerung"},
        }, "required": ["nummer"]},
    },
]


def _wann(text: str) -> datetime | None:
    """Nur mit Datum UND Uhrzeit — bei einer Erinnerung wäre Raten ärgerlich."""
    roh = (text or "").strip().replace(" ", "T", 1)
    try:
        wann = datetime.fromisoformat(roh)
    except ValueError:
        return None
    if wann.time() == time.min and "T00:00" not in roh:
        return None
    return wann if wann.tzinfo else wann.replace(tzinfo=config.jetzt().tzinfo)


def _lesbar(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso).astimezone(config.jetzt().tzinfo)
    except (TypeError, ValueError):
        return "?"
    return f"{WOCHENTAGE[t.weekday()]}, {t:%d.%m.} um {t:%H:%M}"


def _wann_text(e: dict) -> str:
    """Zeitpunkt oder Ankunft — so, wie die Erinnerung ausgelöst wird."""
    if e.get("ausloeser") == "ankunft":
        return "bei Ankunft zu Hause"
    return _lesbar(e["faellig"])


@wirkt("erinnerung", ok=lambda t: re.match(r"Erinnerung \d+ steht", t) is not None, detail=lambda a, t: a.get("titel"))
def erinnerung(titel: str, text: str, zeitpunkt: str = "", kanal: str = "push", an: str = "",
               ausloeser: str = "", fremde_inhalte: bool = False) -> str:
    if fremde_inhalte:
        return ("Der Wunsch stammt aus fremdem Text (Mail, Brief oder Webseite). Daraus lege ich keine "
                "Erinnerung an — sag es mir selbst.")
    titel = (titel or "").strip()
    if not titel:
        return "Wie soll die Erinnerung heißen?"
    kanal = (kanal or "push").strip().lower().replace("ä", "ae")
    if kanal not in erinnerungen.KANAELE:
        return "Der Kanal muss push, snapchat, beides oder gespräch sein."
    ausloeser = (ausloeser or "zeit").strip().lower().replace("ä", "ae")
    if ausloeser not in erinnerungen.AUSLOESER:
        return "Der Auslöser muss zeit (feste Uhrzeit) oder ankunft (zu Hause) sein."
    jetzt = config.jetzt()
    if ausloeser == "ankunft":
        # „Erinnere mich, wenn ich nach Hause komme“ — nur für Eike selbst, ohne Zeitpunkt.
        if (an or "").strip():
            return "Die Ankunft messe ich nur für dich selbst — an andere erinnere ich zu einer festen Zeit."
        if not os.getenv("FRIDAY_HEIMADRESSE", "").strip():
            return ("Für „wenn ich nach Hause komme“ brauche ich deine Heimadresse in FRIDAY_HEIMADRESSE — "
                    "sonst weiß ich nicht, wo „zu Hause“ ist.")
        from kern import konnektoren
        from kern.dienste import homeassistant
        if not (konnektoren.benutzbar("standort") or konnektoren.benutzbar("personenstandort")
                or konnektoren.benutzbar(homeassistant.KONNEKTOR)):
            return ("Dafür brauche ich auf der Freigaben-Seite „Standort“, „Standort der Familie“ oder "
                    "„Home Assistant“ — sonst merke ich nicht, wann du ankommst.")
        wann = jetzt                                   # nur der Anlagezeitpunkt; ausgelöst wird über die Position
    else:
        wann = _wann(zeitpunkt)
        if wann is None:
            return "Dafür brauche ich Datum UND Uhrzeit, z.B. 2026-09-15T19:00."
        if wann < jetzt:
            return f"Der {wann:%d.%m.%Y %H:%M} ist schon vorbei. Wann genau soll ich erinnern?"
        if wann > jetzt + timedelta(days=MAX_VORAUS_TAGE):
            return "Das liegt mehr als zwei Jahre voraus — bitte prüf das Datum."

    empfaenger, name, nachtsatz = None, "Eike", ""
    if (an or "").strip():
        from kern.werkzeuge import snapchat as snapwerkzeug
        profil, verstanden = snapwerkzeug._deuten(an)
        if profil is None:
            return verstanden
        if verstanden:
            return (f"{verstanden} Frag Eike kurz, ob er {profil['name']} meint — nach seinem Ja rufst du mich mit genau diesem "
                    "Namen noch einmal auf. Noch nichts angelegt.")
        if kanal in ("push", "beides"):
            return "An andere schicke ich keine Pushes — nimm snapchat oder gespräch."
        if profil.get("pausiert"):
            return f"{profil['name']} ist pausiert — dafür lege ich nichts an."
        if kanal == "snapchat" and not ((profil.get("kanaele") or {}).get("snapchat") or "").strip():
            return f"{profil['name']} hat keinen Snapchat-Namen — dafür lege ich nichts an."
        if kanal == "gespraech" and not profil.get("proaktiv"):
            return f"Bei {profil['name']} ist Proaktiv aus — sie würde es nie von selbst erwähnen. Schalte es bei den Kontakten ein."
        empfaenger, name = profil["id"], profil["name"]
    if kanal in ("snapchat", "beides"):
        from kern import konnektoren
        if not (konnektoren.benutzbar("snapchat") and konnektoren.schreiben_erlaubt("snapchat")):
            return "Dafür brauche ich das Schreibrecht für Snapchat — es ist auf der Freigaben-Seite noch aus."
    sauber = " ".join(str(text or "").split()) or titel
    if kanal != "push":
        from kern.denken.buch import zu_lang
        from kern.werkzeuge import sendepruefung
        if absage := zu_lang(sauber, erinnerungen.SNAP_TEXT_MAX):
            return absage
        if grund := sendepruefung.heikel(sauber):
            return grund
    ablage = erinnerungen.standard()
    if empfaenger:
        if ablage.offene_an(empfaenger) >= erinnerungen.MAX_OFFEN_JE_PERSON:
            return f"Für {name} sind schon {erinnerungen.MAX_OFFEN_JE_PERSON} Erinnerungen offen — das reicht."
        if ablage.offene_an_personen() >= erinnerungen.MAX_OFFEN_PERSONEN:
            return f"An andere sind schon {erinnerungen.MAX_OFFEN_PERSONEN} Erinnerungen offen — das reicht."
        wann, verschoben = erinnerungen.person_zeit(wann)
        if verschoben:
            nachtsatz = f" Nachts schreibe ich anderen nicht, deshalb erst am {wann:%d.%m.} um 07:00."
    from kern.dienste import push
    eid = ablage.anlegen(titel, sauber if sauber != titel else (text or "").strip(), wann, kanal=kanal,
                         empfaenger=empfaenger, ausloeser=ausloeser)
    wohin = {"push": "als Benachrichtigung", "snapchat": "per Snapchat", "beides": "per Push und Snapchat",
             "gespraech": "beim nächsten Gespräch"}[kanal]
    wenn = ("sobald du zu Hause ankommst" if ausloeser == "ankunft"
            else f"{WOCHENTAGE[wann.weekday()]}, {wann:%d.%m.} um {wann:%H:%M}")
    antwort = f"Erinnerung {eid} steht: {wenn} — {titel}, an {name}, {wohin}.{nachtsatz}"
    if kanal in ("push", "beides") and not push.geraete():
        antwort += (" Achtung: es ist noch kein Gerät für Benachrichtigungen angemeldet. "
                    "Schalte sie in den Einstellungen ein, sonst kommt nichts an.")
    return antwort


def _ziel(e: dict) -> str:
    """Kanal und Empfänger für die Anzeige; reine Push-Erinnerungen an Eike erscheinen wie bisher ohne Zusatz."""
    if e.get("kanal", "push") == "push" and not e.get("empfaenger"):
        return ""
    kanal = {"snapchat": "Snapchat", "beides": "Push+Snapchat", "gespraech": "im Gespräch"}.get(e.get("kanal"), "Push")
    an = ""
    if e.get("empfaenger"):
        from kern.personen import register
        p = register.holen(e["empfaenger"])
        an = f" an {p['name'] if p else e['empfaenger']}"
    return f" [{kanal}{an}]"


def erinnerungen_zeigen() -> str:
    offen = erinnerungen.standard().offene()
    if not offen:
        return "Es stehen keine Erinnerungen an."
    return "\n".join(f"{e['id']}: {_wann_text(e)} — {e['titel']}"
                     + (f" ({e['text']})" if e["text"] else "") + _ziel(e) for e in offen)


@wirkt("erinnerung", ok=("Erinnerung ",), kopf_ok="Abgesagt", detail=lambda a, t: f"Erinnerung {a.get('nummer')}")
def erinnerung_absagen(nummer: int) -> str:
    if erinnerungen.standard().absagen(int(nummer)):
        return f"Erinnerung {nummer} ist abgesagt."
    return f"Eine offene Erinnerung mit der Nummer {nummer} finde ich nicht."


erinnerung.kennt_fremde_inhalte = True

# Ändert etwas und verlangt kein „ja“: nach fremdem Text im selben Zug gesperrt (katalog.ausfuehren).
NICHT_NACH_FREMDTEXT = {"erinnerung_absagen"}

WERKZEUGE = {"erinnerung": erinnerung, "erinnerungen": erinnerungen_zeigen,
             "erinnerung_absagen": erinnerung_absagen}
