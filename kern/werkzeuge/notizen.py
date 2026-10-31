"""Goodnotes-Werkzeuge — Eikes Hefte im Gespräch.

Abgeschrieben wird im Hintergrund (`kern/goodnotes/abgleich.py`); hier wird
nur in der Ablage gesucht und vorgelesen. Rein lesend.

Die Hefte enthalten eingefügte Arbeitsblätter und Texte Dritter, deshalb
stehen alle Werkzeuge in FREMDE_WERKZEUGE.
"""

from __future__ import annotations

from kern.goodnotes import ablage

KONNEKTOR = "goodnotes"
FREMDE_WERKZEUGE = ("notizen_suchen", "notizseite", "notizhefte")
MAX_SEITE_ZEICHEN = 6000

ERKLAERUNGEN = [
    {
        "name": "notizen_suchen",
        "description": ("Durchsucht Eikes Goodnotes-Hefte (Mitschriften, Arbeitsblätter, alle Fächer) im Volltext "
                        "und nennt passende Seiten mit Heft-Nummer, Seite, Titel und Textstelle. Benutzen bei Fragen "
                        "zum Unterrichtsstoff, zu Mitschriften oder „was hatten wir zu …“."),
        "parameters": {"type": "OBJECT", "properties": {
            "suche": {"type": "STRING", "description": "Fachbegriffe, Themen oder Namen"},
            "fach": {"type": "STRING", "description": "Optional: nur Hefte dieses Fachs (z. B. Biologie)"},
            "anzahl": {"type": "INTEGER", "description": "Wie viele Treffer (Standard 8)"},
        }, "required": ["suche"]},
    },
    {
        "name": "notizseite",
        "description": ("Die vollständige Abschrift einer Heftseite. heft ist die Heft-Nummer aus notizen_suchen "
                        "oder notizhefte."),
        "parameters": {"type": "OBJECT", "properties": {
            "heft": {"type": "INTEGER"},
            "seite": {"type": "INTEGER"},
        }, "required": ["heft", "seite"]},
    },
    {
        "name": "notizhefte",
        "description": ("Listet Eikes Goodnotes-Hefte mit Nummer, Ordner, Seitenzahl, letzter Änderung und den "
                        "Titeln der letzten Seiten — für „was haben wir zuletzt in … gemacht“."),
        "parameters": {"type": "OBJECT", "properties": {
            "fach": {"type": "STRING", "description": "Optional: nur Hefte, deren Ordner oder Name das enthält"},
        }},
    },
]


def _ort(z: dict) -> str:
    return f"{z['ordner'] + ' / ' if z['ordner'] else ''}{z['name']}"


def _datum(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if len(iso or "") >= 10 else ""


def _stand() -> str:
    s = ablage.standard().stand()
    if s["offen"]:
        return f"\n(Noch nicht alles gelesen: {s['fertig']} von {s['gesamt']} Seiten sind durchsuchbar.)"
    return ""


def notizen_suchen(suche: str, fach: str = "", anzahl: int = 8) -> str:
    anzahl = max(1, min(int(anzahl), 20))
    treffer = ablage.standard().suchen(suche, fach, anzahl)
    if not treffer:
        return f"In den Heften finde ich nichts zu „{suche}“{f' im Fach {fach}' if fach else ''}.{_stand()}"
    zeilen = []
    for t in treffer:
        kopf = f"Heft {t['heft']} „{_ort(t)}“, Seite {t['nr']}"
        extra = ", ".join(x for x in (t["titel"], _datum(t["datum"])) if x)
        zeilen.append(f"{kopf}{f' ({extra})' if extra else ''}: {' '.join((t['stelle'] or '').split())}")
    return "\n".join(zeilen) + _stand()


def notizseite(heft: int, seite: int) -> str:
    s = ablage.standard().seite(int(heft), int(seite))
    if s and ablage.ausgeschlossen(s["ordner"], ablage.standard().ausgeschlossen()):
        return f"Heft {heft} liegt in einem Ordner, den Friday nicht lesen soll."
    if not s:
        h = ablage.standard().heft(int(heft))
        return (f"Heft {heft} hat keine Seite {seite} (es hat {h['seiten']} Seiten)." if h
                else f"Ein Heft {heft} gibt es nicht.")
    if s["status"] != "fertig":
        return f"Seite {seite} in „{_ort(s)}“ ist noch nicht gelesen."
    text = s["text"] or "(leere Seite)"
    if len(text) > MAX_SEITE_ZEICHEN:
        text = text[:MAX_SEITE_ZEICHEN] + " …"
    extra = ", ".join(x for x in (s["titel"], _datum(s["datum"]), s["art"]) if x)
    return f"„{_ort(s)}“, Seite {seite} von {s['heft_seiten']} ({extra}):\n{text}"


def notizhefte(fach: str = "") -> str:
    a = ablage.standard()
    klein = (fach or "").casefold()[:4]
    aus = a.ausgeschlossen()
    hefte = [h for h in a.hefte() if not ablage.ausgeschlossen(h["ordner"], aus)
             and (not klein or klein in f"{h['ordner']} {h['name']}".casefold())]
    if not hefte:
        return "Keine passenden Hefte." if fach else "Noch keine Hefte abgeglichen."
    hefte.sort(key=lambda h: h["geaendert"], reverse=True)
    zeilen = []
    for h in hefte[:25]:
        letzte = [f"S. {x['nr']}: {x['titel']}" for x in a.letzte_seiten(h["id"]) if x["titel"]]
        zeilen.append(f"Heft {h['id']} „{_ort(h)}“: {h['seiten']} Seiten, geändert {_datum(h['geaendert'])}, "
                      f"{h['gelesen'] or 0} gelesen" + (f" — zuletzt {'; '.join(letzte)}" if letzte else ""))
    return "\n".join(zeilen)


WERKZEUGE = {"notizen_suchen": notizen_suchen, "notizseite": notizseite, "notizhefte": notizhefte}
