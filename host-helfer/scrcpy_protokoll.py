"""scrcpy-Protokoll (Server 3.3.x) — nur reine Funktionen, ohne Netz und ohne adb, damit sie sich testen lassen.

Bild: Der Server schickt H.264-Pakete mit je 12 Byte Kopf (pts+Flags 8, Länge 4). Eingaben gehen als Nachrichten an
den Steuer-Socket (alles Big-Endian). Genutzt von `handy_steuerung.py`.
"""

from __future__ import annotations

import struct

# Android-Tastencodes, die die Seite als Knöpfe oder Sondertasten schickt (nie frei vom Browser vorgegeben).
TASTEN = {
    "home": 3, "zurueck": 4, "uebersicht": 187, "ein_aus": 26, "aufwecken": 224,
    "lauter": 24, "leiser": 25, "enter": 66, "loeschen": 67, "tab": 61, "esc": 111,
    "hoch": 19, "runter": 20, "links": 21, "rechts": 22, "pos1": 122, "ende": 123, "entf": 112,
}

# Nachrichtentypen des Steuer-Sockets
KEYCODE, TEXT, TOUCH, SCROLL, ZURUECK_ODER_AN, SET_CLIPBOARD, BILD_NEU = 0, 1, 2, 3, 4, 9, 17
# Aktionen
DOWN, UP, MOVE = 0, 1, 2
FLAG_KONFIG, FLAG_SCHLUESSEL = 1 << 63, 1 << 62
MAX_TEXT = 300      # so viel Text pro Nachricht; der Server nimmt höchstens 300 Byte
MAX_PAKET = 8 << 20  # größer ist kein Bildpaket — dann ist der Strom kaputt


def kopf_lesen(kopf: bytes) -> tuple[int, bool, bool, int]:
    """12 Byte Paketkopf → (pts, konfig, schluessel, laenge)."""
    wert, laenge = struct.unpack(">QI", kopf)
    konfig = bool(wert & FLAG_KONFIG)
    schluessel = bool(wert & FLAG_SCHLUESSEL)
    return wert & ~(FLAG_KONFIG | FLAG_SCHLUESSEL) & 0xFFFFFFFFFFFFFFFF, konfig, schluessel, laenge


def codec_aus_sps(konfig: bytes) -> str | None:
    """`avc1.PPCCLL` aus dem Konfig-Paket (Annex B mit SPS, NAL-Typ 7) — das braucht der Browser-Decoder."""
    i = 0
    while i + 3 < len(konfig):
        if konfig[i:i + 3] == b"\x00\x00\x01":
            nal = i + 3
            if nal + 3 < len(konfig) and konfig[nal] & 0x1F == 7:
                return "avc1." + konfig[nal + 1:nal + 4].hex()
            i = nal
        else:
            i += 1
    return None


def bild_neu() -> bytes:
    """Der Server soll sofort ein Schlüsselbild schicken (nach verworfenen Bildern)."""
    return bytes([BILD_NEU])


def taste(aktion: int, code: int) -> bytes:
    """Taste drücken (DOWN) oder loslassen (UP)."""
    return struct.pack(">BBiii", KEYCODE, aktion, code, 0, 0)


def text(s: str) -> bytes:
    daten = s.encode("utf-8")[:MAX_TEXT]
    return struct.pack(">BI", TEXT, len(daten)) + daten


def einfuegen(s: str, folge: int = 0) -> bytes:
    """Text über die Zwischenablage einfügen (Umlaute, Emoji — `text` kann nur ASCII sicher)."""
    daten = s.encode("utf-8")[:MAX_TEXT]
    return struct.pack(">BQBI", SET_CLIPBOARD, folge, 1, len(daten)) + daten


def beruehrung(aktion: int, x: int, y: int, breite: int, hoehe: int, zeiger: int = 0) -> bytes:
    """Ein Finger. Die Position gilt im Bild der Größe `breite`×`hoehe`; außerhalb wird sie auf den Rand gezogen."""
    x = min(max(int(x), 0), breite - 1)
    y = min(max(int(y), 0), hoehe - 1)
    druck = 0xFFFF if aktion != UP else 0
    taste_ = 1 if aktion != UP else 0      # „primäre Taste gedrückt“, wie bei der Maus
    return struct.pack(">BBqiiHHHii", TOUCH, aktion, zeiger, x, y, breite, hoehe, druck, taste_, taste_)


def scrollen(x: int, y: int, breite: int, hoehe: int, dx: float, dy: float) -> bytes:
    """Mausrad: dx/dy in „Rastern“ (−16 … 16), positiv = nach oben/rechts wie bei scrcpy."""
    x = min(max(int(x), 0), breite - 1)
    y = min(max(int(y), 0), hoehe - 1)

    def fest(v: float) -> int:
        return max(-32768, min(32767, int(max(-1.0, min(1.0, v / 16)) * 32767)))

    return struct.pack(">BiiHHhhi", SCROLL, x, y, breite, hoehe, fest(dx), fest(dy), 0)


def aus_ereignis(e: dict, breite: int, hoehe: int) -> list[bytes]:
    """Ereignis der Webseite (JSON) → Nachrichten für den Steuer-Socket. Unbekanntes oder Kaputtes → leere Liste."""
    try:
        art = e.get("t")
        if art == "touch":
            aktion = {"down": DOWN, "up": UP, "move": MOVE}[e["a"]]
            return [beruehrung(aktion, e["x"], e["y"], breite, hoehe, int(e.get("id", 0)) & 0xFF)]
        if art == "scroll":
            return [scrollen(e["x"], e["y"], breite, hoehe, float(e.get("dx", 0)), float(e.get("dy", 0)))]
        if art == "taste":
            code = TASTEN[e["name"]]
            return [taste(DOWN, code), taste(UP, code)]
        if art == "text":
            s = str(e["s"])
            if not s:
                return []
            return [text(s)] if s.isascii() else [einfuegen(s), taste(DOWN, 279), taste(UP, 279)]
    except (KeyError, TypeError, ValueError):
        return []
    return []
