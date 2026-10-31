"""Trainiert models/einbrand.npz neu (Aufruf siehe unten). Läuft einmalig, ca. 10 Minuten auf der CPU.

  pip install scikit-learn   # nur zum Trainieren, nicht im Betrieb
  python -m training.einbrand_trainieren EXPORT1.zip EXPORT2.zip

Daten:
  Paare   500 Memories aus EXPORT1 mit Overlay: das Hauptbild (0) und Hauptbild + Overlay (1)
  Hand    training/labels.json (513 Bilder ohne Overlay-Datei, von Hand markiert) aus EXPORT1/EXPORT2
Gewicht der Handmarkierungen ist 5, die Schwellen (0,1 bei großer UUID, 0,3 bei kleiner) stammen aus
der Kreuzvalidierung vom 06.10.2026 an 513 Handbildern (49/67 erkannt, 39/446 zuviel; schwere Auswahl).
"""

import io
import json
import random
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from snapmem import einbrand

LABELS = Path(__file__).parent / "labels.json"
ZIEL = einbrand.GEWICHTE
PAARE = 500


def paare(z: zipfile.ZipFile):
    namen = set(z.namelist())
    mains = sorted(n for n in namen if n.startswith("memories/") and n.endswith("-main.jpg")
                   and n.replace("-main.jpg", "-overlay.png") in namen)
    random.Random(5).shuffle(mains)
    x, y, benutzt = [], [], 0
    for m in mains:
        if benutzt >= PAARE:
            break
        haupt = Image.open(io.BytesIO(z.read(m))).convert("RGBA")
        over = Image.open(io.BytesIO(z.read(m.replace("-main.jpg", "-overlay.png")))).convert("RGBA")
        if over.size != haupt.size:
            over = over.resize(haupt.size, Image.LANCZOS)
        if (np.asarray(over)[:, :, 3] > 16).mean() < 0.01:
            continue                                   # Overlay (fast) leer: kein Unterschied zu lernen
        for bild, label in ((haupt, 0), (Image.alpha_composite(haupt, over), 1)):
            x.append(einbrand.merkmale(bild.convert("RGB")))
            y.append(label)
        benutzt += 1
        if benutzt % 50 == 0:
            print(f"Paare {benutzt}/{PAARE}", flush=True)
    return np.array(x), np.array(y)


def hand(zips: dict[str, zipfile.ZipFile]):
    x, y = [], []
    for eintrag in json.loads(LABELS.read_text(encoding="utf-8"))["bilder"]:
        data = zips[eintrag["zip"]].read(eintrag["datei"])
        x.append(einbrand.merkmale(Image.open(io.BytesIO(data))))
        y.append(int(eintrag["eingebrannt"]))
    return np.array(x), np.array(y)


def main(pfade: list[str]) -> None:
    from sklearn.linear_model import LogisticRegression
    zips = {Path(p).name: zipfile.ZipFile(p) for p in pfade}
    erste = zips[sorted(zips)[0]]
    for name, z in zips.items():
        if any(n.startswith("memories/") and n.endswith("-overlay.png") for n in z.namelist()):
            erste = z
            break
    xp, yp = paare(erste)
    xh, yh = hand(zips)
    modell = LogisticRegression(C=10, max_iter=5000).fit(
        np.vstack([xp, xh]), np.concatenate([yp, yh]),
        sample_weight=np.concatenate([np.ones(len(xp)), np.full(len(xh), 5.0)]))
    np.savez(ZIEL, coef=modell.coef_[0].astype(np.float32), intercept=np.float32(modell.intercept_[0]),
             schwelle_gross=np.float32(0.1), schwelle_klein=np.float32(0.3),
             info=f"Paare {len(xp) // 2}, Hand {len(xh)} ({int(yh.sum())} eingebrannt), 06.10.2026")
    print("geschrieben:", ZIEL)


if __name__ == "__main__":
    main(sys.argv[1:])
