"""Module (Hebel) einer Kontakt-Person: Basis-Friday plus einzeln schaltbare Fähigkeiten (14.10.2026).

Die einzige Stelle, die entscheidet, was für eine Person WIRKSAM ist. Werkzeugliste, Prompt, Prüfungen und Läufe fragen hier.
Ein Modul öffnet nur, was in den Positivlisten (`rechte.FREMDEN_ERLAUBT`, `skills.primitive`) steht — ein Eintrag im Register
(auch ein manipulierter) öffnet nie etwas darüber hinaus. Müll im Feld `module` gilt als Standard (aus)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from kern import config
from kern.personen import PersonenFehler, rechte

log = logging.getLogger("friday.personen")

MODULE = ("plan", "garantie", "medien", "snap_wuensche", "flammen", "ueberwachung")
PLAN_STUFEN = ("aus", "frei", "voll")
GARANTIE_WERTE = ("auto", "an", "aus")
STANDARD = {"plan": "aus", "garantie": "auto", "medien": False, "snap_wuensche": False, "flammen": False,
            "ueberwachung": False}   # aktive Überwachung: Push, wenn die Person schreibt (15.10.2026) — nicht in `wirksam`, kein Werkzeug
PROTOKOLL = "modul_protokoll.jsonl"
# Module, deren Fähigkeit es schon gibt.
VERFUEGBAR = frozenset({"plan", "garantie", "medien", "flammen", "snap_wuensche", "ueberwachung"})

PLAN_WERKZEUGE = {
    "aus": (),
    "frei": ("kalender_frei",),
    "voll": ("stundenplan", "kalender", "kalender_suchen", "kalender_frei"),
}
_ALLE_PLAN = frozenset(PLAN_WERKZEUGE["voll"])
GARANTIE_PRIMITIVE = frozenset({"garantie_antrag_aus_akte", "akte_zusammenfassung", "ticket_lesen", "ticket_bestaetigen"})      # die Akte-Primitive sind generisch und bleiben beim Skill
MEDIEN_PRIMITIVE = frozenset({"medien_wunsch_stellen"})


def _plan_aus_werkzeugen(werkzeuge) -> str:
    hat = set(werkzeuge or [])
    if hat & {"stundenplan", "kalender", "kalender_suchen"}:
        return "voll"
    if "kalender_frei" in hat:
        return "frei"
    return "aus"


def _gueltig(name: str, wert) -> bool:
    if name == "plan":
        return wert in PLAN_STUFEN
    if name == "garantie":
        return wert in GARANTIE_WERTE
    return isinstance(wert, bool)


def von_person(person: dict) -> dict:
    """Vollständiges, gültiges Modul-Dict. Fehlt `module` (noch nicht migriert), wird `plan` aus den alten `werkzeuge`
    abgeleitet; ungültige Einzelwerte fallen auf den Standard."""
    m = dict(STANDARD)
    gespeichert = person.get("module")
    if not isinstance(gespeichert, dict):
        m["plan"] = _plan_aus_werkzeugen(person.get("werkzeuge"))
        return m
    for k in MODULE:
        if k in gespeichert and _gueltig(k, gespeichert[k]):
            m[k] = gespeichert[k]
    if "plan" not in gespeichert:
        m["plan"] = _plan_aus_werkzeugen(person.get("werkzeuge"))
    return m


def pruefen(roh) -> dict:
    if not isinstance(roh, dict):
        raise PersonenFehler("Die Module müssen ein Objekt sein.")
    aus = {}
    for k, v in roh.items():
        if k not in MODULE:
            raise PersonenFehler(f"Ein Modul „{k}“ gibt es nicht.")
        if not _gueltig(k, v):
            raise PersonenFehler(f"„{v}“ ist für das Modul „{k}“ kein gültiger Wert.")
        aus[k] = v
    return aus


def hat_garantie_profil(person: dict) -> bool:
    try:
        return int((person.get("quellen") or {}).get("garantie") or 0) > 0
    except (TypeError, ValueError):
        return False


def wirksam(person: dict) -> set[str]:
    m = von_person(person)
    an = {k for k in ("medien", "snap_wuensche", "flammen") if m[k]}
    if m["plan"] != "aus":
        an.add("plan")
    if m["garantie"] in ("an", "auto") and hat_garantie_profil(person):
        an.add("garantie")
    return an


def zusammenfuehren(person: dict, aenderung: dict) -> dict:
    neu = {**von_person(person), **pruefen(aenderung)}
    if neu["garantie"] == "an" and not hat_garantie_profil(person):
        raise PersonenFehler("Garantie lässt sich nur einschalten, wenn die Person ein Garantie-Profil hat.")
    return neu


BASIS = ("uhrzeit", "suchen", "nachrichten")      # Grundfunktionen jeder Person: immer an, kein Hebel (Eike, 14.10.2026)


def werkzeuge_fuer(person: dict) -> list[str]:
    """Die Grundfunktionen (immer) plus die Werkzeuge der Plan-Stufe. In der Reihenfolge der Positivliste. Das gespeicherte Feld
    `werkzeuge` entscheidet nicht mehr: Es ist nur noch der abgeleitete Stand für ältere Leser."""
    m = von_person(person)
    gewollt = set(BASIS) | set(PLAN_WERKZEUGE[m["plan"]])
    return [w for w in rechte.FREMDEN_ERLAUBT if w in gewollt]


def primitiv_erlaubt(person: dict, name: str) -> bool:
    """Zusätzlicher Riegel auf die Skill-Primitive. Garantie-Antrag: nur, wenn Eike die Garantie nicht ausgeschaltet hat (ohne
    Profil erreicht der Aufruf das Primitiv, das Eike „Profil fehlt“ meldet). Medien-Wunsch: nur mit dem Hebel `medien`."""
    if name in GARANTIE_PRIMITIVE:
        return von_person(person)["garantie"] != "aus"
    if name in MEDIEN_PRIMITIVE:
        return von_person(person)["medien"]
    return True


def oeffnet(person: dict, name: str) -> bool:
    """Werkzeuge, die ein Modul selbst mitbringt (nicht in `FREMDEN_ERLAUBT`)."""
    return name == "snap_wunsch" and "snap_wuensche" in wirksam(person)


_FAEHIGKEIT = {
    "uhrzeit": "die Uhrzeit sagen", "suchen": "im Internet nachsehen", "nachrichten": "Nachrichten aus dem Netz nachsehen",
}

_BAUSTEINE = {
    "plan": "Eikes Zeit: Über {nutzers} Zeit sagst du nur, was im Block [FREIGEGEBEN] steht, nie mehr.",
    "garantie": ("Garantie: Du hilfst {name} bei der ÜSTRA-Garantie nach dem Ablauf, den du für {name} übernimmst: fragen, was fehlt, das "
                 "Ticket mit {name} gemeinsam prüfen, zusammenfassen und erst nach ihrem Ja den Antrag vorbereiten. Du schickst nie "
                 "selbst ab — das macht {nutzer} per Knopf. Sag nie, etwas sei eingereicht oder erstattet, wenn du es nicht "
                 "nachgesehen hast."),
    "medien": "Filme und Serien: {name} darf sich Filme und Serien wünschen. Du nimmst den Wunsch auf; {nutzer} entscheidet.",
    "snap_wuensche": ("Snap-Wünsche: {name} darf sich ein Bild als Snap wünschen. Dafür rufst du snap_wunsch auf; der Snap geht "
                      "immer nur an {name}, höchstens 5 am Tag. Findest du nichts Passendes, sagst du das ehrlich."),
    "flammen": ("Flamme: Du und {name} haltet eine Snapchat-Flamme (täglich ein Snap). Die schickst du automatisch; du willst sie "
                "behalten und ermutigst {name} freundlich und beiläufig, dabeizubleiben — nie drängend, nie mehr als ab und zu. "
                "Sagt {name}, dass das nicht gewünscht ist, nimmst du es ernst und sagst {nutzer} Bescheid (frage_eike)."),
}


def _erreichbar(person: dict, modul: str) -> bool:
    """Ein Baustein verspricht nur, was die Person auch kann: Garantie und Medien arbeiten über Skill-Werkzeuge, die es nur mit einem
    aktiven Skill gibt (`skills.zugriff`)."""
    if modul == "flammen":                      # der Lauf muss auch wirklich senden (Schalter „an“), sonst kein Versprechen
        from kern.personen import flammen
        return flammen.modus() == "an"
    wz = {"garantie": ("akte_erfassen", "garantie_antrag_aus_akte"), "medien": ("medien_wunsch_stellen",)}.get(modul)
    if wz is None:
        return True
    from kern.skills import zugriff
    pid = person.get("id")
    try:
        return any(zugriff.erlaubt(pid, w) and primitiv_erlaubt(person, w) for w in wz)
    except Exception:  # noqa: BLE001 — im Zweifel kein Versprechen
        return False


def baustein_text(person: dict, nutzer: str) -> str:
    """Der Prompt-Block „was du für <Name> kannst“: die Basis, dazu je wirksamem Modul ein Baustein (feste Reihenfolge). Was
    fehlt, steht nicht drin — Friday behauptet nichts, was sie nicht kann —, dafür die feste Regel zum Rest."""
    name = person.get("name", "")
    kann = [_FAEHIGKEIT[w] for w in werkzeuge_fuer(person) if w in _FAEHIGKEIT]
    zeilen = [f"[WAS DU FÜR {name.upper()} KANNST]",
              f"Du kannst mit {name} plaudern" + (", " + ", ".join(kann) if kann else "") + "."]
    an = wirksam(person) & VERFUEGBAR
    for m in MODULE:
        if m in an and _erreichbar(person, m):
            zeilen.append("- " + _BAUSTEINE[m].format(name=name, nutzer=nutzer, nutzers=nutzer + ("" if nutzer.endswith("s") else "s")))
    zeilen.append(f"Alles andere kannst du nicht. Sag das ehrlich; betrifft es {nutzer}, fragst du über frage_eike nach.")
    return "\n".join(zeilen)


def protokollieren(pid: str, modul: str, alt, neu, von: str) -> None:
    """Append-only: wer hat wann welchen Hebel umgelegt (`von`: app · friday · migration)."""
    zeile = {"zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "person": pid, "modul": modul,
             "alt": alt, "neu": neu, "von": von}
    pfad = config.daten_ordner() / PROTOKOLL
    pfad.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def migrieren() -> int:
    """Einmal beim Start: Personen ohne `module` bekommen es aus ihrem Bestand (Plan aus `werkzeuge`, Medien, wenn ein aktiver
    Skill `medien_wunsch_stellen` für sie öffnet). Garantie bleibt `auto`. Gibt die Zahl der migrierten Personen zurück."""
    from kern.personen import register
    from kern.skills import zugriff
    n = 0
    for p in register.liste():
        if isinstance(p.get("module"), dict):
            continue
        m = von_person(p)
        try:
            if "medien_wunsch_stellen" in zugriff.werkzeuge_fuer(p):
                m["medien"] = True
        except Exception:  # noqa: BLE001 — ohne Skill-Auskunft bleibt Medien aus (strengster Wert)
            log.exception("Migration: Skills nicht lesbar für %s", p.get("id"))
        register.aendern(p["id"], module=m, von="migration")
        protokollieren(p["id"], "migration", None, m, "migration")      # auch wenn sich nichts gegenüber der Ableitung ändert
        n += 1
    return n
