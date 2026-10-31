"""Werkzeuge rund ums Nachdenken.

`gedanke_festhalten` und `hinweis_geben` gibt es nur in der Denkrunde (`katalog.NUR_DENKEN`); `gedanken_lesen` auch im
Gespräch. Die Runde setzt der Kern ein (`kennt_runde`), nie das Modell.
"""

from __future__ import annotations

from kern.denken import buch, hinweis

ERKLAERUNGEN = [
    {
        "name": "gedanke_festhalten",
        "description": ("Hält EINEN Gedanken aus dieser Denkrunde fest (höchstens 6 je Runde). art: erkenntnis (etwas "
                        "gelernt/verstanden), verbindung (zwei Dinge hängen zusammen), luecke (etwas fehlt oder "
                        "widerspricht sich), frage (offene Frage zum Weiterdenken). Nenne die Quellen, auf die du dich "
                        "stützt (kurz, z. B. 'Notiz Klausur', 'Kalender 12.10.'). Ohne Quelle gilt der Gedanke als "
                        "Vermutung."),
        "parameters": {"type": "OBJECT", "properties": {
            "art": {"type": "STRING", "description": "erkenntnis, verbindung, luecke oder frage"},
            "text": {"type": "STRING", "description": "Der Gedanke in ein bis zwei Sätzen, höchstens 400 Zeichen"},
            "quellen": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Woher du das weißt, kurz"},
            "sicherheit": {"type": "STRING", "description": "belegt (Quelle genannt) oder vermutet"},
        }, "required": ["art", "text"]},
    },
    {
        "name": "hinweis_geben",
        "description": ("Meldet Eike etwas WIRKLICH Wichtiges aus deinem Nachdenken (höchstens 1 je Runde, 3 am Tag). "
                        "wichtigkeit hoch NUR bei: Terminkollision, Frist in unter 48 Stunden, die er vermutlich "
                        "übersieht, klarer Widerspruch zwischen zwei Quellen, gefährdeter Ablauf. Sonst normal "
                        "(erscheint als Karte). Im Zweifel: nicht melden."),
        "parameters": {"type": "OBJECT", "properties": {
            "titel": {"type": "STRING", "description": "Kurze Überschrift"},
            "text": {"type": "STRING", "description": "Was ist los und was könnte Eike tun, höchstens 300 Zeichen"},
            "wichtigkeit": {"type": "STRING", "description": "normal oder hoch"},
        }, "required": ["titel", "text"]},
    },
    {
        "name": "gedanken_lesen",
        "description": ("Liest frühere Gedanken aus deinem Gedankenbuch: neueste oder nach Stichwort. Benutzen, wenn du "
                        "wissen willst, was du dir schon überlegt hast."),
        "parameters": {"type": "OBJECT", "properties": {
            "suche": {"type": "STRING", "description": "Stichwort, leer = neueste"},
            "art": {"type": "STRING", "description": "Optional: erkenntnis, verbindung, luecke oder frage"},
            "tage": {"type": "INTEGER", "description": "Wie weit zurück, Standard 30"},
        }},
    },
    {
        "name": "faden_anlegen",
        "description": ("Legt einen neuen Faden an: ein Thema, an dem du über mehrere Denkrunden weiterdenkst (höchstens 1 "
                        "neuer je Runde, höchstens 5 offene). Nur, wenn eine offene Frage mehr als eine Runde braucht. "
                        "Die Aufgabe beschreibt, was du herausfinden oder prüfen willst — nur Nachdenken und Nachschlagen, "
                        "nie etwas nach außen tun."),
        "parameters": {"type": "OBJECT", "properties": {
            "titel": {"type": "STRING", "description": "Kurzer Titel, höchstens 80 Zeichen"},
            "aufgabe": {"type": "STRING", "description": "Was willst du herausfinden? Höchstens 400 Zeichen"},
        }, "required": ["titel", "aufgabe"]},
    },
    {
        "name": "faden_weiter",
        "description": ("Hält den neuen Stand deines FOKUS-Fadens fest (nur der Faden, den dir diese Runde nennt). "
                        "Nach 6 Schritten ruht ein Faden — schließe ihn vorher, wenn du eine Antwort hast."),
        "parameters": {"type": "OBJECT", "properties": {
            "id": {"type": "INTEGER", "description": "Nummer des Fokus-Fadens"},
            "stand": {"type": "STRING", "description": "Was du jetzt weißt, 2–4 ganze Sätze, höchstens 600 Zeichen"},
        }, "required": ["id", "stand"]},
    },
    {
        "name": "faden_schliessen",
        "description": ("Schließt deinen FOKUS-Faden mit einem Ergebnis ab. Das Ergebnis wird zugleich ein Gedanke."),
        "parameters": {"type": "OBJECT", "properties": {
            "id": {"type": "INTEGER", "description": "Nummer des Fokus-Fadens"},
            "ergebnis": {"type": "STRING", "description": "Was herausgekommen ist: Fristen, Orte, nächste Schritte in 3–5 ganzen Sätzen, höchstens 800 Zeichen"},
        }, "required": ["id", "ergebnis"]},
    },
    {
        "name": "faeden_lesen",
        "description": ("Zeigt deine Fäden (Themen, über die du über mehrere Runden nachdenkst) mit Stand und Ergebnis. "
                        "Benutzen, wenn Eike fragt, woran du gerade denkst."),
        "parameters": {"type": "OBJECT", "properties": {
            "status": {"type": "STRING", "description": "Optional: offen, ruht oder erledigt"},
        }},
    },
    {
        "name": "faden_vorschlagen",
        "description": ("Nimmt ein Thema auf, über das Eike dich nachdenken lassen will ('denk mal über X nach'). "
                        "Es wird ein Faden, an dem du in den nächsten stillen Denkrunden arbeitest. Nur wenn er es wirklich "
                        "wünscht."),
        "parameters": {"type": "OBJECT", "properties": {
            "titel": {"type": "STRING", "description": "Kurzer Titel"},
            "aufgabe": {"type": "STRING", "description": "Was du herausfinden sollst"},
        }, "required": ["titel", "aufgabe"]},
    },
]


def gedanke_festhalten(art: str, text: str, quellen: list | None = None, sicherheit: str = "vermutet",
                       wichtigkeit: str = "normal", runde: int | None = None, fremde_inhalte: bool = False) -> str:
    if runde is None:
        return "Gedanken halte ich nur in einer Denkrunde fest."
    e = buch.standard().gedanke_festhalten(runde, art, text, quellen if isinstance(quellen, list) else None,
                                          sicherheit, fremd=fremde_inhalte, wichtigkeit=wichtigkeit)
    return "Festgehalten." if e["ok"] else f"Nicht festgehalten: {e['grund']}"


async def hinweis_geben(titel: str, text: str, wichtigkeit: str = "normal", runde: int | None = None,
                        fremde_inhalte: bool = False) -> str:
    if runde is None:
        return "Hinweise gebe ich nur in einer Denkrunde."
    e = await hinweis.geben(buch.standard(), runde, titel, text, wichtigkeit, fremd=fremde_inhalte)
    return f"Eike ist informiert ({e['weg']})." if e["ok"] else f"Nicht gemeldet: {e['grund']}"


def gedanken_lesen(suche: str = "", art: str = "", tage: int = 30) -> str:
    try:
        tage = int(tage or 30)
    except (TypeError, ValueError):
        tage = 30
    b = buch.standard()
    liste = b.suchen(suche or "", tage, art if art in buch.ARTEN else "") if (suche or art) else b.neueste(8)
    if not liste:
        return "Ich habe dazu keine Gedanken festgehalten."
    zeilen = []
    for g in liste:
        zusatz = ", gelesen und nicht bestätigt" if g["fremd"] else ""
        if g.get("opencode"):
            zusatz += ", freies Modell, ungeprüft"         # aus einer OpenCode-Runde, nicht von Gemini
        quellen = f" [{'; '.join(g['quellen'])}]" if g["quellen"] else ""
        zeilen.append(f"{g['zeit'][:10]} {g['art']} ({g['sicherheit']}{zusatz}): {g['text']}{quellen}")
    return "\n".join(zeilen)


def _fokus_pruefen(fid, runde: int | None) -> str:
    """Leer = erlaubt, sonst die Absage. Bewegen darf die Runde nur ihren Fokus-Faden."""
    if runde is None:
        return "Fäden bewege ich nur in einer Denkrunde."
    fokus = buch.standard().runde_fokus(runde)
    try:
        fid = int(fid)
    except (TypeError, ValueError):
        return "Welche Nummer hat der Faden?"
    if fokus is None or fid != fokus:
        return (f"Heute ist Faden #{fokus} dein Fokus." if fokus is not None
                else "Diese Runde hat keinen Fokus-Faden; du kannst nur freie Gedanken festhalten.")
    return ""


def faden_anlegen(titel: str, aufgabe: str, runde: int | None = None, fremde_inhalte: bool = False) -> str:
    if runde is None:
        return "Fäden lege ich nur in einer Denkrunde an."
    e = buch.standard().faden_anlegen(titel, aufgabe, quelle="runde", fremd=fremde_inhalte, runde=runde)
    return f"Faden #{e['id']} angelegt." if e["ok"] else f"Nicht angelegt: {e['grund']}"


def faden_weiter(id, stand: str, runde: int | None = None, fremde_inhalte: bool = False) -> str:   # noqa: A002 (Name kommt vom Modell)
    if absage := _fokus_pruefen(id, runde):
        return absage
    e = buch.standard().faden_weiter(int(id), stand, fremd=fremde_inhalte)
    if not e["ok"]:
        return f"Nicht festgehalten: {e['grund']}"
    return "Stand festgehalten." + (" Der Faden ruht jetzt (6 Schritte)." if e["ruht"] else "")


def faden_schliessen(id, ergebnis: str, runde: int | None = None, fremde_inhalte: bool = False) -> str:   # noqa: A002
    if absage := _fokus_pruefen(id, runde):
        return absage
    e = buch.standard().faden_schliessen(int(id), ergebnis, runde=runde, fremd=fremde_inhalte)
    if not e["ok"]:
        return f"Nicht abgeschlossen: {e['grund']}"
    try:
        from kern.anliegen import fach
        f = buch.standard().faden(int(id))
        fach.standard().anlegen("eike", "faden", f"Faden fertig: {f['titel'][:60]}", fach.kuerzen_satz(ergebnis),
                                fremd=bool(f["fremd"]))
    except Exception:                                  # noqa: BLE001
        pass
    return "Faden abgeschlossen; das Ergebnis ist jetzt ein Gedanke."


def faeden_lesen(status: str = "") -> str:
    liste = buch.standard().faeden(status if status in ("offen", "ruht", "erledigt") else "", 20)
    if not liste:
        return "Ich habe dazu keine Fäden."
    zeilen = []
    for f in liste:
        zusatz = ", gelesen und unbestätigt" if f["fremd"] else ""
        ende = f" → Ergebnis: {f['ergebnis']}" if f["ergebnis"] else (f" → Stand: {f['stand']}" if f["stand"] else "")
        zeilen.append(f"#{f['id']} [{f['status']}, {f['schritte']} Schritte, von {f['quelle']}{zusatz}] {f['titel']}: "
                      f"{f['aufgabe']}{ende}")
    return "\n".join(zeilen)


def faden_vorschlagen(titel: str, aufgabe: str) -> str:
    e = buch.standard().faden_anlegen(titel, aufgabe, quelle="eike")
    return (f"Gut, darüber denke ich in den nächsten stillen Runden nach (Faden #{e['id']})." if e["ok"]
            else f"Das geht nicht: {e['grund']}")


gedanke_festhalten.kennt_runde = True
gedanke_festhalten.kennt_fremde_inhalte = True
hinweis_geben.kennt_runde = True
hinweis_geben.kennt_fremde_inhalte = True

faden_anlegen.kennt_runde = True
faden_anlegen.kennt_fremde_inhalte = True
faden_weiter.kennt_runde = True
faden_weiter.kennt_fremde_inhalte = True
faden_schliessen.kennt_runde = True
faden_schliessen.kennt_fremde_inhalte = True

# Ändert etwas und verlangt kein „ja“: nach Fremdtext im selben Zug gesperrt (katalog.ausfuehren).
NICHT_NACH_FREMDTEXT = {"faden_vorschlagen"}

# Gedanken können aus Fremdtext stammen (Mails, Webseiten): ihr Inhalt ist Zitat, nicht Anweisung.
FREMDE_WERKZEUGE: set[str] = set()

WERKZEUGE = {"gedanke_festhalten": gedanke_festhalten, "hinweis_geben": hinweis_geben,
             "gedanken_lesen": gedanken_lesen, "faden_anlegen": faden_anlegen, "faden_weiter": faden_weiter,
             "faden_schliessen": faden_schliessen, "faeden_lesen": faeden_lesen, "faden_vorschlagen": faden_vorschlagen}
