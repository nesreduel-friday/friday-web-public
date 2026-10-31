"""Konfiguration aus der Umgebung.

Wird bei jedem Zugriff gelesen, nicht einmal beim Import. So wirkt eine
geänderte .env nach einem Neustart, und Tests können Werte gezielt setzen.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = "0.4.0"
# 1 Stimme · 2 Gedächtnis · 3 Werkzeuge und Konnektoren · 4 proaktiv (begonnen:
# Benachrichtigungen stehen, Auslöser wie Erinnerungen fehlen noch).
PHASE = 4

STANDARD_STIMMMODELL = "gemini-3.1-flash-live-preview"
# Mehrere Modelle der Reihe nach: ist eins überlastet (503) oder am Limit
# (429), übernimmt das nächste. Mit nur einem Modell sah Eike am 11.09.2026
# um 23:12 "503 high demand" im Chat.
#
# Reihenfolge nach Messung vom 11.09.2026 (Median aus 3 Fragen, Nachdenken
# auf "low"): 3.6-flash 1,1 s · 3.5-flash 1,4 s · 3.8-flash 1,9 s ·
# 3.5-flash-lite 0,6 s. Lite ist am schnellsten, aber das schwächste Modell —
# deshalb nur letzte Reserve. 3.8-flash ohne "low" brauchte 7,7 s.
STANDARD_TEXTMODELLE = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"]
STANDARD_STIMME = "Callirrhoe"
ZEITZONE = "Europe/Berlin"

GEMINI_WS = ("wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage."
             "v1beta.GenerativeService.BidiGenerateContent")
GEMINI_REST = "https://generativelanguage.googleapis.com/v1beta"


def gemini_schluessel() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def gespraech_schluessel_liste() -> list[str]:
    """Schlüssel für Eikes Chat: erst der eigene, dann die Reserve (vierter,
    eigenes Projekt), dann der ganze Pool zusätzlicher Schlüssel — Chat/Sprechen
    darf davon jeden nehmen. Am 12.09.2026 war der erste zweimal am Tageslimit."""
    from kern.dienste import schluessel_pool
    liste = [gemini_schluessel(), os.getenv("RESERVE_GEMINI_API_KEY", "").strip(), *schluessel_pool.werte()]
    return list(dict.fromkeys(s for s in liste if s))


def basis_url() -> str:
    """Unter welcher Adresse Friday von außen erreichbar ist — für Rückläufer
    von OAuth-Anmeldungen (Spotify). Muss dort exakt eingetragen sein."""
    return os.getenv("FRIDAY_URL", "").strip() or "https://hphome.beispiel.ts.net"


def graph_schluessel() -> str:
    """Der Schlüssel, mit dem der Graphiti-Container arbeitet (eigenes Projekt).
    Friday selbst ruft damit nichts ab — nur zum Nachsehen, ob sein Tageslimit
    erschöpft ist."""
    return (os.getenv("GRAPH_GEMINI_API_KEY", "").strip()
            or os.getenv("GRAPHITI_GEMINI_API_KEY", "").strip() or gemini_schluessel())


def graph_schluessel_liste() -> list[str]:
    """Kandidaten, auf die der Graphiti-Container umschalten kann, wenn sein Schlüssel
    am Tageslimit ist: erst der eigene, dann der Pool ohne die letzten 2 — Graphiti
    läuft nicht interaktiv, bekommt also dieselbe Reserve wie der Hintergrund."""
    from kern.dienste import schluessel_pool
    liste = [graph_schluessel()]
    pool = schluessel_pool.werte()
    if len(pool) > 2:
        liste += pool[:-2]
    return list(dict.fromkeys(s for s in liste if s))


def graph_embedder() -> str:
    """Womit Graphiti Vektoren rechnet: 'lokal' (Container `embedder`) oder 'gemini' (Standard).
    Nur der exakte Wert 'openai' in EMBEDDER_PROVIDER_GRAPHITI schaltet um — alles andere bleibt Gemini."""
    return "lokal" if os.getenv("EMBEDDER_PROVIDER_GRAPHITI", "").strip().lower() == "openai" else "gemini"


def hintergrund_schluessel_liste() -> list[str]:
    """Schlüssel für Arbeit im Hintergrund (Elternbriefe, Mails), der Reihe nach.

    Das Limit zählt pro Google-Projekt, nicht pro Schlüssel — jeder hier kommt
    aus einem eigenen Projekt. Zuerst der dritte (nur für den Hintergrund), dann
    als Reserve der zweite (den teilt sich Graphiti), dann der Pool zusätzlicher
    Schlüssel — ABER ohne die letzten 2: die bleiben Chat/Sprechen vorbehalten,
    damit ein voll ausgeschöpfter Hintergrund (z. B. die nächtliche Verdichtung)
    nie den ganzen Vorrat für den Chat wegnimmt. Der erste bleibt Eikes
    Gesprächen vorbehalten und ist nur Notnagel, wenn sonst keiner da ist.
    """
    from kern.dienste import schluessel_pool
    liste = [os.getenv(n, "").strip() for n in ("HINTERGRUND_GEMINI_API_KEY", "GRAPHITI_GEMINI_API_KEY")]
    liste = list(dict.fromkeys(s for s in liste if s))
    liste = liste or ([gemini_schluessel()] if gemini_schluessel() else [])
    pool = schluessel_pool.werte()
    if len(pool) > 2:
        liste += pool[:-2]
    return list(dict.fromkeys(s for s in liste if s))


def hausaufgaben_schluessel_liste() -> list[str]:
    """Bücher indexieren und Hausaufgaben bearbeiten: erst der eigene Schlüssel
    (eigenes Projekt, viele Bildseiten), dann die Hintergrund-Schlüssel."""
    eigener = os.getenv("HAUSAUFGABEN_GEMINI_API_KEY", "").strip()
    return list(dict.fromkeys(s for s in [eigener, *hintergrund_schluessel_liste()] if s))


def buecher_schluessel_liste() -> list[str]:
    """Schulbücher indexieren (viele Bildseiten): eigener Schlüssel (eigenes
    Projekt, getrennt vom Lösen), dann die Hausaufgaben-Schlüssel."""
    eigener = os.getenv("BUECHER_GEMINI_API_KEY", "").strip()
    return list(dict.fromkeys(s for s in [eigener, *hausaufgaben_schluessel_liste()] if s))


def loesen_schluessel_liste() -> list[str]:
    """Hausaufgaben lösen (Buchseiten ansehen, Aufgaben bearbeiten): erst der
    eigene Schlüssel (eigenes Projekt, getrennt vom Einlesen), dann die Reserve
    (eigenes Projekt, für Tage, an denen das Kontingent des ersten reicht),
    dann die Hausaufgaben-Schlüssel."""
    eigener = os.getenv("LOESEN_GEMINI_API_KEY", "").strip()
    reserve = os.getenv("LOESEN_RESERVE_GEMINI_API_KEY", "").strip()
    return list(dict.fromkeys(s for s in [eigener, reserve, *hausaufgaben_schluessel_liste()] if s))


def arbeiten_schluessel_liste() -> list[str]:
    """Klassenarbeiten lesen (Fach, Datum, Note, Randnotizen): eigener Schlüssel (eigenes Projekt),
    dann die Hausaufgaben-Schlüssel."""
    eigener = os.getenv("ARBEITEN_GEMINI_API_KEY", "").strip()
    return list(dict.fromkeys(s for s in [eigener, *hausaufgaben_schluessel_liste()] if s))


def goodnotes_schluessel_liste() -> list[str]:
    """Goodnotes-Hefte abschreiben: eigener Schlüssel (eigenes Projekt, viele Seiten),
    dann die Hausaufgaben-Schlüssel."""
    eigener = os.getenv("GOODNOTES_GEMINI_API_KEY", "").strip()
    return list(dict.fromkeys(s for s in [eigener, *hausaufgaben_schluessel_liste()] if s))


def hintergrund_schluessel() -> str:
    liste = hintergrund_schluessel_liste()
    return liste[0] if liste else ""


def stimmmodell() -> str:
    return os.getenv("GEMINI_LIVE_MODEL", "").strip() or STANDARD_STIMMMODELL


def textmodelle() -> list[str]:
    """Erstes Modell zuerst, die übrigen als Ersatz. GEMINI_TEXTMODELL darf
    eines oder mehrere (kommagetrennt) nennen; die Standardliste bleibt
    dahinter als letzter Ersatz."""
    eigene = [m.strip() for m in os.getenv("GEMINI_TEXTMODELL", "").split(",") if m.strip()]
    return list(dict.fromkeys(eigene + STANDARD_TEXTMODELLE))


def textmodell() -> str:
    return textmodelle()[0]


def stimme() -> str:
    return os.getenv("FRIDAY_STIMME", "").strip() or STANDARD_STIMME


# Überschreibbar, damit Tests gegen eine Gemini-Attrappe laufen können —
# so ist der Wiederaufbau geprüft, bevor er gebraucht wird.
def gemini_ws_url() -> str:
    return os.getenv("GEMINI_WS_URL", "").strip() or GEMINI_WS


def gemini_rest() -> str:
    return os.getenv("GEMINI_REST_URL", "").strip() or GEMINI_REST


def jetzt() -> datetime:
    return datetime.now(ZoneInfo(ZEITZONE))


def daten_ordner() -> Path:
    standard = Path(__file__).resolve().parent.parent / "data"
    return Path(os.getenv("FRIDAY_DATEN", "") or standard)


def schwaerzen(text: str) -> str:
    """Schlüssel aus Texten entfernen, bevor sie in Logs oder zum Browser gehen."""
    from kern.dienste import schluessel_pool
    for schluessel in (gemini_schluessel(), os.getenv("GRAPHITI_GEMINI_API_KEY", "").strip(),
                       os.getenv("HINTERGRUND_GEMINI_API_KEY", "").strip(),
                       os.getenv("RESERVE_GEMINI_API_KEY", "").strip(),
                       os.getenv("HAUSAUFGABEN_GEMINI_API_KEY", "").strip(),
                       os.getenv("GOODNOTES_GEMINI_API_KEY", "").strip(),
                       os.getenv("BUECHER_GEMINI_API_KEY", "").strip(),
                       os.getenv("LOESEN_GEMINI_API_KEY", "").strip(),
                       os.getenv("LOESEN_RESERVE_GEMINI_API_KEY", "").strip(),
                        os.getenv("ARBEITEN_GEMINI_API_KEY", "").strip(),
                        *schluessel_pool.werte()):
        if schluessel:
            text = text.replace(schluessel, "<SCHLÜSSEL>")
    return text
