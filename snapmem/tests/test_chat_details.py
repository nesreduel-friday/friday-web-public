import json
import unittest

from snapmem.export import parse_chat_details, parse_chat_ids

DATEN = {
    "slow_gabriel704": [
        {"Media Type": "MEDIA", "Created": "2026-09-23 19:06:28 UTC", "Media IDs": "b~AAA", "Conversation Title": None},
        {"Media Type": "TEXT", "Created": "2026-09-24 08:00:00 UTC", "Media IDs": "", "Conversation Title": None},
    ],
    "49595f91-a18f-4a27-bd24-3e04f83dbb49": [
        {"Media Type": "MEDIA", "Created": "2026-08-15 21:05:43 UTC", "Media IDs": "b~BBB, b~CCC", "Conversation Title": "Feuerwehr"},
        {"Media Type": "TEXT", "Created": "2026-09-23 10:00:00 UTC", "Media IDs": "", "Conversation Title": None},
    ],
}


class ChatDetails(unittest.TestCase):
    def setUp(self):
        self.info = parse_chat_details(json.dumps(DATEN).encode())

    def test_media_id_kennt_den_chat(self):
        self.assertEqual(self.info.ids["b~AAA"][1], "slow_gabriel704")
        self.assertEqual(self.info.ids["b~BBB"][1], "Feuerwehr")       # Gruppentitel statt UUID
        self.assertEqual(self.info.ids["b~CCC"][1], "Feuerwehr")       # mehrere IDs in einer Nachricht

    def test_tage_nennen_alle_aktiven_chats(self):
        self.assertEqual(self.info.tage["2026-09-23"], ["Feuerwehr", "slow_gabriel704"])
        self.assertEqual(self.info.tage["2026-08-15"], ["Feuerwehr"])

    def test_alte_funktion_bleibt_gleich(self):
        self.assertEqual(set(parse_chat_ids(json.dumps(DATEN).encode())), {"b~AAA", "b~BBB", "b~CCC"})


if __name__ == "__main__":
    unittest.main()
