"""Generische personenfähige Primitive für Skills (12.10.2026): Personen wählen, Rundnachricht, Medienwunsch.

Keine festen Fähigkeiten: was damit geschieht, steht im Skill-Text, den Eike bestätigt hat. Die Grenzen stehen hier:
- Alle drei gibt es nur für Personen mit aktivem Skill, der sie nennt (`katalog._person_darf`); Eike bekommt sie nie. Nur
  `rundnachricht_freigeben` ist ein Eike-Werkzeug (Ja in eigener Nachricht, nie nach Fremdtext).
- Gruppen nur aus `fach.GRUPPEN` (Code). Gruppenregel und Empfänger müssen im Skill-Text stehen, der Nachrichtentext WORTGLEICH:
  die Person-Instanz kann ihn nicht ändern und keinen eigenen Wortlaut an andere schicken.
- Zustellung nur über `erinnerungen` (Snapchat-Weg mit allen Prüfungen), nie direkt. Einmal je (Schlüssel, Person).
- Medien: nur Vorschlag/Download, es gibt hier keinen Löschweg. Titel sind Fremdtext."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from kern import config, konnektoren
from kern.dienste import radarr, sonarr
from kern.ehrlichkeit.wirkung import Wirkung, grund_satz, quittung_bauen, wirkt
from kern.personen import register
from kern.skills import primitive, runden, wuensche, zugriff
from kern.werkzeuge import medien as medienwerkzeug, sendepruefung

log = logging.getLogger("friday.personenprimitive")

STANDARD_LIMIT = "2"


def _medien_selbst_pruefen(wert) -> str:
    """Die Option `selbst` gibt es nur, wenn der bestehende Medien-Weg (Konnektor + Schreibrecht) offen ist."""
    if wert == "selbst" and not (konnektoren.benutzbar("medien") and konnektoren.schreiben_erlaubt("medien")):
        return ("Herunterladen ohne Rückfrage ist gerade nicht freigegeben (Freigaben-Seite). Plane es so, dass Eike jeden Wunsch "
                "selbst bestätigt.")
    return ""


def registrieren_alle() -> None:
    primitive.registrieren(
        "personen_auswaehlen", "Nennt die Personen, die eine Gruppe trifft (nur lesen, nie Zugangsdaten)",
        gates=["Gruppen nur aus der Registry im Code", "Regel muss vereinbart sein"])
    primitive.registrieren(
        "nachricht_an_personen", "Schickt einen vorher vereinbarten Text einmal je Person an eine Gruppe (die Kennung merkt, wer ihn hatte)",
        {"senden": {"werte": ["vorschau_und_ja", "selbst"], "beschreibung": "vorschau_und_ja: erst Eikes Ja; selbst: sofort"}},
        gates=["Text wortgleich wie vereinbart", "einmal je Person und Schlüssel", "nur über den geprüften Snapchat-Weg",
               "bei vorschau_und_ja erst nach Eikes Ja in eigener Nachricht"])
    primitive.registrieren(
        "medien_wunsch_stellen", "Stellt im Namen der Person einen Film-/Serienwunsch (Vorschlag an Eike oder Download)",
        {"herunterladen": {"werte": ["mit_freigabe", "selbst"], "pruefen": _medien_selbst_pruefen,
                           "beschreibung": "mit_freigabe: Eike entscheidet; selbst: Download startet sofort (nur wenn Medien dafür freigegeben sind)"},
         "limit_pro_tag": {"werte": ["1", "2", "3", "5"], "standard": STANDARD_LIMIT, "beschreibung": "Wünsche je Person und Tag"}},
        gates=["nur Suche und Anlegen, nie Löschen", "Tageslimit je Person", "Titel ist Fremdtext (bereinigt, kurz)"])


registrieren_alle()

ERKLAERUNGEN = [
    {"name": "personen_auswaehlen",
     "description": ("Zeigt Namen und Nummern der Personen, die eine Gruppenregel trifft (z.B. garantie_beteiligte). Mit schluessel nur "
                     "die, die zu diesem Schlüssel noch keine Nachricht bekamen. Nur lesen."),
     "parameters": {"type": "OBJECT", "properties": {
         "regel": {"type": "STRING", "description": "Gruppenregel aus dem Vereinbarten (oder person:<Nummer>)"},
         "schluessel": {"type": "STRING", "description": "Optional: nur Personen, die diesen Schlüssel noch nicht bekamen"}},
         "required": ["regel"]}},
    {"name": "nachricht_an_personen",
     "description": ("Schickt den vereinbarten Text WORTGLEICH einmal an jede Person der Gruppe (Snapchat). "
                     "Der Schlüssel benennt die Mitteilung; mit gleichem Schlüssel bekommt niemand sie doppelt. Je nach Einstellung "
                     "sieht Eike erst eine Vorschau und muss Ja sagen. Eigene Texte sind nicht möglich."),
     "parameters": {"type": "OBJECT", "properties": {
         "empfaenger": {"type": "STRING", "description": "Gruppenregel aus dem Vereinbarten oder Name/Nummer einer Person"},
         "text": {"type": "STRING", "description": "Der Wortlaut genau wie vereinbart (höchstens 280 Zeichen)"},
         "schluessel": {"type": "STRING", "description": "Kurzer Schlüssel der Mitteilung, z.B. hinweis_selbst_einreichen"}},
         "required": ["empfaenger", "text", "schluessel"]}},
    {"name": "medien_wunsch_stellen",
     "description": ("Stellt im Namen der Person einen Wunsch nach einem Film oder einer Serie. Sucht den Titel; Eike entscheidet über den "
                     "Download (oder er startet, wenn das vereinbart ist). Löschen gibt es nicht."),
     "parameters": {"type": "OBJECT", "properties": {
         "titel": {"type": "STRING", "description": "Titel, wie die Person ihn nennt"},
         "art": {"type": "STRING", "description": "'film' oder 'serie'"}}, "required": ["titel", "art"]}},
    {"name": "rundnachricht_freigeben",
     "description": ("Nur für Eike: Zeigt wartende Rundnachrichten (ohne Schlüssel: alle; mit Schlüssel: Vorschau mit Empfängern und Wortlaut). "
                     "Nach Eikes ausdrücklichem Ja in seiner Nachricht verschickt der erneute Aufruf sie."),
     "parameters": {"type": "OBJECT", "properties": {
         "schluessel": {"type": "STRING", "description": "Schlüssel der Rundnachricht (leer = Liste der wartenden)"}}}},
]


# ---------------------------------------------------------------- Gemeinsames

_ANF = str.maketrans("", "", "\"'„“”‚‘’«»")


def _norm(t: str) -> str:
    return " ".join(str(t or "").translate(_ANF).split())


def _skills(kid: str, werkzeug: str) -> list[dict]:
    return [s for s in zugriff.skills_fuer({"id": kid}) if werkzeug in s["werkzeuge"]]


def _erwaehnt(skills: list[dict], begriffe: list[list[str]]) -> bool:
    """Steht jede benutzte Gruppenregel / Person (Nummer oder Name) im Text eines der aktiven Skills?"""
    texte = [_norm(s["anweisung"]).casefold() for s in skills]
    return all(any(b.casefold() in t for t in texte for b in alt) for alt in begriffe)


def _snapchat_offen() -> bool:
    return konnektoren.benutzbar("snapchat") and konnektoren.schreiben_erlaubt("snapchat")


def _ort(r: dict) -> str:
    teile = []
    if r["gelegt"]:
        teile.append(f"Angelegt für: {', '.join(r['gelegt'])}")
    if r["schon"]:
        teile.append(f"Hatten sie schon: {', '.join(r['schon'])}")
    if r["nicht"]:
        teile.append("Nicht möglich: " + "; ".join(f"{n} ({g})" for n, g in r["nicht"]))
    return ". ".join(teile) or "Niemand."


# ---------------------------------------------------------------- 1. Personen wählen

def personen_auswaehlen(kontakt_id: str = "", regel: str = "", schluessel: str = "") -> str:
    if not zugriff.erlaubt(kontakt_id, "personen_auswaehlen"):
        return "Das ist dir nicht erlaubt."
    personen, begriffe, absage = runden.regel_personen(regel)
    if absage:
        return absage
    if not _erwaehnt(_skills(kontakt_id, "personen_auswaehlen"), begriffe):
        return "Dazu ist nichts vereinbart. Nenn keine Namen und sag schlicht, dass du dabei nicht helfen kannst."
    if (schluessel or "").strip():
        schl = runden.schluessel_sauber(schluessel)
        bekam = runden.standard().bekam(schl)
        personen = [p for p in personen if p["id"] not in bekam]
        kopf = f"Noch ohne Nachricht „{schl}“"
    else:
        kopf = "Treffer"
    if not personen:
        return f"{kopf}: niemand."
    return f"{kopf} ({len(personen)}): " + "; ".join(f"{p['name']} ({p['id']})" for p in personen)


personen_auswaehlen.kennt_kontakt = True


# ---------------------------------------------------------------- 2. Rundnachricht

def _eike_hinweis(schluessel: str, n: int) -> None:
    titel = "Eine Nachricht wartet auf dein Ja"
    text = f"Eine Nachricht an {n} Person(en) wartet auf dein Ja. Zeig ihm den Wortlaut und frag, ob sie so rausgehen soll."
    try:
        from kern.anliegen import fach as anliegen
        anliegen.standard().anlegen("eike", "rundnachricht", titel, text)
    except Exception:  # noqa: BLE001
        log.exception("Anliegen zur Rundnachricht nicht angelegt")
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden("erinnerungen", titel, text, "/", f"rundnachricht-{schluessel}")
    except Exception:  # noqa: BLE001
        log.exception("Push zur Rundnachricht nicht gesendet")


def nachricht_an_personen(kontakt_id: str = "", empfaenger="", text: str = "", schluessel: str = "") -> str:
    r = _nachricht_an_personen(kontakt_id, empfaenger, text, schluessel)
    if r.startswith("Gesendet"):
        return Wirkung("ok", "versand", quittung_bauen("versand", "ok", "Mitteilung an mehrere Personen"), r)
    if r.startswith(("Eike muss dem noch zustimmen", "Das wartet schon auf Eikes Ja")):
        return Wirkung("rueckfrage", "versand", quittung_bauen("versand", "rueckfrage", "wartet auf Eikes Ja"),
                       "NICHTS gesendet (liegt als Vorschau bei Eike zur Freigabe). " + r)
    return Wirkung("nicht", "versand", quittung_bauen("versand", "nicht", grund_satz(r)),
                   "NICHTS gesendet. Behaupte nie, du hättest geschickt. " + r)


def _nachricht_an_personen(kontakt_id: str = "", empfaenger="", text: str = "", schluessel: str = "") -> str:
    name = "nachricht_an_personen"
    if not zugriff.erlaubt(kontakt_id, name):
        return "Das ist dir nicht erlaubt."
    schl, wortlaut = runden.schluessel_sauber(schluessel), runden.text_sauber(text)
    if not schl:
        return "Es fehlt die Kennung der Mitteilung."
    if not wortlaut:
        return "Es fehlt der Text."
    if len(wortlaut) > runden.TEXT_MAX:
        return f"Der Text ist zu lang (höchstens {runden.TEXT_MAX} Zeichen)."
    skills = _skills(kontakt_id, name)
    if not any(_norm(wortlaut) in _norm(s["anweisung"]) for s in skills):
        return ("Nur der vereinbarte Wortlaut darf rausgehen, eigene oder geänderte Texte schicke ich nicht. "
                "Nichts gesendet.")
    if grund := sendepruefung.heikel(wortlaut):
        return grund
    personen, begriffe, absage = runden.regel_personen(empfaenger)
    if absage:
        return absage
    if not _erwaehnt(skills, begriffe):
        return "An diese Empfänger ist nichts vereinbart. Nichts gesendet."
    bekam = runden.standard().bekam(schl)
    offen = [p for p in personen if p["id"] not in bekam]
    if not offen:
        return "Alle aus dieser Gruppe haben diese Mitteilung schon bekommen. Nichts zu tun."
    if not _snapchat_offen():
        return "Gerade kann ich niemandem schreiben. Nichts gesendet."
    if (zugriff.richtlinie(kontakt_id, name, "senden") or "vorschau_und_ja") == "selbst":
        return "Gesendet (wird zugestellt). " + _ort(runden.zustellen(schl, wortlaut, offen))
    ablage = runden.standard()
    ids = [p["id"] for p in offen]
    for v in ablage.offene_vorschauen(schl):
        if v["text"] == wortlaut and v["personen"] == ids:
            return "Das wartet schon auf Eikes Ja. Nichts gesendet."
        ablage.vorschau_status(v["id"], "verfallen")
    ablage.vorschau_anlegen(schl, wortlaut, ids, kontakt_id)
    _eike_hinweis(schl, len(offen))
    return "Eike muss dem noch zustimmen. Gesendet wurde noch nichts."


nachricht_an_personen.kennt_kontakt = True


def _vorschau_text(v: dict) -> str:
    namen = ", ".join((register.holen(p) or {}).get("name", p) for p in v["personen"])
    return f"Nachricht an {len(v['personen'])}: {namen}.\nWortlaut: „{v['text']}“ (Kennung „{v['schluessel']}“, intern)"


def _frisch(v: dict) -> bool:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(v["erstellt"]) <= timedelta(days=runden.VORSCHAU_TAGE)
    except ValueError:
        return False


@wirkt("versand", ok=("Freigegeben.",), rueckfrage=("Noch nichts gesendet.", "Wartende Nachrichten:"),
       detail=lambda a, t: "Mitteilung an mehrere Personen")
def rundnachricht_freigeben(schluessel: str = "", zustimmung: bool = False) -> str:
    ablage = runden.standard()
    schl = runden.schluessel_sauber(schluessel)
    offene = []
    for v in ablage.offene_vorschauen(schl or None):
        if _frisch(v):
            offene.append(v)
        else:
            ablage.vorschau_status(v["id"], "verfallen")
    if not offene:
        return "Es wartet keine Nachricht auf dein Ja."
    if not schl:
        return "Wartende Nachrichten:\n" + "\n".join(_vorschau_text(v) for v in offene)
    if not zustimmung:
        return ("Noch nichts gesendet. " + "\n".join(_vorschau_text(v) for v in offene)
                + "\nFrag Eike, ob sie so rausgehen soll; erst nach seinem Ja in seiner eigenen Nachricht schickst du sie ab.")
    if not _snapchat_offen():
        return "Gerade kann ich nichts verschicken. Nichts gesendet, die Nachricht wartet weiter."
    zeilen = []
    for v in offene:
        personen = [p for p in (register.holen(i) for i in v["personen"]) if p and not p.get("pausiert")]
        zeilen.append(f"„{v['schluessel']}“: " + _ort(runden.zustellen(v["schluessel"], v["text"], personen)))
        ablage.vorschau_status(v["id"], "freigegeben")
    return "Freigegeben. " + " ".join(zeilen)


rundnachricht_freigeben.kennt_zustimmung = True


# ---------------------------------------------------------------- 3. Medienwunsch

ARTEN = {"film": "film", "filme": "film", "movie": "film", "serie": "serie", "serien": "serie", "series": "serie"}


def _bez(art: str, e: dict) -> str:
    return f"{'Serie' if art == 'serie' else 'Film'} „{e.get('title', '?')}“" + (f" ({e['year']})" if e.get("year") else "")


@wirkt("antrag", ok=lambda t: "weitergegeben" in t or "wird heruntergeladen" in t,
       detail=lambda a, t: f"Wunsch „{a.get('titel', '')}“ angenommen")
async def medien_wunsch_stellen(kontakt_id: str = "", titel: str = "", art: str = "") -> str:
    name = "medien_wunsch_stellen"
    if not zugriff.erlaubt(kontakt_id, name):
        return "Das ist dir nicht erlaubt."
    a = ARTEN.get((art or "").strip().lower())
    if not a:
        return "Ist es ein Film oder eine Serie?"
    wunsch = wuensche.titel_sauber(titel)
    if len(wunsch) < 2:
        return "Wie heißt der Titel?"
    person = register.holen(kontakt_id)
    if not person or person.get("pausiert"):
        return "Das ist dir nicht erlaubt."
    limit = int(zugriff.richtlinie(kontakt_id, name, "limit_pro_tag") or STANDARD_LIMIT)
    ablage = wuensche.standard()
    if ablage.heute(kontakt_id) >= limit:
        return f"Für heute sind {limit} Wünsche genug. Versuch es morgen wieder."
    holen, exc = (sonarr.online_suchen, sonarr.SonarrFehler) if a == "serie" else (radarr.online_suchen, radarr.RadarrFehler)
    try:
        treffer = await holen(wunsch)
    except exc:
        return "Ich komme gerade nicht an die Mediathek. Versuch es später noch einmal."
    worte = medienwerkzeug._worte(wunsch)
    passend = [e for e in treffer if worte <= medienwerkzeug._worte(e.get("title", ""))]
    genau = [e for e in passend if medienwerkzeug._worte(e.get("title", "")) == worte]
    wahl = genau or passend
    if not wahl:
        return "Den Titel finde ich nicht. Vielleicht anders geschrieben?"
    if len(wahl) != 1:
        return "Das ist nicht eindeutig: " + "; ".join(_bez(a, e) for e in wahl[:6]) + ". Welcher ist es (mit Jahr)?"
    e = wahl[0]
    off_titel, jahr = wuensche.titel_sauber(e.get("title", "")), e.get("year") if isinstance(e.get("year"), int) else None
    e = {**e, "title": off_titel}
    if e.get("id"):
        return f"{_bez(a, e)} ist schon in der Mediathek."
    if ablage.gibt_es(kontakt_id, a, off_titel, jahr):
        return f"{_bez(a, e)} hast du dir schon gewünscht."
    selbst = (zugriff.richtlinie(kontakt_id, name, "herunterladen") or "mit_freigabe") == "selbst"
    if selbst and not _medien_selbst_pruefen("selbst"):
        antwort = await medienwerkzeug._einen_herunterladen(off_titel, a, jahr)
        if "ist angelegt" in antwort:
            ablage.anlegen(kontakt_id, a, off_titel, jahr, "gestartet")
            return f"{_bez(a, e)} wird heruntergeladen. Es kann ein paar Minuten dauern."
        return antwort
    wid = ablage.anlegen(kontakt_id, a, off_titel, jahr, "wartet")
    zusatz = " (Direkt herunterladen geht gerade nicht.)" if selbst else ""
    try:
        from kern.anliegen import fach as anliegen
        anliegen.standard().anlegen(
            "eike", "medienwunsch", f"Medienwunsch von {person['name']}"[:80],
            (f"{person['name']} wünscht sich {_bez(a, e)}. Frag Eike, ob es heruntergeladen werden soll."
             + zusatz)[:300], fremd=True)
    except Exception:  # noqa: BLE001
        log.exception("Anliegen zum Medienwunsch nicht angelegt (Wunsch %s)", wid)
    return f"{_bez(a, e)} habe ich an Eike weitergegeben. Er entscheidet, ob es runtergeladen wird."


medien_wunsch_stellen.kennt_kontakt = True

NICHT_NACH_FREMDTEXT = {"rundnachricht_freigeben"}

WERKZEUGE = {"personen_auswaehlen": personen_auswaehlen, "nachricht_an_personen": nachricht_an_personen,
             "medien_wunsch_stellen": medien_wunsch_stellen, "rundnachricht_freigeben": rundnachricht_freigeben}
