"""WhatsApp-Exporte einlesen — iPhone und Android, deutsches Format.

Ein Export ist eine `.txt` (oft in einer `.zip`). Jede Nachricht beginnt mit
einem Zeitstempel; Folgezeilen ohne Stempel gehören zur vorigen Nachricht.

  iPhone:  [16.03.22, 21:34:55] Max Mustermann: Text
  Android: 16.03.22, 21:34 - Max Mustermann: Text

Systemnachrichten ("… Ende-zu-Ende-verschlüsselt", "… hat den Betreff geändert)
haben keinen Absender mit Doppelpunkt und werden übersprungen. Medienplatzhalter
("Bild weggelassen", "<Medien ausgeschlossen>") auch — bei diesem Export ist
ohnehin nur Text da.

Bewusst zeilenweise (ein Generator), nicht alles im Speicher: die Exporte sind
zusammen ~0,6 GB.
"""

from __future__ import annotations

import re
from datetime import datetime

# Der Zeitstempel am Zeilenanfang, beide Formate. Das optionale [ und das
# unsichtbare Steuerzeichen (U+200E) am Anfang kommen bei iPhone-Exporten vor.
_KOPF = re.compile(
    r"^[‎‏]?\[?"
    r"(\d{1,2})\.(\d{1,2})\.(\d{2,4}),?\s+"       # Datum
    r"(\d{1,2}):(\d{2})(?::(\d{2}))?"             # Uhrzeit (Sekunden optional)
    r"\s*(?:\]|-)\s*"                             # ] (iPhone) oder - (Android)
    r"(.*)$",                                     # Rest: "Absender: Text" oder Systemtext
    re.DOTALL,
)

# Platzhalter für weggelassene Medien — in mehreren Sprachvarianten.
_MEDIEN = re.compile(r"(weggelassen|ausgeschlossen|omitted|<Medien|<Media|GIF ausgel|sticker weggel)", re.I)


def _zeit(tag, monat, jahr, stunde, minute, sekunde) -> str | None:
    try:
        j = int(jahr)
        if j < 100:
            j += 2000
        return datetime(j, int(monat), int(tag), int(stunde), int(minute),
                        int(sekunde or 0)).isoformat(timespec="seconds")
    except ValueError:
        return None


def _saeubern(text: str) -> str:
    return text.replace("‎", "").replace("‏", "").strip()


def nachrichten(zeilen):
    """Aus rohen Zeilen Nachrichten machen: {"zeit","absender","text"}.

    `zeilen` ist alles Iterierbare über Strings (eine geöffnete Datei etwa).
    Systemnachrichten und reine Medienplatzhalter fallen weg.
    """
    aktuell = None

    def fertig(n):
        if not n:
            return None
        text = _saeubern("\n".join(n["zeilen"]))
        if not text or _MEDIEN.search(text):
            return None
        return {"zeit": n["zeit"], "absender": n["absender"], "text": text}

    for zeile in zeilen:
        zeile = zeile.rstrip("\n")
        m = _KOPF.match(zeile)
        if not m:
            if aktuell is not None:                      # Folgezeile einer Nachricht
                aktuell["zeilen"].append(zeile)
            continue
        zeit = _zeit(*m.groups()[:6])
        rest = m.group(7)
        if zeit is None:
            if aktuell is not None:
                aktuell["zeilen"].append(zeile)
            continue
        fertige = fertig(aktuell)
        if fertige:
            yield fertige
        # "Absender: Text" — ein Doppelpunkt trennt. Ohne Doppelpunkt: Systemnachricht.
        if ": " in rest:
            absender, _, text = rest.partition(": ")
            aktuell = {"zeit": zeit, "absender": _saeubern(absender)[:120], "zeilen": [text]}
        else:
            aktuell = None                               # Systemnachricht überspringen
    fertige = fertig(aktuell)
    if fertige:
        yield fertige


def partner_aus_name(dateiname: str) -> str:
    """Chatname aus dem Dateinamen raten: 'WhatsApp Chat mit Kontakt P.txt' -> 'Kontakt P'."""
    name = dateiname.rsplit("/", 1)[-1].removesuffix(".txt")
    name = re.sub(r"(?:WhatsApp )?Chat (?:mit|with|- )\s*", "", name, flags=re.I).strip()
    return name or dateiname.rsplit("/", 1)[-1].removesuffix(".txt")
