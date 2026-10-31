"""Protokoll: Person je Gespräch, Sprecher in der Abschrift, Kopie-an-Eike-Merker."""
import os
import tempfile
import unittest
from unittest import mock

from kern.protokoll import speicher as pspeicher


class Person(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.p = pspeicher.standard()

    def tearDown(self):
        self.env.stop()

    def test_ohne_person_gehoert_das_gespraech_eike_wie_bisher(self):
        g = self.p.gespraech_beginnen("chat")
        self.assertIsNone(self.p.kopf(g)["person"])
        self.assertEqual(self.p.kopf(g)["graph_eike_kopie"], 0)

    def test_person_wird_gemerkt(self):
        g = self.p.gespraech_beginnen("snapchat", person="p1")
        self.assertEqual(self.p.kopf(g)["person"], "p1")

    def test_abschrift_nennt_den_sprecher_beim_namen(self):
        g = self.p.gespraech_beginnen("snapchat", person="p1")
        self.p.beitrag(g, "du", "Ich mag Schach")
        self.p.beitrag(g, "friday", "Schön!")
        self.assertEqual(self.p.abschrift(g, sprecher="Kontakt B"), "Kontakt B: Ich mag Schach\nFriday: Schön!")
        self.assertEqual(self.p.abschrift(g), "Eike: Ich mag Schach\nFriday: Schön!")  # Standard unverändert

    def test_eike_kopie_offen_nur_fuer_fremde_mit_angekommener_episode(self):
        eigene = self.p.gespraech_beginnen("chat")
        fremd = self.p.gespraech_beginnen("snapchat", person="p1")
        noch_nicht = self.p.gespraech_beginnen("snapchat", person="p1")
        for g in (eigene, fremd, noch_nicht):
            self.p.beitrag(g, "du", "x")
            self.p.zusammenfassen(g, "z")
        self.p.graph_markieren(eigene, "angekommen")
        self.p.graph_markieren(fremd, "angekommen")
        self.p.graph_markieren(noch_nicht, "gesendet")
        self.assertEqual(self.p.eike_kopie_offen(), [fremd])
        self.p.eike_kopie_markieren(fremd)
        self.assertEqual(self.p.eike_kopie_offen(), [])
        self.assertEqual(self.p.kopf(fremd)["graph_eike_kopie"], 1)

    def test_alte_datenbank_ohne_die_spalten_wird_nachgeruestet(self):
        import sqlite3
        ordner = tempfile.mkdtemp()
        db = sqlite3.connect(f"{ordner}/friday.db")
        db.executescript("CREATE TABLE gespraeche (id INTEGER PRIMARY KEY, art TEXT NOT NULL, beginn TEXT NOT NULL, ende TEXT);"
                         "INSERT INTO gespraeche (art, beginn) VALUES ('chat', '2026-09-01T10:00:00+00:00');")
        db.commit()
        db.close()
        neu = pspeicher.Protokoll(__import__("pathlib").Path(ordner))
        self.assertIsNone(neu.kopf(1)["person"])


if __name__ == "__main__":
    unittest.main()
