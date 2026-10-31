"""Privatfotos für die Snapmem-Tests.

Die echten Fotos liegen bewusst nicht im Repo (gitignored unter
``data/privat/fotos``). Fehlt ein Foto überall, überspringt sich der Test
mit klarem Grund — nie stillschweigend grün.
"""
import os
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_TESTDATEN = Path(__file__).resolve().parent / "daten"


def _kandidaten(name: str):
    umgebung = os.environ.get("FRIDAY_PRIVAT_FOTOS")
    if umgebung:
        yield Path(umgebung) / name
    yield _REPO / "data" / "privat" / "fotos" / name
    yield _TESTDATEN / name


def foto(name: str) -> Path:
    """Privatfoto aus data/privat/fotos (nicht im Repo). Fehlt es: Test überspringen."""
    for pfad in _kandidaten(name):
        if pfad.is_file():
            return pfad
    raise unittest.SkipTest("Privatfoto liegt nicht im Repo (data/privat/fotos)")
