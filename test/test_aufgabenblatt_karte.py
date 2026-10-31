"""Datei-Karte auf der Übersicht: Download der Ausarbeitung per Knopf — nur Adressen der Ausarbeitung, nie fremde."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.dashboard import friday_karten


def run(c):
    return asyncio.run(c)


class DateiKarte(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_karte_traegt_die_datei_und_kommt_in_der_liste(self):
        run(friday_karten.legen_datei("Aufgaben: Blatt", "1: 42", "/api/aufgabenblatt/7/ausarbeitung", "Ausarbeitung Blatt"))
        k = run(friday_karten.karten())[0]
        self.assertEqual((k["titel"], k["art"]), ("Aufgaben: Blatt", "friday"))
        self.assertEqual(k["datei"], {"url": "/api/aufgabenblatt/7/ausarbeitung", "titel": "Ausarbeitung Blatt"})

    def test_fremde_adressen_werden_abgelehnt(self):
        for url in ("https://boese.example/x.pdf", "/api/chat", "/api/aufgabenblatt/x/ausarbeitung", "//boese.example/api/aufgabenblatt/1/ausarbeitung",
                    "/api/aufgabenblatt/1/ausarbeitung?x=1", "javascript:alert(1)", ""):
            with self.assertRaises(friday_karten.KarteFehler, msg=url):
                run(friday_karten.legen_datei("T", "t", url, "x"))
        self.assertEqual(run(friday_karten.karten()), [])


if __name__ == "__main__":
    unittest.main()
