"""Werkzeuge: Eike lässt ein angehängtes Aufgaben-PDF im Hintergrund bearbeiten (15.10.2026) — „löse das mit erweitertem Denken“.

Nur Eike im Gespräch (Personen haben es nicht: Positivliste). Der Auftrag ist rein lesend und wirkt nicht nach außen, deshalb darf er auch in einem Zug
mit PDF (Fremdtext) starten; Senden bleibt dort gesperrt. Der Auftrag läuft im Hintergrund (`kern/aufgabenblatt/planer.py`); das Ergebnis kommt als
Push und Karte, und hier liest Friday die Lösung vor. Ob sie sie weiterschickt, entscheidet Eike danach mit den bestehenden Sende-Werkzeugen."""

from __future__ import annotations

from kern.aufgabenblatt import speicher
from kern.ehrlichkeit.wirkung import wirkt

STATUS_TEXT = {"wartend": "wartet", "laeuft": "läuft", "fertig": "fertig", "fehler": "gescheitert", "abgebrochen": "abgebrochen"}
HINWEIS = ("Die Lösungen stammen von mir und sind nur so sicher, wie sie oben markiert sind. Frag Eike, ob du sie jemandem schicken sollst — "
           "schicken darfst du nur auf Eikes ausdrücklichen Auftrag in einem späteren Zug.")

ERKLAERUNGEN = [
    {"name": "aufgaben_starten",
     "description": ("Bearbeitet das zuletzt angehängte Aufgaben-PDF gründlich im Hintergrund (mehrere Runden mit Suche und Gedächtnis; Friday meldet sich per Push "
                     "und Karte). NUR wenn Eike ausdrücklich „mit erweitertem Denken“ oder „gründlich im Hintergrund“ will. Normalerweise löst du Aufgaben "
                     "direkt im Chat und rufst das hier NICHT auf."),
     "parameters": {"type": "OBJECT", "properties": {
         "hinweise": {"type": "STRING", "description": "Wünsche von Eike (z. B. nur Aufgabe 1 bis 3, ausführlicher Rechenweg). Sonst leer."},
         "blatt": {"type": "INTEGER", "description": "Nummer des Aufgabenblatts aus dem Anhang-Hinweis; 0 oder leer = das neueste"}}}},
    {"name": "aufgaben_lesen",
     "description": ("Zeigt die Aufgabenblatt-Aufträge: ohne Nummer die Liste mit Status, mit Nummer den Stand oder die fertige Lösung. "
                     "Benutzen, wenn Eike nach dem Ergebnis oder dem Stand fragt."),
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER", "description": "Nummer des Auftrags; 0 oder leer = Liste"}}}},
    {"name": "aufgaben_pdf",
     "description": ("Erstellt aus einem fertigen Aufgabenblatt-Auftrag eine Ausarbeitung als PDF (Aufgabenseiten, Lösungen, Wege) zum Herunterladen. "
                     "Benutzen, wenn Eike „gib mir das als PDF“ oder „Ausarbeitung“ sagt."),
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER", "description": "Nummer des fertigen Auftrags"}}, "required": ["id"]}},
    {"name": "aufgaben_abbrechen",
     "description": "Bricht einen wartenden oder laufenden Aufgabenblatt-Auftrag ab. Nur wenn Eike es verlangt.",
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER", "description": "Nummer des Auftrags"}}, "required": ["id"]}},
]


@wirkt("sonstiges", ok=("Auftrag angelegt",), nicht_praefix="Nichts gestartet. ")
def aufgaben_starten(hinweise: str = "", blatt: int = 0) -> str:
    try:
        a = speicher.anlegen(int(blatt) if blatt else None, hinweise)
    except (speicher.AufgabenFehler, TypeError, ValueError) as exc:
        return str(exc) if isinstance(exc, speicher.AufgabenFehler) else "Die Blattnummer ist ungültig."
    name = (speicher.blatt_holen(a["blatt"]) or {}).get("name", "Aufgabenblatt")
    return (f"Auftrag angelegt: Aufgaben #{a['id']} für „{name}“. Sag Eike, dass du die Aufgaben jetzt gründlich im Hintergrund bearbeitest, es einige Minuten "
            "dauern kann und du dich per Push meldest.")


def _holen(aid) -> dict | None:
    try:
        return speicher.holen(int(aid))
    except (TypeError, ValueError):
        return None


def aufgaben_lesen(id: int = 0) -> str:  # noqa: A002
    if not id:
        liste = speicher.liste(10)
        if not liste:
            return "Es gibt noch keine Aufgabenblatt-Aufträge."
        return "\n".join(f"Nr. {a['id']} — {STATUS_TEXT[a['status']]} — {(speicher.blatt_holen(a['blatt']) or {}).get('name', '?')}" for a in liste)
    a = _holen(id)
    if a is None:
        return "Nichts gefunden. Diesen Auftrag gibt es nicht — nimm aufgaben_lesen ohne Nummer für die Liste."
    name = (speicher.blatt_holen(a["blatt"]) or {}).get("name", "?")
    kopf = f"Aufgaben #{a['id']} ({STATUS_TEXT[a['status']]}): {name}"
    if a["status"] in ("wartend", "laeuft"):
        s = a["schritte"]
        return f"{kopf}\nSie ist noch nicht fertig (bisher {s.get('suchen', 0)} Suchen, {s.get('seiten', 0)} Seiten gelesen). Sag Eike, dass sie noch läuft."
    if a["status"] in ("fehler", "abgebrochen"):
        return f"{kopf}\n{a['fehler'] or 'Sie wurde abgebrochen.'}"
    return f"{kopf}\nSicherheit: {a['sicherheit']}\n\nLösung:\n{speicher.loesungstext(a)}\n\n{HINWEIS}"


@wirkt("sonstiges", ok=("Erledigt:",), kopf_ok="Abgebrochen", nicht_praefix="Nicht abgebrochen. ")
def aufgaben_abbrechen(id: int) -> str:  # noqa: A002
    a = _holen(id)
    if a is None:
        return "Diesen Auftrag gibt es nicht."
    if not speicher.abbrechen(a["id"]):
        return f"Der Auftrag #{a['id']} steht auf „{STATUS_TEXT[a['status']]}“ und lässt sich nicht mehr abbrechen."
    return f"Erledigt: Der Auftrag #{a['id']} ist abgebrochen."


@wirkt("sonstiges", ok=("PDF bereit",), nicht_praefix="Kein PDF. ")
def aufgaben_pdf(id: int, aktionen: list | None = None) -> str:  # noqa: A002
    from kern.aufgabenblatt import ausarbeitung
    try:
        ausarbeitung.ablegen(int(id))
    except (speicher.AufgabenFehler, TypeError, ValueError) as exc:
        return str(exc) if isinstance(exc, speicher.AufgabenFehler) else "Die Nummer ist ungültig."
    if aktionen is None:
        return "PDF bereit — aber einen Download-Knopf gibt es nur im Schreib-Chat. Sag Eike, dass er die Ausarbeitung dort noch einmal anfordern soll."
    auftrag = speicher.holen(int(id)) or {}
    name = (speicher.blatt_holen(auftrag.get("blatt")) or {}).get("name", "Aufgabenblatt")
    aktionen.append({"typ": "datei", "url": f"/api/aufgabenblatt/{int(id)}/ausarbeitung", "titel": f"Ausarbeitung {name}"[:80]})
    return "PDF bereit: Unter deiner Antwort liegt ein Knopf zum Herunterladen. Sag Eike kurz, dass er darauf tippen kann."


aufgaben_pdf.kennt_aktionen = True


# Die Lösung entstand aus einem fremden PDF und Webseiten: wer sie liest, hat fremden Text im Zug (merken wird Vorschlag, Senden/Ändern gesperrt).
FREMDE_WERKZEUGE = {"aufgaben_lesen"}
FREMD_WENN = {"aufgaben_lesen": lambda e: "Lösung:" in str((e or {}).get("ergebnis") or "")}
NICHT_NACH_FREMDTEXT: set = set()
WERKZEUGE = {"aufgaben_starten": aufgaben_starten, "aufgaben_lesen": aufgaben_lesen, "aufgaben_abbrechen": aufgaben_abbrechen,
              "aufgaben_pdf": aufgaben_pdf}
