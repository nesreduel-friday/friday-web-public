"""Ein Bild für einen ausgehenden Snap aufbereiten: JPEG in vernünftiger Größe, auf Wunsch mit Text darauf.

Der Text wird HIER ins Bild gerechnet (Pillow, ein Band unten wie die Bildunterschrift in Snapchat), nicht am
Handy über das Text-Werkzeug von Snapchat: Das brauchte blinde Koordinaten, ein Snapchat-Update würde es
still verschieben. Ein fertiges Bild kann der Helfer einfach hochladen und senden.
"""

from __future__ import annotations

import logging
import time
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from kern import config

log = logging.getLogger("friday.snapbild")

MAX_SEITE = 2000        # längste Seite in Pixeln — Snapchat zeigt ohnehin nur Handybildschirm-Größe
QUALITAET = 90
HOCH_BREITE, HOCH_HOEHE = 1080, 1920     # Handy-Hochformat 9:16 für automatische Snaps
ALTER_S = 24 * 3600     # abgelegte Bilder, die der Helfer längst gesendet hat, verschwinden nach einem Tag


class SnapbildFehler(RuntimeError):
    pass


def _schrift(groesse: int):
    return ImageFont.load_default(size=groesse)


def _zeilen(text: str, schrift, breite: int, zeichner: ImageDraw.ImageDraw) -> list[str]:
    zeilen: list[str] = []
    aktuell = ""
    for wort in text.split():
        probe = f"{aktuell} {wort}".strip()
        if zeichner.textlength(probe, font=schrift) <= breite or not aktuell:
            aktuell = probe
        else:
            zeilen.append(aktuell)
            aktuell = wort
    if aktuell:
        zeilen.append(aktuell)
    return zeilen


def _text_auflegen(bild: Image.Image, text: str) -> Image.Image:
    breite, hoehe = bild.size
    grundflaeche = bild.convert("RGBA")
    ebene = Image.new("RGBA", grundflaeche.size, (0, 0, 0, 0))
    zeichner = ImageDraw.Draw(ebene)
    groesse = max(24, breite // 16)
    rand = groesse // 2
    schrift = _schrift(groesse)
    zeilen = _zeilen(text, schrift, breite - 2 * rand, zeichner)[:6]
    zeilenhoehe = int(groesse * 1.25)
    bandhoehe = zeilenhoehe * len(zeilen) + rand
    oben = int(hoehe * 0.72)
    oben = min(oben, hoehe - bandhoehe - rand)
    zeichner.rectangle([0, oben, breite, oben + bandhoehe], fill=(0, 0, 0, 140))
    y = oben + rand // 2
    for zeile in zeilen:
        x = (breite - zeichner.textlength(zeile, font=schrift)) / 2
        zeichner.text((x, y), zeile, font=schrift, fill=(255, 255, 255, 255))
        y += zeilenhoehe
    return Image.alpha_composite(grundflaeche, ebene).convert("RGB")


def _hochformat(bild: Image.Image) -> Image.Image:
    """Das Bild ganz sichtbar (nichts abgeschnitten) in die Mitte einer 9:16-Fläche; was links/rechts oder oben/unten frei bleibt,
    füllt dasselbe Bild groß, abgedunkelt und stark unscharf — statt schwarzer Balken."""
    b, h = bild.size
    if abs(b / h - HOCH_BREITE / HOCH_HOEHE) < 0.02:
        return bild
    hinten = ImageOps.fit(bild, (HOCH_BREITE, HOCH_HOEHE), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    hinten = Image.eval(hinten, lambda v: int(v * 0.7))
    faktor = min(HOCH_BREITE / b, HOCH_HOEHE / h)
    vorn = bild.resize((max(1, round(b * faktor)), max(1, round(h * faktor))), Image.LANCZOS)
    hinten.paste(vorn, ((HOCH_BREITE - vorn.width) // 2, (HOCH_HOEHE - vorn.height) // 2))
    return hinten


def vorbereiten(daten: bytes, text: str = "", hochformat: bool = False) -> bytes:
    """`daten` (PNG/JPEG/WebP/GIF …) als JPEG, höchstens `MAX_SEITE` lang, mit `text` als Band unten, falls
    angegeben. `hochformat`: auf eine 9:16-Fläche mit unscharf gefülltem Rand setzen (automatische Snaps). Ein Bild, das sich
    nicht lesen lässt, ist ein Fehler."""
    try:
        with Image.open(BytesIO(daten)) as roh:
            bild = ImageOps.exif_transpose(roh).convert("RGB")
    except Exception as exc:
        raise SnapbildFehler("Das Bild ließ sich nicht lesen.") from exc
    bild.thumbnail((MAX_SEITE, MAX_SEITE))
    if hochformat:
        bild = _hochformat(bild)
    text = " ".join((text or "").split())[:240]
    if text:
        bild = _text_auflegen(bild, text)
    aus = BytesIO()
    bild.save(aus, "JPEG", quality=QUALITAET, optimize=True)
    return aus.getvalue()


def ablegen(jpeg: bytes) -> str:
    """Legt das fertige Bild für den Helfer ab und gibt den Pfad relativ zum Datenordner zurück (so steht er im
    Auftrag; der Helfer löst ihn gegen den echten Ordner auf dem Host auf). Räumt alte Dateien gleich mit auf."""
    ordner = config.daten_ordner() / "snapchat" / "ausgang"
    ordner.mkdir(parents=True, exist_ok=True)
    grenze = time.time() - ALTER_S
    for datei in ordner.glob("*.jpg"):
        try:
            if datei.stat().st_mtime < grenze:
                datei.unlink()
        except OSError:
            pass
    name = f"{uuid.uuid4().hex}.jpg"
    (ordner / name).write_bytes(jpeg)
    return str(Path("snapchat") / "ausgang" / name)
