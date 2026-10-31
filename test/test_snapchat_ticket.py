"""Ticket-Annahme per Snapchat (09.10.2026): Schalter, Erwartet-Liste, Bildnamen — ohne Netz, ohne Handy."""

import os
import tempfile
import unittest
from unittest import mock

from kern.dienste import snapchat


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class SchalterUndListe(Basis):
    def test_ohne_datei_ist_die_annahme_aus(self):
        self.assertEqual(snapchat.ticket_modus(), "aus")

    def test_bekannte_modi_werden_gelesen_unbekannte_sind_aus(self):
        for wert, soll in (("trocken", "trocken"), ("an", "an"), ("aus", "aus"), ("ja", "aus"), (5, "aus"), (None, "aus")):
            snapchat._schreiben("einstellungen.json", {"heimat": True, "ticket_annahme": wert})
            self.assertEqual(snapchat.ticket_modus(), soll, wert)

    def test_erwartet_setzen_und_lesen(self):
        self.assertEqual(snapchat.ticket_erwartet(), set())
        snapchat.ticket_erwartet_setzen("p3", True)
        snapchat.ticket_erwartet_setzen("eike", True)
        self.assertEqual(snapchat.ticket_erwartet(), {"p3", "eike"})
        snapchat.ticket_erwartet_setzen("p3", False)
        self.assertEqual(snapchat.ticket_erwartet(), {"eike"})

    def test_ungueltige_ids_werden_abgelehnt(self):
        for schlecht in ("", "p0", "../x", "Lea", "p1; rm", None, 3):
            with self.assertRaises(ValueError, msg=repr(schlecht)):
                snapchat.ticket_erwartet_setzen(schlecht, True)

    def test_kaputte_liste_gibt_leere_menge(self):
        snapchat._schreiben("ticket_erwartet.json", {"p1": True})
        self.assertEqual(snapchat.ticket_erwartet(), set())


class Bildnamen(Basis):
    def test_jpg_wird_gelesen_und_entfernt_pfade_nicht(self):
        name = "a" * 32 + ".jpg"
        (snapchat._ordner() / "bilder").mkdir()
        (snapchat._ordner() / "bilder" / name).write_bytes(b"jpegdaten")
        self.assertEqual(snapchat.bild_lesen(name), b"jpegdaten")
        self.assertIsNone(snapchat.bild_lesen("../" + name))
        self.assertIsNone(snapchat.bild_lesen("a" * 32 + ".gif"))
        snapchat.bild_entfernen(name)
        self.assertIsNone(snapchat.bild_lesen(name))
