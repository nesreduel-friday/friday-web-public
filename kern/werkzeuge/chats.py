"""Chat-Werkzeuge — die ausgewerteten WhatsApp-Verläufe im Gespräch.

Rein lesend, immer verfügbar (kein Konnektor, keine fremden Zugangsdaten —
die Verläufe hat Eike selbst hochgeladen). Chatinhalte sind fremde Texte:
danach wird `merken` zum Vorschlag (FREMDE_WERKZEUGE).
"""

from __future__ import annotations

from kern.chats import ablage
from kern.personen import PersonenFehler, blick, register

MAX_TREFFER = 15

ERKLAERUNGEN = [
    {
        "name": "person_ueber",
        "description": ("ALLES, was Friday über einen Menschen weiß, aus allen Quellen zugleich (Verläufe, Kontakte, "
                        "Fahrkarten-Profile mit Anschrift, Langzeitgedächtnis): wer die Person für Eike ist, Themen, "
                        "Ereignisse, Geburtstag, Wohnort, Snapchat. Vorname, Nachname oder beides genügt. Bei "
                        "JEDER Frage zu einem Menschen zuerst benutzen. Nenne Eike nie, aus welchem Topf etwas kommt."),
        "parameters": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING", "description": "Name der Person, z.B. Kontakt P oder Kontakt A"},
        }, "required": ["name"]},
    },
    {
        "name": "person_korrigieren",
        "description": ("Korrigiert den Namen eines Menschen (Schreibweise, Nachname) überall, wo er als Person "
                        "geführt wird: Verläufe und Kontakte. NUR wenn Eike den richtigen Namen ausdrücklich "
                        "nennt („Nachname ist Pyttel“). Fahrkarten-Profile bleiben unberührt (amtliche Angaben)."),
        "parameters": {"type": "OBJECT", "properties": {
            "alt": {"type": "STRING", "description": "Wie die Person bisher heißt, z.B. 'Kontakt M' oder 'Kontakt M'"},
            "neu": {"type": "STRING", "description": "Der volle richtige Name, z.B. 'Kontakt M Pyttel'"},
        }, "required": ["alt", "neu"]},
    },
    {
        "name": "chat_suchen",
        "description": ("Sucht NUR in den WhatsApp-Verläufen nach Nachrichten zu einem Stichwort, neueste zuerst. "
                        "Snapchat und Gespräche mit Friday stehen dort nicht — dafür rueckblick. "
                        "Benutzen, um etwas Konkretes nachzulesen, das jemand geschrieben hat."),
        "parameters": {"type": "OBJECT", "properties": {
            "suche": {"type": "STRING", "description": "Suchwörter"},
            "person": {"type": "STRING", "description": "Nur Nachrichten von dieser Person (optional)"},
        }, "required": ["suche"]},
    },
]


GRAPH_FAKTEN = 6


async def _graph_fakten(name: str) -> list[str]:
    """Fakten aus dem Langzeitgedächtnis zu diesem Namen — leer, wenn es nicht erreichbar ist."""
    try:
        from kern.gedaechtnis import graph
        if not graph.url():
            return []
        fakten = await graph.suchen(name, anzahl=GRAPH_FAKTEN)
    except Exception:  # noqa: BLE001 — Gedächtnis weg = nur die übrigen Quellen, nie ein Absturz
        return []
    # Die Gedächtnissuche ist unscharf und liefert bei jedem Namen irgendetwas: nur Fakten, in denen
    # der Name (ein Wort davon) wirklich vorkommt, gehören zu dieser Person.
    worte = [w for w in blick._worte(name) if len(w) >= 3]
    return [f"- {f['fact']}" for f in fakten
            if f.get("fact") and not f.get("invalid_at")
            and set(worte) & set(blick._worte(str(f["fact"])))]       # ganze Wörter: „Kontakt O“ ≠ „Klasse“


async def person_ueber(name: str) -> str:
    """Alles über einen Menschen aus allen Quellen zugleich (Chats, Kontakte, Fahrkarten-Profile,
    Gedächtnis) — ohne Herkunftsangabe: Eike will keine Töpfe sehen."""
    gruppen, vorschlaege = blick.finden(name)
    if not gruppen:
        wissen = await _graph_fakten(name)
        if wissen:
            return f"Zu „{name}“ habe ich nur das aus dem Gedächtnis:\n" + "\n".join(wissen)
        if vorschlaege:
            return f"„{name}“ kenne ich nicht genau — meintest du " + " oder ".join(f"„{v}“" for v in vorschlaege) + "?"
        return f"Zu „{name}“ habe ich nichts — weder in Verläufen, Kontakten noch im Gedächtnis."
    teile = []
    for g in gruppen[:3]:
        text = blick.beschreiben(g)
        wissen = await _graph_fakten(blick.graph_name(g))
        if wissen:
            text += "\nAus dem Gedächtnis:\n" + "\n".join(wissen)
        teile.append(text)
    kopf = ""
    if len(gruppen) > 1:
        kopf = (f"„{name}“ passt auf {len(gruppen)} Menschen — nenne beim Antworten, wen du meinst, "
                "oder frag, wenn die Frage sich nicht aus dem Gespräch klärt:\n\n")
    return kopf + "\n\n".join(teile)


def chat_suchen(suche: str, person: str = "") -> str:
    a = ablage.standard()
    pid = None
    if (person or "").strip():
        p = a.person_nach_name(person)
        if not p:
            return f"Eine Person „{person}“ kenne ich in den Verläufen nicht."
        pid = p["id"]
    treffer = a.suchen(suche, anzahl=MAX_TREFFER, person=pid)
    if not treffer:
        return ("Dazu finde ich in den WhatsApp-Verläufen nichts. Snapchat und eigene Gespräche "
                "stehen dort nicht — dafür rueckblick mit person benutzen.")
    return "\n".join(f"{t['zeit'][:10]} {t['absender']} (mit {t['partner']}): {t['text'][:200]}" for t in treffer)


def person_korrigieren(alt: str, neu: str) -> str:
    neu = " ".join((neu or "").split())
    if not neu or len(neu) > 60:
        return "Der neue Name fehlt oder ist zu lang."
    gruppen, _ = blick.finden(alt)
    if not gruppen:
        return f"Eine Person „{alt}“ finde ich nicht — nichts geändert."
    if len(gruppen) > 1:
        return ("Mehrere passen: " + ", ".join(g["name"] for g in gruppen)
                + " — nichts geändert. Frag Eike, welchen er meint.")
    geaendert = 0
    for e in gruppen[0]["eintraege"]:
        if e["quelle"] == "chats":
            ablage.standard().person_umbenennen(e["daten"]["id"], neu)
            geaendert += 1
        elif e["quelle"] == "register":
            try:
                register.aendern(e["daten"]["id"], name=neu)
                geaendert += 1
            except PersonenFehler as fehler:
                return f"Der Name „{neu}“ ging nicht: {fehler}"
    if not geaendert:
        return "Diese Person steht nur in den Fahrkarten-Profilen — die ändere ich nicht."
    return (f"Erledigt: „{gruppen[0]['name']}“ heißt jetzt „{neu}“ in Verläufen und Kontakten. "
            "Die Fahrkarten-Profile bleiben, wie sie sind.")


WERKZEUGE = {"person_ueber": person_ueber, "person_korrigieren": person_korrigieren, "chat_suchen": chat_suchen}
# person_ueber (Nachschlagen im eigenen Register + Gedächtnis) blockierte „sag Kontakt A …“ (15.10.2026): zählt nicht als Fremdtext.
FREMDE_WERKZEUGE = {"chat_suchen"}
