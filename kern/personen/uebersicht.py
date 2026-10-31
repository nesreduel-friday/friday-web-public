"""Was die Kontakte-Seite je Person zeigt, ohne dass Eike es zusammensuchen muss (13.10.2026).

Nur lesend und nur Anzeige: Nichts hier erlaubt oder sperrt etwas. Gesendet wird weiter nur über den geprüften Snapchat-Weg;
`senden_erlaubt` im Register gilt seit 04.10.2026 nicht mehr und wird deshalb nirgends gezeigt."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from kern.personen import rechte, register
from kern.skills import primitive

log = logging.getLogger("friday.personen")

ALT_TAGE = 14   # so lange gilt eine Freundschaftsprüfung am Handy als frisch


def _tage(epoche: float | None, jetzt: float) -> str:
    if not epoche:
        return ""
    t = int((jetzt - epoche) // 86400)
    return "heute" if t <= 0 else ("gestern" if t == 1 else f"vor {t} Tagen")


def snapchat_status(person: dict, *, stand: dict | None, fehlschlag: dict | None, anfrage: dict | None,
                    anschluss_an: bool, schreibrecht: bool, jetzt: float, austausch: dict | None = None) -> dict:
    """Ampel für „Kann Friday dieser Person in Snapchat schreiben?“ — {stufe: gruen|gelb|rot|grau, titel, grund, geprueft}.
    Grün nur mit Beweis (Chatliste zeigt den Kamera-Knopf); alles Unklare ist gelb, nie grün."""
    def antwort(stufe, titel, grund, geprueft=None):
        return {"stufe": stufe, "titel": titel, "grund": grund, "geprueft": geprueft}

    if person.get("pausiert"):
        return antwort("rot", "Pausiert", "Friday antwortet dieser Person nicht und schreibt ihr nicht, bis du sie wieder aktivierst.")
    if not (person.get("kanaele") or {}).get("snapchat"):
        return antwort("grau", "Kein Snapchat-Name", "Ohne den Anzeigenamen aus Snapchat erkennt Friday die Person dort nicht.")
    if not anschluss_an:
        return antwort("rot", "Snapchat ist aus", "Der Snapchat-Anschluss ist in den Freigaben ausgeschaltet.")
    zeit = float(stand["zeit"]) if stand and stand.get("zeit") else None
    if fehlschlag and float(fehlschlag.get("zeit") or 0) > (zeit or 0):
        return antwort("rot", "Chat nicht gefunden",
                       "Das Handy fand beim letzten Nachsehen keinen Chat mit diesem Namen. Steht der Name genau so in Snapchat, "
                       "und sind beide befreundet?", float(fehlschlag["zeit"]))
    wann = _tage(zeit, jetzt)
    if stand and stand.get("stand") == "befreundet":
        if not schreibrecht:
            return antwort("gelb", "Befreundet, Schreiben aus",
                           "Das Schreibrecht für Snapchat ist in den Freigaben aus. Geprüft " + wann + ".", zeit)
        alt = (jetzt - (zeit or 0)) > ALT_TAGE * 86400
        return antwort("gruen", "Kann schreiben", f"Befreundet (Kamera-Knopf in der Chatliste), geprüft {wann}."
                       + (" Die Prüfung ist schon älter — bei Zweifel neu prüfen." if alt else ""), zeit)
    if stand and stand.get("stand") == "einseitig":
        return antwort("gelb", "Hat Friday noch nicht hinzugefügt",
                       f"Snapchat meldet es (geprüft {wann}). Bis zur Annahme kommen Nachrichten nicht sicher an.", zeit)
    # Der Chat war offen, Snapchat zeigte keine Warnung („noch nicht hinzugefügt“) — und es wurde in BEIDE Richtungen geschrieben:
    # Die Person hat Friday geschrieben und Friday ihr (das Handy bestätigt jedes Senden). Das ist ein Beleg (14.10.2026).
    beide = bool(austausch and austausch.get("person") and austausch.get("friday"))
    if stand and stand.get("stand") == "kein_hinweis" and beide:
        if not schreibrecht:
            return antwort("gelb", "Befreundet, Schreiben aus",
                           "Ihr habt euch geschrieben, aber das Schreibrecht für Snapchat ist in den Freigaben aus.", zeit)
        return antwort("gruen", "Kann schreiben",
                       f"Ihr habt euch in beide Richtungen geschrieben und Snapchat zeigt keinen Hinweis auf eine einseitige "
                       f"Freundschaft (Chat geprüft {wann}). Mit „Am Handy prüfen“ siehst du es zusätzlich an der Chatliste.", zeit)
    if stand:
        return antwort("gelb", "Freundschaft nicht bewiesen",
                       f"Der Chat ist offen, Snapchat meldet kein „noch nicht hinzugefügt“ (geprüft {wann}). "
                       "Ob die Person Friday hinzugefügt hat, steht damit nicht fest.", zeit)
    if anfrage and anfrage.get("zustand") == "angenommen":
        return antwort("gelb", "Anfrage angenommen", "Die Person hat die Anfrage angenommen; zum Beweis einmal am Handy prüfen.")
    if anfrage:
        return antwort("gelb", "Anfrage wartet", "Die Freundschaftsanfrage ist raus. Friday fragt nicht zweimal.")
    return antwort("gelb", "Noch nicht geprüft", "Das Handy hat den Chat noch nie angesehen. „Am Handy prüfen“ klärt es.")


def _iso_epoche(text: str | None) -> float | None:
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() if text else None
    except (TypeError, ValueError):
        return None


def letzter_austausch(roh: dict | None, jetzt: float) -> dict:
    """{"zeit": Epoche|None, "von": "person"|"friday"|None, "person": Epoche|None, "friday": Epoche|None} aus dem Protokoll."""
    roh = roh or {}
    p, f = _iso_epoche(roh.get("du")), _iso_epoche(roh.get("friday"))
    zeit = max((z for z in (p, f) if z), default=None)
    von = None if zeit is None else ("person" if zeit == p else "friday")
    return {"zeit": zeit, "von": von, "person": p, "friday": f}


def _option_beschreibung(werkzeug: str, name: str) -> str:
    return ((primitive.PERSONENFAEHIG.get(werkzeug, {}).get("optionen") or {}).get(name) or {}).get("beschreibung", "")


def arbeitsweisen(person: dict) -> list[dict]:
    """Aktive Skills, die diese Person treffen (das, was Eike mit Friday für sie vereinbart hat, z. B. Filmwünsche)."""
    from kern.skills import zugriff
    try:
        skills = zugriff.skills_fuer(person)
    except Exception:  # noqa: BLE001 — die Seite lebt auch ohne Skill-Speicher
        log.exception("Arbeitsweisen")
        return []
    aus = []
    for s in skills:
        aus.append({
            "id": s["id"], "name": s["name"], "zweck": s["zweck"], "anweisung": s["anweisung"],
            "werkzeuge": [{"name": w, "text": primitive.PERSONENFAEHIG.get(w, {}).get("beschreibung", w)} for w in s["werkzeuge"]],
            "optionen": [{"name": k, "wert": str(v), "text": _option_beschreibung(w_, k)}
                         for k, v in (s.get("optionen") or {}).items() for w_ in s["werkzeuge"][:1]],
        })
    return aus


def rechte_gruppiert() -> list[dict]:
    return [{"name": n, "text": t, "gruppe": rechte.GRUPPEN.get(n, "Weiteres"), "eikes_daten": n in rechte.EIKES_DATEN}
            for n, t in rechte.FREMDEN_ERLAUBT.items()]
