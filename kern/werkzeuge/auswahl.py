"""Welche Werkzeuge Friday im Chat pro Zug zu sehen bekommt (13.10.2026).

147 Werkzeuge mit zusammen rund 79.000 Zeichen gingen bei jeder Anfrage mit — das sind etwa 22.000 Token Rauschen, in dem das Modell das
richtige Werkzeug auswählen muss (Fehlgriffe: kein Aufruf, falsches Werkzeug nach Eikes Ja). Jetzt gehen nur der Kern und die Gruppen mit,
zu denen der Gesprächsausschnitt passt. Fehlt eine Gruppe, ruft das Modell `werkzeuge_laden` auf; der nächste Durchgang desselben Zuges hat sie.
Das ist nur eine Auswahl der Sichtbarkeit: Rechte, Schreibrecht und Personenfilter prüft weiter `katalog` unverändert."""

from __future__ import annotations

import re

META = "werkzeuge_laden"
FENSTER = 4          # so viele letzte Nachrichten (beider Seiten) entscheiden mit — ein bloßes „Ja“ trägt die Gruppe des Zuges davor

KERN = ("basis", "gedaechtnis", "suche", "erinnerung", "chats", "anliegen", "werkbank", "chatverlauf", "fragen")

# gruppe -> (Module, Auslöser, Kurztext fürs Meta-Werkzeug)
GRUPPEN: dict[str, tuple[tuple[str, ...], str, str]] = {
    "schule": (("schule", "buecher", "lernen"), r"schul|stundenplan|stunde|hausaufgab|elternbrief|vertretung|unterricht|lehrer|klassenarbeit|fach\b|schulbuch|lernen",
               "Stundenplan, Hausaufgaben, Elternbriefe, Schulbücher"),
    "mail": (("post", "gmail"), r"mail|postfach|posteingang|iserv", "E-Mails (IServ, Gmail)"),
    "kalender": (("kalender",), r"termin|kalender|verabred|zeit hab|\bfrei\b|besprech|treffen", "Kalender und Termine"),
    "musik": (("musik",), r"musik|song|lied|playlist|spotify|\bspiel\b|abspiel|pause|lauter|leiser|nächste[rn]?\b|album|künstler|warteschlange",
              "Musik steuern (Spotify)"),
    "fahrt": (("fahrplan", "route", "standort"), r"bus\b|bahn|zug\b|züge|abfahrt|verbindung|fahrt|haltestelle|fahrplan|route|wie komme|entfernung|standort|wo bin ich|wie weit",
              "Fahrplan, Verbindungen, Route, Standort"),
    "garantie": (("garantie", "akte"), r"garantie|erstattung|fahrgastrecht|verspätung|antrag|akte|kundenkarte|erfass", "Garantieanträge und Akten"),
    "wetter": (("wetter",), r"wetter|regen|temperatur|sonne|grad\b|schnee|gewitter", "Wetter"),
    "briefing": (("morgenbriefing", "dashboard"), r"briefing|übersicht|karte\b|dashboard|guten morgen", "Morgenbriefing, Karten auf der Übersicht"),
    "oeffnen": (("oeffnen", "bilder"), r"öffne|öffnen|link|seite\b|webseite|zeig|bild|foto|ansehen", "Seiten öffnen, Bilder zeigen"),
    "notizen": (("notizen",), r"notiz|goodnotes|heft\b|notizbuch|mitschrift", "Goodnotes-Notizen"),
    "personen": (("personenstandort", "kontakte"), r"wo ist|wo steckt|standort von|mama|papa", "Wo eine Person gerade ist"),
    "whatsapp": (("whatsapp",), r"whatsapp|whats app", "WhatsApp lesen und schreiben"),
    "snapchat": (("snapchat",), r"snap|schreib|schick|sende|nachricht an|anfrage|freund|kontakt|an maya|an tim|an kontakt_c|an torge", "Snapchat: Nachrichten, Snaps, Kontakte"),
    "meldungen": (("benachrichtigung",), r"push|benachrichtig|ruhezeit|meldung", "Push-Meldungen und Ruhezeit"),
    "pc": (("pc",), r"\bpc\b|computer|rechner|bildschirm|programm|fenster|datei|herunterfahr|zwischenablage|energiespar|tastatur", "Eikes PC steuern"),
    "medien": (("medien",), r"film|serie|download|runterlad|herunterlad|mediathek|jellyfin|sonarr|radarr|staffel|folge", "Filme und Serien suchen, laden, löschen"),
    "server": (("hphome", "studio", "verbrauch", "minecraft_bot"),
               r"server|hphome|docker|studio|speicher|platte|token|verbrauch|minecraft|mindcraft|mc-?bot|\bbot\b"
               r"|inventar|koordinaten|position|wo steht|wo ist der bot",
               "Server-Status, Studio, Minecraft-Bot"),
    "recherche": (("recherche",), r"recherch|herausfinden|find(e)? heraus|schau mal|nachschauen|nachschlagen|such(e)? mal|ermittel",
                  "Gründliche Internet-Recherche im Hintergrund"),
    "aufgaben": (("aufgabenblatt", "dokument"),
                 r"aufgabenblatt|aufgaben\b|arbeitsblatt|\bpdf\b|dokument|tabelle|erweitert\w* denken|gründlich im hintergrund|"
                 r"\blös(e|en|ung)\b|bearbeite|ausarbeit\w*|als pdf",
                 "Aufgaben-PDFs im Hintergrund und Dokumente als PDF mit Tabelle"),
    "gedanken": (("gedanken",), r"nachdenk|gedanke|faden|fäden|thema", "Gedankenbuch und Fäden"),
    "regeln": (("vorgaben", "skills", "personenprimitive", "module_schalten", "snapwunsch", "garantie_annahme", "ticket_annahme", "regeln"),
               r"sobald|automatisch|auslöser|wenn \w+ (?:schreibt|antwortet|meldet)|hebel|flamme|snap-?wunsch|modul|regel|vorgabe|ab jetzt|immer\b|nie mehr|nie wieder|darf\b|dürfen|limit|skill|arbeitsweise|vereinbar|erlaub|freigab|pro tag|rundnachricht|medienwunsch|einstellung",
               "Regeln, Vorgaben und Arbeitsweisen für Personen"),
}

_REGEX = {g: re.compile(a, re.I) for g, (_, a, _) in GRUPPEN.items()}


def waehlen(texte: list[str], geladen: set[str] | None = None) -> set[str]:
    """Die Gruppen, zu denen die letzten Nachrichten passen, plus bereits per `werkzeuge_laden` geholte."""
    text = "\n".join(t for t in texte[-FENSTER:] if t)
    gruppen = {g for g, rx in _REGEX.items() if rx.search(text)}
    return gruppen | set(geladen or ())


def module_von(gruppen: set[str]) -> set[str]:
    return set(KERN) | {m for g in gruppen if g in GRUPPEN for m in GRUPPEN[g][0]}


def meta_erklaerung() -> dict:
    liste = "; ".join(f"{g} ({t})" for g, (_, _, t) in GRUPPEN.items())
    return {
        "name": META,
        "description": ("Holt weitere Werkzeuge für diesen Zug, wenn dir für Eikes Anliegen eines fehlt. Du siehst nicht immer alle Werkzeuge, "
                        "nur die zum Thema. Fehlt dir etwas, rufst du das hier mit der passenden Gruppe auf und nutzt danach das Werkzeug. "
                        "Sag nie, etwas gehe nicht, bevor du die passende Gruppe geholt hast. Gruppen: " + liste + "."),
        "parameters": {"type": "OBJECT", "properties": {
            "gruppen": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Namen der Gruppen, z. B. [\"musik\"]"}},
            "required": ["gruppen"]},
    }
