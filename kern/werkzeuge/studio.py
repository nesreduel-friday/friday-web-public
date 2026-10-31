"""Studio-Werkzeuge — der YouTube-Kanal.

Lesen ist frei, Veröffentlichen nicht: `studio_hochladen` hängt am Schreibrecht
UND braucht Eikes ausdrückliches Ja im selben Zug — wie beim Kalender. Ein
öffentlicher Kanal ist nichts, was ein Modell allein entscheiden darf.

Die Aufträge selbst gehen als Datei an den Studio-Container (studio/arbeiter.py).
Friday hat bewusst keinen Zugriff auf den Docker-Socket.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from kern import config
from kern.dienste import studio as dienst

KONNEKTOR = "studio"
MAX_ANZEIGE = 10


def _ordner(name: str) -> Path:
    pfad = config.daten_ordner() / "studio" / name
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


def _fertige() -> list[dict]:
    """Was der Arbeiter abgeliefert hat, neueste zuerst."""
    eintraege = []
    for datei in _ordner("fertig").glob("*.json"):
        try:
            d = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d["kennung"] = datei.stem
        eintraege.append(d)
    return sorted(eintraege, key=lambda d: str(d.get("beendet") or ""), reverse=True)


def _zeit(wert) -> str:
    try:
        return datetime.fromisoformat(str(wert)).astimezone().strftime("%d.%m. %H:%M")
    except (TypeError, ValueError):
        return ""


async def studio_stand() -> str:
    """Wie der Kanal läuft — mit Rückhaltung, nicht nur Aufrufen.

    Aufrufe allein sagen nichts darüber, WARUM etwas lief. Über die Ausspielung
    entscheidet, wie viel Prozent tatsächlich geschaut wurden; Abonnenten sind
    davon unabhängig (auf diesem Kanal brachte ein Video mit 39,5 % die meisten).
    """
    try:
        daten = await dienst.leistung(90)
    except dienst.StudioFehler as exc:
        return str(exc)
    except Exception as exc:
        return f"YouTube antwortet gerade nicht ({type(exc).__name__})."

    videos = daten.get("videos") or []
    if not videos:
        # Ohne Auswertungsdaten wenigstens die nackten Zahlen, statt gar nichts.
        try:
            k = await dienst.kanal()
        except Exception:
            return "Zum Kanal bekomme ich gerade keine Zahlen."
        return (f"Kanal {k['name']}: {k['aufrufe']} Aufrufe, {k['abonnenten']} Abonnenten, "
                f"{k['videos']} Videos. Auswertungsdaten liegen noch keine vor.")

    # Derselbe Mindestfilter wie im Planer: Ein Video mit 20 Aufrufen kann 74 %
    # halten und ist trotzdem kein Vorbild — und eines mit einem einzigen Aufruf
    # taugt nicht als Negativbeispiel.
    belastbar = [v for v in videos if v["aufrufe"] >= dienst.MINDESTAUFRUFE] or videos

    zeilen = [daten["zusammenfassung"], "", "Am besten gehalten:"]
    for v in belastbar[:5]:
        zeilen.append(f"  {v['rueckhaltung']:.0f}% geschaut, {v['aufrufe']} Aufrufe"
                      f"{f', +{v['abos']} Abos' if v['abos'] else ''}  — {v['titel'][:52]}")
    schwach = sorted(belastbar, key=lambda v: v["rueckhaltung"])[:3]
    if schwach:
        zeilen.append("Am frühesten weggeklickt:")
        for v in schwach:
            zeilen.append(f"  {v['rueckhaltung']:.0f}% geschaut, {v['aufrufe']} Aufrufe"
                          f"  — {v['titel'][:52]}")
    return "\n".join(zeilen)


async def studio_wartet() -> str:
    """Welche Videos auf Eikes Freigabe warten."""
    offen = [d for d in _fertige() if d.get("status") == "fertig" and not d.get("hochgeladen")]
    fehler = [d for d in _fertige() if d.get("status") == "fehler"]
    if not offen and not fehler:
        return "Es wartet gerade kein Video auf dich."

    zeilen = []
    if offen:
        zeilen.append(f"{len(offen)} Video{'s' if len(offen) > 1 else ''} wartet auf deine Freigabe:")
        for d in offen[:MAX_ANZEIGE]:
            zeilen.append(f"  {d['kennung']}: „{d.get('titel') or d.get('thema', '?')}“"
                          f" ({d.get('groesse_mb', '?')} MB, fertig {_zeit(d.get('beendet'))})")
            # Worum es geht, steht im eigenen Plan — Gespräch 231 suchte es im Internet.
            if inhalt := str(d.get("beschreibung") or d.get("aufhaenger") or "").strip():
                zeilen.append(f"    Inhalt: {inhalt[:300]}")
    if fehler:
        zeilen.append(f"{len(fehler)} Versuch{'e' if len(fehler) > 1 else ''} ging schief:")
        for d in fehler[:3]:
            grund = str(d.get("fehler") or "").splitlines()[0][:90]
            zeilen.append(f"  {d['kennung']}: {grund}")
    return "\n".join(zeilen)


async def studio_hochladen(kennung: str = "", zustimmung: bool = False) -> str:
    """Ein wartendes Video veröffentlichen — nur mit Eikes ausdrücklichem Ja."""
    wartende = [d for d in _fertige() if d.get("status") == "fertig" and not d.get("hochgeladen")]
    if not wartende:
        return "Es wartet gerade kein fertiges Video."
    eintrag = next((d for d in wartende if d["kennung"] == kennung), wartende[0]) if kennung else wartende[0]

    titel = eintrag.get("titel") or eintrag.get("thema", "")
    if not zustimmung:
        return (f"Soll ich „{titel}“ auf dem Kanal {dienst.KANAL} veröffentlichen? "
                "Es geht dann sofort öffentlich. Sag ja, dann lade ich es hoch.")

    video = _ordner("fertig") / str(eintrag.get("video") or f"{eintrag['kennung']}.mp4")
    try:
        ergebnis = await dienst.hochladen(
            video, titel=titel,
            beschreibung=eintrag.get("beschreibung", ""),
            schlagworte=eintrag.get("schlagworte") or [],
            oeffentlich=True)
    except dienst.StudioFehler as exc:
        return str(exc)
    except Exception as exc:
        return f"Das Hochladen ging nicht ({type(exc).__name__})."

    eintrag["hochgeladen"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    eintrag["url"] = ergebnis["url"]
    (_ordner("fertig") / f"{eintrag['kennung']}.json").write_text(
        json.dumps(eintrag, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"Veröffentlicht: {ergebnis['url']} — „{titel}“."


studio_hochladen.kennt_zustimmung = True


async def studio_verwerfen(kennung: str = "") -> str:
    """Ein Video verwerfen. Die Datei wird gelöscht, das Thema bleibt vermerkt."""
    wartende = [d for d in _fertige() if d.get("status") in ("fertig", "fehler")
                and not d.get("hochgeladen")]
    if not wartende:
        return "Es wartet gerade nichts, das ich verwerfen könnte."
    eintrag = next((d for d in wartende if d["kennung"] == kennung), wartende[0]) if kennung else wartende[0]

    for datei in (_ordner("fertig") / str(eintrag.get("video") or ""),
                  _ordner("fertig") / f"{eintrag['kennung']}.json"):
        try:
            datei.unlink(missing_ok=True)
        except OSError:
            pass
    return f"Verworfen: „{eintrag.get('titel') or eintrag.get('thema', eintrag['kennung'])}“."


ERKLAERUNGEN = [
    {"name": "studio_stand",
     "description": ("Wie der YouTube-Kanal läuft: Aufrufe, Abonnenten und wie die letzten Videos "
                     "ankamen. Benutzen bei 'wie läuft der Kanal', 'wie viele Abonnenten'."),
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "studio_wartet",
     "description": ("Welche fertigen Videos auf Eikes Freigabe warten und welche Versuche "
                     "schiefgingen. Benutzen bei 'ist ein Video fertig', 'was wartet'."),
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "studio_hochladen",
     "description": ("Veröffentlicht ein wartendes Video auf dem Kanal — SOFORT ÖFFENTLICH. "
                     "Ohne Eikes ausdrückliches Ja fragst du nur nach und lädst nichts hoch. "
                     "Sage hinterher genau das weiter, was zurückkommt, samt Adresse."),
     "parameters": {"type": "OBJECT", "properties": {
         "kennung": {"type": "STRING", "description": "Welches Video, leer = das älteste wartende"},
     }}},
    {"name": "studio_verwerfen",
     "description": "Verwirft ein wartendes Video, damit ein neues geplant werden kann.",
     "parameters": {"type": "OBJECT", "properties": {
         "kennung": {"type": "STRING", "description": "Welches Video, leer = das älteste wartende"},
     }}},
]

# Veröffentlichen verändert etwas in der Welt und ist nicht zurückzuholen.
NUR_MIT_SCHREIBRECHT = {"studio_hochladen"}

# Ändert etwas und verlangt kein „ja“: nach fremdem Text im selben Zug gesperrt (katalog.ausfuehren).
NICHT_NACH_FREMDTEXT = {"studio_verwerfen"}

WERKZEUGE = {"studio_stand": studio_stand, "studio_wartet": studio_wartet,
             "studio_hochladen": studio_hochladen, "studio_verwerfen": studio_verwerfen}
