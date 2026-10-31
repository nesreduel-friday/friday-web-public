"""Gegenlesen im Sprachkanal (P4 der Ehrlichkeit, 12.10.2026).

Spracheingabe wird verhört („Gap“ statt Kontakt M). Was bleibende Wirkung hat — Vorgaben, Arbeitsweisen, jede Nachricht an eine
Person — läuft im LIVE-Kanal deshalb nicht beim ersten Aufruf: Der Kern antwortet mit „Ich halte fest: <Wortlaut> — richtig?“
(Rückfrage, nichts passiert), und erst Eikes Ja in einer EIGENEN, späteren Äußerung führt es aus — mit genau den Angaben, die
vorgelesen wurden. Im Chat (getippt) entfällt das. Nennt ein Werkzeug eine Person, die das Register nur ähnlich kennt, kommt statt
des Treffers die Frage „Meinst du Kontakt M?“ (`personen/abgleich.py`).

Zustand: `rueckfragen` (im Sitzungszustand) merkt je Werkzeug die vorgelesenen Angaben; ein Ja VOR der Rückfrage zählt nie — der Aufrufer
verbraucht die Zustimmung, wenn eine Rückfrage entsteht (`live._werkzeuge_ausfuehren`)."""

from __future__ import annotations

import time

from kern.ehrlichkeit import wirkung as wirk
from kern.personen import abgleich

GILT_S = 600
# Wirkungswerkzeuge mit Gegenlesen im Sprachkanal. Nicht dabei: was schon ein eigenes Vorlesen mit Ja hat (WhatsApp, Termine, Medien
# löschen, Rundnachricht freigeben, Skill bestätigen, Snapchat-Anfrage).
GEGENLESEN = frozenset({"vorgabe_merken", "vorgabe_aendern", "skill_schreiben", "skill_aendern", "schicke_nachricht", "schicke_snap",
                        "nachricht_an_personen", "erinnerung", "modul_schalten"})
_EIKE = {"", "eike", "mich", "mir", "an eike", "an mich", "ich", "mir selbst", "mich selbst", "eike selbst"}
_TEXT_MAX = 220
_BEKANNT = "_bekannte_personen"                               # im Sitzungszustand `rueckfragen`: Personen, die Eike schon bejaht hat
_PERSON_MERKEN = frozenset({"schicke_nachricht", "schicke_snap", "erinnerung"})


def _kurz(text, n: int = _TEXT_MAX) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _person_feld(name: str, args: dict) -> str | None:
    """Der Name der Person, an die das Werkzeug geht — oder None, wenn es nicht an eine andere Person geht."""
    if name in ("schicke_nachricht", "schicke_snap", "modul_schalten"):
        wert = str(args.get("person") or "").strip()
    elif name == "erinnerung":
        wert = str(args.get("an") or "").strip()
    else:
        return None
    return None if " ".join(wert.casefold().split()) in _EIKE else wert


def betrifft(name: str, args: dict) -> bool:
    if name not in GEGENLESEN:
        return False
    if name in ("schicke_nachricht", "schicke_snap", "erinnerung", "modul_schalten"):
        return _person_feld(name, args or {}) is not None     # an Eike selbst: kein Gegenlesen nötig
    return True


def wortlaut(name: str, args: dict, person: str = "") -> str:
    """Was der Kern vorliest: die Worte, die gelten würden."""
    a = args or {}
    if name in ("vorgabe_merken", "vorgabe_aendern"):
        return _kurz(a.get("text"))
    if name in ("skill_schreiben", "skill_aendern"):
        return _kurz(" — ".join(str(x) for x in (a.get("zweck"), a.get("anweisung")) if x))
    if name == "schicke_nachricht":
        return _kurz(f"Nachricht an {person or a.get('person')}: „{a.get('text')}“")
    if name == "schicke_snap":
        text = f": „{a.get('text')}“" if a.get("text") else ""
        return _kurz(f"Snap an {person or a.get('person')}{text}")
    if name == "nachricht_an_personen":
        return _kurz(f"Mitteilung an {a.get('empfaenger')}: „{a.get('text')}“")
    if name == "modul_schalten":
        return _kurz(f"Bei {person or a.get('person')} „{a.get('modul')}“ auf „{a.get('wert')}“ stellen")
    if name == "erinnerung":
        return _kurz(f"Erinnerung an {person or a.get('an')} um {a.get('zeitpunkt')}: „{a.get('text') or a.get('titel')}“")
    return _kurz(a)


def _rueckfrage(name: str, quittung: str, text: str) -> wirk.Wirkung:
    return wirk.Wirkung("rueckfrage", wirk.art_von(name), quittung, text)


def pruefen(name: str, args: dict, zustimmung: bool, rueckfragen: dict, jetzt: float | None = None):
    """Nur im Sprachkanal aufrufen. Liefert `(args, None)` = ausführen (mit diesen Angaben), oder `(args, Wirkung)` = NICHT ausführen,
    die Wirkung ist die Rückfrage. Ändert `rueckfragen`."""
    args = dict(args or {})
    if not betrifft(name, args):
        return args, None
    jetzt = time.time() if jetzt is None else jetzt
    offen = rueckfragen.get(name)
    if offen and not 0 <= jetzt - offen["zeit"] <= GILT_S:
        rueckfragen.pop(name, None)
        offen = None
    if offen and zustimmung:
        rueckfragen.pop(name, None)
        bestaetigt = offen["args"].get("an" if name == "erinnerung" else "person")
        if bestaetigt and name in _PERSON_MERKEN:
            rueckfragen.setdefault(_BEKANNT, set()).add(bestaetigt)   # Eike hat diese Person schon bejaht: nicht nochmal fragen
        return dict(offen["args"]), None                      # genau das, was vorgelesen wurde
    neue_args, person = dict(args), ""
    feld = _person_feld(name, args)
    if feld is not None:
        d = abgleich.deuten(feld)
        if d["stand"] == "unsicher":
            frage = abgleich.frage(d["personen"])
            if len(d["personen"]) == 1:                        # nach Eikes Ja geht es an genau diese Person
                schluessel = "an" if name == "erinnerung" else "person"
                rueckfragen[name] = {"args": {**args, schluessel: d["personen"][0]["name"]}, "zeit": jetzt}
            else:
                rueckfragen.pop(name, None)
            return args, _rueckfrage(name, frage, (
                f"RÜCKFRAGE: {frage} Du hast „{feld}“ verstanden, das Register kennt es nur ähnlich. Frag Eike genau so und warte auf "
                "seine Antwort. Noch nichts ausgeführt — sage NICHT, dass es gesendet oder festgehalten ist."))
        if d["stand"] == "exakt":
            person = d["personen"][0]["name"]
            schluessel = "an" if name == "erinnerung" else "person"
            neue_args[schluessel] = person
            if name in _PERSON_MERKEN and person in rueckfragen.get(_BEKANNT, ()):
                return neue_args, None                         # „Jarle“ = die Person, die Eike eben schon bejaht hat
    wl = wortlaut(name, neue_args, person)
    haken = ""
    if name in ("vorgabe_merken", "vorgabe_aendern", "skill_schreiben", "skill_aendern"):
        sus = abgleich.verdaechtige(" ".join(str(neue_args.get(k) or "") for k in ("text", "zweck", "anweisung")))
        if sus:
            wort, p = sus[0]
            haken = f" Ich bin bei „{wort}“ unsicher: Meinst du {p.get('name')}?"
    frage = f"Ich halte fest: {wl} — richtig?{haken}"
    rueckfragen[name] = {"args": neue_args, "zeit": jetzt}
    kopf = wirk.TECHNIK.search(frage) is None
    text = (f"RÜCKFRAGE: Lies Eike genau diesen Satz vor und warte auf sein Ja: „{frage}“ Noch nichts ausgeführt. Rufe dieses Werkzeug "
            f"erst nach seinem Ja noch einmal auf: genau `{name}` mit denselben Angaben (nicht ein anderes Werkzeug wie skill_bestaetigen). Sage NICHT, dass es festgehalten oder gesendet ist.")
    return args, _rueckfrage(name, frage if kopf else "Ich halte etwas fest — richtig?", text)
