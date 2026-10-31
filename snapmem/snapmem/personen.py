"""Zählt ganze Personen (auch von hinten) mit YOLOX über OpenCV-DNN.

Modell: OpenCV Zoo `object_detection_yolox_2022nov_int8.onnx` (Apache-2.0), in
models/yolox.onnx (sha256 01a3b0f4…). Die Gesichtserkennung (faces.py) findet nur
Gesichter; hier zählt auch eine Person von hinten oder seitlich.
"""

from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parent.parent / "models" / "yolox.onnx"
SIZE = 640
STRIDES = (8, 16, 32)
PERSON_CLASS = 0
SCORE_THRESHOLD = 0.65   # echte Personen lagen im Test ab 0,71, Schuhe/Hände/Lichter darunter (06.10.2026)
NMS_THRESHOLD = 0.5
MIN_AREA_SHARE = 0.01   # Boxen unter 1 % der Bildfläche sind keine „Person im Bild“
_NET = None
_GRIDS = None


def _net():
    global _NET, _GRIDS
    if _NET is None:
        if not MODEL.exists():
            raise FileNotFoundError(f"Personenmodell fehlt: {MODEL}")
        _NET = cv2.dnn.readNet(str(MODEL))
        grids, strides = [], []
        for s in STRIDES:
            n = SIZE // s
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), s))
        _GRIDS = (np.concatenate(grids), np.concatenate(strides))
    return _NET


def _letterbox(image: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = image.shape[:2]
    scale = min(SIZE / h, SIZE / w)
    resized = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((SIZE, SIZE, 3), 114, np.uint8)
    canvas[: resized.shape[0], : resized.shape[1]] = resized
    return canvas, scale


def person_boxes(data: bytes) -> list[tuple[float, float, float, float, float]]:
    """Gibt (x, y, breite, höhe, score) je gefundener Person in Originalpixeln zurück."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return []
    net = _net()
    canvas, scale = _letterbox(image)
    net.setInput(np.transpose(canvas.astype(np.float32), (2, 0, 1))[None])
    dets = net.forward(net.getUnconnectedOutLayersNames())[0][0].copy()
    grids, strides = _GRIDS
    dets[:, :2] = (dets[:, :2] + grids) * strides
    dets[:, 2:4] = np.exp(dets[:, 2:4]) * strides
    scores = dets[:, 4] * dets[:, 5 + PERSON_CLASS]
    keep = scores > SCORE_THRESHOLD
    if not keep.any():
        return []
    boxes = dets[keep, :4]
    xywh = np.stack([boxes[:, 0] - boxes[:, 2] / 2, boxes[:, 1] - boxes[:, 3] / 2, boxes[:, 2], boxes[:, 3]], 1)
    kept = cv2.dnn.NMSBoxes(xywh.tolist(), scores[keep].tolist(), SCORE_THRESHOLD, NMS_THRESHOLD)
    h, w = image.shape[:2]
    result = []
    for i in np.array(kept).flatten():
        x, y, bw, bh = (xywh[i] / scale).tolist()
        if bw * bh >= MIN_AREA_SHARE * w * h:
            result.append((x, y, bw, bh, float(scores[keep][i])))
    return result


def count_persons(data: bytes) -> int:
    return len(person_boxes(data))
