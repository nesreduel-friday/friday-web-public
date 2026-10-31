"""Werkzeug `dashboard_karte`: Friday legt eine Karte auf die Übersicht.

Bewusst ohne Konnektor und ohne Rückfrage: Die Karte ist nur Fridays eigene
Anzeige, sie geht nicht nach außen und ins Gedächtnis schreibt sie nichts.
"""

from kern.dashboard import friday_karten

ERKLAERUNGEN = [
    {
        "name": "dashboard_karte",
        "description": ("Legt eine Karte auf die Übersicht (Startseite der App), damit Eike etwas sehen oder für "
                        "später griffbereit haben kann: eine Abfahrt, ein Ergebnis, eine kurze Zusammenfassung, "
                        "ein Bild. Benutzen, wenn er etwas zum Ansehen bekommt, das über den Moment hinaus "
                        "nützt, oder wenn er 'zeig mir das auf dem Dashboard' sagt. NICHT bei jeder Antwort. "
                        "NIE für Uhrzeit oder Datum (die zeigt die Übersicht selbst und sie veralten sofort). "
                        "Titel kurz, Text höchstens ein bis zwei Sätze. Die Lebensdauer (minuten) wählst du "
                        "nach dem Inhalt: was sich schnell ändert, 5 bis 10; eine Abfahrt bis kurz nach der "
                        "Abfahrt; etwas, das er noch braucht, 30 bis 60. Danach verschwindet die Karte von selbst."),
        "parameters": {"type": "OBJECT", "properties": {
            "titel": {"type": "STRING", "description": "Kurze Überschrift, z.B. 'Bus 100 um 7:52'"},
            "text": {"type": "STRING", "description": "Ein bis zwei Sätze, optional"},
            "bild_url": {"type": "STRING", "description": "Optional: https-Adresse eines Bildes — oder die Adresse eines Bildes, das du mit bild_zeigen gezeigt hast (/api/netzbild/… oder /api/bilder/…); das wird eine Bildkarte mit Quelle"},
            "minuten": {"type": "INTEGER", "description": "Wie lange die Karte bleibt, in Minuten, 1 bis 720, Standard 10"},
        }, "required": ["titel"]},
    },
]


async def dashboard_karte(titel: str, text: str = "", bild_url: str = "", minuten: int = 10,
                          fremde_inhalte: bool = False) -> str:
    # Stammt der Zug aus fremdem Text (Webseite, Mail), holt Eikes Browser kein Bild von deren
    # Server: das verriete IP und Zeitpunkt. Fridays eigene Bilder bleiben erlaubt.
    ohne_bild = fremde_inhalte and bild_url and not bild_url.startswith(("/api/bilder/", "/api/netzbild/"))
    if ohne_bild:
        bild_url = ""
    try:
        karte = await friday_karten.legen(titel, text, bild_url, minuten)
    except friday_karten.KarteFehler as exc:
        return str(exc)
    hinweis = " — ohne das Bild, denn die Angaben stammen aus fremdem Text" if ohne_bild else ""
    return f"Die Karte „{karte['titel']}“ liegt jetzt auf der Übersicht{hinweis}."


dashboard_karte.kennt_fremde_inhalte = True


WERKZEUGE = {"dashboard_karte": dashboard_karte}
