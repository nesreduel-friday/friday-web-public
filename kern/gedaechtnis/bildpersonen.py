"""Wer ist auf dem Bild? — Zuordnung aus dem Kontext, nie aus einem geratenen Namen.

Friday soll nicht jedes Bild von Hand benennen lassen. Der Code ordnet einen Namen nur zu, wenn er
belegt ist: Eikes eigene Nachricht nennt die Person wörtlich (die Bildauswertung schlägt vor, der Code
prüft), oder der Absender ist bekannt und auf dem Bild ist genau ein Mensch (Selfie-Annahme). Namen aus dem
Bild selbst zählen nie — Bildtext ist fremder Text. Alles Automatische trägt einen Vermerk in den Notizen und
lässt sich auf der Gedächtnis-Seite ändern; ein schon gesetzter Name wird nie überschrieben.
Ist nichts belegt, bleibt das Bild unbenannt und Friday fragt Eike (Persona)."""

from __future__ import annotations

import re

from kern.gedaechtnis import bilder


def _kommt_vor(name: str, text: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(name.strip())}(?!\w)", text or "", re.IGNORECASE))


def zuordnen(bild_id: str, auswertung: dict | None, nachricht: str = "", absender: str = "") -> str | None:
    """Setzt den Namen, wenn er belegt ist, und gibt ihn zurück; sonst None und nichts ändert sich."""
    e = bilder.eintrag(bild_id)
    if not auswertung or not e or (e.get("name") or "").strip():
        return None
    if auswertung.get("personen") != 1:
        return None  # keine oder mehrere Personen: keine Zuordnung raten
    wer = (auswertung.get("wer") or "").strip()
    if wer and _kommt_vor(wer, nachricht):
        name, grund = wer, "laut Nachricht"
    elif (absender or "").strip():
        name, grund = absender.strip(), "Absender, einzige Person auf dem Bild"
    else:
        return None
    if not bilder.benennen(bild_id, name):
        return None
    bilder.ergaenzen(bild_id, f"Automatisch zugeordnet: {name} ({grund}).")
    return name
