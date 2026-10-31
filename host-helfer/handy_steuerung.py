"""Handy-Steuerung — das Samsung vom PC aus im Browser sehen und bedienen (läuft auf hphome, nur bei Bedarf).

Der Dienst startet das `scrcpy-server`-Programm auf dem Handy (per adb, nur im Temp-Ordner `/data/local/tmp`, keine App, keine
Einstellung), liest dessen H.264-Strom und reicht ihn per WebSocket an die Seite weiter; Mausklicks, Wischen, Tasten und Text
gehen als scrcpy-Nachrichten zurück. Solange eine Seite verbunden ist, schreibt der Dienst alle 5 s einen Herzschlag nach
`data/snapchat/handy_steuerung.json` — daran erkennt der Snap-Helfer, dass er pausieren muss (`snap_helfer.fernsteuerung_aktiv`).

Nur Standardbibliothek: HTTP und WebSocket sind klein genug, um sie selbst zu sprechen (keine neue Abhängigkeit).
Start:  host-helfer/handy_steuerung_starten.sh      Ende:  host-helfer/handy_steuerung_starten.sh stopp
Warum so und was nie geändert wird: docs/LEHREN.md (Handy-Steuerung, 16.10.2026).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrcpy_protokoll as proto  # noqa: E402

HOST = os.getenv("HANDY_STEUERUNG_HOST", "127.0.0.1")
PORT = int(os.getenv("HANDY_STEUERUNG_PORT", "8140"))
SERVER_JAR = Path(os.getenv("SCRCPY_SERVER", "/usr/share/scrcpy/scrcpy-server"))
SERVER_VERSION = os.getenv("SCRCPY_VERSION", "3.3.4")
SERVER_OPTIONEN = os.getenv("SCRCPY_OPTIONEN", "")      # zusätzliche scrcpy-Optionen (überschreiben die festen), z. B. ein anderer Encoder
JAR_AUF_HANDY = "/data/local/tmp/friday-handy-steuerung.jar"
WEB = Path(__file__).resolve().parent / "handy_steuerung_web"
SEITEN = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HERZSCHLAG_S = 5.0
STILLE_MAX_S = 20.0           # meldet sich die Seite so lange nicht, gilt sie als weg
INAKTIV_MAX_S = float(os.getenv("HANDY_STEUERUNG_INAKTIV_S", "600"))   # so lange ohne Eingabe, dann endet die Sitzung (der Snap-Helfer läuft wieder)
LEERLAUF_MAX_S = float(os.getenv("HANDY_STEUERUNG_LEERLAUF_S", "20"))   # so lange ohne verbundene Seite, dann beendet sich der Dienst (0 = nie)
FREIGABE_PORT = os.getenv("HANDY_STEUERUNG_FREIGABE_PORT", "8444")
MAX_PUFFER =1_500_000        # so viel Bild darf unterwegs liegen, danach werden Zwischenbilder verworfen
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def daten_ordner() -> Path:
    return Path(os.getenv("FRIDAY_DATEN", "") or (Path(__file__).resolve().parent.parent / "data"))


def herzschlag_datei() -> Path:
    p = daten_ordner() / "snapchat"
    p.mkdir(parents=True, exist_ok=True)
    return p / "handy_steuerung.json"


def herzschlag_schreiben() -> None:
    d = herzschlag_datei()
    tmp = d.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    os.replace(tmp, d)


def herzschlag_loeschen() -> None:
    herzschlag_datei().unlink(missing_ok=True)


def inaktiv(letzte_eingabe: float, jetzt: float, grenze: float = INAKTIV_MAX_S) -> bool:
    """Seit `grenze` Sekunden nichts bedient? Ein vergessener offener Tab würde den Snap-Helfer sonst stundenlang aufhalten."""
    return jetzt - letzte_eingabe > grenze


def herkunft_ok(kopfzeilen: dict[str, str]) -> bool:
    """Ein WebSocket darf nur von der eigenen Seite kommen (Origin-Host == Host); sonst könnte jede fremde Webseite im
    Browser das Handy steuern."""
    origin = kopfzeilen.get("origin", "")
    return bool(origin) and urlsplit(origin).netloc == kopfzeilen.get("host", "\0")


# --------------------------------------------------------------------------- WebSocket (RFC 6455, klein)

def ws_antwort(schluessel: str) -> bytes:
    accept = base64.b64encode(hashlib.sha1(schluessel.encode() + GUID).digest()).decode()
    return (f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()


def ws_rahmen(opcode: int, daten: bytes) -> bytes:
    n = len(daten)
    if n < 126:
        kopf = struct.pack(">BB", 0x80 | opcode, n)
    elif n < 65536:
        kopf = struct.pack(">BBH", 0x80 | opcode, 126, n)
    else:
        kopf = struct.pack(">BBQ", 0x80 | opcode, 127, n)
    return kopf + daten


async def ws_lesen(r: asyncio.StreamReader, max_laenge: int = 65536) -> tuple[int, bytes]:
    """Eine Nachricht des Browsers (maskiert). Fragmentierung gibt es hier nicht: Seiten schicken kurze JSON-Texte."""
    b1, b2 = await r.readexactly(2)
    opcode, laenge = b1 & 0x0F, b2 & 0x7F
    if laenge == 126:
        laenge = struct.unpack(">H", await r.readexactly(2))[0]
    elif laenge == 127:
        laenge = struct.unpack(">Q", await r.readexactly(8))[0]
    if laenge > max_laenge:
        raise ValueError("Nachricht zu groß")
    maske = await r.readexactly(4) if b2 & 0x80 else b"\0\0\0\0"
    daten = bytearray(await r.readexactly(laenge))
    if any(maske):
        for i in range(len(daten)):
            daten[i] ^= maske[i % 4]
    return opcode, bytes(daten)


# --------------------------------------------------------------------------- Handy

class HandyFehler(Exception):
    pass


async def _adb(*args: str, zeit: float = 20.0) -> str:
    p = await asyncio.create_subprocess_exec("adb", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        aus, _ = await asyncio.wait_for(p.communicate(), zeit)
    except asyncio.TimeoutError:
        p.kill()
        raise HandyFehler("adb antwortet nicht") from None
    text = aus.decode("utf-8", "replace").strip()
    if p.returncode != 0:
        raise HandyFehler(text.splitlines()[-1] if text else "adb-Fehler")
    return text


class Handy:
    """Eine laufende scrcpy-Verbindung: Server auf dem Handy, Video-Socket, Steuer-Socket."""

    def __init__(self) -> None:
        self.proz: asyncio.subprocess.Process | None = None
        self.port = 0
        self.video: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        self.steuer: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        self.name = ""
        self.breite = self.hoehe = 0
        self.log: list[str] = []

    async def starten(self) -> None:
        if not SERVER_JAR.exists():
            raise HandyFehler("scrcpy ist nicht installiert")
        scid = f"{secrets.randbits(31):08x}"
        try:
            await _adb("get-state")
        except HandyFehler:
            raise HandyFehler("Das Handy ist nicht per USB verbunden (oder adb nicht erlaubt)") from None
        await _adb("push", str(SERVER_JAR), JAR_AUF_HANDY)
        self.port = int((await _adb("forward", "tcp:0", f"localabstract:scrcpy_{scid}")).split()[-1])
        # Nur, was vom Standard abweicht: Auf dem Galaxy A52 (Android 14) stürzt das Programm bei längerer Befehlszeile mit „stack
        # corruption detected“ ab (gemessen 16.10.2026: schon ein Zusatz wie `cleanup=false` auf die ausgeschriebenen Standardwerte).
        befehl = (f"CLASSPATH={JAR_AUF_HANDY} app_process / com.genymobile.scrcpy.Server {SERVER_VERSION} scid={scid} "
                  "tunnel_forward=true audio=false max_size=1920 video_bit_rate=8000000 max_fps=30 "
                  "clipboard_autosync=false log_level=warn " + SERVER_OPTIONEN).rstrip()
        self.proz = await asyncio.create_subprocess_exec("adb", "shell", befehl, stdout=asyncio.subprocess.PIPE,
                                                          stderr=asyncio.subprocess.STDOUT)
        asyncio.create_task(self._ausgabe_sammeln(self.proz.stdout))
        self.video = await self._verbinden()
        self.steuer = await asyncio.open_connection("127.0.0.1", self.port)   # der Server schickt Namen und Größe erst, wenn beide da sind
        self.name = (await self.video[0].readexactly(64)).split(b"\0")[0].decode("utf-8", "replace")
        _codec, self.breite, self.hoehe = struct.unpack(">III", await self.video[0].readexactly(12))
        asyncio.create_task(self._steuer_leeren(self.steuer[0]))

    async def _verbinden(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Der Tunnel nimmt Verbindungen auch an, wenn der Server noch nicht lauscht; das Zeichen (Dummy-Byte) beweist es."""
        ende = time.monotonic() + 15
        while time.monotonic() < ende:
            r, w = await asyncio.open_connection("127.0.0.1", self.port)
            try:
                if await asyncio.wait_for(r.readexactly(1), 5):
                    return r, w
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
                pass
            w.close()
            await asyncio.sleep(0.3)
        raise HandyFehler("Das Handy antwortet nicht (gesperrt oder scrcpy konnte nicht starten)")

    async def _ausgabe_sammeln(self, r: asyncio.StreamReader) -> None:
        """Die Meldungen des Handy-Programms (nur Warnungen/Fehler) — die letzten Zeilen erklären, warum es endete."""
        try:
            async for zeile in r:
                t = zeile.decode("utf-8", "replace").strip()
                if t:
                    self.log.append(t)
                    print(f"[scrcpy] {t}", flush=True)
        except (ConnectionError, asyncio.CancelledError):
            pass

    @staticmethod
    async def _steuer_leeren(r: asyncio.StreamReader) -> None:
        try:
            while await r.read(4096):
                pass
        except (ConnectionError, asyncio.CancelledError):
            pass

    def senden(self, nachrichten: list[bytes]) -> None:
        if self.steuer:
            for m in nachrichten:
                self.steuer[1].write(m)

    async def beenden(self) -> None:
        """Alles zurück: Sockets zu, Server beenden, Weiterleitung weg, Programm vom Handy löschen. Jeder Schritt einzeln, damit ein
        Fehler die anderen nicht verhindert."""
        for paar in (self.video, self.steuer):
            if paar:
                try:
                    paar[1].close()
                except OSError:
                    pass
        if self.proz and self.proz.returncode is None:
            try:
                self.proz.terminate()
                await asyncio.wait_for(self.proz.wait(), 3)
            except (ProcessLookupError, asyncio.TimeoutError):
                self.proz.kill()
        for args in (("forward", "--remove", f"tcp:{self.port}"), ("shell", "rm", "-f", JAR_AUF_HANDY)):
            if args[0] == "forward" and not self.port:
                continue
            try:
                await _adb(*args, zeit=8)
            except HandyFehler:
                pass


# --------------------------------------------------------------------------- Sitzung

aktuell: "Sitzung | None" = None


class Sitzung:
    def __init__(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        self.r, self.w = r, w
        self.handy = Handy()
        self.zu = asyncio.Event()
        self.aufgaben: list[asyncio.Task] = []
        self.letzte_eingabe = time.monotonic()

    def text(self, **kw) -> None:
        self.w.write(ws_rahmen(1, json.dumps(kw, ensure_ascii=False).encode()))

    async def laufen(self) -> None:
        try:
            herzschlag_schreiben()
            self.text(type="status", text="Verbinde mit dem Handy …")
            await self.handy.starten()
            self.handy.senden([proto.taste(proto.DOWN, proto.TASTEN["aufwecken"]), proto.taste(proto.UP, proto.TASTEN["aufwecken"])])
            self.text(type="info", name=self.handy.name, w=self.handy.breite, h=self.handy.hoehe)
            self.aufgaben = [asyncio.create_task(self._bild()), asyncio.create_task(self._herzschlag()),
                             asyncio.create_task(self._eingaben()), asyncio.create_task(self._wachhund())]
            await self.zu.wait()
        except HandyFehler as exc:
            self._sagen("fehler", str(exc))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            self._sagen("fehler", "Verbindung zum Handy abgerissen")
        finally:
            await self.schliessen()

    def _sagen(self, art: str, text: str) -> None:
        try:
            self.text(type=art, text=text)
        except OSError:
            pass

    async def _bild(self) -> None:
        r = self.handy.video[0]
        verwerfen = False
        try:
            while True:
                pts, konfig, schluessel, laenge = proto.kopf_lesen(await r.readexactly(12))
                if not 0 < laenge <= proto.MAX_PAKET:
                    raise HandyFehler("Bildstrom gestört")
                daten = await r.readexactly(laenge)
                if konfig:
                    codec = proto.codec_aus_sps(daten)
                    if codec:
                        self.text(type="codec", codec=codec)
                    art = 0
                else:
                    puffer = self.w.transport.get_write_buffer_size()
                    if verwerfen and schluessel and puffer < MAX_PUFFER // 4:
                        verwerfen = False
                    if puffer > MAX_PUFFER and not schluessel:
                        if not verwerfen:
                            verwerfen = True
                            self.handy.senden([proto.bild_neu()])   # gleich ein neues Schlüsselbild, statt 10 s zu warten
                        continue
                    if verwerfen and not schluessel:
                        continue
                    art = 1 if schluessel else 2
                self.w.write(ws_rahmen(2, struct.pack(">BQ", art, pts) + daten))
                await self.w.drain()
        except (asyncio.IncompleteReadError, ConnectionError, HandyFehler, OSError) as exc:
            if not self.zu.is_set():
                self._sagen("fehler", str(exc) if isinstance(exc, HandyFehler) else "Das Handy hat die Verbindung beendet")
            self.zu.set()

    async def _wachhund(self) -> None:
        while True:
            await asyncio.sleep(5)
            if inaktiv(self.letzte_eingabe, time.monotonic()):
                self._sagen("fehler", f"Beendet: {int(INAKTIV_MAX_S // 60)} Minuten ohne Eingabe. Der Snap-Helfer läuft wieder.")
                self.zu.set()
                return

    async def _herzschlag(self) -> None:
        while True:
            await asyncio.sleep(HERZSCHLAG_S)
            herzschlag_schreiben()

    async def _eingaben(self) -> None:
        try:
            while True:
                opcode, daten = await asyncio.wait_for(ws_lesen(self.r), STILLE_MAX_S)
                if opcode == 8:
                    break
                if opcode == 9:
                    self.w.write(ws_rahmen(10, daten))
                    continue
                if opcode != 1:
                    continue
                try:
                    e = json.loads(daten)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("t") != "ping":
                    self.letzte_eingabe = time.monotonic()
                    self.handy.senden(proto.aus_ereignis(e, self.handy.breite, self.handy.hoehe))
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ValueError, OSError):
            pass
        self.zu.set()

    async def schliessen(self) -> None:
        global aktuell
        for t in self.aufgaben:
            t.cancel()
        try:
            self.w.write(ws_rahmen(8, b""))
            self.w.close()
        except OSError:
            pass
        await self.handy.beenden()
        if aktuell is self:
            aktuell = None
            herzschlag_loeschen()      # erst nach dem Aufräumen: danach darf der Snap-Helfer wieder ans Handy
        print(f"[handy-steuerung] Sitzung beendet", flush=True)


# --------------------------------------------------------------------------- HTTP

async def anfrage(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    global aktuell
    try:
        kopf = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 10)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        w.close()
        return
    zeilen = kopf.decode("latin-1").split("\r\n")
    teile = zeilen[0].split(" ")
    kopfzeilen = {z.split(":", 1)[0].strip().lower(): z.split(":", 1)[1].strip() for z in zeilen[1:] if ":" in z}
    pfad = urlsplit(teile[1]).path if len(teile) > 1 else ""
    if len(teile) < 3 or teile[0] != "GET":
        await _antwort(w, 405, b"nur GET", "text/plain")
        return
    if pfad == "/ws" and kopfzeilen.get("upgrade", "").lower() == "websocket" and "sec-websocket-key" in kopfzeilen:
        if not herkunft_ok(kopfzeilen):
            await _antwort(w, 403, b"fremde Herkunft", "text/plain")
            return
        w.write(ws_antwort(kopfzeilen["sec-websocket-key"]))
        if aktuell is not None:      # ein neuer Tab übernimmt, der alte wird sauber beendet
            aktuell._sagen("fehler", "Von einem anderen Fenster übernommen")
            aktuell.zu.set()
            for _ in range(60):
                if aktuell is None:
                    break
                await asyncio.sleep(0.25)
        s = aktuell = Sitzung(r, w)
        print("[handy-steuerung] Sitzung gestartet", flush=True)
        await s.laufen()
        return
    if pfad in SEITEN:
        datei, art = SEITEN[pfad]
        await _antwort(w, 200, (WEB / datei).read_bytes(), art)
        return
    await _antwort(w, 404, b"nicht gefunden", "text/plain")


async def _antwort(w: asyncio.StreamWriter, code: int, body: bytes, art: str) -> None:
    gruende = {200: "OK", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed"}
    w.write((f"HTTP/1.1 {code} {gruende.get(code, '')}\r\nContent-Type: {art}\r\nContent-Length: {len(body)}\r\n"
             f"Cache-Control: no-store\r\nContent-Security-Policy: {CSP}\r\nX-Content-Type-Options: nosniff\r\n"
             f"Connection: close\r\n\r\n").encode() + body)
    try:
        await w.drain()
    finally:
        w.close()


def leerlauf_vorbei(ohne_seit: float | None, jetzt: float, grenze: float = LEERLAUF_MAX_S) -> bool:
    """True, wenn seit `grenze` Sekunden keine Seite verbunden ist (None = gerade verbunden). grenze 0 = nie beenden."""
    return grenze > 0 and ohne_seit is not None and jetzt - ohne_seit >= grenze


async def _ohne_verbindung_beenden(ende: asyncio.Event) -> None:
    """Ist ~LEERLAUF_MAX_S lang keine Seite verbunden (nach dem Start oder nach dem Schließen), beendet sich der Dienst und
    nimmt die HTTPS-Freigabe weg — ein offener Dienst hält das Handy per adb besetzt und blockiert Snapchat."""
    ohne_seit: float | None = time.monotonic()
    while not ende.is_set():
        await asyncio.sleep(1)
        jetzt = time.monotonic()
        if aktuell is not None:
            ohne_seit = None
        elif ohne_seit is None:
            ohne_seit = jetzt
        elif leerlauf_vorbei(ohne_seit, jetzt):
            print(f"[handy-steuerung] {int(LEERLAUF_MAX_S)} s ohne Verbindung — beende mich", flush=True)
            subprocess.run(["tailscale", "serve", f"--https={FREIGABE_PORT}", "off"], capture_output=True, timeout=20, check=False)
            ende.set()


async def main() -> None:
    herzschlag_loeschen()      # ein Rest von einem Absturz würde den Snap-Helfer höchstens 15 s halten, aber sauber ist sauber
    ende = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        asyncio.get_running_loop().add_signal_handler(sig, ende.set)
    server = await asyncio.start_server(anfrage, HOST, PORT)
    print(f"[handy-steuerung] lauscht auf http://{HOST}:{PORT}", flush=True)
    wache = asyncio.create_task(_ohne_verbindung_beenden(ende))
    await ende.wait()
    wache.cancel()
    server.close()
    if aktuell is not None:      # laufende Sitzung sauber beenden: Programm vom Handy, Weiterleitung weg
        await aktuell.schliessen()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        herzschlag_loeschen()
