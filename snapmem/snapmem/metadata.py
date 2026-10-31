"""Schreibt Aufnahmedatum und Ort in Bilder und Videos.

Die Dateien werden nur ergänzt, nicht neu kodiert: JPEG über piexif.insert,
MP4 über mutagen. Pixel und Videospur bleiben unverändert.
"""

import os
import tempfile
from datetime import datetime

import piexif
from mutagen.mp4 import MP4

JPEG_SUFFIXES = {".jpg", ".jpeg"}
VIDEO_SUFFIXES = {".mp4", ".mov"}


def has_location(lat: float | None, lon: float | None) -> bool:
    # Snapchat schreibt 0.0, 0.0, wenn kein Ort bekannt ist.
    return lat is not None and lon is not None and not (lat == 0 and lon == 0)


def write_jpeg(data: bytes, taken: datetime, lat: float | None, lon: float | None,
               beschreibung: str | None = None) -> bytes:
    with _temp_file(data, ".jpg") as path:
        exif = piexif.load(path)
        stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
        exif["0th"][piexif.ImageIFD.DateTime] = stamp
        exif["Exif"][piexif.ExifIFD.DateTimeOriginal] = stamp
        exif["Exif"][piexif.ExifIFD.DateTimeDigitized] = stamp
        exif["Exif"][piexif.ExifIFD.OffsetTimeOriginal] = "+00:00"
        if has_location(lat, lon):
            exif["GPS"] = _gps(lat, lon)
        if beschreibung:
            # ImageDescription ist ASCII (Umlaute werden zu "?"), XPComment ist UTF-16 und zeigt Windows als „Kommentare“.
            exif["0th"][piexif.ImageIFD.ImageDescription] = beschreibung.encode("ascii", "replace")
            exif["0th"][piexif.ImageIFD.XPComment] = tuple(beschreibung.encode("utf-16-le"))
        piexif.insert(piexif.dump(exif), path)
        with open(path, "rb") as f:
            return f.read()


def write_mp4(data: bytes, taken: datetime, lat: float | None, lon: float | None,
              beschreibung: str | None = None) -> bytes:
    with _temp_file(data, ".mp4") as path:
        video = MP4(path)
        if video.tags is None:
            video.add_tags()
        video.tags["\xa9day"] = [taken.strftime("%Y-%m-%dT%H:%M:%SZ")]
        if has_location(lat, lon):
            # ISO 6709, wie es Kamera-Apps schreiben: +Breite+Länge/
            video.tags["\xa9xyz"] = [f"{lat:+09.5f}{lon:+010.5f}/"]
        if beschreibung:
            video.tags["\xa9cmt"] = [beschreibung]
        video.save()
        with open(path, "rb") as f:
            return f.read()


def _gps(lat: float, lon: float) -> dict:
    return {
        piexif.GPSIFD.GPSLatitudeRef: "N" if lat >= 0 else "S",
        piexif.GPSIFD.GPSLatitude: _dms(lat),
        piexif.GPSIFD.GPSLongitudeRef: "E" if lon >= 0 else "W",
        piexif.GPSIFD.GPSLongitude: _dms(lon),
    }


def _dms(value: float) -> tuple:
    value = abs(value)
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60 * 10000)
    return ((degrees, 1), (minutes, 1), (seconds, 10000))


class _temp_file:
    """Legt die Bytes als Datei ab, damit piexif und mutagen darauf arbeiten können."""

    def __init__(self, data: bytes, suffix: str):
        self._data = data
        self._suffix = suffix
        self._dir = None

    def __enter__(self) -> str:
        self._dir = tempfile.TemporaryDirectory()
        path = os.path.join(self._dir.name, "medium" + self._suffix)
        with open(path, "wb") as f:
            f.write(self._data)
        return path

    def __exit__(self, *exc) -> None:
        self._dir.cleanup()
