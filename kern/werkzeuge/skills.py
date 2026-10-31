"""Werkzeuge für Skills — nur Eike (12.10.2026). Schreiben, Ändern, Bestätigen, Widerrufen nie nach Fremdtext, nie für Fremde, nie in
der Denkrunde (`skills_lesen` und `skill_testlauf` ändern nichts und dürfen dorthin).

Ein Skill, der Personen betrifft, bleibt ein ENTWURF, bis Eike es in einer eigenen Nachricht bestätigt (`skill_bestaetigen`, Zustimmung
kommt vom Kern, nie vom Modell). Skills nur für eike/system sind sofort aktiv. Jeder aktive Skill steht zusätzlich als Vorgabe (Quelle
`skill`) in Eikes Liste; sie lässt sich nur über den Skill ändern/widerrufen — beides bleibt konsistent."""

from __future__ import annotations

from kern.personen import register
from kern.skills import fach, primitive, zugriff
from kern.vorgaben import fach as vorgaben_fach, riegel
from kern.ehrlichkeit.wirkung import wirkt

NICHTS = ("NICHTS gespeichert. {grund} Versuche es korrigiert noch einmal; klappt es nicht, sag Eike ehrlich, dass es noch nicht "
          "geklappt hat und was du brauchst. Behaupte nie, du hättest es festgehalten.")
GRUPPEN_HINWEIS = "Für „alle, die Anträge haben“ gilt gruppe:garantie_beteiligte."

_FELDER = {
    "name": {"type": "STRING", "description": "Kurzer Name, z.B. verspaetung_entgegennehmen"},
    "zweck": {"type": "STRING", "description": "Wozu der Skill da ist, ein Satz"},
    "ausloeser": {"type": "STRING", "description": "Wann er gilt (woran die andere Instanz ihn erkennt)"},
    "zielgruppe": {"type": "ARRAY", "items": {"type": "STRING"},
                   "description": "Wer ihn befolgt: Namen von Personen aus Eikes Kontakten, 'eike', 'system' oder 'gruppe:<name>'"},
    "anweisung": {"type": "STRING", "description": ("Der Ablauf in freiem Text: was die Instanz tut, welche Fragen sie stellt, "
                                                     "wann sie fertig ist, was dann passiert. Höchstens 2500 Zeichen")},
    "werkzeuge": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Personenfähige Werkzeuge (siehe skills_lesen)"},
    "optionen": {"type": "OBJECT", "description": "Einstellungen der Werkzeuge, z.B. einreichen: mit_freigabe"},
    "felder": {"type": "ARRAY", "description": ("Akte: was die Instanz mit akte_erfassen festhalten soll. Je Feld name (klein, a-z0-9_), "
                                                "typ (text|datum|zahl|auswahl), pflicht (wahr/falsch), bei auswahl die Liste werte, "
                                                "optional hinweis (wie danach zu fragen ist)"),
               "items": {"type": "OBJECT", "properties": {
                   "name": {"type": "STRING"}, "typ": {"type": "STRING"}, "pflicht": {"type": "BOOLEAN"},
                   "werte": {"type": "ARRAY", "items": {"type": "STRING"}}, "hinweis": {"type": "STRING"}}, "required": ["name", "typ"]}},
}
ERKLAERUNGEN = [
    {"name": "skill_schreiben",
     "description": ("Schreibt eine neue Arbeitsweise (Skill) für eine Person, Hintergrund- oder dich selbst: Zweck, Auslöser, "
                     "Zielgruppe, Ablauf, Werkzeuge. Nutze das, wenn Eike eine neue Arbeitsweise, Rolle oder Berechtigung "
                     "beschreibt ('Kontakt M darf mir Verspätungen melden …'). Betrifft er Personen, wirkt er erst nach Eikes Ja: sag ihm "
                     "in einem Satz, was du tun wirst, und frag."),
     "parameters": {"type": "OBJECT", "properties": _FELDER, "required": ["name", "zweck", "ausloeser", "zielgruppe", "anweisung"]}},
    {"name": "skill_aendern",
     "description": "Schreibt einen Skill neu (neue Version; die alte bleibt im Verlauf). Nur die genannten Felder ändern sich.",
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "STRING", "description": "Nummer oder Name des Skills"},
                                                      **{k: v for k, v in _FELDER.items() if k != "name"}}, "required": ["id"]}},
    {"name": "skill_bestaetigen",
     "description": ("Setzt eine noch unbestätigte Aufgabe in Kraft — nur nachdem Eike gehört hat, was du tun wirst, und in seiner "
                     "Nachricht ausdrücklich Ja gesagt hat."),
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "STRING", "description": "Nummer oder Name des Entwurfs"}},
                    "required": ["id"]}},
    {"name": "skill_widerrufen",
     "description": "Beendet einen Skill (aktiv oder Entwurf); er wirkt sofort nicht mehr. Der Verlauf bleibt.",
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "STRING", "description": "Nummer oder Name"}}, "required": ["id"]}},
    {"name": "skills_lesen",
     "description": "Zeigt die aktiven Skills und offenen Entwürfe sowie die personenfähigen Werkzeuge mit ihren Einstellungen.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "skill_testlauf",
     "description": ("Trockenlauf: zeigt, welchen Prompt-Block und welche Werkzeuge eine Person von diesem Skill bekäme. Sendet nichts, "
                     "ändert nichts."),
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "STRING", "description": "Nummer oder Name"},
                                                      "person": {"type": "STRING", "description": "Name der Zielperson"}},
                    "required": ["id", "person"]}},
]


def _person(text) -> tuple[dict | None, str]:
    """Registereintrag zu einem Namen; unbekannt oder mehrdeutig -> Rückfrage."""
    s = register.schluessel(str(text or ""))
    if not s:
        return None, "Welche Person ist gemeint?"
    alle = register.liste()
    gleich = [p for p in alle if s in (register.schluessel(p["name"]), register.schluessel(p.get("graph_name", "")), p["id"])]
    t = gleich or [p for p in alle if s in register.schluessel(p["name"]) or s in register.schluessel(p.get("graph_name", ""))]
    if not t:
        return None, f"Eine Person „{text}“ kenne ich nicht. Frag Eike kurz, wen er meint."
    if len(t) > 1:
        return None, "Das passt auf mehrere: " + ", ".join(p["name"] for p in t) + ". Frag Eike kurz, wen er meint."
    return t[0], ""


def _ziele(liste) -> tuple[list[str] | None, str]:
    out = []
    for z in (liste if isinstance(liste, (list, tuple)) else [liste]):
        t = str(z or "").strip()
        if t.lower().startswith("gruppe:") and t.lower()[7:] not in fach.GRUPPEN:
            return None, (f"Die Zielgruppe „{t}“ gibt es nicht. Gültig: " + ", ".join(f"gruppe:{g}" for g in fach.GRUPPEN)
                          + ", eike, system oder der Name einer Person. " + GRUPPEN_HINWEIS)
        if t.lower() in ("eike", "system") or t.lower().startswith("gruppe:"):
            out.append(t.lower())
            continue
        p, frage = _person(t)
        if p is None:
            return None, frage
        out.append(p["id"])
    return out, ""


def _zusammenfassung(s: dict) -> str:
    ziel = zugriff._ziel_text(s)
    teile = [f"„{s['name']}“ ({'läuft' if s['status'] == 'aktiv' else 'noch nicht bestätigt'}; Kennung {s['id']}, intern, nicht nennen): {s['zweck']}",
             f"Gilt für: {ziel}. Wenn: {s['ausloeser']}",
             f"So läuft es: {s['anweisung']}",
             f"Dafür darf sie nutzen: {', '.join(s['werkzeuge']) or 'nichts Besonderes'}" + (f"; Einstellungen: {zugriff._opt(s)}" if s["optionen"] else "")]
    if s.get("felder"):
        teile.append("Das fragt sie ab: " + zugriff.felder_text(s["felder"]))
    return "\n".join(teile)


def _vorgabe_text(s: dict) -> str:
    return f"{s['name']} v{s['version']} (für {zugriff._ziel_text(s)}): {s['zweck']}"[:400]   # Version: sonst wäre v2 eine Dublette von v1")


def _aktivieren(s: dict) -> str:
    f, v = fach.standard(), vorgaben_fach.standard()
    alte = [a for a in f.aktive() if a["name"] == s["name"]]
    e = v.anlegen(_vorgabe_text(s), "sonstiges", quelle="skill")
    if not e["ok"]:
        return f"Das hat nicht geklappt: {e['grund']} Sag Eike ehrlich, dass es noch nicht gilt."
    r = f.aktivieren(s["id"], vorgabe=e["id"])
    if not r["ok"]:
        v.widerrufen(e["id"], erzwingen=True)
        return f"Das hat nicht geklappt: {r['grund']} Sag Eike ehrlich, dass es noch nicht gilt."
    for a in alte:
        if a.get("vorgabe"):
            v.widerrufen(a["vorgabe"], erzwingen=True)
    return f"Erledigt: „{s['name']}“ gilt ab sofort. Sag Eike kurz in deinen Worten, was du jetzt tust."


def _personenfaehig(werkzeuge) -> tuple[list, list]:
    """(Werkzeuge, die einer Person gegeben werden können; verworfene). Fridays eigene Eike-Werkzeuge gehören in den Ablauftext."""
    ok, weg = [], []
    for w in (werkzeuge or []):
        (ok if w in primitive.PERSONENFAEHIG else weg).append(w)
    return ok, weg


def _verworfen_hinweis(weg: list) -> str:
    if not weg:
        return ""
    return ("\nVerworfen, nicht an die Person vergeben: " + ", ".join(weg) + ". Das machst du selbst für Eike oder es steht im Ablauftext; "
            "die Person bekommt nur, was skills_lesen als personenfähig zeigt.")


def _speichern(felder: dict, version: int = 1) -> str:
    felder = {**felder}
    felder["werkzeuge"], weg = _personenfaehig(felder.get("werkzeuge"))
    if weg and felder.get("optionen"):
        felder["optionen"] = {o: v for o, v in felder["optionen"].items()
                              if any(o in primitive.PERSONENFAEHIG[w]["optionen"] for w in felder["werkzeuge"])}
    return _speichern_geprueft(felder, version) + _verworfen_hinweis(weg)


def _speichern_geprueft(felder: dict, version: int = 1) -> str:
    if absage := riegel.aufhebung(felder.get("name"), felder.get("zweck"), felder.get("ausloeser"), felder.get("anweisung")):
        return absage
    e = fach.standard().anlegen(**felder, version=version)
    if not e["ok"]:
        return NICHTS.format(grund=e["grund"])
    s = fach.standard().eintrag(e["id"])
    if fach.betrifft_personen(s):
        return (_zusammenfassung(s) + "\nNoch nichts davon gilt. Sag Eike in einem Satz, was du für die Person tun wirst, und frag "
                f"nach seinem Ja; erst danach bestätigst du es (Kennung {s['id']}).")
    return _zusammenfassung(s) + "\n" + _aktivieren(s)


@wirkt("skill", ok=("Erledigt:",), rueckfrage=("Noch nichts davon gilt",), detail=lambda a, t: a.get("zweck"),
       detail_frage=lambda a, t: a.get("zweck"))
def skill_schreiben(name: str, zweck: str, ausloeser: str, zielgruppe, anweisung: str, werkzeuge=None, optionen=None,
                    felder=None) -> str:
    ziel, frage = _ziele(zielgruppe)
    if ziel is None:
        return NICHTS.format(grund=frage)
    return _speichern({"name": name, "zweck": zweck, "ausloeser": ausloeser, "zielgruppe": ziel, "anweisung": anweisung,
                       "werkzeuge": werkzeuge or [], "optionen": optionen or {}, "felder": felder or []})


def _eindeutig(id_oder_name, stati=("aktiv", "entwurf")) -> tuple[dict | None, str]:
    t = fach.standard().finden(id_oder_name, stati)
    if not t:
        return None, "Dazu habe ich nichts Passendes. Frag Eike kurz, was er meint."
    if len(t) > 1:
        return None, ("Das passt auf mehrere; frag Eike kurz, welches er meint, oder nimm die Kennung (intern, nicht nennen): "
                      + "; ".join(f"{e['id']} („{e['name']}“, {'läuft' if e['status'] == 'aktiv' else 'noch nicht bestätigt'})" for e in t))
    return t[0], ""


@wirkt("skill", ok=("Erledigt:",), rueckfrage=("Noch nichts davon gilt",), kopf_ok="Geändert", detail=lambda a, t: a.get("zweck"),
       detail_frage=lambda a, t: a.get("zweck"))
def skill_aendern(id, zweck=None, ausloeser=None, zielgruppe=None, anweisung=None, werkzeuge=None, optionen=None,
                  felder=None) -> str:  # noqa: A002
    s, absage = _eindeutig(id)
    if s is None:
        return absage
    ziel = s["zielgruppe"]
    if zielgruppe is not None:
        ziel, frage = _ziele(zielgruppe)
        if ziel is None:
            return NICHTS.format(grund=frage)
    return _speichern({"name": s["name"], "zweck": zweck if zweck is not None else s["zweck"],
                       "ausloeser": ausloeser if ausloeser is not None else s["ausloeser"], "zielgruppe": ziel,
                       "anweisung": anweisung if anweisung is not None else s["anweisung"],
                       "werkzeuge": werkzeuge if werkzeuge is not None else s["werkzeuge"],
                       "optionen": optionen if optionen is not None else ({} if werkzeuge is not None else s["optionen"]),
                       "felder": felder if felder is not None else s["felder"]},
                      version=s["version"] + 1)


@wirkt("skill", ok=("Erledigt:",), kopf_ok="Bestätigt")
def skill_bestaetigen(id, zustimmung: bool = False) -> str:  # noqa: A002
    s, absage = _eindeutig(id, ("entwurf",))
    if s is None:
        return absage
    if not zustimmung:
        return ("Noch nicht gestartet: Sag Eike in einem Satz, was du für die Person tun wirst, und frag nach seinem Ja. Es zählt nur ein "
                "ausdrückliches Ja in seiner eigenen Nachricht.\n" + _zusammenfassung(s))
    return _aktivieren(s)


skill_bestaetigen.kennt_zustimmung = True


@wirkt("skill", ok=("Erledigt:",), kopf_ok="Zurückgenommen")
def skill_widerrufen(id) -> str:  # noqa: A002
    s, absage = _eindeutig(id)
    if s is None:
        return absage
    r = fach.standard().widerrufen(s["id"])
    if not r["ok"]:
        return f"Das hat nicht geklappt: {r['grund']} Sag Eike ehrlich, dass es noch gilt."
    if r.get("vorgabe"):
        vorgaben_fach.standard().widerrufen(r["vorgabe"], erzwingen=True)
    return f"Erledigt: „{s['name']}“ gilt nicht mehr. Sag Eike kurz, dass du das nicht mehr tust."


def skills_lesen() -> str:
    f = fach.standard()
    z = [_zusammenfassung(s) for s in f.aktive() + f.entwuerfe()]
    def _werte(v):  # Werte, die die Primitive gerade nicht zulässt (z. B. fehlendes Schreibrecht), nicht anbieten
        return [w for w in v["werte"] if not (callable(v.get("pruefen")) and v["pruefen"](w))]

    pf = [f"{n}: {d['beschreibung']}" + (" Einstellungen: " + "; ".join(f"{o} = {'/'.join(map(str, _werte(v)))} (Standard {v['standard']})"
                                                                        for o, v in d["optionen"].items()) if d["optionen"] else "")
          for n, d in primitive.PERSONENFAEHIG.items()]
    return (("\n\n".join(z) or "Bisher hast du für niemanden etwas Festes übernommen.") + "\n\nDas darfst du einer Person geben (nur für dich zum Schreiben, nie vorlesen):\n" + ("\n".join(pf) or "noch keine")
            + "\n\nGruppen (nur diese; im Text beim Namen nennen, als Zielgruppe gruppe:<name>): "
            + (", ".join(fach.GRUPPEN) or "keine") + " (oder person:<Nummer>)")


def skill_testlauf(id, person) -> str:  # noqa: A002
    s, absage = _eindeutig(id)
    if s is None:
        return absage
    p, frage = _person(person)
    if p is None:
        return frage
    return zugriff.testlauf(s, p)


NICHT_NACH_FREMDTEXT = {"skill_schreiben", "skill_aendern", "skill_bestaetigen", "skill_widerrufen"}

WERKZEUGE = {"skill_schreiben": skill_schreiben, "skill_aendern": skill_aendern, "skill_bestaetigen": skill_bestaetigen,
             "skill_widerrufen": skill_widerrufen, "skills_lesen": skills_lesen, "skill_testlauf": skill_testlauf}
