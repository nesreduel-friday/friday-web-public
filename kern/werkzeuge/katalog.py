"""Alle Werkzeuge an einer Stelle — für Gemini beschrieben, für den Kern ausführbar.

Gemini entscheidet, WAS aufgerufen wird. Ausgeführt wird nur, was hier steht,
und nur mit den Angaben, die die Funktion kennt. Bisher stehen hier nur
Werkzeuge, die lesen oder ausschließlich Fridays eigenes Gedächtnis anfassen.

Werkzeuge fremder Dienste hängen an einem Konnektor (`KONNEKTOR` im Modul).
Default-Deny: solange der auf der Freigaben-Seite aus ist oder keine
Zugangsdaten hat, sieht Gemini die Werkzeuge nicht — und ruft es sie trotzdem
auf, lehnt `ausfuehren` ab.
"""

import inspect
import re

from kern import config, konnektoren
from kern.ehrlichkeit import wirkung as wirk
from kern.personen import module as personenmodule, register as personenregister, rechte as personenrechte
from kern.skills import primitive as skillprimitive, zugriff as skillzugriff
from kern.werkzeuge import (akte, anliegen, basis, benachrichtigung, bilder, buecher, chats, dashboard, dokument, erinnerung, fahrplan, fragen, garantie,
                            gedaechtnis, gedanken, gmail, hphome, kalender, kontakte, lernen, medien, minecraft_bot, morgenbriefing, musik, notizen, oeffnen, pc,
                            personenstandort, post, route, schule, garantie_annahme, module_schalten, personenprimitive, aufgabenblatt, recherche, regel_aktionen, regeln, skills, snapchat, snapwunsch, ticket_annahme, standort, studio, suche, vorgaben, wetter, whatsapp, werkbank, chatverlauf, verbrauch)

_MODULE = [basis, chatverlauf, verbrauch, gedaechtnis, schule, post, gmail, chats, kalender, suche, erinnerung, musik, fahrplan,
           studio, wetter, morgenbriefing, oeffnen, garantie, notizen, standort, personenstandort, whatsapp, snapchat,
           benachrichtigung, pc, lernen, buecher, dashboard, dokument, bilder, route, hphome, medien, minecraft_bot, kontakte, gedanken, anliegen, vorgaben, skills, personenprimitive, akte, module_schalten, snapwunsch, garantie_annahme, ticket_annahme, regel_aktionen, regeln, recherche, aufgabenblatt, werkbank, fragen]


def vorschlag_hinweise() -> str:
    """Hinweise aller Module auf Vorschläge, die auf Eikes Ja warten (der Verlauf kennt keine Werkzeugaufrufe)."""
    return "\n\n".join(h for m in _MODULE if (f := getattr(m, "vorschlag_hinweis", None)) and (h := f()))


# Gedankenbuch und Fäden tragen Einträge, die die Denkrunde aus Mail/Webseite/Chat gebildet hat („gelesen und nicht bestätigt“,
# `gedanken.py`). Liest Friday sie, steht Fremdtext im Zug: ein darin versteckter „Schick Kontakt C …“ darf nichts auslösen (Audit 11.10.2026).
_FREMD_AUS_ERGEBNIS = {
    "gedanken_lesen": lambda e: "nicht bestätigt" in str((e or {}).get("ergebnis") or ""),
    "faeden_lesen": lambda e: "unbestätigt" in str((e or {}).get("ergebnis") or ""),
}


def liefert_fremdes(name: str, ergebnis: dict | None = None) -> bool:
    """Bringt dieses Werkzeug Text von Dritten ins Gespräch (Mails, Briefe)? Dann
    gilt der Rest des Zugs wie ein Zug mit Link: merken wird zum Vorschlag.

    Mit `ergebnis` kann ein Modul es genauer sagen (`FREMD_WENN`): Ein Werkzeug, das
    nur „nichts gefunden“ meldet, hat nichts Fremdes gebracht."""
    zusatz = _FREMD_AUS_ERGEBNIS.get(name)
    if zusatz is not None and ergebnis is not None and zusatz(ergebnis):
        return True
    for m in _MODULE:
        if name in getattr(m, "FREMDE_WERKZEUGE", ()):
            pruefer = getattr(m, "FREMD_WENN", {}).get(name)
            return bool(pruefer(ergebnis)) if pruefer and ergebnis is not None else True
    return False


# Zustimmung erkennen: nur der Kern darf einem Werkzeug sagen, dass Eike gerade
# "ja" gesagt hat — das Modell kann sich die Erlaubnis so nicht selbst geben.
_JA = re.compile(r"\b(ja+|jawohl|yes|jap|jo|klar|okay|ok|passt|genau|richtig|bitte|gerne|einverstanden|"
                 r"mach(s|e|en)?|trag(e)?\s*(es|das|ihn)?\s*ein|eintragen|leg(e)?\s*an)\b", re.I)
_NEIN = re.compile(r"\b(nein|nee|ne|n(?:ö|oe)+|nope|no|nicht|nich|net|ned|nichts|nix|kein\w*|nie|niemals|niemand\w*|nirgend\w*|"
                   r"weder|doch\s+nicht|lass|abbrechen|stopp?|halt|warte|sp(?:ä|ae)ter|vergiss\w*|verwirf\w*|unterlass\w*|"
                   r"h(?:ö|oe)r\s+auf)\b", re.I)
# Vorbehalte machen aus einem Ja kein reines Ja („ja, aber nur …“, „ja, ohne den Anhang“, „ja, wenn er will“, eine Frage): für
# die Zustimmung zählt das als Nein, löscht aber keine gemerkte Zustimmung im Sprachkanal (`ablehnung_erkannt`).
_VORBEHALT = re.compile(r"\b(na\s+ja|aber|sondern|au(?:ß|ss)er|ohne|wenn|falls|vielleicht|eigentlich|eher|lieber|besser|ungern|dagegen|"
                        r"moment|sekunde|erst)\b|\?", re.I)


def zustimmung_erkannt(text: str) -> bool:
    """Hat Eike in dieser Nachricht ausdrücklich zugestimmt? Bei jedem Anzeichen
    von Ablehnung oder Vorbehalt: nein. Im Zweifel nein."""
    text = (text or "").strip()
    return bool(text) and bool(_JA.search(text)) and not _NEIN.search(text) and not _VORBEHALT.search(text)


def ablehnung_erkannt(text: str) -> bool:
    """Anzeichen von Ablehnung („nein“, „warte“, „später“ …) — löscht eine
    gemerkte Zustimmung im Sprachkanal (Gespräch 439)."""
    return bool((text or "").strip()) and bool(_NEIN.search((text or "")))


def braucht_zustimmung(name: str) -> bool:
    """Läuft dieses Werkzeug nur mit Eikes Ja (kennt_zustimmung)? Für den
    Sprachkanal: ein Ja ist mit dem Aufruf verbraucht (Gespräch 439)."""
    for modul in _MODULE:
        funktion = modul.WERKZEUGE.get(name)
        if funktion is not None:
            return bool(getattr(funktion, "kennt_zustimmung", False))
    return False


# Zugangsschlüssel in Werkzeugergebnissen. Gemessen 20.09.2026 (Gespräch 224): Eikes
# WhatsApp-Chat mit sich selbst enthält API-Schlüssel — whatsapp_lesen schickte sie
# im Klartext an Gemini, ins Protokoll und von dort in den Rückblick. Friday braucht
# sie nie; „ein Schlüssel“ reicht als Auskunft. Fortsetzungszeilen (ein Schlüssel,
# über mehrere Zeilen eingefügt) gehören dazu.
_WEITER = r"(?:[ \t]*\r?\n[ \t]*\w[\w\-]{15,}(?=\s|$))*"
_GEHEIM = re.compile("|".join([
    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}" + _WEITER,  # Anthropic, OpenAI
    r"\bAIza[0-9A-Za-z_\-]{30,}",                           # Google
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",                          # GitHub
    r"\bgithub_pat_[A-Za-z0-9_]{30,}",
    r"\bglpat-[A-Za-z0-9_\-]{20,}",                           # GitLab
    r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",                       # Slack
    r"\bhf_[A-Za-z0-9]{30,}",                                  # Hugging Face
    r"\bgsk_[A-Za-z0-9]{30,}",                                 # Groq
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
]))


def geheimes_schwaerzen(text: str) -> str:
    return config.schwaerzen(_GEHEIM.sub("[Schlüssel ausgeblendet]", text))


def _frei(modul) -> bool:
    k = getattr(modul, "KONNEKTOR", None)
    return k is None or konnektoren.benutzbar(k)


def _sichtbar(modul, name: str) -> bool:
    """Schreibende Werkzeuge zeigt Gemini nur, wenn das Schreibrecht an ist."""
    if name not in getattr(modul, "NUR_MIT_SCHREIBRECHT", ()):
        return True
    return konnektoren.schreiben_erlaubt(getattr(modul, "KONNEKTOR", ""))


# Werkzeuge, die nur eine fremde Person sieht (nie Eike).
NUR_FREMDE = {"frage_eike", "termin_wunsch", "snap_wunsch"}

# Nur in der Denkrunde sichtbar und ausführbar.
NUR_DENKEN = {"gedanke_festhalten", "hinweis_geben", "faden_anlegen", "faden_weiter", "faden_schliessen"}

# Die Denkrunde darf NUR lesen (Positivliste, Spec 2026-10-07-nachdenken). Ein neues Werkzeug ist hier nie
# versehentlich dabei; ein Test hält fest, dass nichts aus NUR_MIT_SCHREIBRECHT / NICHT_NACH_FREMDTEXT darin steht.
DENKEN_ERLAUBT = frozenset({
    # Gedächtnis (lesen)
    "wissen", "erinnern", "rueckblick", "chat_suchen", "person_ueber", "gedanken_lesen",
    # Zeit, Termine, Erinnerungen
    "uhrzeit", "erinnerungen", "kalender", "kalender_suchen", "kalender_frei", "wetter",
    # Schule
    "stundenplan", "hausaufgaben", "elternbriefe", "elternbrief", "schultermine", "lernen", "schulbuecher",
    "notizen_suchen", "notizseite", "notizhefte",
    # Post
    "mails", "mail", "mail_termine", "gmail_mails", "gmail_mail",
    # Unterwegs
    "abfahrten", "verbindung", "fahrten_gemerkt", "haltestellen_hier", "wo_ist", "garantie_stand",
    # Welt
    "suchen", "nachrichten", "webseite",
    # Eigene Werkzeuge der Runde
    "gedanke_festhalten", "hinweis_geben", "faden_anlegen", "faden_weiter", "faden_schliessen", "faeden_lesen",
    # Vorgaben von Eike (nur lesen; anlegen/ändern/widerrufen nie in der Runde)
    "vorgaben_lesen",
    # Skills (nur lesen / Trockenlauf)
    "skills_lesen", "skill_testlauf",
    # Werkbank: lesen, und vorschlagen (legt nur einen Vorschlag an, startet nichts; nach Fremdtext gesperrt, s. u.)
    "vorhaben_lesen", "vorhaben_vorschlagen",
    # Fragen an Eike (stellen; beantworten nie in der Runde)
    "eike_fragen",
})

# Anfragen ins Netz, deren Adresse oder Suchtext das Modell wählt. In der Denkrunde (niemand schaut zu) sind sie gesperrt,
# sobald fremder Text im Spiel war: eine Mail könnte die Runde sonst anstiften, Daten in eine Adresse zu packen.
DENKEN_NACH_FREMDTEXT_GESPERRT = frozenset({"webseite", "suchen", "nachrichten", "vorhaben_vorschlagen", "eike_fragen"})


# Nur im Lauf einer Regel sichtbar und ausführbar (Spec 2026-10-15-regeln): die Aktionen, die Eike einer Regel erlaubt hat.
NUR_REGEL = frozenset({"regel_push", "regel_antwort", "regel_termin", "regel_snap"})


def _modus_darf(name: str, modus: str) -> bool:
    if modus == "regel":
        return name in NUR_REGEL
    if name in NUR_REGEL:
        return False
    if modus == "denken":
        return name in DENKEN_ERLAUBT
    return name not in NUR_DENKEN


def _register_person(person: dict | None) -> dict | None:
    pid = (person or {}).get("id")
    return personenregister.holen(pid) if isinstance(pid, str) and pid else None


def _person_darf(person: dict | None, name: str) -> bool:
    """Eike (person=None) darf alles außer den Werkzeugen für Fremde. Eine Person nur, was in der Positivliste steht UND ihre
    Module es öffnen — plus `frage_eike`. Was ein Modul selbst freigibt (Skill-Primitive, `snap_wunsch`), zählt nach dem
    REGISTER, nicht nach dem übergebenen Dict."""
    if name in skillprimitive.PERSONENFAEHIG:
        # Personenfähige Primitive: nie für Eike, nie über das Register allein — nur wenn ein aktiver Skill sie dieser
        # Person öffnet UND (Garantie/Medien) das Modul wirksam ist.
        if person is None or not skillzugriff.erlaubt(person.get("id"), name):
            return False
        echt = _register_person(person)
        return echt is not None and personenmodule.primitiv_erlaubt(echt, name)
    if person is None:
        return name not in NUR_FREMDE
    if name == "snap_wunsch":
        echt = _register_person(person)
        return echt is not None and personenmodule.oeffnet(echt, name)
    if name in NUR_FREMDE:
        return True
    return name in personenrechte.FREMDEN_ERLAUBT and name in personenmodule.werkzeuge_fuer(person)


def gemini_tools(person: dict | None = None, modus: str = "", module: set[str] | None = None,
                 nur: set[str] | None = None) -> list[dict]:
    """`module`: nur Module mit diesen Kurznamen (`auswahl`); None = alle. `nur`: nur diese Werkzeugnamen (Regel-Läufe)."""
    return [{"functionDeclarations": [e for m in _MODULE if _frei(m) and (module is None or m.__name__.rsplit(".", 1)[-1] in module)
                                      for e in m.ERKLAERUNGEN if _sichtbar(m, e["name"]) and _person_darf(person, e["name"])
                                      and _modus_darf(e["name"], modus) and (nur is None or e["name"] in nur)]}]


def _fehler(name: str, text: str) -> dict:
    """Ein Abbruch im Kern: `fehler` für das Modell (wie bisher), dazu Status und Quittung für den Kern."""
    w = wirk.fehler_wirkung(name, text)
    return {"fehler": text, "status": w.status, "art": w.art, "quittung": w.quittung}


async def ausfuehren(name: str, argumente: dict | None = None, fremde_inhalte: bool = False,
                     gespraech: int | None = None, zustimmung: bool = False,
                     aktionen: list | None = None, eingabe: str = "", person: dict | None = None,
                     modus: str = "", runde: int | None = None) -> dict:
    """`fremde_inhalte`: im laufenden Zug stand ein Link oder Bild. Werkzeuge,
    die das wissen müssen (`merken`), bekommen es mitgeteilt.

    `aktionen`: Aufträge an Eikes App (z.B. eine Seite öffnen). Die Liste
    stellt die Sitzung bereit und gibt sie an den Browser weiter.

    `eingabe`: was Eike in diesem Zug gesagt oder getippt hat. Werkzeuge, die
    prüfen müssen, ob er etwas WIRKLICH verlangt hat (`kennt_eingabe`), bekommen es.

    Asynchron, weil manche Werkzeuge übers Netz antworten (`wissen` fragt
    Graphiti); einfache Funktionen laufen weiter ganz normal."""
    if not _modus_darf(name, modus) or not _person_darf(person, name):
        return _fehler(name, f"{name} ist hier nicht erlaubt.")
    if modus == "denken" and fremde_inhalte and name in DENKEN_NACH_FREMDTEXT_GESPERRT:
        return _fehler(name, f"{name} läuft in der Denkrunde nicht mehr, nachdem fremder Text (Mail, Webseite, Chat) gelesen "
                          "wurde. Halte fest, was du schon weißt.")
    for modul in _MODULE:
        funktion = modul.WERKZEUGE.get(name)
        if funktion is None:
            continue
        if not _frei(modul):
            return _fehler(name, f"{name} ist auf der Freigaben-Seite nicht freigegeben.")
        if not _sichtbar(modul, name):
            return _fehler(name, f"{name} braucht das Schreibrecht, das auf der Freigaben-Seite noch aus ist.")
        muster = getattr(modul, "NACH_FREMDTEXT_WENN_EIKE_VERLANGT", {}).get(name)
        if fremde_inhalte and name in getattr(modul, "NICHT_NACH_FREMDTEXT", ()) \
                and not (muster and muster.search(eingabe or "")):
            # Eine Mail, Webseite oder ein Chat könnte den Aufruf angestoßen haben („verwirf das
            # Video“, „schalte die Warnungen aus“). Ohne Rückfrage-Werkzeug bleibt nur: nicht in
            # diesem Zug. Eikes nächste Nachricht ohne Fremdtext darf es wieder. Ausnahme: Eike hat es in DIESER Äußerung selbst
            # ausdrücklich verlangt („such dir welche aus und lad sie runter“, 10.10.2026) — seine Worte kann kein Fremdtext schreiben.
            return _fehler(name, f"{name} ändert etwas und läuft nicht mehr, nachdem in diesem Zug fremder Text "
                              "(Mail, Webseite, Chat) gelesen wurde. Bitte Eike, es in einer eigenen Nachricht zu sagen.")
        erlaubt = inspect.signature(funktion).parameters
        # Nur bekannte Angaben weitergeben — ein Modell erfindet gern welche dazu.
        # `fremde_inhalte` und `gespraech` setzt nur der Kern, nie das Modell.
        angaben = {k: v for k, v in (argumente or {}).items()
                   if k in erlaubt and k not in ("fremde_inhalte", "gespraech", "zustimmung", "aktionen",
                                                 "eingabe", "kontakt_id", "runde")}
        if getattr(funktion, "kennt_fremde_inhalte", False):
            angaben["fremde_inhalte"] = fremde_inhalte
        if getattr(funktion, "kennt_gespraech", False):
            angaben["gespraech"] = gespraech
        if getattr(funktion, "kennt_zustimmung", False):
            # Ein „ja“ in Eikes Nachricht („bitte lies die Mail vor“) darf keinen Aufruf
            # freischalten, den erst fremder Text ausgelöst hat: Stand in diesem Zug schon
            # eine Mail, Webseite oder ein Chat im Spiel, gilt die Zustimmung nicht. Die
            # Werkzeuge fragen dann noch einmal, und Eikes nächstes „ja“ zählt wieder.
            angaben["zustimmung"] = zustimmung and not fremde_inhalte
        if getattr(funktion, "kennt_aktionen", False):
            angaben["aktionen"] = aktionen
        if getattr(funktion, "kennt_eingabe", False):
            angaben["eingabe"] = eingabe
        if getattr(funktion, "kennt_kontakt", False):
            angaben["kontakt_id"] = (person or {}).get("id", "")
        if getattr(funktion, "kennt_runde", False):
            angaben["runde"] = runde
        try:
            ergebnis = funktion(**angaben)
            if inspect.isawaitable(ergebnis):
                ergebnis = await ergebnis
            text, w = wirk.normalisieren(name, ergebnis)
            if isinstance(text, str):
                text = geheimes_schwaerzen(text)
            # Bei nicht erledigt/offen beginnt das Ergebnis einheitlich mit „NICHT ERLEDIGT: “ (nicht doppelt, kürzt nichts).
            return {"ergebnis": wirk.mit_praefix(text, w.status), "status": w.status, "art": w.art,
                    "quittung": geheimes_schwaerzen(w.quittung)}
        except (TypeError, ValueError):
            return _fehler(name, f"Für {name} fehlen Angaben oder sie passen nicht.")
        except Exception as exc:
            return _fehler(name, f"Das Werkzeug {name} ist fehlgeschlagen ({type(exc).__name__}).")
    return _fehler(name, f"Das Werkzeug {name} gibt es nicht.")
