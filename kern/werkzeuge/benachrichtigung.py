"""Benachrichtigungen im Gespräch schalten.

Eike soll nicht in die Einstellungen tippen müssen, um das Morgenbriefing
loszuwerden — „Friday, schick mir morgens nichts mehr“ reicht. Diese Werkzeuge
hängen an keinem Konnektor: Es geht um Fridays eigene Schalter, nicht um einen
fremden Dienst.

Geändert wird sofort und ohne Rückfrage. Das ist Absicht und kein Widerspruch zum
Kalender: Ein Schalter ist in einem Satz zurückgenommen, ein abgeschickter Termin
nicht. Was Friday tut, sagt sie im selben Atemzug — deshalb gibt jedes Werkzeug
den neuen Stand zurück und nicht nur „erledigt“.

Die Namen der Arten kennt nur der Server (`benachrichtigungen.ARTEN`). Ein
Modell, das sich eine ausdenkt, bekommt eine Fehlermeldung mit der echten Liste,
statt still ins Leere zu schalten.
"""

from __future__ import annotations

from kern.dienste import benachrichtigungen as dienst

ERKLAERUNGEN = [
    {
        "name": "benachrichtigungen",
        "description": ("Sagt, welche Benachrichtigungen an sind, ob eine Ruhezeit gilt und ob "
                        "überhaupt ein Gerät angemeldet ist. Benutzen bei „was schickst du mir “, "
                        "„warum kommt nichts an“ oder bevor du etwas umstellst."),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "benachrichtigung_stellen",
        "description": ("Schaltet EINE Art von Benachrichtigung an oder aus. Benutzen bei „schick mir "
                        "morgens nichts mehr“, „sag mir Bescheid, wenn ein Video fertig ist“. Wenn du "
                        "nicht sicher bist, welche Art gemeint ist, erst benachrichtigungen aufrufen."),
        "parameters": {"type": "OBJECT", "properties": {
            "art": {"type": "STRING", "description": ("Die Kennung der Art: briefing, abendblick, "
                                                      "erinnerungen, fahrt, verspaetungen, garantie, "
                                                      "studio oder friday")},
            "an": {"type": "BOOLEAN", "description": "wahr zum Einschalten, falsch zum Ausschalten"},
        }, "required": ["art", "an"]},
    },
    {
        "name": "ruhezeit_stellen",
        "description": ("Stellt die Ruhezeit, in der keine Benachrichtigungen kommen. Erinnerungen und "
                        "Warnungen zu einer Fahrt, die gleich weg ist, kommen trotzdem — sag das dazu, "
                        "wenn Eike es einschaltet."),
        "parameters": {"type": "OBJECT", "properties": {
            "an": {"type": "BOOLEAN", "description": "Ruhezeit ein- oder ausschalten"},
            "ab": {"type": "STRING", "description": "Beginn als 22:30"},
            "bis": {"type": "STRING", "description": "Ende als 06:30"},
        }},
    },
]


def _arten_text() -> str:
    return ", ".join(a["id"] for a in dienst.ARTEN)


def _finden(wunsch: str) -> dict | None:
    """Die Art auch dann finden, wenn das Modell den Anzeigenamen nennt.

    Es sagt eher „Morgenbriefing" als „briefing" — und eine Fehlermeldung, bei der
    Eike hört, dass Friday ihr eigenes Briefing nicht kennt, wäre albern."""
    gesucht = (wunsch or "").strip().casefold()
    if not gesucht:
        return None
    for a in dienst.ARTEN:
        if gesucht in (a["id"], a["name"].casefold()):
            return a
    # Erst danach lockerer suchen, damit ein genauer Treffer immer gewinnt.
    treffer = [a for a in dienst.ARTEN if gesucht in a["name"].casefold() or gesucht in a["id"]]
    return treffer[0] if len(treffer) == 1 else None


async def benachrichtigungen() -> str:
    stand = dienst.uebersicht()
    an = [a["name"] for a in stand["arten"] if a["an"]]
    aus = [a["name"] for a in stand["arten"] if not a["an"]]
    zeilen = []
    if not stand["geraete"]:
        zeilen.append("Es ist kein Gerät für Benachrichtigungen angemeldet — bei Eike kommt gerade "
                      "nichts an. Er schaltet sie in den Einstellungen ein.")
    zeilen.append("An: " + (", ".join(an) if an else "nichts"))
    if aus:
        zeilen.append("Aus: " + ", ".join(aus))
    r = stand["ruhe"]
    zeilen.append(f"Ruhezeit: {r['ab']} bis {r['bis']}" if r["an"] else "Ruhezeit: aus")
    return "\n".join(zeilen)


async def benachrichtigung_stellen(art: str, an: bool) -> str:
    gemeint = _finden(art)
    if gemeint is None:
        return f"Die Art „{art}“ kenne ich nicht. Es gibt: {_arten_text()}."
    dienst.stellen(gemeint["id"], bool(an))
    return (f"„{gemeint['name']}“ ist jetzt {'an' if an else 'aus'}." if an else
            f"„{gemeint['name']}“ ist jetzt aus. Sag Bescheid, wenn du es zurückwillst.")


async def ruhezeit_stellen(an: bool | None = None, ab: str = "", bis: str = "") -> str:
    r = dienst.ruhe_stellen(ein=an, ab=(ab or None), bis=(bis or None))
    if not r["an"]:
        return "Die Ruhezeit ist aus — Benachrichtigungen kommen rund um die Uhr."
    return (f"Ruhezeit von {r['ab']} bis {r['bis']}. Erinnerungen und Warnungen zu einer Fahrt, "
            "die gleich weg ist, kommen trotzdem durch.")


# Ändert etwas und verlangt kein „ja“: nach fremdem Text im selben Zug gesperrt (katalog.ausfuehren).
NICHT_NACH_FREMDTEXT = {"benachrichtigung_stellen", "ruhezeit_stellen"}

WERKZEUGE = {"benachrichtigungen": benachrichtigungen,
             "benachrichtigung_stellen": benachrichtigung_stellen,
             "ruhezeit_stellen": ruhezeit_stellen}
