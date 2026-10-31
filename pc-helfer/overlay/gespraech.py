"""Ein Gespräch mit Friday über den Kern — dasselbe Protokoll wie `web/sprechen.js`.

Der Overlay schickt: `start` (mit Kennung), `audio` (16 kHz PCM16, base64), `stop`.
Der Kern schickt: `ready`, `audio` (24 kHz PCM16), `text`, `turn_end`, `interrupted`, `werkzeug`, `fehler`.

Halb-Duplex: Solange Friday spricht, geht das Mikro nicht hinaus. Sonst hört sie sich selbst.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import queue
import threading
import time
from collections import deque
from typing import Callable

import numpy as np
import websockets

log = logging.getLogger("friday_overlay")

EIN_RATE = 16000
AUS_RATE = 24000
LEERLAUF_SEKUNDEN = 90      # so lange ohne Stimme (in beide Richtungen) bleibt die Sitzung offen
BEREIT_SEKUNDEN = 20        # Wartezeit auf `ready` vom Kern
NACHHALL_SEKUNDEN = 0.5     # nach Fridays letztem Ton noch so lange nichts senden
SPRACHE_PEGEL = 0.02        # Schwelle, ab der ein Mikro-Block als Sprache zählt

# Eikes Satz zum Beenden. Erkannt wird das aus dem Transkript, nicht vom Modell — so bleibt es verlässlich.
ENDE_SAETZE = ("danke reicht", "das reicht", "friday schließen", "friday beenden", "tschüss friday")


def _woerter(text: str) -> list[str]:
    return "".join(z if z.isalnum() else " " for z in text.lower()).split()


def soll_enden(text: str) -> bool:
    """Hat Eike das Gespräch beendet? Groß/klein und Satzzeichen spielen keine Rolle, und die Spracherkennung
    darf ein Wort anders schreiben: „reich“ statt „reicht“, „schließ“ statt „schließen“. Die Wörter müssen
    dicht beieinander stehen (höchstens zwei dazwischen), damit „danke für deine Hilfe“ nicht zählt."""
    woerter = _woerter(text)
    sauber = " ".join(woerter)
    if any(satz in sauber for satz in ENDE_SAETZE):
        return True
    for i, wort in enumerate(woerter):
        fenster = woerter[i:i + 3]
        if wort == "danke" and any(w.startswith("reich") for w in fenster[1:]):
            return True
        if wort.startswith("reich") and "danke" in woerter[max(0, i - 2):i + 3]:
            return True                      # „reicht, danke“
        if wort == "friday" and any(w.startswith(("schlie", "beend")) for w in fenster[1:]):
            return True
    return False


def _nah_am_endesatz(text: str) -> bool:
    """Klingt es nach einem Endesatz, ohne einer zu sein? Dann lohnt sich ein Protokolleintrag."""
    w = _woerter(text)
    return any(x.startswith(("danke", "reich", "schlie", "beend")) or x in ("tschüss", "tschuss") for x in w)


def _bereit_ton() -> bytes:
    """Ein kurzer, leiser Ton (0,12 s) — Zeichen, dass Friday zuhört. Kommt aus dem Lautsprecher, nicht von Gemini."""
    n = int(AUS_RATE * 0.12)
    t = np.arange(n) / AUS_RATE
    welle = 0.25 * np.sin(2 * np.pi * 880 * t) * np.hanning(n)
    return (welle * 32767).astype(np.int16).tobytes()


class Abspielpuffer:
    """Fertige Friday-Stimme, die der Lautsprecher-Strom nach und nach holt. Thread-sicher."""

    def __init__(self):
        self._sperre = threading.Lock()
        self._daten = bytearray()

    def hinzu(self, daten: bytes) -> None:
        with self._sperre:
            self._daten.extend(daten)

    def lies(self, n: int) -> bytes:
        """Höchstens n Bytes; was fehlt, füllt der Aufrufer mit Stille."""
        with self._sperre:
            stueck = bytes(self._daten[:n])
            del self._daten[:n]
            return stueck

    def leeren(self) -> None:
        with self._sperre:
            self._daten.clear()

    def leer(self) -> bool:
        with self._sperre:
            return not self._daten


def pegel(daten: bytes) -> float:
    """Lautstärke eines 16-Bit-Blocks als Zahl von 0 bis 1 (Effektivwert)."""
    if not daten:
        return 0.0
    welle = np.frombuffer(daten, dtype=np.int16).astype(np.float32) / 32768.0
    return float(min(1.0, np.sqrt(np.mean(welle * welle))))


class Sendetor:
    """Lärmschwelle vor dem Senden: Was leiser ist als das Grundrauschen (mal Faktor) und nicht kurz nach/vor Sprache
    liegt, geht als digitale Stille hinaus — nie gar nichts, sonst sieht Geminis Satzende-Erkennung keine Pause.
    Nachlauf hält das Tor nach Sprache offen (Atempausen), Vorlauf öffnet es rückwirkend (leise Satzanfänge)."""

    def __init__(self, absolut: float = 0.01, faktor: float = 2.0, nachlauf: int = 8, vorlauf: int = 2):
        self._absolut = absolut
        self._faktor = faktor
        self._nachlauf = nachlauf
        self._vorlauf = vorlauf
        self._boden: deque = deque(maxlen=100)      # 100 Blöcke à 100 ms = 10 s
        self._puffer: deque = deque()               # [Block, offen] — um `vorlauf` Blöcke verzögert
        self._rest = 0
        self.gesamt = 0
        self.stumm = 0

    def zuruecksetzen(self) -> None:
        """Friday spricht: nichts davon darf als Vorlauf/Nachlauf später hinausgehen (sonst hört sie ihr Satzende)."""
        self._puffer.clear()
        self._rest = 0

    def durch(self, daten: bytes, lautstaerke: float) -> bytes:
        self._boden.append(lautstaerke)
        boden = sorted(self._boden)[len(self._boden) // 5]       # 20. Perzentil = Grundrauschen
        laut = lautstaerke >= max(self._absolut, self._faktor * boden)
        eintrag = [daten, False]
        if laut:
            for e in self._puffer:
                e[1] = True
            self._rest = self._nachlauf
        if laut or self._rest > 0:
            eintrag[1] = True
            if not laut:
                self._rest -= 1
        self._puffer.append(eintrag)
        if len(self._puffer) <= self._vorlauf:
            return b"\x00" * len(daten)
        alt, offen = self._puffer.popleft()
        self.gesamt += 1
        if offen:
            return alt
        self.stumm += 1
        return b"\x00" * len(alt)


def kern_adresse(kern: str) -> str:
    """https://hphome… → wss://hphome…/ws/live"""
    basis = kern.rstrip("/")
    if basis.startswith("https://"):
        basis = "wss://" + basis[len("https://"):]
    elif basis.startswith("http://"):
        basis = "ws://" + basis[len("http://"):]
    return basis + "/ws/live"


class Gespraech:
    def __init__(self, kern: str, kennung: str, mikro: queue.Queue, abspiel: Abspielpuffer,
                 melden: Callable[..., None], leerlauf: float = LEERLAUF_SEKUNDEN,
                 tor: Sendetor | None = None, aktivitaet: dict | None = None):
        self._url = kern_adresse(kern)
        self._kennung = kennung
        self._mikro = mikro
        self._abspiel = abspiel
        self._melden = melden
        self._leerlauf = leerlauf
        self._tor = tor
        self._sperre_bis = 0.0
        self._aktivitaet = aktivitaet
        self._bereit = asyncio.Event()
        self._spricht_bis = 0.0
        self._zuletzt_aktiv = time.monotonic()
        self._zustand = ""
        self._nutzer = ""                 # was Eike zuletzt gesagt hat (für den Endesatz)
        self._enden_nach_stimme = False   # Endesatz gehört: nach Fridays Antwort beenden
        self._enden_seit = 0.0

    def _setze(self, zustand: str) -> None:
        if zustand != self._zustand:
            self._zustand = zustand
            self._melden("zustand", zustand)

    async def laufen(self) -> str:
        """Läuft, bis Leerlauf, Abbruch oder Fehler. Gibt den Grund zurück: leerlauf | abbruch | fehler."""
        self._setze("verbinde")
        log.info("Sitzung beginnt → %s", self._url)
        grund = await self._laufen()
        log.info("Sitzung beendet: %s", grund)
        if self._tor is not None and self._tor.gesamt:
            log.info("Sendetor: %d von %d Blöcken stumm geschaltet", self._tor.stumm, self._tor.gesamt)
        return grund

    async def _laufen(self) -> str:
        try:
            async with websockets.connect(self._url, max_size=None, ping_interval=20,
                                          open_timeout=15) as ws:
                start = {"type": "start", "kennung": self._kennung}
                if self._aktivitaet:
                    start["aktivitaet"] = self._aktivitaet
                await ws.send(json.dumps(start))
                aufgaben = [asyncio.ensure_future(self._senden(ws)),
                            asyncio.ensure_future(self._empfangen(ws))]
                fertig, offen = await asyncio.wait(aufgaben, return_when=asyncio.FIRST_COMPLETED)
                for aufgabe in offen:
                    aufgabe.cancel()
                await asyncio.gather(*offen, return_exceptions=True)
                ergebnis = fertig.pop()
                if ergebnis.exception() is not None:
                    raise ergebnis.exception()
                return ergebnis.result()
        except websockets.exceptions.ConnectionClosed:
            return "abbruch"
        except (OSError, asyncio.TimeoutError) as exc:
            log.warning("Verbindung fehlgeschlagen: %s %s", type(exc).__name__, exc)
            self._melden("fehler", f"Verbindung zu Friday: {type(exc).__name__}")
            return "abbruch"
        except _KernFehler as exc:
            log.warning("Kern-Fehler: %s", exc)
            self._melden("fehler", str(exc))
            return "fehler"

    async def _senden(self, ws) -> str:
        try:
            await asyncio.wait_for(self._bereit.wait(), timeout=BEREIT_SEKUNDEN)
        except asyncio.TimeoutError:
            raise _KernFehler("Friday antwortet nicht (Zeitüberschreitung).")
        self._zuletzt_aktiv = time.monotonic()
        self._setze("hoert")
        while True:
            self._pruefe_zustand()
            if self._enden_nach_stimme and not self._spricht() and time.monotonic() - self._enden_seit > 2.0:
                # Friday hat nicht mehr geantwortet (oder ihr Satz ist fertig): trotzdem beenden.
                await ws.send(json.dumps({"type": "stop"}))
                return "ende"
            if time.monotonic() - self._zuletzt_aktiv > self._leerlauf and not self._spricht():
                await ws.send(json.dumps({"type": "stop"}))
                return "leerlauf"
            try:
                daten = self._mikro.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.02)
                continue
            lautstaerke = pegel(daten)
            self._melden("pegel", lautstaerke)
            if lautstaerke > SPRACHE_PEGEL:
                self._zuletzt_aktiv = time.monotonic()
            if self._spricht():
                # Halb-Duplex: während Friday spricht, geht nichts hinaus — und das Tor merkt sich davon nichts.
                if self._tor is not None:
                    self._tor.zuruecksetzen()
                self._sperre_bis = time.monotonic() + NACHHALL_SEKUNDEN
                continue
            if time.monotonic() < self._sperre_bis:
                continue                      # Nachhall im Raum und Lautsprecher-Verzögerung abwarten
            if self._tor is not None:
                daten = self._tor.durch(daten, lautstaerke)
            await ws.send(json.dumps({"type": "audio",
                                      "data": base64.b64encode(daten).decode("ascii")}))

    async def _empfangen(self, ws) -> str:
        async for roh in ws:
            if isinstance(roh, bytes):
                continue
            nachricht = json.loads(roh)
            art = nachricht.get("type")
            if art != "audio":
                log.info("Kern: %s", art)
            if art == "ready":
                self._bereit.set()
                self._abspiel.hinzu(_bereit_ton())        # kurzer Ton: jetzt kannst du sprechen
                self._setze("hoert")
            elif art == "audio":
                raw = base64.b64decode(nachricht.get("data", ""))
                jetzt = time.monotonic()
                self._spricht_bis = max(self._spricht_bis, jetzt) + len(raw) / (2 * AUS_RATE)
                self._abspiel.hinzu(raw)
                self._zuletzt_aktiv = jetzt
                self._setze("spricht")
            elif art == "text":
                self._zuletzt_aktiv = time.monotonic()
                rolle, text = str(nachricht.get("role", "")), str(nachricht.get("text", ""))
                self._melden("text", rolle, text)
                if rolle == "du":
                    self._nutzer = (self._nutzer + text)[-200:]
                    if not soll_enden(self._nutzer) and _nah_am_endesatz(self._nutzer):
                        log.info("Nah am Endesatz, nicht erkannt: %r", self._nutzer[-80:])
                    if soll_enden(self._nutzer):
                        log.info("Eike hat beendet (%r)", self._nutzer[-60:])
                        self._enden_nach_stimme = True
                        self._enden_seit = time.monotonic()
            elif art == "turn_end":
                self._melden("zugende")
                self._nutzer = ""
                if self._enden_nach_stimme:
                    # Friday hat ihren Abschiedssatz fertig gesprochen — jetzt beenden.
                    await ws.send(json.dumps({"type": "stop"}))
                    return "ende"
            elif art == "interrupted":
                self._abspiel.leeren()
                self._spricht_bis = 0.0
                self._setze("hoert")
            elif art == "werkzeug":
                self._melden("werkzeug", str(nachricht.get("name", "")))
                self._setze("denkt")
            elif art == "fehler":
                raise _KernFehler(str(nachricht.get("message", "Unbekannter Fehler"))[:300])
        return "abbruch"

    def _spricht(self) -> bool:
        return time.monotonic() < self._spricht_bis

    def _pruefe_zustand(self) -> None:
        if self._zustand == "spricht" and not self._spricht():
            self._setze("hoert")


class _KernFehler(Exception):
    pass
