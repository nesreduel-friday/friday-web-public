"""Werkzeuge: Eike gibt Friday einen Rechercheauftrag (15.10.2026) — „recherchier, wann die Vorlesungen der Informatik an der LUH sind“.

Nur Eike im Gespräch; nie nach Fremdtext (`NICHT_NACH_FREMDTEXT`), nie für Personen, Denkrunde oder Regel-Läufe. Der Auftrag läuft im
Hintergrund (`kern/recherche/planer.py`, bis zu einer Stunde); das Ergebnis kommt als Push, Karte und Gedanke, und hier liest Friday den
Bericht vor. Der Bericht stammt aus Webseiten und ist nicht von Friday geprüft — das sagt jedes Ergebnis dazu."""

from __future__ import annotations

from kern.ehrlichkeit.wirkung import wirkt
from kern.recherche import speicher
from kern.werkzeuge import sendepruefung

STATUS_TEXT = {"wartend": "wartet", "laeuft": "läuft", "fertig": "fertig", "fehler": "gescheitert", "abgebrochen": "abgebrochen"}
HINWEIS = "Der Bericht stammt aus Webseiten und ist nicht von mir geprüft — nenne die Quellen dazu."

ERKLAERUNGEN = [
    {"name": "recherche_starten",
     "description": ("Startet eine gründliche Internet-Recherche im Hintergrund (bis zu einer Stunde; Friday meldet sich per Push, Karte und Gedanke). "
                     "Nur wenn Eike ausdrücklich etwas recherchieren lassen will („recherchier mal …“, „finde heraus …“). Für schnelle Fragen "
                     "nimm weiter suchen. Frag nicht nach, sondern formuliere die Frage selbst vollständig (Ort, Zeitraum, Zielgruppe) — aber ohne persönliche Daten (Name, Adresse, Schule, Nummern)."),
     "parameters": {"type": "OBJECT", "properties": {
         "frage": {"type": "STRING", "description": "Die Rechercheaufgabe, vollständig und genau"},
         "hinweise": {"type": "STRING", "description": "Wünsche von Eike (z. B. nur offizielle Seiten, bis wann). Sonst leer."}},
         "required": ["frage"]}},
    {"name": "recherche_lesen",
     "description": ("Zeigt Rechercheaufträge: ohne Nummer die Liste mit Status, mit Nummer den Stand oder den Bericht samt Quellen. "
                     "Benutzen, wenn Eike nach dem Ergebnis oder dem Stand einer Recherche fragt."),
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER", "description": "Nummer der Recherche; 0 oder leer = Liste"}}}},
    {"name": "recherche_abbrechen",
     "description": "Bricht einen wartenden oder laufenden Rechercheauftrag ab. Nur wenn Eike es verlangt.",
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER", "description": "Nummer der Recherche"}}, "required": ["id"]}},
]


@wirkt("sonstiges", ok=("Auftrag angelegt",), nicht_praefix="Nichts gestartet. ")
def recherche_starten(frage: str = "", hinweise: str = "") -> str:
    if sendepruefung.heikel(f"{frage} {hinweise}"):
        return "Persönliche Angaben (Zugangsdaten, Adresse, Nummern) gehören nicht in eine Internet-Recherche. Formuliere die Frage ohne sie."
    try:
        r = speicher.anlegen(frage, hinweise)
    except speicher.RechercheFehler as exc:
        return str(exc)
    return (f"Auftrag angelegt: Recherche #{r['id']}. Sag Eike, dass du jetzt gründlich recherchierst, es bis zu einer Stunde dauern kann und du dich "
            "per Push meldest.")


def _holen(rid) -> dict | None:
    try:
        return speicher.holen(int(rid))
    except (TypeError, ValueError):
        return None


def recherche_lesen(id: int = 0) -> str:  # noqa: A002
    if not id:
        liste = speicher.liste(10)
        if not liste:
            return "Es gibt noch keine Rechercheaufträge."
        return "\n".join(f"Nr. {r['id']} — {STATUS_TEXT[r['status']]} — {r['frage'][:90]}" for r in liste)
    r = _holen(id)
    if r is None:
        return "Nichts gefunden. Diese Recherche gibt es nicht — nimm recherche_lesen ohne Nummer für die Liste."
    kopf = f"Recherche #{r['id']} ({STATUS_TEXT[r['status']]}): {r['frage']}"
    if r["status"] in ("wartend", "laeuft"):
        s = r["schritte"]
        return f"{kopf}\nSie ist noch nicht fertig (bisher {s.get('suchen', 0)} Suchen, {s.get('seiten', 0)} Seiten gelesen). Sag Eike, dass sie noch läuft."
    if r["status"] in ("fehler", "abgebrochen"):
        return f"{kopf}\n{r['fehler'] or 'Sie wurde abgebrochen.'}"
    quellen = "\n".join(f"- {q['url']}" + (f" ({q['titel']})" if q.get("titel") else "") + f", abgerufen {q.get('abruf', '?')}" for q in r["quellen"]) or "- keine"
    return (f"{kopf}\nSicherheit: {r['sicherheit']}\n\nBericht:\n{r['bericht']}\n\nOffen: {r['offen'] or '–'}\n\nQuellen:\n{quellen}\n\n{HINWEIS}")


@wirkt("sonstiges", ok=("Erledigt:",), kopf_ok="Abgebrochen", nicht_praefix="Nicht abgebrochen. ")
def recherche_abbrechen(id: int) -> str:  # noqa: A002
    r = _holen(id)
    if r is None:
        return "Diese Recherche gibt es nicht."
    if not speicher.abbrechen(r["id"]):
        return f"Die Recherche #{r['id']} steht auf „{STATUS_TEXT[r['status']]}“ und lässt sich nicht mehr abbrechen."
    return f"Erledigt: Die Recherche #{r['id']} ist abgebrochen."


# Der Bericht stammt aus Webseiten: wer ihn liest, hat fremden Text im Zug (merken wird Vorschlag, Senden/Ändern ist gesperrt). Liste und Stand nicht.
FREMDE_WERKZEUGE = {"recherche_lesen"}
FREMD_WENN = {"recherche_lesen": lambda e: "Bericht:" in str((e or {}).get("ergebnis") or "")}
NICHT_NACH_FREMDTEXT = {"recherche_starten"}
WERKZEUGE = {"recherche_starten": recherche_starten, "recherche_lesen": recherche_lesen, "recherche_abbrechen": recherche_abbrechen}
