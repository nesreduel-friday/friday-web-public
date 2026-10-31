import io
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import piexif
from mutagen.mp4 import MP4
from PIL import Image

from snapmem.metadata import has_location, write_jpeg, write_mp4

TAKEN = datetime(2023, 7, 29, 10, 15, 45, tzinfo=timezone.utc)


def make_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (200, 30, 90)).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def make_mp4() -> bytes:
    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", 8 + len(payload)) + kind + payload

    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    mvhd_body = (
        struct.pack(">I", 0) + struct.pack(">IIII", 0, 0, 1000, 0)
        + struct.pack(">I", 0x10000) + struct.pack(">H", 0x100) + b"\0" * 10
        + bytes([0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x40, 0, 0, 0])
        + b"\0" * 24 + struct.pack(">I", 2)
    )
    return ftyp + box(b"moov", box(b"mvhd", mvhd_body))


class JpegTest(unittest.TestCase):
    def test_writes_date_and_gps_and_keeps_pixels(self):
        original = make_jpeg()
        result = write_jpeg(original, TAKEN, 52.37669, 9.739314)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bild.jpg"
            path.write_bytes(result)
            exif = piexif.load(str(path))

        self.assertEqual(exif["Exif"][piexif.ExifIFD.DateTimeOriginal], b"2023:07:29 10:15:45")
        gps = exif["GPS"]
        self.assertEqual(gps[piexif.GPSIFD.GPSLatitudeRef], b"N")
        self.assertEqual(gps[piexif.GPSIFD.GPSLongitudeRef], b"E")
        lat_deg, lat_min, lat_sec = gps[piexif.GPSIFD.GPSLatitude]
        self.assertEqual(lat_deg[0], 52)
        self.assertEqual(lat_min[0], 22)

        # Keine Neukodierung: die Pixel müssen identisch bleiben.
        self.assertEqual(
            Image.open(io.BytesIO(result)).tobytes(),
            Image.open(io.BytesIO(original)).tobytes(),
        )

    def test_without_location_writes_only_date(self):
        result = write_jpeg(make_jpeg(), TAKEN, 0.0, 0.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bild.jpg"
            path.write_bytes(result)
            exif = piexif.load(str(path))
        self.assertIn(piexif.ExifIFD.DateTimeOriginal, exif["Exif"])
        self.assertEqual(exif["GPS"], {})

    def test_pillow_reads_same_date(self):
        result = write_jpeg(make_jpeg(), TAKEN, None, None)
        exif = Image.open(io.BytesIO(result)).getexif()
        self.assertEqual(exif.get_ifd(0x8769)[piexif.ExifIFD.DateTimeOriginal], "2023:07:29 10:15:45")


class BeschreibungTest(unittest.TestCase):
    def test_jpeg_traegt_chatnamen_auch_mit_umlauten(self):
        data = write_jpeg(make_jpeg(), datetime(2026, 7, 27, tzinfo=timezone.utc), None, None, "Snapchat-Chat: Müller")
        zeroth = piexif.load(data)["0th"]
        self.assertEqual(zeroth[piexif.ImageIFD.ImageDescription], b"Snapchat-Chat: M?ller")
        self.assertEqual(bytes(zeroth[piexif.ImageIFD.XPComment]).decode("utf-16-le"), "Snapchat-Chat: Müller")

    def test_ohne_beschreibung_bleibt_alles_wie_vorher(self):
        zeroth = piexif.load(write_jpeg(make_jpeg(), datetime(2026, 7, 27, tzinfo=timezone.utc), None, None))["0th"]
        self.assertNotIn(piexif.ImageIFD.ImageDescription, zeroth)

    def test_mp4_traegt_kommentar(self):
        data = write_mp4(make_mp4(), datetime(2026, 7, 27, tzinfo=timezone.utc), None, None, "Snapchat-Chat: Müller")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.mp4"
            path.write_bytes(data)
            self.assertEqual(MP4(path).tags["\xa9cmt"], ["Snapchat-Chat: Müller"])


class Mp4Test(unittest.TestCase):
    def test_writes_creation_date_and_location(self):
        result = write_mp4(make_mp4(), TAKEN, 52.37669, 9.739314)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.write_bytes(result)
            tags = MP4(str(path)).tags
        self.assertEqual(tags["\xa9day"], ["2023-07-29T10:15:45Z"])
        self.assertEqual(tags["\xa9xyz"], ["+52.37669+009.73931/"])


class HasLocationTest(unittest.TestCase):
    def test_zero_or_missing_is_no_location(self):
        self.assertFalse(has_location(None, None))
        self.assertFalse(has_location(0.0, 0.0))
        self.assertTrue(has_location(52.0, 9.0))


if __name__ == "__main__":
    unittest.main()
