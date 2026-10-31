"""Prozess-Übersicht: welche Hintergrundläufe gerade laufen oder abgestürzt sind."""

import unittest

from fastapi.testclient import TestClient

from kern.dienste import prozesse
from kern.main import app


class ProzesseTest(unittest.TestCase):
    def setUp(self):
        prozesse._stand.clear()

    def test_gestarteter_lauf_gilt_als_laeuft(self):
        prozesse.gestartet("test")
        e = next(p for p in prozesse.uebersicht() if p["name"] == "test")
        self.assertTrue(e["laeuft"])
        self.assertIsNone(e["fehler"])
        self.assertIsInstance(e["seit"], float)

    def test_abgestuerzter_lauf_zeigt_den_grund(self):
        prozesse.gestartet("test")
        prozesse.abgestuerzt("test", "RuntimeError: kaputt")
        e = next(p for p in prozesse.uebersicht() if p["name"] == "test")
        self.assertFalse(e["laeuft"])
        self.assertEqual(e["fehler"], "RuntimeError: kaputt")

    def test_abgestuerzt_ohne_vorherige_meldung_wird_trotzdem_erfasst(self):
        prozesse.abgestuerzt("nie-gestartet", "Fehler")
        e = next(p for p in prozesse.uebersicht() if p["name"] == "nie-gestartet")
        self.assertFalse(e["laeuft"])

    def test_uebersicht_ist_alphabetisch(self):
        prozesse.gestartet("zuletzt")
        prozesse.gestartet("zuerst")
        namen = [p["name"] for p in prozesse.uebersicht()]
        self.assertEqual(namen, sorted(namen))

    def test_route_zeigt_die_uebersicht(self):
        prozesse.gestartet("test")
        d = TestClient(app).get("/api/prozesse").json()
        self.assertIn("test", [p["name"] for p in d["prozesse"]])


if __name__ == "__main__":
    unittest.main()
