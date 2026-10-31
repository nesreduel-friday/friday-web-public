"""Umgebung — wo Friday gerade läuft.

Phase 3: Der Browser meldet, worin er steckt (Handy im Browser, Handy als
installierte App, PC im Browser, Desktop-Installation). Friday bekommt das in
den System-Prompt, die Einstellungen-Seite zeigt es an.

Gedacht als Grundlage: Später soll „mach mir XY auf" wissen, ob ein Handy oder
ein PC vor ihr steht — auf einem iPhone öffnet man anderes als auf Windows.

WICHTIG: Der Browser ist eine fremde Quelle. Was er meldet, landet im
System-Prompt, also wird KEIN Freitext übernommen. Jeder Wert muss in einer
festen Liste stehen, sonst wird er zu "unbekannt". Damit lässt sich über eine
erfundene Gerätemeldung nichts in den Prompt schmuggeln.

Gehalten wird das im Arbeitsspeicher, nicht auf der Platte: Nach einem Neustart
meldet sich der Browser beim nächsten Laden ohnehin neu, und es ist nichts,
was Eike überdauern muss.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

# Feste Listen statt Freitext — siehe Modulkopf.
ARTEN = {"browser": "im Browser", "installiert": "als App installiert", "overlay": "über das Friday-Overlay"}
GERAETE = {"Handy", "Tablet", "PC"}
SYSTEME = {"iOS", "iPadOS", "Android", "Windows", "macOS", "Linux"}
BROWSER = {"Safari", "Chrome", "Firefox", "Edge", "Opera"}

UNBEKANNT = "unbekannt"
MAX_CLIENTS = 10          # mehr Oberflächen hat niemand gleichzeitig offen
FRISCH_SEKUNDEN = 180     # so lange gilt eine Oberfläche als offen (Browser meldet sich alle 60 s)
VERFALL_SEKUNDEN = 900    # danach verschwindet sie ganz aus der Liste

_sperre = threading.Lock()
_clients: dict[str, dict] = {}


def _wahl(wert, erlaubt: set[str]) -> str:
    """Nur was in der Liste steht — alles andere ist unbekannt."""
    text = str(wert or "").strip()
    return text if text in erlaubt else UNBEKANNT


def _zahl(wert) -> int:
    try:
        return max(0, min(20000, int(wert)))
    except (TypeError, ValueError):
        return 0


def _kennung(wert) -> str:
    """Die Kennung kommt vom Browser und dient nur dazu, mehrere Oberflächen
    auseinanderzuhalten. Auf harmlose Zeichen begrenzt, damit sie nirgends
    etwas anrichtet."""
    text = "".join(z for z in str(wert or "") if z.isalnum() or z in "-_")[:64]
    return text or "ohne-kennung"


def _aufraeumen() -> None:
    """Geschlossene Oberflächen verschwinden. Ohne das bleibt jede Sitzung, die
    je geöffnet war, für immer stehen — am 13.09.2026 zeigte die Liste drei
    Einträge, von denen keiner mehr offen war (einer davon eine Prüfmeldung).
    Nur innerhalb von `_sperre` aufrufen: die Sperre ist nicht wiedereintrittsfähig."""
    grenze = datetime.now(timezone.utc) - timedelta(seconds=VERFALL_SEKUNDEN)
    for k, e in list(_clients.items()):
        try:
            if datetime.fromisoformat(str(e.get("zuletzt"))) < grenze:
                del _clients[k]
        except (TypeError, ValueError):
            del _clients[k]


def melden(daten: dict) -> dict:
    """Nimmt eine Meldung des Browsers an und gibt zurück, was davon gilt."""
    eintrag = {
        "kennung": _kennung(daten.get("kennung")),
        "art": daten.get("art") if daten.get("art") in ARTEN else UNBEKANNT,
        "geraet": _wahl(daten.get("geraet"), GERAETE),
        "system": _wahl(daten.get("system"), SYSTEME),
        "browser": _wahl(daten.get("browser"), BROWSER),
        "breite": _zahl(daten.get("breite")),
        "hoehe": _zahl(daten.get("hoehe")),
        "zuletzt": datetime.now(timezone.utc).isoformat(),
    }
    with _sperre:
        _clients[eintrag["kennung"]] = eintrag
        _aufraeumen()
        # Älteste rauswerfen, falls doch mehr auflaufen.
        if len(_clients) > MAX_CLIENTS:
            for k in sorted(_clients, key=lambda k: _clients[k]["zuletzt"])[:-MAX_CLIENTS]:
                _clients.pop(k, None)
    return eintrag


def aktuelle() -> dict | None:
    """Die zuletzt gemeldete Oberfläche — dort sitzt Eike gerade."""
    with _sperre:
        if not _clients:
            return None
        return dict(max(_clients.values(), key=lambda e: e["zuletzt"]))


def alle() -> list[dict]:
    """Alle bekannten Oberflächen, zuletzt benutzte zuerst. Verfallenes ist weg."""
    with _sperre:
        _aufraeumen()
        return sorted((dict(e) for e in _clients.values()), key=lambda e: e["zuletzt"], reverse=True)


def offene() -> list[dict]:
    """Nur, worin Friday GERADE offen ist — und jede Oberfläche nur einmal.

    Zwei Kennungen können dieselbe Oberfläche beschreiben: Eine als App
    installierte Seite hat auf iOS einen eigenen Speicher als Safari daneben.
    Für die Anzeige zählt, was Eike sieht, nicht wie viele Speicher es gibt.
    """
    gesehen: dict[str, dict] = {}
    for e in alle():                       # neueste zuerst
        if ist_aktiv(e):
            gesehen.setdefault(satz(e), e)
    return list(gesehen.values())


def ist_aktiv(eintrag: dict) -> bool:
    """Ist diese Oberfläche gerade offen? Der Browser meldet sich regelmäßig;
    bleibt das aus, gilt sie als geschlossen."""
    try:
        zuletzt = datetime.fromisoformat(str(eintrag.get("zuletzt")))
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - zuletzt).total_seconds() <= FRISCH_SEKUNDEN


def satz(eintrag: dict | None = None) -> str:
    """Eine Zeile im Klartext: „Handy (iOS), als App installiert — Safari“."""
    e = eintrag if eintrag is not None else aktuelle()
    if not e:
        return ""
    art = ARTEN.get(e.get("art"), "auf unbekanntem Weg")
    browser = e.get("browser", UNBEKANNT)
    zusatz = f" — {browser}" if browser != UNBEKANNT else ""     # der Overlay hat keinen Browser
    return f"{e.get('geraet', UNBEKANNT)} ({e.get('system', UNBEKANNT)}), {art}{zusatz}"


def fuer(kennung: str) -> dict | None:
    """Die Oberfläche mit dieser Kennung — die, an der Eike gerade sitzt."""
    if not kennung:
        return None
    schluessel = _kennung(kennung)
    with _sperre:
        eintrag = _clients.get(schluessel)
        return dict(eintrag) if eintrag else None


def beschreibung(kennung: str = "") -> str:
    """Der Block für den System-Prompt. Leer, solange sich nichts gemeldet hat.

    MIT Kennung ist es sicher: Die Anfrage kam von genau dieser Oberfläche.
    OHNE Kennung bleibt nur die zuletzt gemeldete — und die kann die falsche
    sein. Am 13.09.2026 antwortete Friday dreimal hintereinander verschieden
    auf „Wo bist du?", weil Handy und PC gleichzeitig offen waren. Dann sagt
    sie das lieber dazu, statt sich auf eine Oberfläche festzulegen.
    """
    genau = fuer(kennung)
    e = genau or aktuelle()
    if not e:
        return ""
    if genau:
        text = ("[UMGEBUNG] Wo Eike dich gerade benutzt: " + satz(e) + ". "
                "Fragt er danach, sag genau das. Rate nichts dazu — mehr weißt du über sein Gerät nicht.")
        if e.get("geraet") == "PC":
            # Sonst nimmt das Modell „öffne Spotify“ für das Handy (13.09.-Fall, 06.10.2026 am PC wieder).
            text += (" Er sitzt am PC: Programme öffnest du mit pc_starten, Webseiten mit pc_webseite — "
                     "nie mit oeffnen, das ist für sein Handy oder Tablet. Sagt er „öffne …“, tu es SOFORT und "
                     "frag nicht nach, ob es am PC sein soll (07.10.2026: unnötige Rückfrage); sag danach "
                     "kurz, was du geöffnet hast.")
        return text
    offen = len(offene())
    text = "[UMGEBUNG] Zuletzt gemeldet wurde: " + satz(e) + "."
    if offen > 1:
        text += (f" Es sind gerade {offen} Oberflächen offen — du weißt NICHT sicher, an welcher er "
                 "gerade sitzt. Sag das dazu, statt dich festzulegen.")
    return text + " Rate nichts dazu — mehr weißt du über sein Gerät nicht."


def vergessen() -> None:
    """Alles verwerfen. Für Tests."""
    with _sperre:
        _clients.clear()
