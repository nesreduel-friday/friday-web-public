"""Werkzeuge für Vorgaben — verbindliche Arbeitsanweisungen von Eike.

Anlegen, Ändern und Widerrufen nur in einem Zug, in dem Eike selbst spricht: Sie stehen in `NICHT_NACH_FREMDTEXT` und in keinem
Katalog für Fremde oder die Denkrunde. `vorgaben_lesen` darf auch die Denkrunde. Jedes Ergebnis nennt die aktuelle Gesamtliste, damit
auch eine laufende Sprachsitzung (Prompt nur beim Start gebaut) die Änderung sofort im Kontext hat.
"""

from __future__ import annotations

from kern.vorgaben import block, fach, riegel
from kern.ehrlichkeit.wirkung import wirkt

ERKLAERUNGEN = [
    {
        "name": "vorgabe_merken",
        "description": ("Hält eine verbindliche, dauerhafte Arbeitsanweisung von Eike fest — bei 'künftig', 'immer', 'nie', "
                        "'ab jetzt', 'wenn X, dann Y', 'bei Garantieanträgen nie ohne mich'. Nur wenn Eike sie in DIESER "
                        "Nachricht selbst sagt. Für bloße Fakten (Adresse, Vorliebe) nimmst du merken, nicht dieses Werkzeug. "
                        "Bereich: allgemein, antworten, garantie, snapchat, erinnerungen, schule oder sonstiges."),
        "parameters": {"type": "OBJECT", "properties": {
            "text": {"type": "STRING", "description": "Die Anweisung als ganzer Satz, höchstens 400 Zeichen"},
            "bereich": {"type": "STRING", "description": "allgemein, antworten, garantie, snapchat, erinnerungen, schule, sonstiges"},
        }, "required": ["text"]},
    },
    {
        "name": "vorgabe_aendern",
        "description": ("Ersetzt eine aktive Vorgabe durch einen neuen Wortlaut (die alte bleibt als ersetzt im Verlauf). "
                        "id = Kennung aus der Liste deiner festen Aufträge (Klammer vor dem Text) oder ein Stichwort aus ihrem Text."),
        "parameters": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING", "description": "Nummer oder Stichwort der Vorgabe"},
            "text": {"type": "STRING", "description": "Der neue Wortlaut, höchstens 400 Zeichen"},
        }, "required": ["id", "text"]},
    },
    {
        "name": "vorgabe_widerrufen",
        "description": ("Nimmt eine Vorgabe zurück, wenn Eike sie nicht mehr will ('das gilt nicht mehr', 'vergiss die Regel "
                        "mit …'). id = Nummer oder Stichwort."),
        "parameters": {"type": "OBJECT", "properties": {
            "id": {"type": "STRING", "description": "Nummer oder Stichwort der Vorgabe"},
        }, "required": ["id"]},
    },
    {
        "name": "vorgaben_lesen",
        "description": "Zeigt alle aktiven Vorgaben von Eike, nach Bereich gruppiert (optional nur ein Bereich).",
        "parameters": {"type": "OBJECT", "properties": {
            "bereich": {"type": "STRING", "description": "Optional: nur dieser Bereich"},
        }},
    },
]


def _liste() -> str:
    t = block.formatieren(fach.standard().aktive())
    return "Aktuell gelten (nur für dich, nicht vorlesen):\n" + t if t else "Eike hat dir bisher nichts fest aufgetragen."


def _eindeutig(id_oder_stichwort) -> tuple[dict | None, str]:
    treffer = fach.standard().finden(id_oder_stichwort)
    if not treffer:
        return None, "Dazu hat Eike dir nichts Passendes aufgetragen. Frag ihn kurz, was er meint.\n" + _liste()
    if len(treffer) > 1:
        return None, ("Das passt auf mehrere Aufträge; frag Eike kurz, welchen er meint, oder nimm die Kennung (intern, nicht nennen): "
                      + "; ".join(f"{v['id']} ({v['text'][:60]})" for v in treffer))
    return treffer[0], ""


@wirkt("vorgabe", ok=("Festgehalten",), detail=lambda a, t: a.get("text"))
def vorgabe_merken(text: str, bereich: str = "allgemein") -> str:
    if absage := riegel.aufhebung(text):
        return absage
    e = fach.standard().anlegen(text, bereich)
    if not e["ok"]:
        return f"Das hat nicht geklappt: {e['grund']} Es ist NICHTS festgehalten; sag Eike ehrlich, dass es noch nicht geklappt hat."
    return f"Festgehalten (Kennung {e['id']}, intern). Sag Eike kurz in deinen Worten, woran du dich ab jetzt hältst.\n{_liste()}"


@wirkt("vorgabe", ok=("Geändert",), kopf_ok="Geändert", detail=lambda a, t: a.get("text"))
def vorgabe_aendern(id, text: str) -> str:            # noqa: A002 (Name kommt vom Modell)
    if absage := riegel.aufhebung(text):
        return absage
    v, absage = _eindeutig(id)
    if v is None:
        return absage
    e = fach.standard().ersetzen(v["id"], text)
    if not e["ok"]:
        return f"Das hat nicht geklappt: {e['grund']} Es ist NICHTS geändert; sag Eike ehrlich, dass es noch nicht geklappt hat."
    return f"Geändert (neue Kennung {e['id']}, intern). Sag Eike kurz, was ab jetzt gilt.\n{_liste()}"


@wirkt("vorgabe", ok=("Zurückgenommen",), kopf_ok="Zurückgenommen")
def vorgabe_widerrufen(id) -> str:                    # noqa: A002
    v, absage = _eindeutig(id)
    if v is None:
        return absage
    e = fach.standard().widerrufen(v["id"])
    if not e["ok"]:
        return f"Das hat nicht geklappt: {e['grund']} Sag Eike ehrlich, dass es noch gilt."
    return f"Zurückgenommen. Sag Eike kurz, dass das nicht mehr gilt.\n{_liste()}"


def vorgaben_lesen(bereich: str = "") -> str:
    if (bereich or "").strip():
        t = block.formatieren(fach.standard().aktive(bereich))
        return t or f"Zum Thema {bereich} hat Eike dir nichts fest aufgetragen."
    return _liste()


# Ändern etwas und verlangen kein „ja“: nach Fremdtext im selben Zug gesperrt (katalog.ausfuehren). Nicht in FREMDEN_ERLAUBT,
# nicht in DENKEN_ERLAUBT (dort nur vorgaben_lesen).
NICHT_NACH_FREMDTEXT = {"vorgabe_merken", "vorgabe_aendern", "vorgabe_widerrufen"}

WERKZEUGE = {"vorgabe_merken": vorgabe_merken, "vorgabe_aendern": vorgabe_aendern,
             "vorgabe_widerrufen": vorgabe_widerrufen, "vorgaben_lesen": vorgaben_lesen}
