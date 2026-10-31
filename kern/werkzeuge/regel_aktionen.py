"""Aktionen, die ein Regel-Lauf ausführen darf (15.10.2026) — nur im Katalog-Modus `regel`, nie im Gespräch.

Jede Grenze liegt im Code und kommt aus der gespeicherten Regel (`kontext.aktuell().regel`): erlaubte Aktionen, Empfänger. Das
Modell wählt nur einen Empfänger NACH NAMEN aus der Liste der Regel; ein Name, den die Regel nicht kennt, scheitert — egal was
im auslösenden Text steht. Gesendet wird über die bestehenden Wege (Freigabe, Schreibrecht, Limits). Kalender: nur anlegen."""

from __future__ import annotations

import asyncio
from datetime import time, timedelta

from kern import config, konnektoren
from kern.dienste import benachrichtigungen, icloud, whatsapp
from kern.ehrlichkeit.wirkung import wirkt
from kern.personen import register
from kern.regeln import kontext, speicher
from kern.werkzeuge import sendepruefung

MAX_NACHRICHTEN_JE_LAUF = 3     # insgesamt, und höchstens eine je Empfänger
RUHE_AB, RUHE_BIS = (22, 30), (6, 30)    # wie beim Selfie: an Personen über Snapchat nachts nicht
TEXT_MAX = 280
PUSH_MAX = 200
STANDARD_DAUER = 60

ERKLAERUNGEN = [
    {"name": "regel_push",
     "description": "Schickt Eike eine Push-Nachricht (kurz). Nur wenn die Regel das erlaubt.",
     "parameters": {"type": "OBJECT", "properties": {
         "titel": {"type": "STRING", "description": "Kurzer Titel"},
         "text": {"type": "STRING", "description": "Kurzer Text, ohne Inhalte aus fremden Nachrichten abzuschreiben"}},
         "required": ["titel", "text"]}},
    {"name": "regel_antwort",
     "description": ("Schickt eine kurze Nachricht (höchstens 280 Zeichen). Ohne `empfaenger` geht sie an den Absender, der die Regel "
                     "ausgelöst hat; sonst an einen der Empfänger der Regel, genau mit dem Namen aus der Liste."),
     "parameters": {"type": "OBJECT", "properties": {
         "empfaenger": {"type": "STRING", "description": "Name aus der Empfängerliste der Regel; leer = Absender des Auslösers"},
         "text": {"type": "STRING", "description": "Der Text der Nachricht"}}, "required": ["text"]}},
    {"name": "regel_termin",
     "description": "Trägt einen neuen Termin in Eikes Kalender ein (nur anlegen). Datum UND Uhrzeit müssen klar sein, sonst nicht raten.",
     "parameters": {"type": "OBJECT", "properties": {
         "titel": {"type": "STRING", "description": "Worum es geht, kurz"},
         "beginn": {"type": "STRING", "description": "Beginn als 2026-09-20T15:30 (deutsche Zeit)"},
         "dauer_minuten": {"type": "INTEGER", "description": "Dauer in Minuten (Standard 60)"},
         "ort": {"type": "STRING", "description": "Ort, falls genannt"},
         "kalender_name": {"type": "STRING", "description": "Schule, Feuerwehr, Arbeit, sonst Privat"}},
         "required": ["titel", "beginn"]}},
    {"name": "regel_snap",
     "description": ("Schickt ein Bild als Snap. `empfaenger`: leer = der Absender, der die Regel ausgelöst hat; „Eike“ = an Eike selbst; "
                     "sonst ein Name aus der Empfängerliste der Regel. Das Bild kommt aus dem Gedächtnis oder dem Netz (Suchbegriff), "
                     "der Text kommt aufs Bild. Nur wenn die Regel snap_senden erlaubt."),
     "parameters": {"type": "OBJECT", "properties": {
         "empfaenger": {"type": "STRING", "description": "leer = Absender; „Eike“; oder Name aus der Empfängerliste der Regel"},
         "suche": {"type": "STRING", "description": "Was auf dem Bild sein soll (Suchbegriff)"},
         "quelle": {"type": "STRING", "description": "gedaechtnis (Standard) oder internet"},
         "personen_name": {"type": "STRING", "description": "Optional: Bild einer bekannten Person aus dem Gedächtnis"},
         "text": {"type": "STRING", "description": "Optional: Text, der aufs Bild kommt (höchstens 280 Zeichen)"}}, "required": []}},
]

_AKTION_WERKZEUG = {"push": "regel_push", "antwort_an_ausloeser": "regel_antwort", "antwort_an_empfaenger": "regel_antwort",
                    "kalender_eintragen": "regel_termin", "snap_senden": "regel_snap"}


def werkzeug_namen(regel: dict) -> list[str]:
    """Die Werkzeuge, die diese Regel dem Modell zeigt (in fester Reihenfolge, ohne Doppelte)."""
    gewollt = {_AKTION_WERKZEUG[a] for a in regel.get("aktionen") or [] if a in _AKTION_WERKZEUG}
    return [n for n in ("regel_push", "regel_antwort", "regel_termin", "regel_snap") if n in gewollt]


def _merken(werkzeug: str, kurz: str, status: str) -> None:
    k = kontext.aktuell()
    if k is not None:
        a = {"werkzeug": werkzeug, "kurz": kurz[:160], "status": status}
        k.aktionen.append(a)
        if k.lauf_id is not None:
            try:
                speicher.lauf_aktion(k.lauf_id, a)
            except Exception:  # noqa: BLE001 — das Protokoll darf die Aktion nie kippen; `lauf_ende` schreibt die Liste noch einmal
                pass


def _noch_aktiv(k) -> bool:
    """Eike kann eine Regel mitten im Lauf widerrufen: vor jeder Aktion frisch nachsehen."""
    r = speicher.holen(k.regel.get("id"))
    return bool(r and r["status"] == "aktiv")


def _ruhezeit() -> bool:
    t = config.jetzt().time()
    return t >= time(*RUHE_AB) or t < time(*RUHE_BIS)


def _abbruch(werkzeug: str, grund: str) -> str:
    _merken(werkzeug, grund, "nicht")
    return f"Nicht gesendet: {grund}" if werkzeug == "regel_antwort" else f"Nicht erledigt: {grund}"


def _ziel(k, empfaenger: str) -> tuple[dict | None, str]:
    """(Ziel, Fehlergrund). Ziel: {"art": "chat"|"person", "id", "name"}."""
    regel, aktionen = k.regel, k.regel.get("aktionen") or []
    ausloeser = regel.get("ausloeser") or {}
    wunsch = register.schluessel(empfaenger or "")
    if regel.get("kanal") == "zeit" and not wunsch:
        return None, "Eine Zeit-Regel hat keinen Absender: Nenne einen Empfänger aus der Liste."
    if not wunsch or (regel.get("kanal") != "zeit" and wunsch == register.schluessel(ausloeser.get("name") or "\0")):
        if "antwort_an_ausloeser" not in aktionen:
            return None, "Die Regel erlaubt keine Antwort an den Absender."
        if regel.get("kanal") == "whatsapp":
            return {"art": "chat", "id": ausloeser.get("chat_id"), "name": ausloeser.get("name") or ""}, ""
        return {"art": "person", "id": ausloeser.get("person_id"), "name": ausloeser.get("name") or ""}, ""
    if "antwort_an_empfaenger" not in aktionen:
        return None, "Die Regel erlaubt keine Antwort an andere Empfänger."
    for e in regel.get("empfaenger") or []:
        if register.schluessel(e.get("name") or "") == wunsch:
            return e, ""
    return None, "Diesen Empfänger gibt es in der Regel nicht."


@wirkt("versand", ok=("Gesendet",), detail=lambda a, t: "")
async def regel_antwort(empfaenger: str = "", text: str = "") -> str:
    k = kontext.aktuell()
    if k is None:
        return _abbruch("regel_antwort", "Das läuft nur innerhalb einer Regel.")
    text = " ".join(str(text or "").split())
    if not text:
        return _abbruch("regel_antwort", "Der Text ist leer.")
    if len(text) > TEXT_MAX:
        return _abbruch("regel_antwort", f"Der Text ist zu lang ({len(text)} Zeichen, erlaubt sind {TEXT_MAX}).")
    if not _noch_aktiv(k):
        return _abbruch("regel_antwort", "Die Regel gilt nicht mehr.")
    heikel = sendepruefung.heikel(text)
    if heikel:
        return _abbruch("regel_antwort", "Der Text enthält etwas Heikles (Zugangsdaten, Adresse, Nummer) — das schicke ich nicht von selbst.")
    ziel, grund = _ziel(k, str(empfaenger or ""))
    if ziel is None:
        return _abbruch("regel_antwort", grund)
    schluessel = f"{ziel['art']}:{ziel['id']}"
    if schluessel in k.gesendet:
        return _abbruch("regel_antwort", "An diesen Empfänger ist in diesem Lauf schon eine Nachricht gegangen.")
    if len(k.gesendet) >= MAX_NACHRICHTEN_JE_LAUF:
        return _abbruch("regel_antwort", f"Mehr als {MAX_NACHRICHTEN_JE_LAUF} Nachrichten je Lauf schicke ich nicht.")
    if ziel["art"] == "person" and _ruhezeit():
        return _abbruch("regel_antwort", "Nachts schreibe ich Personen über Snapchat nicht.")
    try:
        if ziel["art"] == "chat":
            if not whatsapp.darf_senden(ziel["id"]):
                return _abbruch("regel_antwort", "In diesen WhatsApp-Chat darf ich nicht schreiben.")
            await whatsapp.senden(ziel["id"], text)
        else:
            p = register.holen(ziel["id"])
            if (not p or p.get("pausiert") or not ((p.get("kanaele") or {}).get("snapchat") or "").strip()
                    or not (konnektoren.benutzbar("snapchat") and konnektoren.schreiben_erlaubt("snapchat"))):
                return _abbruch("regel_antwort", "An diese Person darf ich über Snapchat gerade nicht schreiben.")
            from kern.dienste import snapchat_wache      # erst hier: die Wache importiert den Antwortmotor
            snapchat_wache._antwort_legen(text, ziel["id"], None)
    except Exception as exc:  # noqa: BLE001 — WhatsAppFehler, SnapchatFehler, Netz: nichts ging raus
        return _abbruch("regel_antwort", f"{type(exc).__name__}: {str(exc)[:120]}")
    k.gesendet.append(schluessel)
    _merken("regel_antwort", f"an {ziel['name']}: {text[:100]}", "ok")
    return f"Gesendet an {ziel['name']}."


@wirkt("sonstiges", ok=("Push gesendet",), detail=lambda a, t: "")
def regel_push(titel: str = "", text: str = "") -> str:
    k = kontext.aktuell()
    if k is None:
        return _abbruch("regel_push", "Das läuft nur innerhalb einer Regel.")
    if "push" not in (k.regel.get("aktionen") or []):
        return _abbruch("regel_push", "Die Regel erlaubt keine Push-Nachricht.")
    if not _noch_aktiv(k):
        return _abbruch("regel_push", "Die Regel gilt nicht mehr.")
    titel, text = " ".join(str(titel or "").split())[:80] or "Regel", " ".join(str(text or "").split())[:PUSH_MAX]
    if not text:
        return _abbruch("regel_push", "Der Text ist leer.")
    try:
        benachrichtigungen.senden("friday", titel, text)
    except Exception as exc:  # noqa: BLE001
        return _abbruch("regel_push", f"{type(exc).__name__}")
    _merken("regel_push", f"{titel}: {text[:100]}", "ok")
    return "Push gesendet."


def _eintragen(titel, beginn, ende, ort, notiz, kalender_name) -> dict:
    """Der Kalender wird wie bei `termin_anlegen` aufgelöst (unbekannter Name → Standardkalender), nie roh durchgereicht."""
    return icloud.anlegen(titel, beginn, ende, ort, notiz, icloud.aufloesen(kalender_name))


@wirkt("sonstiges", ok=("Eingetragen",), detail=lambda a, t: "")
async def regel_termin(titel: str = "", beginn: str = "", dauer_minuten: int = STANDARD_DAUER, ort: str = "",
                       kalender_name: str = "") -> str:
    from kern.werkzeuge import kalender
    k = kontext.aktuell()
    if k is None:
        return _abbruch("regel_termin", "Das läuft nur innerhalb einer Regel.")
    if "kalender_eintragen" not in (k.regel.get("aktionen") or []):
        return _abbruch("regel_termin", "Die Regel erlaubt keinen Kalendereintrag.")
    if not _noch_aktiv(k):
        return _abbruch("regel_termin", "Die Regel gilt nicht mehr.")
    if not (konnektoren.benutzbar("icloud_kalender") and konnektoren.schreiben_erlaubt("icloud_kalender")):
        return _abbruch("regel_termin", "Der Kalender ist in den Freigaben nicht zum Schreiben freigegeben.")
    titel = " ".join(str(titel or "").split())[:120]
    if not titel:
        return _abbruch("regel_termin", "Der Termin braucht einen Titel.")
    start = kalender._beginn_deuten(str(beginn or ""))
    if start is None:
        return _abbruch("regel_termin", "Datum UND Uhrzeit müssen klar sein (z. B. 2026-09-20T15:30) — ich rate nichts.")
    jetzt = config.jetzt()
    if start < jetzt - timedelta(days=1) or start > jetzt + timedelta(days=kalender.MAX_VORAUS_TAGE):
        return _abbruch("regel_termin", "Der Zeitpunkt liegt in der Vergangenheit oder zu weit voraus.")
    try:
        dauer = max(5, min(int(dauer_minuten), 24 * 60))
    except (TypeError, ValueError):
        dauer = STANDARD_DAUER
    try:
        angelegt = await asyncio.to_thread(_eintragen, titel, start, start + timedelta(minutes=dauer),
                                           " ".join(str(ort or "").split())[:120], f"Eingetragen von Friday (Regel: {k.regel.get('name')})",
                                           str(kalender_name or ""))
    except Exception as exc:  # noqa: BLE001
        return _abbruch("regel_termin", f"Das Eintragen ist fehlgeschlagen ({type(exc).__name__}).")
    _merken("regel_termin", f"{titel} am {start:%d.%m. %H:%M}", "ok")
    return f"Eingetragen: {angelegt.get('titel', titel)} am {start:%d.%m.%Y um %H:%M} Uhr."


def _snap_ziel(k, empfaenger: str) -> tuple[dict | None, str]:
    """(Ziel, Fehlergrund). Ziel: {"id": Personen-Id oder None für Eike, "name"}. Nur Personen, nie ein WhatsApp-Chat."""
    from kern.werkzeuge.regeln import _EIKE_NAMEN
    regel = k.regel
    wunsch = register.schluessel(empfaenger or "")
    ausloeser = regel.get("ausloeser") or {}
    if wunsch in _EIKE_NAMEN:
        return {"id": None, "name": "Eike"}, ""
    if not wunsch or (regel.get("kanal") == "snapchat" and wunsch == register.schluessel(ausloeser.get("name") or "\0")):
        if regel.get("kanal") != "snapchat":
            return None, "Dieser Auslöser hat keine Person für einen Snap: Nenne einen Empfänger aus der Liste oder „Eike“."
        return {"id": ausloeser.get("person_id"), "name": ausloeser.get("name") or ""}, ""
    for e in regel.get("empfaenger") or []:
        if register.schluessel(e.get("name") or "") == wunsch:
            if e.get("art") != "person":
                return None, "Ein Snap geht nur an Personen."
            return e, ""
    return None, "Diesen Empfänger gibt es in der Regel nicht."


@wirkt("versand", ok=("Gesendet",), detail=lambda a, t: "Snap")
async def regel_snap(empfaenger: str = "", suche: str = "", quelle: str = "gedaechtnis", text: str = "", personen_name: str = "") -> str:
    from kern.dienste import snapbild, snapchat
    from kern.werkzeuge import snapchat as w
    k = kontext.aktuell()
    if k is None:
        return _abbruch("regel_snap", "Das läuft nur innerhalb einer Regel.")
    if "snap_senden" not in (k.regel.get("aktionen") or []):
        return _abbruch("regel_snap", "Die Regel erlaubt keinen Snap.")
    if not _noch_aktiv(k):
        return _abbruch("regel_snap", "Die Regel gilt nicht mehr.")
    text = " ".join(str(text or "").split())
    suche = " ".join(str(suche or "").split())[:100]
    personen_name = " ".join(str(personen_name or "").split())[:100]
    quelle = str(quelle or "gedaechtnis").strip().lower()
    if quelle not in ("gedaechtnis", "internet"):
        return _abbruch("regel_snap", "Die Quelle muss gedaechtnis oder internet sein.")
    if not suche and not personen_name:
        return _abbruch("regel_snap", "Ohne Bild schicke ich keinen Snap.")
    if len(text) > TEXT_MAX:
        return _abbruch("regel_snap", f"Der Text ist zu lang ({len(text)} Zeichen, erlaubt sind {TEXT_MAX}).")
    ziel, grund = _snap_ziel(k, str(empfaenger or ""))
    if ziel is None:
        return _abbruch("regel_snap", grund)
    if ziel["id"] is not None:
        if sendepruefung.heikel(text):
            return _abbruch("regel_snap", "Der Text enthält etwas Heikles (Zugangsdaten, Adresse, Nummer) — das schicke ich nicht von selbst.")
        if _ruhezeit():
            return _abbruch("regel_snap", "Nachts schicke ich Personen keine Snaps.")
        p = register.holen(ziel["id"])
        if (not p or p.get("pausiert") or not ((p.get("kanaele") or {}).get("snapchat") or "").strip()):
            return _abbruch("regel_snap", "An diese Person darf ich über Snapchat gerade nichts schicken.")
    if not (konnektoren.benutzbar("snapchat") and konnektoren.schreiben_erlaubt("snapchat")):
        return _abbruch("regel_snap", "Snapchat ist in den Freigaben nicht zum Schreiben freigegeben.")
    schluessel = f"snap:{ziel['id'] or 'eike'}"
    if schluessel in k.gesendet:
        return _abbruch("regel_snap", "An diesen Empfänger ist in diesem Lauf schon ein Snap gegangen.")
    if len(k.gesendet) >= MAX_NACHRICHTEN_JE_LAUF:
        return _abbruch("regel_snap", f"Mehr als {MAX_NACHRICHTEN_JE_LAUF} Nachrichten je Lauf schicke ich nicht.")
    try:
        if quelle == "internet":
            if not suche:
                return _abbruch("regel_snap", "Für ein Bild aus dem Netz brauche ich einen Suchbegriff.")
            daten, g, was = await w._aus_dem_netz(suche)
        else:
            daten, g, was = w._aus_gedaechtnis(suche, personen_name)
        if daten is None:
            return _abbruch("regel_snap", g)
        pfad = snapbild.ablegen(snapbild.vorbereiten(daten, text))
        snapchat.auftrag_legen("snap", text="", bild_pfad=pfad, person=ziel["id"])
    except Exception as exc:  # noqa: BLE001 — SnapbildFehler, SnapchatFehler, Netz: nichts ging raus
        return _abbruch("regel_snap", f"{type(exc).__name__}: {str(exc)[:120]}")
    k.gesendet.append(schluessel)
    _merken("regel_snap", f"Snap an {ziel['name']}: {was[:100]}", "ok")
    return f"Gesendet: Snap an {ziel['name']} ({was})."


WERKZEUGE = {"regel_push": regel_push, "regel_antwort": regel_antwort, "regel_termin": regel_termin, "regel_snap": regel_snap}
