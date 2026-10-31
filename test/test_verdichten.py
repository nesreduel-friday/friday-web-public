"""Notizbuch verdichten: Doppeltes verschmelzen, ohne zu erfinden."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import speicher as gspeicher
from kern.gedaechtnis import verdichten
from kern.schule import briefe


class Zusammenfuehren(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.g = gspeicher.standard()

    def tearDown(self):
        self.env.stop()

    def test_verschmelzen_ueberholt_alte_ohne_zu_loeschen(self):
        self.g.merken("Adresse", "Beispielweg 1, 12345 Musterstadt")
        self.g.merken("Identität/Wohnort", "Eike, Musterdorf, Musterhausen")
        self.g.zusammenfuehren("Wohnort", "Eike, Beispielweg 1, 12345 Musterstadt (Musterhausen)",
                               ["Adresse", "Identität/Wohnort"])
        gueltig = {f["thema"]: f["inhalt"] for f in self.g.fakten()}
        self.assertEqual(list(gueltig), ["Wohnort"])                         # nur noch einer gilt
        self.assertIn("Beispielweg", gueltig["Wohnort"])
        self.assertEqual(len(self.g.historie("adresse")), 1)                 # alt bleibt nachlesbar
        self.assertIsNotNone(self.g.historie("adresse")[0]["gueltig_bis"])   # aber überholt


class Verdichtung(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "k"})
        self.env.start()
        self.g = gspeicher.standard()
        self.g.merken("Adresse", "Beispielweg 1, 12345 Musterstadt")
        self.g.merken("Identität/Wohnort", "Eike, Musterdorf, Musterhausen")
        self.g.merken("Kaffee", "schwarz")

    def tearDown(self):
        self.env.stop()

    def _mit(self, gruppen):
        antwort = {"gruppen": gruppen}
        return mock.patch.object(briefe, "_gemini", mock.AsyncMock(return_value=(antwort, "m")))

    def test_nur_echte_gruppen_von_mindestens_zwei_themen(self):
        with self._mit([
            {"thema": "Wohnort", "inhalt": "Beispielweg 1, 12345 Musterstadt (Musterhausen)",
             "ersetzt": ["Adresse", "Identität/Wohnort"]},
            {"thema": "Kaffee", "inhalt": "schwarz mit Zucker", "ersetzt": ["Kaffee"]},   # nur 1 → ignoriert
        ]):
            n = asyncio.run(verdichten.verdichten())
        self.assertEqual(n, 1)
        gueltig = {f["thema"] for f in self.g.fakten()}
        self.assertEqual(gueltig, {"Wohnort", "Kaffee"})
        self.assertEqual(self.g.fakten("Kaffee")[0]["inhalt"], "schwarz")     # unangetastet

    def test_unbekannte_themen_werden_nicht_angefasst(self):
        with self._mit([{"thema": "X", "inhalt": "y", "ersetzt": ["Gibtsnicht", "AuchNicht"]}]):
            self.assertEqual(asyncio.run(verdichten.verdichten()), 0)
        self.assertEqual(len(self.g.fakten()), 3)                            # nichts verändert
