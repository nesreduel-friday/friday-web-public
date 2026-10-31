"""Zustand des Overlays ohne Qt — damit die Logik ohne Bildschirm testbar ist."""

from __future__ import annotations

from dataclasses import dataclass, field

# Zustand → (Farbe, Beschriftung)
ZUSTAENDE = {
    "aus": ("#64748b", "aus"),
    "lauscht": ("#6ea8ff", "Sag „Friday“"),
    "verbinde": ("#6ea8ff", "verbinde …"),
    "hoert": ("#34d399", "hört zu"),
    "denkt": ("#fbbf24", "denkt nach"),
    "spricht": ("#c084fc", "spricht"),
    "fehler": ("#f87171", "Fehler"),
}
# Ohne Gespräch bleibt das Fenster weg. Sichtbar nur, solange eine Sitzung läuft (oder ein Fehler steht).
RUHE = ("aus", "lauscht")
BREITE = 380
KOPF_HOEHE = 64
ZEILE_HOEHE = 16         # eine Zeile Transkript bei 12 px
ZEILEN_ABSTAND = 6
MAX_ZEILEN = 2           # die letzten zwei Sätze (Du und Friday)
MAX_TEXT = 220           # mehr als das wird nicht angezeigt; der Anfang bleibt weg, der Schluss zählt
RAND = 8


def werkzeug_anzeige(name: str) -> str:
    """Interner Werkzeugname → kurzer Text für Eike. Unbekanntes wird nicht gezeigt."""
    if name.startswith("pc_"):
        return "am PC"
    if name.startswith("browser_"):
        return "im Browser"
    return "arbeitet"


def sauber(text: str) -> str:
    """Zeilenumbrüche und doppelte Leerzeichen raus — das Transkript kommt in Stücken."""
    return " ".join(text.split())


@dataclass
class Stand:
    zustand: str = "aus"
    pegel: float = 0.0
    werkzeug: str = ""
    fehler: str = ""
    zeilen: list = field(default_factory=list)   # [rolle, text]
    _offen: bool = False                          # der letzte Satz ist noch nicht zu Ende

    @property
    def sichtbar(self) -> bool:
        return self.zustand not in RUHE

    def verarbeite(self, ev: tuple) -> None:
        art = ev[0]
        if art == "zustand":
            self.zustand = ev[1]
            if ev[1] == "verbinde":
                # Neue Sitzung: das alte Transkript gehört nicht mehr dazu.
                self.zeilen = []
                self._offen = False
            if ev[1] != "denkt":
                self.werkzeug = ""
            if ev[1] != "fehler":
                self.fehler = ""
        elif art == "pegel":
            # weich folgen: schnell hoch, langsam runter
            self.pegel = max(ev[1], self.pegel * 0.85)
        elif art == "text":
            rolle, stueck = ev[1], str(ev[2])
            if self._offen and self.zeilen and self.zeilen[-1][0] == rolle:
                self.zeilen[-1][1] = sauber(self.zeilen[-1][1] + stueck)
            else:
                self.zeilen.append([rolle, sauber(stueck)])
            self.zeilen[-1][1] = self.zeilen[-1][1][-MAX_TEXT:]
            self.zeilen = self.zeilen[-MAX_ZEILEN:]
            self._offen = True
        elif art == "zugende":
            self._offen = False
        elif art == "werkzeug":
            self.werkzeug = ev[1]
        elif art == "fehler":
            self.zustand = "fehler"
            self.fehler = ev[1]
