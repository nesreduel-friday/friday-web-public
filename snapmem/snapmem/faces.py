"""Erkennt Gesichter auf Bildern mit OpenCVs YuNet-Detektor.

Das Modell liegt im Repo unter models/ (ca. 230 KB). YuNet findet auch seitliche
und kleinere Gesichter besser als die alte Haar-Cascade.
Gedreht wird nur mit rotate=True (um 90, 180, 270 Grad), nicht mehr standardmäßig. Für Tempo wird das Bild auf höchstens 1280 Pixel verkleinert.
"""

from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"
DETECTOR_THRESHOLD = 0.6    # was der Detektor überhaupt meldet
SCORE_THRESHOLD = 0.8       # was als Gesicht zählt (06.10.2026 gemessen: 0,6 meldete Motorrad, Pizza, Cartoons)
MAX_SIDE = 1280
_ROTATIONS = (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE)
_DETECTOR = None


def _detector() -> cv2.FaceDetectorYN:
    global _DETECTOR
    if _DETECTOR is None:
        if not MODEL.exists():
            raise FileNotFoundError(f"Gesichtsmodell fehlt: {MODEL}")
        _DETECTOR = cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320), DETECTOR_THRESHOLD, 0.3, 5000)
    return _DETECTOR


def count_faces(data: bytes, rotate: bool = False) -> int:
    """Zählt Gesichter mit Score ab SCORE_THRESHOLD. Gedreht wird nur auf Wunsch: Die Exporte sind
    aufrecht, und das Drehen erfand auf Laub, Straße und Motorrädern Gesichter."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return 0
    image = _shrink(image)
    detector = _detector()
    for rotation in (_ROTATIONS if rotate else _ROTATIONS[:1]):
        turned = image if rotation is None else cv2.rotate(image, rotation)
        detector.setInputSize((turned.shape[1], turned.shape[0]))
        _, faces = detector.detect(turned)
        gut = [f for f in faces if f[-1] >= SCORE_THRESHOLD] if faces is not None else []
        if gut:
            return len(gut)
    return 0


def _shrink(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= MAX_SIDE:
        return image
    scale = MAX_SIDE / longest
    return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
