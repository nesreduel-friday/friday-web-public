"""Automatische Zuordnung: nur mit Beleg, nie überschreiben, nie aus Bildtext."""
import base64
import os
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import bilder, bildpersonen

PNG = base64.b64encode(open("web/icons/icon-192.png", "rb").read()).decode()
EINE = {"beschreibung": "x", "personen": 1, "wer": "Kontakt B"}


class Zuordnen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.b = bilder.speichern(None, "image/png", PNG)

    def tearDown(self):
        self.env.stop()

    def test_nachricht_nennt_die_person_wird_zugeordnet_und_vermerkt(self):
        self.assertEqual(bildpersonen.zuordnen(self.b, EINE, "Schau mal, das ist Kontakt B am Strand"), "Kontakt B")
        e = bilder.eintrag(self.b)
        self.assertEqual(e["name"], "Kontakt B")
        self.assertIn("Automatisch zugeordnet", e["notizen"])

    def test_name_ohne_beleg_in_der_nachricht_zaehlt_nicht(self):
        # z. B. der Name stand nur im Bild (fremder Text) oder das Modell hat geraten
        self.assertIsNone(bildpersonen.zuordnen(self.b, EINE, "Schau mal hier"))
        self.assertIsNone(bildpersonen.zuordnen(self.b, EINE, "Das ist Kontakt Brin"))  # kein Wortteil-Treffer
        self.assertFalse(bilder.eintrag(self.b)["name"])

    def test_nicht_bei_null_oder_mehreren_personen(self):
        for n in (0, 2):
            self.assertIsNone(bildpersonen.zuordnen(self.b, {**EINE, "personen": n}, "das ist Kontakt B"))
        self.assertFalse(bilder.eintrag(self.b)["name"])

    def test_absender_gilt_bei_genau_einer_person(self):
        keine = {"beschreibung": "x", "personen": 1, "wer": None}
        self.assertIsNone(bildpersonen.zuordnen(self.b, keine, "", ""))  # ohne Absender wird nicht geraten
        self.assertEqual(bildpersonen.zuordnen(self.b, keine, "", "Kontakt B"), "Kontakt B")

    def test_gesetzter_name_wird_nie_ueberschrieben(self):
        bilder.benennen(self.b, "Kontakt Q")
        self.assertIsNone(bildpersonen.zuordnen(self.b, EINE, "das ist Kontakt B"))
        self.assertEqual(bilder.eintrag(self.b)["name"], "Kontakt Q")

    def test_ohne_auswertung_nichts(self):
        self.assertIsNone(bildpersonen.zuordnen(self.b, None, "das ist Kontakt B"))


if __name__ == "__main__":
    unittest.main()
