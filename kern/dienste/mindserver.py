"""MindServer (Mindcraft) über socket.io — ein kleiner Client in Python (29.10.2026).

Der MindServer im Bot-Container bietet KEINE REST-Routen. Gemessen am 29.10.2026 antwortet
`GET /api/agents` mit 404 („Cannot GET /api/agents"); die einzige HTTP-Route ist ein Bild-Proxy für
Gegenstandstexturen, alles andere ist die Web-Oberfläche. Die Schnittstelle ist socket.io
(Engine.IO 4 / Socket.IO 5):

    0{...}          der Server begrüßt (Handshake, sid)
    40              Client verbindet den Standard-Namensraum
    40{...}         der Server bestätigt
    42["name", ...] ein Ereignis
    2 / 3           Ping (Server) / Pong (Client)

Dieser Client spricht genau das, über die ohnehin vorhandene Bibliothek `websockets` — eine weitere
Abhängigkeit (`python-socketio` zöge aiohttp bzw. requests mit) braucht es dafür nicht.

Nur Lesen und Senden fester Ereignisse: keine freie Wahl von Ereignisnamen, keine Rohdaten.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import websockets

# Der Handshake und einzelne Ereignisse sind klein; der Spielzustand kann größer werden.
MAX_NACHRICHT = 4 * 1024 * 1024


class Fehler(Exception):
    """Der MindServer ist nicht erreichbar oder antwortet nicht, wie er soll."""


def adresse(url: str) -> str:
    """`http://host:port` → die WebSocket-Adresse des Standard-Namensraums.

    Der Pfad `/socket.io/` ist die Vorgabe von Engine.IO; `EIO=4` ist die Fassung, die dieser
    MindServer spricht (Socket.IO 5 auf der Serverseite)."""
    roh = (url or "").strip().rstrip("/")
    if "://" not in roh:
        roh = "http://" + roh
    teil = urlsplit(roh)
    schema = "wss" if teil.scheme == "https" else "ws"
    pfad = (teil.path or "") + "/socket.io/?EIO=4&transport=websocket"
    return f"{schema}://{teil.netloc}{pfad}"


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def rahmen(text) -> tuple[str, object]:
    """Einen Text-Rahmen lesen: `("open", dict)`, `("connect", dict)`, `("event", (name, args))`,
    `("ping", None)`, `("disconnect", None)`, `("fehler", text)` — sonst `("unbekannt", None)`.

    Reine Auswertung ohne Netz: so lässt sich die Leitung prüfen, ohne einen Server zu brauchen."""
    if not isinstance(text, str) or not text:
        return ("unbekannt", None)
    art, rest = text[0], text[1:]
    if art == "0":                                   # Engine.IO: offen
        return ("open", _json(rest))
    if art == "2":                                   # Engine.IO: Ping
        return ("ping", None)
    if art == "3":                                   # Engine.IO: Pong
        return ("pong", None)
    if art != "4":                                   # alles andere ist für uns nicht vorgesehen
        return ("unbekannt", None)
    if not rest:
        return ("unbekannt", None)
    typ, inhalt = rest[0], rest[1:]
    if typ == "0":                                   # Socket.IO: verbunden
        return ("connect", _json(inhalt))
    if typ == "1":                                   # Socket.IO: getrennt
        return ("disconnect", None)
    if typ == "2":                                   # Socket.IO: Ereignis
        daten = _json(inhalt)
        if isinstance(daten, list) and daten and isinstance(daten[0], str):
            return ("event", (daten[0], daten[1:]))
        return ("unbekannt", None)
    if typ == "4":                                   # Socket.IO: Fehler
        return ("fehler", inhalt)
    return ("unbekannt", None)


class Verbindung:
    """Eine offene Verbindung: Ereignisse senden und empfangen."""

    def __init__(self, ws):
        self._ws = ws

    async def senden(self, ereignis: str, *daten) -> None:
        rahmen_text = "42" + json.dumps([ereignis, *daten], ensure_ascii=False, separators=(",", ":"))
        try:
            await self._ws.send(rahmen_text)
        except Exception as exc:                                  # noqa: BLE001 — ein Abbruch ist eine Auskunft
            raise Fehler(f"Der MindServer nimmt nichts mehr an ({type(exc).__name__}).") from exc

    async def empfangen(self, zeitlimit: float) -> tuple[str, list]:
        """Das nächste Ereignis als (Name, Argumente). Ping wird beantwortet, Unbekanntes übersprungen."""
        schleife = asyncio.get_running_loop()
        ende = schleife.time() + max(0.1, zeitlimit)
        while True:
            rest = ende - schleife.time()
            if rest <= 0:
                raise Fehler("Der MindServer hat nicht geantwortet.")
            try:
                text = await asyncio.wait_for(self._ws.recv(), timeout=rest)
            except asyncio.TimeoutError:
                raise Fehler("Der MindServer hat nicht geantwortet.") from None
            except Exception as exc:                              # noqa: BLE001 — Verbindungsabbruch
                raise Fehler(f"Der MindServer hat die Verbindung beendet ({type(exc).__name__}).") from exc
            art, daten = rahmen(text)
            if art == "ping":
                try:
                    await self._ws.send("3")
                except Exception as exc:                          # noqa: BLE001
                    raise Fehler(f"Der MindServer hat die Verbindung beendet ({type(exc).__name__}).") from exc
                continue
            if art == "event":
                return daten
            if art == "disconnect":
                raise Fehler("Der MindServer hat die Verbindung beendet.")
            if art == "fehler":
                raise Fehler("Der MindServer hat die Verbindung abgelehnt.")
            # open/connect/pong/unbekannt: für den Aufrufer nicht von Belang

    async def schliessen(self) -> None:
        for schritt in ("41", None):
            try:
                if schritt is None:
                    await self._ws.close()
                else:
                    await self._ws.send(schritt)
            except Exception:                                     # noqa: BLE001 — Aufräumen darf nie werfen
                pass


async def verbinden(url: str, zeitlimit: float = 2.5) -> Verbindung:
    """Verbinden und den Handshake abschließen. Wirft `Fehler` mit einem Satz fürs Gespräch."""
    ziel = adresse(url)
    try:
        ws = await asyncio.wait_for(
            websockets.connect(ziel, open_timeout=zeitlimit, close_timeout=1, max_size=MAX_NACHRICHT),
            timeout=zeitlimit + 0.5)
    except asyncio.TimeoutError:
        raise Fehler("Der MindServer antwortet gerade nicht (Zeitüberschreitung).") from None
    except Exception as exc:                                      # noqa: BLE001 — Netzfehler sind eine Auskunft
        raise Fehler(f"Der MindServer ist nicht erreichbar ({type(exc).__name__}).") from exc
    verbindung = Verbindung(ws)
    try:
        if not await _warten(verbindung, "open", zeitlimit):
            raise Fehler("Der MindServer hat sich nicht begrüßt (kein Engine.IO-Handshake).")
        try:
            await ws.send("40")                                   # Standard-Namensraum verbinden
        except Exception as exc:                                  # noqa: BLE001
            raise Fehler(f"Der MindServer hat die Verbindung beendet ({type(exc).__name__}).") from exc
        if not await _warten(verbindung, "connect", zeitlimit):
            raise Fehler("Der MindServer hat die Verbindung nicht bestätigt.")
    except Fehler:
        await verbindung.schliessen()
        raise
    except Exception as exc:                                      # noqa: BLE001
        await verbindung.schliessen()
        raise Fehler(f"Der MindServer hat die Verbindung beendet ({type(exc).__name__}).") from exc
    return verbindung


async def _warten(verbindung: Verbindung, gesucht: str, zeitlimit: float) -> bool:
    """Auf den Handshake warten (open/connect); Pings werden dabei beantwortet."""
    schleife = asyncio.get_running_loop()
    ende = schleife.time() + max(0.1, zeitlimit)
    while True:
        rest = ende - schleife.time()
        if rest <= 0:
            raise Fehler("Der MindServer antwortet gerade nicht (Zeitüberschreitung).")
        try:
            text = await asyncio.wait_for(verbindung._ws.recv(), timeout=rest)
        except asyncio.TimeoutError:
            raise Fehler("Der MindServer antwortet gerade nicht (Zeitüberschreitung).") from None
        except Exception as exc:                                  # noqa: BLE001
            raise Fehler(f"Der MindServer hat die Verbindung beendet ({type(exc).__name__}).") from exc
        art, _ = rahmen(text)
        if art == gesucht:
            return True
        if art == "ping":
            try:
                await verbindung._ws.send("3")
            except Exception as exc:                              # noqa: BLE001
                raise Fehler(f"Der MindServer hat die Verbindung beendet ({type(exc).__name__}).") from exc
        elif art == "fehler":
            raise Fehler("Der MindServer hat die Verbindung abgelehnt.")
