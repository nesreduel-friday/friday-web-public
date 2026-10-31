import csv
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from snapmem.chat import STATUS_CHAT_DATE, STATUS_CHAT_OK, STATUS_EXTRA, chat_name, match_chat
from snapmem.cli import METADATA_NAME, PEOPLE_DIR, process
from snapmem.export import parse_chat_ids
from snapmem.faces import count_faces
from snapmem.tests.fotos import foto
from tests.test_metadata import make_jpeg

CHAT_JSON = {
    "freundin": [
        {"Created": "2026-07-06 18:30:00 UTC", "Media IDs": "b~ABC123", "Media Type": "MEDIA"},
        {"Created": "2026-07-07 09:00:00 UTC", "Media IDs": "", "Media Type": "TEXT"},
    ]
}


class ChatTest(unittest.TestCase):
    def test_parse_chat_ids(self):
        ids = parse_chat_ids(json.dumps(CHAT_JSON).encode("utf-8"))
        self.assertEqual(ids, {"b~ABC123": datetime(2026, 7, 6, 18, 30, tzinfo=timezone.utc)})

    def test_chat_name(self):
        self.assertEqual(chat_name("mydata/chat_media/x.jpg"), "chat_media/x.jpg")
        self.assertIsNone(chat_name("mydata/memories/x.jpg"))

    def test_matched_id_gives_exact_time_unmatched_gives_date_only(self):
        names = [
            "chat_media/2026-07-06_b~ABC123.jpg",
            "chat_media/2026-07-08_b~ZZZ999.mp4",
            "chat_media/2026-07-08_overlay~b~ZZZ999.png",
        ]
        result = match_chat(names, parse_chat_ids(json.dumps(CHAT_JSON).encode("utf-8")))
        status, memory = result[names[0]]
        self.assertEqual(status, STATUS_CHAT_OK)
        self.assertEqual(memory.taken.hour, 18)
        status, memory = result[names[1]]
        self.assertEqual(status, STATUS_CHAT_DATE)
        self.assertEqual(memory.kind, "video")
        self.assertEqual(result[names[2]][0], STATUS_EXTRA)


class FacesTest(unittest.TestCase):
    def test_plain_image_has_no_faces(self):
        self.assertEqual(count_faces(make_jpeg()), 0)

    def test_broken_bytes_have_no_faces(self):
        self.assertEqual(count_faces(b"kein bild"), 0)


class ProcessChatAndPeopleTest(unittest.TestCase):
    def test_chat_media_and_people_folder(self):
        memories = {"Saved Media": [
            {"Date": "2023-07-29 10:15:45 UTC", "Media Type": "Image", "Location": ""},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "export.zip"
            dst = Path(tmp) / "out.zip"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mydata/json/memories_history.json", json.dumps(memories))
                z.writestr("mydata/json/chat_history.json", json.dumps(CHAT_JSON))
                z.writestr("mydata/memories/2023-07-29_aaaa-main.jpg", make_jpeg())
                z.writestr("mydata/chat_media/2026-07-06_b~ABC123.jpg", make_jpeg())
                z.writestr("mydata/chat_media/2026-07-08_b~ZZZ999.jpg", make_jpeg())

            with mock.patch("snapmem.cli.count_faces", return_value=1):
                counts = process(str(src), str(dst))

            with zipfile.ZipFile(dst) as z:
                names = set(z.namelist())
                rows = list(csv.reader(
                    io.StringIO(z.read(METADATA_NAME).decode("utf-8-sig")), delimiter=";"))[1:]

            self.assertIn("chat_media/2026-07-06_b~ABC123.jpg", names)
            self.assertIn(f"{PEOPLE_DIR}/2026-07-06_b~ABC123.jpg", names)
            self.assertIn(f"{PEOPLE_DIR}/2023-07-29_aaaa-main.jpg", names)
            by_file = {r[0]: r for r in rows if r[0]}
            self.assertEqual(by_file["chat_media/2026-07-06_b~ABC123.jpg"][1], STATUS_CHAT_OK)
            self.assertEqual(by_file["chat_media/2026-07-06_b~ABC123.jpg"][2], "2026-07-06 18:30:00")
            self.assertEqual(by_file["chat_media/2026-07-08_b~ZZZ999.jpg"][1], STATUS_CHAT_DATE)
            self.assertEqual(by_file["chat_media/2026-07-08_b~ZZZ999.jpg"][2], "2026-07-08")
            self.assertEqual(by_file["chat_media/2026-07-06_b~ABC123.jpg"][6], "chat")
            self.assertEqual(counts["mit Gesicht"], 3)




class GesichtsFixtures(unittest.TestCase):
    """Echte Fotos aus dem Export (verkleinert): Fehltreffer vom 06.10.2026 dürfen nicht wiederkommen."""

    def test_laub_ohne_gesicht(self):
        self.assertEqual(count_faces(foto("kein_mensch_laub.jpg").read_bytes()), 0)

    def test_selfie_hat_gesichter(self):
        self.assertGreaterEqual(count_faces(foto("gesichter_selfie.jpg").read_bytes()), 1)


if __name__ == "__main__":
    unittest.main()
