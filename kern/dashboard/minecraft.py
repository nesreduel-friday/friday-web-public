"""Die Karte „Minecraft“ in der Übersicht (21.10.2026): zeigt, was die Mod `fridaycard` auf Eikes PC meldet.

Die Mod (`minecraft-mod/`) schickt alle paar Sekunden den Spielstand an `POST /api/minecraft` (Schlüssel
`MINECRAFT_SCHLUESSEL`). Friday hält nur den letzten Stand im RAM. Meldet die Mod sich länger als `VERALTET_S`
nicht, ist das Spiel aus und die Karte verschwindet. Alles, was die Mod schickt, wird hier auf bekannte Felder,
Typen und Längen zurückgeschnitten: Namen von Servern und Welten sind fremder Text und gehen nie als Anweisung
irgendwohin, nur auf die Karte.
"""

import base64
import binascii
import re
import time

ID = "minecraft"
VERALTET_S = 150      # Mod-Standard 30 s, einstellbar bis 300 s: nach 150 s ohne Meldung gilt das Spiel als aus
ZUSTAENDE = ("menu", "welt", "server", "aus")
SKIN_URL = re.compile(r"https://textures\.minecraft\.net/texture/[0-9a-f]{16,128}")
KENNUNG = re.compile(r"[a-z0-9_]{0,64}")

_stand: dict | None = None
_zeit: float = 0.0
_skin_png: bytes | None = None      # der eigene Skin, den die Mod als Base64 mitschickt (≤ 64 KB)


def _text(v, n=80) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _zahl(v, lo, hi, standard=0):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return standard
    return max(lo, min(hi, v))


def _kennung(v) -> str:
    return v if isinstance(v, str) and KENNUNG.fullmatch(v) else ""


def _stapel(v) -> dict:
    v = v if isinstance(v, dict) else {}
    return {"id": _kennung(v.get("id")), "anzahl": int(_zahl(v.get("anzahl"), 0, 999, 0))}


def bereinigen(roh: dict) -> dict:
    """Schneidet eine Meldung der Mod auf bekannte Felder zurück. Wirft ValueError bei Unbrauchbarem."""
    if not isinstance(roh, dict) or roh.get("zustand") not in ZUSTAENDE:
        raise ValueError("zustand fehlt oder ist unbekannt")
    k = {"zustand": roh["zustand"], "version": _text(roh.get("version"), 24), "loader": _text(roh.get("loader"), 24),
         "loader_version": _text(roh.get("loader_version"), 24), "mods": int(_zahl(roh.get("mods"), 0, 5000))}
    if k["zustand"] in ("welt", "server"):
        r = roh.get("ruestung") if isinstance(roh.get("ruestung"), dict) else {}
        hot = roh.get("hotbar") if isinstance(roh.get("hotbar"), list) else []
        inv = roh.get("inventar") if isinstance(roh.get("inventar"), list) else []
        url = roh.get("skin_url")
        k.update(
            name=_text(roh.get("name")), adresse=_text(roh.get("adresse"), 120),
            dimension=_kennung(roh.get("dimension")),
            x=int(_zahl(roh.get("x"), -30_000_000, 30_000_000)), y=int(_zahl(roh.get("y"), -2048, 2048)),
            z=int(_zahl(roh.get("z"), -30_000_000, 30_000_000)),
            tageszeit=int(_zahl(roh.get("tageszeit"), 0, 23999)), tag=int(_zahl(roh.get("tag"), 0, 10_000_000)),
            leben=_zahl(roh.get("leben"), 0, 1024), leben_max=_zahl(roh.get("leben_max"), 1, 1024, 20),
            absorption=_zahl(roh.get("absorption"), 0, 1024), hunger=int(_zahl(roh.get("hunger"), 0, 20)),
            ruestung_wert=int(_zahl(roh.get("ruestung_wert"), 0, 40)), erfahrung=int(_zahl(roh.get("erfahrung"), 0, 100_000)),
            ruestung={s: _kennung(r.get(s)) for s in ("kopf", "brust", "beine", "fuesse")},
            hotbar=[_stapel(h) for h in (hot + [{}] * 9)[:9]], inventar=[_stapel(h) for h in (inv + [{}] * 27)[:27]], auswahl=int(_zahl(roh.get("auswahl"), 0, 8)),
            hand=_stapel(roh.get("hand")), nebenhand=_stapel(roh.get("nebenhand")),
            skin_url=url if isinstance(url, str) and SKIN_URL.fullmatch(url) else "",
            skin_pfad=_text(roh.get("skin_pfad"), 80), skin_schmal=bool(roh.get("skin_schmal")))
    return k


def _png(v) -> bytes | None:
    if not isinstance(v, str) or len(v) > 90000:
        return None
    try:
        b = base64.b64decode(v, validate=True)
    except (binascii.Error, ValueError):
        return None
    return b if b.startswith(b"\x89PNG\r\n\x1a\n") and len(b) <= 65536 else None


def annehmen(roh: dict) -> None:
    global _stand, _zeit, _skin_png
    k = bereinigen(roh)
    png = _png(roh.get("skin_png")) if k["zustand"] in ("welt", "server") else None
    if png:
        _skin_png = png
    k["skin_da"] = bool(png or (_skin_png and k.get("skin_pfad", "").startswith("skins/")))
    _stand, _zeit = (None if k["zustand"] == "aus" else k), time.time()


def stand() -> dict | None:
    return _stand if _stand and time.time() - _zeit < VERALTET_S else None


def _reset() -> None:
    global _stand, _zeit, _skin_png
    _stand, _zeit, _skin_png = None, 0.0, None


async def karten() -> list[dict]:
    s = stand()
    if not s:
        return []
    return [{"id": ID, "art": "minecraft", **s, "alter_s": int(time.time() - _zeit)}]


_SKINS: dict[str, bytes] = {}


async def skin_holen() -> bytes | None:
    """Der Skin aus dem letzten Stand. Friday holt ihn selbst (feste Mojang-Adresse, ≤ 64 KB, PNG) und gibt ihn aus dem RAM aus."""
    import httpx
    s = stand()
    if s and s.get("skin_da") and _skin_png:
        return _skin_png
    url = s and s.get("skin_url")
    if not url:
        return None
    if url not in _SKINS:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as c:
            r = await c.get(url)
        if r.status_code != 200 or len(r.content) > 65536 or not r.content.startswith(b"\x89PNG"):
            return None
        if len(_SKINS) >= 4:
            _SKINS.clear()
        _SKINS[url] = r.content
    return _SKINS[url]
