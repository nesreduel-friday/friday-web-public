"""Studio — der YouTube-Kanal als eigener Arbeitsbereich.

Phase 3: Hier liegt, was den Kanal PLANT und begleitet. Bewusst getrennt von:

- `kern/dienste/studio.py` — spricht mit YouTube (OAuth, Zahlen, Hochladen)
- `kern/werkzeuge/studio.py` — was Friday im Gespräch aufrufen darf
- `studio/` im Projektwurzelverzeichnis — der eigene Container, der rendert

Der Planer wählt ein Thema pro Tag anhand echter Rückhaltedaten, legt den
Renderauftrag als Datei ab und meldet sich, wenn ein Video fertig ist.
Veröffentlicht wird von hier aus NIE — das entscheidet Eike.
"""
