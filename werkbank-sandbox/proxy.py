"""Egress-Proxy der Werkbank-Sandbox: HTTP-CONNECT nur zu einer kleinen Positivliste, nur Port 443.

Die Sandbox hängt an einem `internal`-Netz (kein Weg nach draußen, nicht ins Host-LAN). Dieser Proxy ist der
einzige Ausgang. SSRF-Schutz: Der Hostname muss auf der Liste stehen; er wird HIER aufgelöst, JEDE aufgelöste
Adresse muss öffentlich sein (nicht privat, loopback, link-local, CGNAT-Bereich, multicast, unspecified,
reserviert), und verbunden wird nur zu der geprüften IP (kein zweites Auflösen). Kein TLS-Aufbrechen: reiner Tunnel.

Nur Standardbibliothek.
"""
import asyncio
import ipaddress
import os
import re
import socket
import sys

ERLAUBTE_HOSTS = ("opencode.ai", "models.dev")           # genau
ERLAUBTE_ENDUNGEN = (".opencode.ai", ".models.dev")      # Unterdomänen
ERLAUBTE_PORTS = (443,)
CGNAT = ipaddress.ip_network("100.64.0.0/10")
ANHOEREN = ("0.0.0.0", int(os.environ.get("WERKBANK_PROXY_PORT", "3128")))
KOPF_MAX = 8192
KOPF_ZEIT_S = 10
VERBINDEN_ZEIT_S = 10
LEERLAUF_S = 120
GESAMT_ZEIT_S = 900
GESAMT_BYTES = 200_000_000
GLEICHZEITIG = 32


class Abgelehnt(Exception):
    pass


def adresse_oeffentlich(text: str) -> bool:
    try:
        a = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a in CGNAT:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                or a.is_unspecified or a.is_reserved or not a.is_global)


_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOST = re.compile(rf"{_LABEL}(\.{_LABEL})*\.?")


def host_normal(host: str) -> str | None:
    """Streng ASCII-LDH, kleingeschrieben, ohne Schlusspunkt; sonst None. Das Ergebnis ist der EINZIGE Name,
    der geprüft und aufgelöst wird (getaddrinfo schneidet sonst z. B. am NUL-Byte ab)."""
    h = host.lower()
    if len(h) > 254 or not _HOST.fullmatch(h):
        return None
    h = h[:-1] if h.endswith(".") else h
    return h if len(h) <= 253 else None


def host_erlaubt(host: str) -> bool:
    h = host_normal(host)
    if h is None:
        return False
    return h in ERLAUBTE_HOSTS or any(h.endswith(e) and len(h) > len(e) for e in ERLAUBTE_ENDUNGEN)


def ziel_pruefen(host: str, port: int, aufloesen=socket.getaddrinfo) -> list[str]:
    """Geprüfte Zieladressen oder `Abgelehnt`. Reine Entscheidung; der Auflöser ist austauschbar (Tests)."""
    if port not in ERLAUBTE_PORTS:
        raise Abgelehnt("Port nicht erlaubt")
    name = host_normal(host) if host else None
    if name is None or not host_erlaubt(name):
        raise Abgelehnt("Host nicht auf der Liste")
    try:
        infos = aufloesen(name, port, type=socket.SOCK_STREAM)
    except OSError:
        raise Abgelehnt("Host nicht auflösbar")
    adressen = [i[4][0].split("%")[0] for i in infos]
    if not adressen:
        raise Abgelehnt("Keine Adresse")
    for a in adressen:
        if not adresse_oeffentlich(a):
            raise Abgelehnt("Adresse nicht öffentlich")
    return adressen


def connect_ziel(zeile: str) -> tuple[str, int]:
    """`CONNECT host:port HTTP/1.x` -> (host, port); alles andere `Abgelehnt`."""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in zeile):
        raise Abgelehnt("Steuerzeichen in der Anfrage")
    teile = zeile.split()
    if len(teile) != 3 or teile[0] != "CONNECT" or not teile[2].startswith("HTTP/1."):
        raise Abgelehnt("nur CONNECT")
    host, sep, port = teile[1].rpartition(":")
    if not sep or not re.fullmatch(r"[1-9][0-9]{0,4}", port) or host.startswith("[") or ":" in host:
        raise Abgelehnt("Ziel unlesbar")
    return host, int(port)


_sperre = None


async def _pumpe(r, w, stand: dict) -> None:
    try:
        while True:
            daten = await asyncio.wait_for(r.read(65536), LEERLAUF_S)
            if not daten:
                break
            stand["bytes"] += len(daten)
            if stand["bytes"] > GESAMT_BYTES:
                break
            w.write(daten)
            await w.drain()
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        try:
            w.close()
        except OSError:
            pass


async def _antwort(w, text: str) -> None:
    w.write(f"HTTP/1.1 {text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
    try:
        await w.drain()
    except OSError:
        pass


async def bedienen(r, w) -> None:
    global _sperre
    if _sperre is None:
        _sperre = asyncio.Semaphore(GLEICHZEITIG)
    if _sperre.locked():
        await _antwort(w, "503 Service Unavailable")
        w.close()
        return
    async with _sperre:
        try:
            try:
                kopf = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), KOPF_ZEIT_S)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, OSError):
                return
            if len(kopf) > KOPF_MAX:
                return
            zeile = kopf.split(b"\r\n", 1)[0].decode("latin-1")
            try:
                host, port = connect_ziel(zeile)
                adressen = await asyncio.to_thread(ziel_pruefen, host, port)
            except Abgelehnt as e:
                await _antwort(w, "403 Forbidden")
                print("abgelehnt:", str(e), flush=True)
                return
            try:
                ur, uw = await asyncio.wait_for(asyncio.open_connection(adressen[0], port), VERBINDEN_ZEIT_S)
            except (OSError, asyncio.TimeoutError):
                await _antwort(w, "502 Bad Gateway")
                return
            w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await w.drain()
            stand = {"bytes": 0}
            try:
                await asyncio.wait_for(asyncio.gather(_pumpe(r, uw, stand), _pumpe(ur, w, stand)), GESAMT_ZEIT_S)
            except asyncio.TimeoutError:
                pass
            uw.close()
        finally:
            try:
                w.close()
            except OSError:
                pass


async def haupt() -> None:
    server = await asyncio.start_server(bedienen, *ANHOEREN, limit=KOPF_MAX)
    print("Werkbank-Proxy bereit", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(haupt())
    except KeyboardInterrupt:
        sys.exit(0)
