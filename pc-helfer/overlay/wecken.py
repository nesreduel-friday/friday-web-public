"""Wake-Word „Friday“ — läuft lokal auf dem PC, ohne Konto und ohne Cloud.

Vosk mit einer kleinen Grammatik: das Wort, ähnlich klingende Lockvögel und `[unk]`.
Nur das Wort selbst löst aus; die Lockvögel fangen Fehltreffer ab.
Eingabe: 16 kHz, mono, 16 Bit (little endian), beliebige Blockgröße.
"""

from __future__ import annotations

import json


# Ein echtes „Friday“ trifft über ~1 s hintereinander (Messung 06.10.2026: 27 Blöcke). Ein einzelner
# Fehltreffer aus dem Hintergrund dauert nur einen Block. Drei Blöcke in Folge trennen beides.
MIN_FOLGE = 4

# Ähnlich klingende Wörter als Lockvögel (Log 06./07.10.2026: 79 Auslöser in 1,5 Tagen, der Pegel trennt
# sie nicht von echten Rufen). Mit nur „friday“ + [unk] wird jedes ähnliche Geräusch zu „friday“; mit
# Alternativen gewinnt bei Unklarheit das passendere Wort. Fehlt eines im Modell, ignoriert Vosk es.
LOCKVOEGEL = [
    "fried", "fry", "fries", "friend", "friends", "frida", "freddy", "fright", "fridge", "bridge", "pride",
    "right", "bright", "fine", "five", "fire", "i", "my", "the", "a", "day", "today", "yesterday", "holiday",
    "birthday", "monday", "tuesday", "wednesday", "thursday", "saturday", "sunday", "friday's",
]


class Wecker:
    def __init__(self, modell_pfad: str, wort: str = "friday"):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        self._modell = Model(modell_pfad)
        self._wort = wort.lower()
        self._grammatik = json.dumps([self._wort, *LOCKVOEGEL, "[unk]"])
        self._rec = KaldiRecognizer(self._modell, 16000, self._grammatik)
        self._folge = 0           # Treffer hintereinander (ein Block = 100 ms)

    def hoere(self, daten: bytes) -> bool:
        """True, sobald das Wort erkannt wurde. Danach startet der Erkenner neu."""
        if not daten:
            return False
        fertig = self._rec.AcceptWaveform(daten)
        if fertig:
            text = json.loads(self._rec.Result()).get("text", "")
        else:
            text = json.loads(self._rec.PartialResult()).get("partial", "")
        if self._wort in text.split():
            self._folge += 1
            if self._folge >= MIN_FOLGE:
                self._rec.Reset()
                self._folge = 0
                return True
            return False
        self._folge = 0
        return False


def pegel_reicht(spitze: float, boden: float, absolut: float = 0.006, faktor: float = 3.0) -> bool:
    """Ist das Wort laut genug? Die Spitze muss über einer festen Untergrenze liegen UND deutlich über dem
    Grundrauschen (`boden`), das der Aufrufer laufend misst. So zählt ein leises Mikro genauso wie ein lautes."""
    return spitze >= absolut and spitze >= faktor * boden
