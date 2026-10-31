"""Erkennt Bilder, in die Snapchat Schrift, Aufkleber oder einen Linsen-Effekt FEST eingebrannt hat.

Warum nötig: Die Overlay-Datei fehlt oft trotzdem. Bei Dateien mit GROSSgeschriebener UUID (iOS) sind
26 von 87 Bildern ohne Overlay-Datei eingebrannt (Stichprobe 06.10.2026), bei kleingeschriebener 3 von 61.

Zwei Erkenner, ein Urteil:
  1. Geo-Beschriftung („— Musterdorf —“): Regel auf dünne weiße Waagerechte links und rechts vom Text.
  2. CLIP-Bildmerkmale (models/clip_vision_q.onnx) plus logistische Regression (models/einbrand.npz),
     trainiert an Overlay-Paaren (Bild ohne / mit eingebranntem Overlay) und 148 Handmarkierungen.
Bei großer UUID gilt eine niedrigere Schwelle (strenger), weil dort viel mehr eingebrannt ist.

Gemessene Güte (Kreuzvalidierung, 148 Bilder, 29 eingebrannt): ca. 90 % erkannt, ca. 11 % der sauberen
Bilder gehen fälschlich verloren. Kleine Stichprobe: die Zahlen sind ungefähr.
"""

from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

MODELLE = Path(__file__).resolve().parent.parent / "models"
CLIP = MODELLE / "clip_vision_q.onnx"
GEWICHTE = MODELLE / "einbrand.npz"
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
GEO_DICHTE = 0.03
SCHWELLE_CHAT = 0.4   # empfangene Chat-Bilder: bei 0,1 fielen etwa 70 von 150 normalen Fotos fälschlich raus (06.10.2026, ZIP 2)
_SITZUNG = None
_GEW = None


def geo_text(data: bytes) -> bool:
    """True, wenn eine Geo-Beschriftung (Text zwischen zwei gleich hohen dünnen Linien) zu sehen ist."""
    bild = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if bild is None:
        return False
    breite = 540
    bild = cv2.resize(bild, (breite, max(1, int(breite * bild.shape[0] / bild.shape[1]))))
    grau = cv2.cvtColor(bild, cv2.COLOR_BGR2GRAY)
    hell = cv2.morphologyEx(grau, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 7)))
    maske = (hell >= 28).astype(np.uint8)
    laenge = int(breite * 0.05)
    linien = cv2.morphologyEx(maske, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (laenge, 1)))
    _, _, st, _ = cv2.connectedComponentsWithStats(linien, connectivity=8)
    teile = [(x, y, w, h) for x, y, w, h, _ in st[1:] if w >= laenge and h <= 4 and w <= breite * 0.3]
    for i, (x1, y1, w1, h1) in enumerate(teile):
        for x2, y2, w2, h2 in teile[i + 1:]:
            if abs((y1 + h1 / 2) - (y2 + h2 / 2)) > 3:
                continue
            (ax, aw), (bx, bw) = sorted(((x1, w1), (x2, w2)))
            luecke = bx - (ax + aw)
            mitte = (ax + bx + bw) / 2
            if luecke < breite * 0.12 or abs(mitte - breite / 2) > breite * 0.12 or abs(aw - bw) > max(aw, bw) * 0.5:
                continue
            y = int((y1 + y2) / 2 + 0.5)
            zone = maske[max(0, y - 9):y + 10, ax + aw + 4:bx - 4]
            if zone.size and zone.mean() > GEO_DICHTE:
                return True
    return False


def _sitzung():
    global _SITZUNG
    if _SITZUNG is None:
        if not CLIP.exists():
            raise FileNotFoundError(f"CLIP-Modell fehlt: {CLIP} (siehe models/README.md)")
        import onnxruntime as ort
        _SITZUNG = ort.InferenceSession(str(CLIP), providers=["CPUExecutionProvider"])
    return _SITZUNG


def _vorbereiten(bild: Image.Image) -> np.ndarray:
    bild = bild.convert("RGB").resize((224, 224), Image.BICUBIC)
    return ((np.asarray(bild, np.float32) / 255 - _MEAN) / _STD).transpose(2, 0, 1)


def _norm(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def merkmale(bild: Image.Image) -> np.ndarray:
    """1024 Zahlen: CLIP-Merkmale des ganzen Bildes und seiner unteren Hälfte (dort steht meist Schrift)."""
    breite, hoehe = bild.size
    sitz = _sitzung()
    roh = sitz.run(None, {sitz.get_inputs()[0].name: np.stack(
        [_vorbereiten(bild), _vorbereiten(bild.crop((0, int(hoehe * 0.55), breite, hoehe)))])})[0]
    return np.concatenate([_norm(roh[0]), _norm(roh[1])]).astype(np.float32)


def _gewichte():
    global _GEW
    if _GEW is None:
        if not GEWICHTE.exists():
            raise FileNotFoundError(f"Gewichte fehlen: {GEWICHTE} (training/einbrand_trainieren.py)")
        _GEW = np.load(GEWICHTE)
    return _GEW


def wahrscheinlichkeit(data: bytes) -> float:
    g = _gewichte()
    z = float(merkmale(Image.open(BytesIO(data))) @ g["coef"] + g["intercept"])
    return 1 / (1 + np.exp(-z))


def pruefen(data: bytes, grosse_uuid: bool, chat: bool = False) -> tuple[bool, str]:
    """(eingebrannt?, Begründung für die CSV). Unlesbare Bilder gelten als nicht eingebrannt."""
    if geo_text(data):
        return True, "Geo-Beschriftung"
    try:
        p = wahrscheinlichkeit(data)
    except OSError:
        return False, "nicht lesbar"
    g = _gewichte()
    schwelle = SCHWELLE_CHAT if chat else float(g["schwelle_gross"] if grosse_uuid else g["schwelle_klein"])
    return p >= schwelle, f"Modell {p:.2f} (Schwelle {schwelle:.2f})"
