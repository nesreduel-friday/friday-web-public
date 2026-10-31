"""Werkzeug nur für fremde Personen: Rückfrage an Eike.

Eike selbst sieht es nie (`katalog.NUR_FREMDE`). Die Person, für die gefragt wird, setzt der Kern (`kontakt_id`), nie das
Modell. Die Antwort verrät nichts über Eike: Sie sagt nur, dass gefragt wurde."""

from __future__ import annotations

import asyncio

from kern.personen import PersonenFehler, rueckfragen

ERKLAERUNGEN = [{
    "name": "frage_eike",
    "description": ("Fragt Eike, ob du der Person etwas sagen darfst, das ihn oder andere betrifft und nicht freigegeben "
                    "ist. Benutzen, wenn sie danach fragt — statt es zu verraten oder zu leugnen. Eike entscheidet."),
    "parameters": {"type": "OBJECT", "properties": {
        "thema": {"type": "STRING", "description": "Worum es geht, in wenigen Wörtern, z. B. 'Geburtstag'"},
        "wunsch": {"type": "STRING", "description": "Was die Person genau wissen möchte, in einem Satz"},
    }, "required": ["thema"]},
},
    {
        "name": "termin_wunsch",
        "description": ("Schlägt Eike einen Termin mit der Person vor — trägt NICHTS ein. Nur benutzen, wenn sie nach "
                        "einem Treffen fragt und Tag und Uhrzeit klar sind (sonst frage_eike). Eike nimmt im Menü an "
                        "oder lehnt ab; bei Ja steht es mit Vermerk in seinem Kalender und du sagst es ihr."),
        "parameters": {"type": "OBJECT", "properties": {
            "titel": {"type": "STRING", "description": "Worum es geht, kurz, z.B. 'Kaffee trinken'"},
            "beginn": {"type": "STRING", "description": "Beginn als 2026-10-10T15:00 (Datum UND Uhrzeit)"},
            "dauer_minuten": {"type": "INTEGER", "description": "Dauer in Minuten (Standard 60)"},
            "ort": {"type": "STRING", "description": "Wo, falls genannt"},
        }, "required": ["titel", "beginn"]},
    },
]

NEUTRAL = ("Ich habe Eike gefragt. Sag der Person freundlich und ohne Einzelheiten, dass du das im Moment nicht sagen "
           "kannst und dich meldest, falls es freigegeben wird.")


async def frage_eike(thema: str, wunsch: str = "", kontakt_id: str = "") -> str:
    if not kontakt_id:
        raise ValueError("Ohne Person keine Rückfrage.")
    try:
        # `stellen` schickt einen Web-Push (bis 15 s Zeitlimit je Gerät): in einem Thread, damit eine fremde Person mit
        # ihren Rückfragen nie den ganzen Server (Eikes Chat, Sprache) anhält.
        await asyncio.to_thread(rueckfragen.stellen, kontakt_id, thema, wunsch)
    except PersonenFehler:
        pass  # zu viele offene, pausiert, leer: die Antwort an die Person bleibt dieselbe neutrale
    return NEUTRAL


frage_eike.kennt_kontakt = True


async def termin_wunsch(titel: str = "", beginn: str = "", dauer_minuten: int = 60,
                        ort: str = "", kontakt_id: str = "") -> str:
    if not kontakt_id:
        raise ValueError("Ohne Person kein Terminwunsch.")
    try:
        await asyncio.to_thread(rueckfragen.termin_wunsch_stellen, kontakt_id, titel, beginn,
                                dauer_minuten, ort)
    except PersonenFehler as exc:
        return str(exc)
    return ("Ich habe Eike gefragt. Sag der Person freundlich, dass du dich meldest, "
            "sobald er entschieden hat.")


termin_wunsch.kennt_kontakt = True

WERKZEUGE = {"frage_eike": frage_eike, "termin_wunsch": termin_wunsch}
# Was die Person fragt, ist fremder Text; das Ergebnis (neutraler Satz) ist es nicht.
FREMDE_WERKZEUGE: set = set()
