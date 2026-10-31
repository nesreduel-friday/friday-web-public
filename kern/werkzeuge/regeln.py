"""Werkzeuge: Eike legt Regeln an („Wenn Kontakt F auf WhatsApp antwortet, antworte kurz und trag es in den Kalender ein“) — 15.10.2026.

Nur Eike im Gespräch, nie nach Fremdtext (`NICHT_NACH_FREMDTEXT`), nie für Personen, Denkrunde oder Regel-Läufe. Friday schreibt nur einen
ENTWURF und liest ihn mit Auslöser, Empfängern, Aktionen und Limits im Klartext vor; erst Eikes ausdrückliches Ja (`regel_bestaetigen`,
`kennt_zustimmung`) macht ihn aktiv und fixiert die Empfänger. Auslöser und Empfänger werden aus Kontakten bzw. freigegebenen
WhatsApp-Chats NACH NAMEN aufgelöst; ein geratener Name legt nichts an."""

from __future__ import annotations

from kern import config
from kern.dienste import whatsapp
from kern.ehrlichkeit.wirkung import wirkt
from kern.personen import register
from kern.regeln import speicher, zeit

NICHTS = "Nichts geändert. {grund}"
KANAL_NAME = {"whatsapp": "WhatsApp", "snapchat": "Snapchat", "zeit": "die Uhrzeit"}
AKTION_TEXT = {"push": "dir eine Push-Nachricht schicken", "antwort_an_ausloeser": "dem Absender antworten",
               "antwort_an_empfaenger": "den festgelegten Empfängern schreiben", "kalender_eintragen": "Termine in deinen Kalender eintragen",
               "snap_senden": "ein Bild als Snap schicken (an dich, den Absender oder die festgelegten Empfänger)"}

ERKLAERUNGEN = [
    {"name": "regel_schreiben",
     "description": ("Legt eine Regel als ENTWURF an: Wenn eine Nachricht von einer bestimmten Person (Snapchat) oder in einem bestimmten "
                     "freigegebenen Chat (WhatsApp) ankommt ODER eine Uhrzeit erreicht ist (kanal zeit, z. B. „schreib Kontakt F um 18 Uhr, dass ich nicht "
                     "komme“), führst du Eikes Auftrag aus. Bei kanal zeit gibst du `wann` an und keinen Absender; Antworten gehen dann nur an "
                     "feste `empfaenger`. Nur wenn Eike das ausdrücklich verlangt. Danach liest "
                     "du ihm den Entwurf vor und rufst regel_bestaetigen erst nach seinem Ja auf. Aktionen: push, antwort_an_ausloeser, "
                     "antwort_an_empfaenger, kalender_eintragen, snap_senden (ein Bild als Snap an Eike, den Absender oder feste Empfänger)."),
     "parameters": {"type": "OBJECT", "properties": {
         "name": {"type": "STRING", "description": "Kurzer Name der Regel"},
         "kanal": {"type": "STRING", "description": "whatsapp, snapchat oder zeit"},
         "wer": {"type": "STRING", "description": "Name der Person (Kontakte) bzw. des freigegebenen WhatsApp-Chats, deren Nachricht auslöst (nicht bei zeit)"},
         "wann": {"type": "STRING", "description": ("Nur bei kanal zeit: einmalig 2026-10-15T18:00 (deutsche Zeit, Datum UND Uhrzeit, in der "
                                                    "Zukunft), „täglich 08:00“ oder Wochentage „mo,mi 17:30“")},
         "auftrag": {"type": "STRING", "description": "Eikes Auftrag in seinen Worten, z. B. 'Antworte kurz und trag es in den Kalender ein'"},
         "erlaubt": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Erlaubte Aktionen"},
         "empfaenger": {"type": "STRING", "description": "Nur bei antwort_an_empfaenger oder snap_senden: Namen, mit Komma getrennt (Kontakte, bei antwort_an_empfaenger auch freigegebene Chats). Snaps an Eike selbst brauchen keinen Empfänger."},
         "limit_stunde": {"type": "INTEGER", "description": "Höchstens so viele Läufe pro Stunde (Standard 5)"},
         "limit_tag": {"type": "INTEGER", "description": "Höchstens so viele Läufe pro Tag (Standard 20)"}},
         "required": ["name", "kanal", "auftrag", "erlaubt"]}},
    {"name": "regel_bestaetigen",
     "description": "Macht den Entwurf einer Regel aktiv. NUR nach Eikes ausdrücklichem Ja zum vorgelesenen Entwurf.",
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER", "description": "Nummer der Regel"}}, "required": ["id"]}},
    {"name": "regel_widerrufen",
     "description": "Beendet eine Regel (aktiv, pausiert oder Entwurf). Nur wenn Eike es verlangt.",
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER", "description": "Nummer der Regel"}}, "required": ["id"]}},
    {"name": "regel_streichen",
     "description": ("LÖSCHT eine Regel endgültig samt ihrer Läufe (nicht rückgängig). Nur wenn Eike es ausdrücklich verlangt und danach "
                     "ausdrücklich Ja sagt. Zum bloßen Beenden nimmst du regel_widerrufen."),
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER", "description": "Nummer der Regel"}}, "required": ["id"]}},
    {"name": "regeln_lesen",
     "description": "Listet Eikes Regeln mit Status und letztem Lauf.",
     "parameters": {"type": "OBJECT", "properties": {}}},
]


_EIKE_NAMEN = {"eike", "mich", "mir", "ich", "mir selbst", "mich selbst", "eike selbst"}


def _person_nach_name(name: str, vorname_reicht: bool = False) -> dict | None:
    k = register.schluessel(name)
    personen = register.liste()
    treffer = [p for p in personen if register.schluessel(p["name"]) == k]
    if not treffer and vorname_reicht and len(k) >= 3:  # „Kontakt C“ meint „Kontakt C Speery“: ein einzelnes Namenswort reicht, wenn es eindeutig ist
        treffer = [p for p in personen if k in register.schluessel(p["name"]).split()]
    return treffer[0] if k and len(treffer) == 1 else None


def _chat_nach_name(name: str) -> tuple[dict | None, str]:
    treffer = whatsapp.finden(name)
    if len(treffer) == 1:
        return treffer[0], ""
    if not treffer:
        return None, f"Einen freigegebenen WhatsApp-Chat „{name}“ gibt es nicht."
    return None, "Es passen mehrere Chats: " + ", ".join(c.get("name", "") for c in treffer[:4]) + ". Eike soll den genauen Namen nennen."


def _klartext(r: dict) -> str:
    a = r["ausloeser"]
    wer = a.get("name") or "?"
    wenn = (f"Wenn {wer} dir auf {KANAL_NAME[r['kanal']]} schreibt" if r["kanal"] != "zeit"
            else f"Zu dieser Zeit ({zeit.beschreibung(a)})")
    zeilen = [f"Regel „{r['name']}“ (Nr. {r['id']}, {r['status']}): {wenn}, tue ich: {r['auftrag']}",
              "Erlaubt: " + "; ".join(AKTION_TEXT[x] for x in r["aktionen"]) + ".",
              "Empfänger (fest): " + (", ".join(e["name"] for e in r["empfaenger"]) or "keine zusätzlichen") + ".",
              f"Limits: höchstens {r['limit_stunde']} Läufe pro Stunde, {r['limit_tag']} pro Tag; nach 3 Fehlern in Folge pausiert sie."]
    return "\n".join(zeilen)


_ENTWURF_EINGABE: dict[int, str] = {}      # Regel-Id -> Eikes Äußerung, in der der Entwurf entstand (nur im Speicher)


@wirkt("skill", ok=("Erledigt:",), rueckfrage=("Entwurf",), kopf_ok="Festgehalten")
def regel_schreiben(name: str = "", kanal: str = "", wer: str = "", auftrag: str = "", erlaubt=None, empfaenger: str = "",
                    limit_stunde: int = 5, limit_tag: int = 20, eingabe: str = "", wann: str = "") -> str:
    kanal = str(kanal or "").strip().lower()
    if kanal == "zeit":
        try:
            ausloeser = zeit.wann_lesen(wann, config.jetzt())
        except speicher.RegelFehler as exc:
            return NICHTS.format(grund=str(exc))
    elif kanal == "whatsapp":
        chat, grund = _chat_nach_name(str(wer or ""))
        if chat is None:
            return NICHTS.format(grund=grund)
        ausloeser = {"chat_id": chat["id"], "name": chat.get("name") or ""}
    elif kanal == "snapchat":
        p = _person_nach_name(str(wer or ""), vorname_reicht=True)
        if p is None:
            return NICHTS.format(grund=f"Eine Person „{wer}“ gibt es in den Kontakten nicht oder nicht eindeutig. Nenn Eike das und lass ihn den genauen Namen sagen.")
        ausloeser = {"person_id": p["id"], "name": p["name"]}
    else:
        return NICHTS.format(grund="Der Kanal muss whatsapp, snapchat oder zeit sein.")
    liste = [a for a in (erlaubt if isinstance(erlaubt, (list, tuple)) else [erlaubt]) if a]
    emp = []
    for n in [x.strip() for x in str(empfaenger or "").split(",") if x.strip()]:
        if " ".join(n.casefold().split()) in _EIKE_NAMEN:    # Eike ist immer erlaubt (snap_senden), kein fester Empfänger
            continue
        p = _person_nach_name(n)
        if p is not None:
            emp.append({"art": "person", "id": p["id"], "name": p["name"]})
            continue
        chat, grund = _chat_nach_name(n)
        if chat is None:
            return NICHTS.format(grund=f"Empfänger „{n}“: {grund}")
        emp.append({"art": "chat", "id": chat["id"], "name": chat.get("name") or ""})
    if emp and "antwort_an_empfaenger" not in liste and "snap_senden" not in liste:
        return NICHTS.format(grund="Empfänger brauchen die Aktion antwort_an_empfaenger oder snap_senden.")
    if "snap_senden" in liste and any(e["art"] == "chat" for e in emp) and "antwort_an_empfaenger" not in liste:
        return NICHTS.format(grund="Ein Snap geht nur an Personen, nicht an einen WhatsApp-Chat.")
    if "antwort_an_empfaenger" in liste and not emp:
        return NICHTS.format(grund="Für antwort_an_empfaenger fehlen die Empfänger.")
    try:
        r = speicher.anlegen(name, kanal, ausloeser, auftrag, emp, liste, limit_stunde=limit_stunde, limit_tag=limit_tag)
    except speicher.RegelFehler as exc:
        return NICHTS.format(grund=str(exc))
    _ENTWURF_EINGABE[r["id"]] = (eingabe or "").strip()
    return ("Entwurf angelegt, gilt noch NICHT. Lies Eike genau das vor und frag nach seinem Ja; erst dann regel_bestaetigen:\n"
            + _klartext(r))


def _eindeutig(rid, erlaubt: tuple[str, ...]) -> tuple[dict | None, str]:
    try:
        r = speicher.holen(int(rid))
    except (TypeError, ValueError):
        r = None
    if r is None:
        return None, NICHTS.format(grund="Diese Regel gibt es nicht. Nimm regeln_lesen.")
    if r["status"] not in erlaubt:
        return None, NICHTS.format(grund=f"Die Regel steht auf „{r['status']}“.")
    return r, ""


@wirkt("skill", ok=("Erledigt:",), kopf_ok="Bestätigt")
def regel_bestaetigen(id, zustimmung: bool = False, eingabe: str = "") -> str:  # noqa: A002
    r, absage = _eindeutig(id, ("entwurf",))
    if r is None:
        return absage
    if (eingabe or "").strip() and _ENTWURF_EINGABE.get(r["id"]) == (eingabe or "").strip():
        return ("Noch nicht aktiv: Der Entwurf entstand in derselben Nachricht, in der Eike „ja“ gesagt hat. Lies ihm den Entwurf vor und frag "
                "nach seinem Ja in einer neuen Nachricht.\n" + _klartext(r))
    if not zustimmung:
        return ("Noch nicht aktiv. Lies Eike den Entwurf vor und frag nach seinem Ja; es zählt nur ein ausdrückliches Ja in seiner "
                "eigenen Nachricht.\n" + _klartext(r))
    try:
        r = speicher.bestaetigen(r["id"])
    except speicher.RegelFehler as exc:
        return NICHTS.format(grund=str(exc))
    return f"Erledigt: Die Regel „{r['name']}“ ist aktiv. Sag Eike kurz, was sie tut."


regel_bestaetigen.kennt_zustimmung = True
regel_bestaetigen.kennt_eingabe = True
regel_schreiben.kennt_eingabe = True


@wirkt("skill", ok=("Erledigt:",), kopf_ok="Zurückgenommen")
def regel_widerrufen(id) -> str:  # noqa: A002
    r, absage = _eindeutig(id, ("entwurf", "aktiv", "pausiert"))
    if r is None:
        return absage
    speicher.setzen(r["id"], "widerrufen")
    return f"Erledigt: Die Regel „{r['name']}“ gilt nicht mehr."


@wirkt("skill", ok=("Erledigt:",), kopf_ok="Gelöscht")
def regel_streichen(id, zustimmung: bool = False) -> str:  # noqa: A002
    r, absage = _eindeutig(id, tuple(speicher.STATUS))
    if r is None:
        return absage
    if not zustimmung:
        return (f"Noch nicht gelöscht: Das Löschen ist endgültig (die Regel „{r['name']}“ und ihre Läufe wären weg). Frag Eike ausdrücklich, ob "
                "ich sie wirklich löschen soll; es zählt nur ein Ja in seiner eigenen Nachricht.")
    speicher.loeschen(r["id"])
    return f"Erledigt: Die Regel „{r['name']}“ ist gelöscht, samt ihren Läufen."


regel_streichen.kennt_zustimmung = True


def regeln_lesen() -> str:
    regeln = [r for r in speicher.liste() if r["status"] != "ersetzt"]
    if not regeln:
        return "Es gibt noch keine Regeln."
    zeilen = []
    for r in regeln:
        letzter = next((l for l in speicher.laeufe(200) if l["regel_id"] == r["id"]), None)
        zeilen.append(f"Nr. {r['id']} „{r['name']}“ — {r['status']} — {zeit.ausloeser_text(r)} — letzter Lauf: "
                      + (f"{letzter['zeit'][:16].replace('T', ' ')} {letzter['status']}" if letzter else "noch keiner"))
    return "\n".join(zeilen)


NICHT_NACH_FREMDTEXT = {"regel_schreiben", "regel_bestaetigen", "regel_widerrufen", "regel_streichen"}
WERKZEUGE = {"regel_schreiben": regel_schreiben, "regel_bestaetigen": regel_bestaetigen, "regel_widerrufen": regel_widerrufen, "regel_streichen": regel_streichen,
             "regeln_lesen": regeln_lesen}
