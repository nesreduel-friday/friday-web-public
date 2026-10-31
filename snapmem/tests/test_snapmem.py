import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import piexif

from snapmem.cli import METADATA_NAME, process
from snapmem.export import parse_memories
from tests.test_metadata import make_jpeg
from snapmem.matching import (
    STATUS_AMBIGUOUS,
    STATUS_NO_METADATA,
    STATUS_OK,
    match,
)

JPEG = make_jpeg()

ITEMS = [
    {"Date": "2023-07-29 10:15:45 UTC", "Media Type": "Image",
     "Location": "Latitude, Longitude: 52.37669, 9.739314"},
    {"Date": "2023-07-29 18:00:00 UTC", "Media Type": "Video",
     "Location": "Latitude, Longitude: 0.0, 0.0"},
    {"Date": "2023-08-01 09:00:00 UTC", "Media Type": "Image", "Location": ""},
    {"Date": "2023-08-02 09:00:00 UTC", "Media Type": "Image", "Location": ""},
    {"Date": "2023-08-02 12:00:00 UTC", "Media Type": "Image", "Location": ""},
]

FILES = [
    "2023-07-29_aaaa-main.jpg",
    "2023-07-29_aaaa-overlay.png",
    "2023-07-29_bbbb-main.mp4",
    "2023-08-01_cccc-main.jpg",
    "2023-08-01_cccc-overlay.png",
    "2023-08-02_dddd-main.jpg",
    "2023-08-02_eeee-main.jpg",
    "2023-09-09_EEFF0011-2233-4455-8899-AABBCCDDEEFF-main.jpg",
    "kaputt.jpg",
]


class ParseTest(unittest.TestCase):
    def test_parses_date_type_and_location(self):
        raw = json.dumps({"Saved Media": ITEMS}).encode("utf-8")
        memories = parse_memories(raw)

        self.assertEqual(len(memories), 5)
        self.assertEqual(memories[0].taken.isoformat(), "2023-07-29T10:15:45+00:00")
        self.assertEqual(memories[0].kind, "image")
        self.assertAlmostEqual(memories[0].lat, 52.37669)
        self.assertAlmostEqual(memories[0].lon, 9.739314)
        self.assertEqual(memories[1].kind, "video")

    def test_missing_location_gives_none(self):
        memories = parse_memories(json.dumps({"Saved Media": ITEMS}).encode("utf-8"))
        self.assertIsNone(memories[2].lat)
        self.assertIsNone(memories[2].lon)


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.memories = parse_memories(json.dumps({"Saved Media": ITEMS}).encode("utf-8"))
        self.result = match(FILES, self.memories)

    def test_unique_day_and_type_is_matched(self):
        status, memory = self.result.files["2023-08-01_cccc-main.jpg"]
        self.assertEqual(status, STATUS_OK)
        self.assertEqual(memory.day, "2023-08-01")

    def test_video_is_matched_to_video_entry(self):
        status, memory = self.result.files["2023-07-29_bbbb-main.mp4"]
        self.assertEqual(status, STATUS_OK)
        self.assertEqual(memory.kind, "video")

    def test_overlay_inherits_main_entry(self):
        main_status, main_memory = self.result.files["2023-08-01_cccc-main.jpg"]
        overlay_status, overlay_memory = self.result.files["2023-08-01_cccc-overlay.png"]
        self.assertEqual(overlay_status, main_status)
        self.assertIs(overlay_memory, main_memory)

    def test_same_day_same_type_is_ambiguous_and_not_guessed(self):
        for name in ("2023-08-02_dddd-main.jpg", "2023-08-02_eeee-main.jpg"):
            status, memory = self.result.files[name]
            self.assertEqual(status, STATUS_AMBIGUOUS)
            self.assertIsNone(memory)
        self.assertEqual(len(self.result.candidates["2023-08-02_dddd-main.jpg"]), 2)

    def test_uppercase_uuid_is_recognized(self):
        self.assertEqual(self.result.files["2023-09-09_EEFF0011-2233-4455-8899-AABBCCDDEEFF-main.jpg"][0],
                         STATUS_NO_METADATA)

    def test_file_without_entry_or_wrong_pattern(self):
        self.assertEqual(self.result.files["kaputt.jpg"], (STATUS_NO_METADATA, None))

    def test_unassigned_entries_are_reported(self):
        # 2023-08-02 hat zwei Dateien und zwei Einträge, beide sind mehrdeutig.
        days = sorted(m.day for m in self.result.unassigned)
        self.assertEqual(days, ["2023-08-02", "2023-08-02"])


class ProcessTest(unittest.TestCase):
    def test_zip_in_zip_out_keeps_media_and_writes_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "export.zip"
            dst = Path(tmp) / "out.zip"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mydata~1/json/memories_history.json",
                           json.dumps({"Saved Media": ITEMS}))
                z.writestr("mydata~1/memories/2023-07-29_aaaa-main.jpg", JPEG)
                z.writestr("mydata~1/memories/2023-09-01_zzzz-main.jpg", b"jpg-bytes-2")
                z.writestr("mydata~1/html/chat_history.html", b"<html></html>")

            counts = process(str(src), str(dst))

            with zipfile.ZipFile(dst) as z:
                names = sorted(z.namelist())
                self.assertEqual(names, [
                    "memories/2023-07-29_aaaa-main.jpg",
                    "memories/2023-09-01_zzzz-main.jpg",
                    METADATA_NAME,
                ])
                written = z.read("memories/2023-07-29_aaaa-main.jpg")
                self.assertEqual(piexif.load(written)["Exif"][piexif.ExifIFD.DateTimeOriginal], b"2023:07:29 10:15:45")
                rows = list(csv.reader(
                    io.StringIO(z.read(METADATA_NAME).decode("utf-8-sig")), delimiter=";"))

            self.assertEqual(rows[0][0], "datei")
            by_file = {r[0]: r for r in rows[1:] if r[0]}
            self.assertEqual(by_file["memories/2023-07-29_aaaa-main.jpg"][1], STATUS_OK)
            self.assertEqual(by_file["memories/2023-07-29_aaaa-main.jpg"][2], "2023-07-29 10:15:45")
            self.assertEqual(by_file["memories/2023-09-01_zzzz-main.jpg"][1], STATUS_NO_METADATA)
            self.assertEqual(counts[STATUS_OK], 1)

    def test_broken_image_is_kept_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "export.zip"
            dst = Path(tmp) / "out.zip"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mydata~1/json/memories_history.json",
                           json.dumps({"Saved Media": ITEMS}))
                z.writestr("mydata~1/memories/2023-07-29_aaaa-main.jpg", b"kein jpeg")

            counts = process(str(src), str(dst))

            with zipfile.ZipFile(dst) as z:
                self.assertEqual(z.read("memories/2023-07-29_aaaa-main.jpg"), b"kein jpeg")
            self.assertEqual(counts["metadaten fehlgeschlagen"], 1)

    def test_missing_json_stops_with_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "leer.zip"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mydata~1/memories/2023-07-29_aaaa-main.jpg", b"x")
            with self.assertRaises(SystemExit):
                process(str(src), str(Path(tmp) / "out.zip"))


if __name__ == "__main__":
    unittest.main()
