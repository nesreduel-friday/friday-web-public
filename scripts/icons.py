"""Erzeugt Fridays App-Symbole — einen Bogenreaktor — ohne Zusatzbibliothek.

iOS nimmt für den Home-Bildschirm nur PNG, kein SVG. Damit dafür nicht
Pillow ins Projekt muss, schreibt dieses Skript die PNGs selbst (zlib +
struct). Kanten werden über den Pixelabstand geglättet statt über
Mehrfachabtastung — das ist schnell genug für 512 px.

Aufruf: python3 scripts/icons.py   (schreibt nach web/icons/)
"""

import math
import struct
import zlib
from pathlib import Path

ZIEL = Path(__file__).resolve().parent.parent / "web" / "icons"


def klemmen(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def mischen(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def band(r: float, mitte: float, breite: float, px: float) -> float:
    """1 innerhalb eines Rings, weiche Kante über etwa einen Pixel."""
    return klemmen(0.5 - (abs(r - mitte) - breite / 2) / px)


def farbe(u: float, v: float, px: float):
    r = math.hypot(u, v)
    a = math.atan2(v, u)

    c = mischen((10, 13, 18), (18, 28, 46), klemmen(1 - r))

    # Leuchten um Ring und Kern
    glanz = math.exp(-((r - 0.58) / 0.17) ** 2) * 0.30 + math.exp(-(r / 0.24) ** 2) * 0.55
    c = mischen(c, (77, 163, 255), klemmen(glanz))

    # Äusserer Ring in zehn Segmenten
    seg = (a / (2 * math.pi) * 10) % 1.0
    abstand = (min(seg, 1 - seg) - 0.07) * (2 * math.pi * max(r, 1e-6) / 10)
    luecke = klemmen(0.5 - abstand / px)
    c = mischen(c, (150, 214, 255), band(r, 0.58, 0.085, px) * (1 - luecke))

    # Innerer Ring
    c = mischen(c, (95, 180, 255), band(r, 0.38, 0.035, px))

    # Kern
    c = mischen(c, (236, 248, 255), klemmen(0.5 + (0.16 - r) / px))
    return tuple(int(round(k)) for k in c)


def png(pfad: Path, groesse: int) -> None:
    px = 2.0 / groesse
    zeilen = bytearray()
    for y in range(groesse):
        zeilen.append(0)
        v = (y + 0.5) * px - 1
        for x in range(groesse):
            zeilen.extend(farbe((x + 0.5) * px - 1, v, px))

    def block(art: bytes, daten: bytes) -> bytes:
        return (struct.pack(">I", len(daten)) + art + daten
                + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF))

    kopf = struct.pack(">IIBBBBB", groesse, groesse, 8, 2, 0, 0, 0)
    pfad.write_bytes(b"\x89PNG\r\n\x1a\n" + block(b"IHDR", kopf)
                     + block(b"IDAT", zlib.compress(bytes(zeilen), 9)) + block(b"IEND", b""))


if __name__ == "__main__":
    ZIEL.mkdir(parents=True, exist_ok=True)
    for name, groesse in [("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)]:
        png(ZIEL / name, groesse)
        print(f"{name}: {groesse}x{groesse}")
