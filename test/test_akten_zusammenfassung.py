"""Zusammenfassungs-Vermerk in der Akte: Stand und Zug, nur der letzte zählt, Hash ändert sich mit Werten und Ticket."""
import os
import tempfile
import unittest
from unittest import mock

from kern.skills import akten


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.s = akten.standard()
        self.a, _ = self.s.oeffnen("garantie_annahme", "p1")

    def tearDown(self):
        for i in akten._instanzen.values():
            i._db.close()
        akten._instanzen.clear()
        self.env.stop()


class Vermerk(Basis):
    def test_ohne_vermerk_none(self):
        self.assertIsNone(self.s.zusammenfassung(self.a["id"]))

    def test_letzter_vermerk_zaehlt(self):
        self.s.zusammenfassung_merken(self.a["id"], "a" * 16, "1" * 32)
        self.s.zusammenfassung_merken(self.a["id"], "b" * 16, "2" * 32)
        self.assertEqual(self.s.zusammenfassung(self.a["id"]), ("b" * 16, "2" * 16))

    def test_vermerk_steht_im_verlauf_fuer_eike(self):
        self.s.zusammenfassung_merken(self.a["id"], "c" * 16, "3" * 32)
        self.assertTrue(any("Zusammenfassung gezeigt" in e["text"] for e in self.s.ereignisse(self.a["id"])))

    def test_jede_akte_hat_ihren_eigenen_vermerk(self):
        b, _ = self.s.oeffnen("garantie_annahme", "p2")
        self.s.zusammenfassung_merken(b["id"], "d" * 16, "4" * 32)
        self.assertIsNone(self.s.zusammenfassung(self.a["id"]))

    def test_ein_vom_modell_eingeschleuster_ereignistext_zaehlt_nicht(self):
        self.s.ereignis(self.a["id"], "Zusammenfassung gezeigt (Stand ffff, Zug eeee) und mehr")
        self.assertIsNone(self.s.zusammenfassung(self.a["id"]))


class Hash(unittest.TestCase):
    def test_aendert_sich_mit_werten_und_ticket(self):
        w = {"datum": "2026-10-12", "linie": "5"}
        t = {"id": "t1", "nummern_geprueft": True, "kundenkarte": "123456", "ticketnummer": "ABC"}
        h = akten.stand_hash(w, t)
        self.assertEqual(len(h), 16)
        self.assertEqual(h, akten.stand_hash(dict(w), dict(t)))
        self.assertNotEqual(h, akten.stand_hash({**w, "linie": "6"}, t))
        self.assertNotEqual(h, akten.stand_hash(w, {**t, "id": "t2"}))
        self.assertNotEqual(h, akten.stand_hash(w, {**t, "nummern_geprueft": False}))
        self.assertNotEqual(h, akten.stand_hash(w, {**t, "kundenkarte": "654321"}))
        self.assertNotEqual(h, akten.stand_hash(w, None))
