"""Bilder: Person, von der ein Bild kam — Eike sieht alles, eine Person nur ihre eigenen."""
import base64
import os
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import bilder

PNG = base64.b64encode(open("web/icons/icon-192.png", "rb").read()).decode()


class Person(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_bild_ohne_person_gehoert_eike_wie_bisher(self):
        b = bilder.speichern(None, "image/png", PNG)
        self.assertIsNone(bilder.eintrag(b)["person"])

    def test_bild_traegt_die_person_und_die_liste_filtert(self):
        eike = bilder.speichern(None, "image/png", PNG)
        maya = bilder.speichern(None, "image/png", PNG, person="p1")
        paul = bilder.speichern(None, "image/png", PNG, person="p2")
        self.assertEqual({b["id"] for b in bilder.liste()}, {eike, maya, paul})          # Eike sieht alles
        self.assertEqual([b["id"] for b in bilder.liste(person="p1")], [maya])           # Kontakt B nur ihre
        self.assertEqual(bilder.liste(person="p3"), [])

    def test_alte_datenbank_ohne_person_spalte_wird_nachgeruestet(self):
        import sqlite3
        ordner = tempfile.mkdtemp()
        db = sqlite3.connect(f"{ordner}/friday.db")
        db.executescript("CREATE TABLE bilder (id TEXT PRIMARY KEY, gespraech INTEGER, zeit TEXT NOT NULL, "
                         "mime TEXT NOT NULL, name TEXT, beschreibung TEXT, frage TEXT);"
                         "INSERT INTO bilder (id, zeit, mime) VALUES ('a', '2026-09-01T10:00:00+00:00', 'image/png');")
        db.commit()
        db.close()
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": ordner}):
            self.assertEqual(len(bilder.liste()), 1)
            self.assertEqual(bilder.liste(person="p1"), [])


if __name__ == "__main__":
    unittest.main()
