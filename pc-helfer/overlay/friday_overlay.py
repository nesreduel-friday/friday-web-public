"""Friday am PC: Wake-Word „Friday“, Overlay über allen Fenstern, Gespräch über den Kern.

Start: `pythonw friday_overlay.py` (ohne Konsole) oder `python friday_overlay.py` zum Testen.
Konfiguration: `konfig.json` neben dieser Datei (fehlende Werte nehmen die Standardwerte).

Drei Threads und der Qt-Hauptthread:
- Mikro-Callback (sounddevice): legt jeden Block in zwei Queues, eine für das Wake-Word, eine fürs Gespräch.
- Wecker-Thread: hört nur, solange Friday „lauscht“. Erkennt „Friday“ → Gespräch startet.
- Gesprächs-Thread (asyncio): eine Sitzung nach der anderen zum Kern (`/ws/live`).
- Herzschlag: meldet den PC alle 60 s beim Kern (`POST /api/umgebung`), damit Friday weiß, dass Eike am PC sitzt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import queue
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

HIER = Path(__file__).resolve().parent
STANDARD = {
    "kern": "https://hphome.beispiel.ts.net",
    "modell": "modelle/vosk-model-small-en-us-0.15",   # relativ zu HIER
    "wort": "friday",
    "kennung": "pc-overlay",
    "mikro": None,        # Gerätenummer oder Name; None = Standardgerät
    "ausgabe": None,
    "wake_absolut": 0.006,  # „Friday“ zählt nur, wenn es in den letzten 1,5 s über dieser Untergrenze lag ...
    "wake_faktor": 3.0,     # ... und mindestens so viel lauter als das Grundrauschen der letzten 10 s
    "sende_tor": True,      # Lärmschwelle vor dem Senden (Fernseher, Raumgespräche → digitale Stille)
    "sende_absolut": 0.01,  # Block zählt als Sprache ab dieser Lautstärke (nach Verstärkung) ...
    "sende_faktor": 2.0,    # ... und mindestens so laut wie das Zweifache des Grundrauschens der letzten 10 s
    "sende_nachlauf": 8,    # Blöcke (je 100 ms), die das Tor nach Sprache offen bleibt
    # Sprach-Aktivierung bei Gemini (Start/Ende: low|high, Millisekunden). Leer = Gemini-Standard.
    "aktivitaet": {"start": "low", "ende": "low", "vorlauf_ms": 300, "pause_ms": 900},
    "verstaerkung": 6.0,    # Software-Verstärkung des Mikros (Messung 06.10.: Sprache nur bei 0,01–0.04)
}
EIN_RATE = 16000
AUS_RATE = 24000
BLOCK = 1600             # 100 ms bei 16 kHz
VORLAUF_BLOECKE = 20     # 2 s Mikro von vor dem Wake-Word, damit der Satz vollständig ankommt
HERZSCHLAG_SEKUNDEN = 60

log = logging.getLogger("friday_overlay")


def konfig_laden() -> dict:
    cfg = dict(STANDARD)
    datei = HIER / "konfig.json"
    if datei.exists():
        cfg.update(json.loads(datei.read_text(encoding="utf-8")))
    return cfg


def _log_einrichten() -> None:
    handler = logging.handlers.RotatingFileHandler(HIER / "overlay.log", maxBytes=500_000, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def hinein(q: queue.Queue, daten) -> None:
    """Legt etwas in eine Queue. Ist sie voll, fällt der älteste Eintrag weg (nie blockieren)."""
    try:
        q.put_nowait(daten)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        q.put_nowait(daten)


class Overlay:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.kern = cfg["kern"].rstrip("/")
        self.ui = queue.Queue(maxsize=500)
        self.wecken_q: queue.Queue = queue.Queue(maxsize=50)
        self.gespraech_q: queue.Queue = queue.Queue(maxsize=50)
        self.lauscht = threading.Event()
        self.wach = threading.Event()
        self.beenden = threading.Event()
        self.abspiel = None          # Abspielpuffer, siehe gespraech.py
        self._ein = None
        self._aus = None

    def melden(self, *ev) -> None:
        hinein(self.ui, ev)

    # ---- Audio -----------------------------------------------------------------

    def _mikro_callback(self, indata, frames, zeit, status) -> None:
        daten = bytes(indata)
        if self.cfg["verstaerkung"] != 1.0:
            welle = np.frombuffer(daten, dtype=np.int16).astype(np.float32) * self.cfg["verstaerkung"]
            daten = np.clip(welle, -32768, 32767).astype(np.int16).tobytes()
        hinein(self.wecken_q, daten)
        hinein(self.gespraech_q, daten)

    def _ausgabe_callback(self, outdata, frames, zeit, status) -> None:
        daten = self.abspiel.lies(frames * 2)
        outdata[:len(daten)] = daten
        outdata[len(daten):] = b"\x00" * (frames * 2 - len(daten))

    def audio_starten(self) -> None:
        import sounddevice as sd
        from gespraech import Abspielpuffer

        self.abspiel = Abspielpuffer()
        self._ein = sd.RawInputStream(samplerate=EIN_RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                      device=self.cfg["mikro"], callback=self._mikro_callback)
        self._aus = sd.RawOutputStream(samplerate=AUS_RATE, channels=1, dtype="int16", blocksize=AUS_RATE // 10,
                                       device=self.cfg["ausgabe"], callback=self._ausgabe_callback)
        self._ein.start()
        self._aus.start()

    # ---- Threads ---------------------------------------------------------------

    def wecker_schleife(self) -> None:
        from wecken import Wecker

        modell = Path(self.cfg["modell"])
        if not modell.is_absolute():
            modell = HIER / modell
        wecker = Wecker(str(modell), self.cfg["wort"])
        log.info("Wake-Word bereit (%s)", self.cfg["wort"])
        from collections import deque
        from gespraech import pegel
        from wecken import pegel_reicht
        lautheit: deque = deque(maxlen=15)          # 15 Blöcke à 100 ms = 1,5 s
        grundrauschen: deque = deque(maxlen=100)    # 100 Blöcke = 10 s
        while not self.beenden.is_set():
            try:
                daten = self.wecken_q.get(timeout=0.5)
            except queue.Empty:
                continue
            lautheit.append(pegel(daten))
            grundrauschen.append(lautheit[-1])
            if not self.lauscht.is_set():
                continue
            if wecker.hoere(daten):
                laut = max(lautheit)
                boden = sorted(grundrauschen)[len(grundrauschen) // 5]     # 20. Perzentil = Grundrauschen
                if not pegel_reicht(laut, boden, self.cfg["wake_absolut"], self.cfg["wake_faktor"]):
                    log.info("Wort gehört, aber zu leise (Spitze %.3f, Grundrauschen %.3f) — verworfen", laut, boden)
                    continue
                log.info("Wake-Word erkannt (Spitze %.3f, Grundrauschen %.3f)", laut, boden)
                self.lauscht.clear()
                self.melden("zustand", "verbinde")
                self.wach.set()

    def gespraech_schleife(self) -> None:
        asyncio.run(self._gespraeche())

    async def _gespraeche(self) -> None:
        from gespraech import Gespraech, Sendetor

        self.lauscht.set()
        self.melden("zustand", "lauscht")
        while not self.beenden.is_set():
            await asyncio.to_thread(self.wach.wait, 1.0)
            if not self.wach.is_set():
                continue
            self.wach.clear()
            # Nur was älter als 2 s ist, fliegt raus. Was Eike direkt nach „Friday“ sagt, bleibt in der
            # Queue und geht los, sobald die Verbindung steht — sonst fehlt der Anfang des Satzes.
            while self.gespraech_q.qsize() > VORLAUF_BLOECKE:
                try:
                    self.gespraech_q.get_nowait()
                except queue.Empty:
                    break
            tor = (Sendetor(self.cfg["sende_absolut"], self.cfg["sende_faktor"], self.cfg["sende_nachlauf"])
                   if self.cfg["sende_tor"] else None)
            sitzung = Gespraech(self.kern, self.cfg["kennung"], self.gespraech_q, self.abspiel, self.melden,
                                tor=tor, aktivitaet=self.cfg["aktivitaet"])
            try:
                grund = await sitzung.laufen()
            except BaseException as exc:      # ein Fehler darf den Overlay nicht beenden — und landet im Log
                log.exception("Gespräch abgebrochen")
                grund = "fehler"
                self.melden("fehler", f"{type(exc).__name__}")
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
            if grund == "ende":
                # Fridays Abschied läuft noch aus den Lautsprechern. Erst danach wieder horchen,
                # sonst hört das Wake-Word ihre eigene Stimme und das Mikro nimmt sie als Vorlauf auf.
                await asyncio.to_thread(self._nachhall_abwarten)
            else:
                self.abspiel.leeren()
            self.melden("zustand", "lauscht")
            self.lauscht.set()

    def _nachhall_abwarten(self) -> None:
        """Wartet, bis Fridays Stimme aus ist, und einen Moment dazu. Danach ist das Mikro leer."""
        import time
        while not self.abspiel.leer():
            time.sleep(0.1)
        time.sleep(1.0)
        for q in (self.gespraech_q, self.wecken_q):      # auch der Wecker darf nichts Altes mehr hören
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    def herzschlag_schleife(self) -> None:
        while not self.beenden.is_set():
            self.umgebung_melden()
            self.beenden.wait(HERZSCHLAG_SEKUNDEN)

    def umgebung_melden(self) -> None:
        """Meldet den PC beim Kern. Ein Fehler (Kern nicht erreichbar) wird nur protokolliert."""
        daten = json.dumps({"kennung": self.cfg["kennung"], "art": "overlay", "geraet": "PC",
                            "system": "Windows", "browser": ""}).encode("utf-8")
        anfrage = urllib.request.Request(f"{self.kern}/api/umgebung", data=daten, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(anfrage, timeout=10).read()
        except Exception as exc:
            log.warning("Umgebung nicht gemeldet: %s", type(exc).__name__)

    def starten(self) -> None:
        self.audio_starten()
        for ziel in (self.wecker_schleife, self.gespraech_schleife, self.herzschlag_schleife):
            threading.Thread(target=ziel, daemon=True).start()

    def stoppen(self) -> None:
        self.beenden.set()
        for strom in (self._ein, self._aus):
            if strom is not None:
                strom.stop()
                strom.close()


def _tablett(app):
    """Symbol im Infobereich mit „Beenden“ — das Overlay ist meist versteckt, dort wäre sonst kein Menü."""
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon
    bild = QPixmap(64, 64)
    bild.fill(QColor(0, 0, 0, 0))
    mal = QPainter(bild)
    mal.setRenderHint(QPainter.Antialiasing)
    mal.setBrush(QColor("#6ea8ff"))
    mal.setPen(QColor(0, 0, 0, 0))
    mal.drawEllipse(10, 10, 44, 44)
    mal.end()
    tablett = QSystemTrayIcon(QIcon(bild), app)
    tablett.setToolTip("Friday")
    menue = QMenu()
    menue.addAction("Beenden", app.quit)
    tablett.setContextMenu(menue)
    tablett.show()
    return tablett


def main() -> int:
    _log_einrichten()
    cfg = konfig_laden()
    from PySide6.QtWidgets import QApplication
    from oberflaeche import Oberflaeche

    app = QApplication(sys.argv)
    # Das Fenster ist meist versteckt. Nur das Schließen des letzten Fensters darf nicht das Programm beenden.
    app.setQuitOnLastWindowClosed(False)
    overlay = Overlay(cfg)
    try:
        overlay.starten()
    except Exception as exc:
        log.exception("Start fehlgeschlagen")
        print(f"Start fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    fenster = Oberflaeche(overlay.ui)
    tablett = _tablett(app)
    # Ereignisse der Threads sind in `overlay.ui`; das Fenster liest sie selbst.
    app.aboutToQuit.connect(overlay.stoppen)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
