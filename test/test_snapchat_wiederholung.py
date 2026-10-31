"""Bremse gegen Schleifen: Kommt derselbe Text immer wieder (Lesefehler im Helfer, Vorfall 14./15.10.2026: 25× „Ja“ → 25× „Sir?“),
antwortet Friday ab dem dritten Mal nicht mehr und sagt Eike einmal Bescheid."""
import asyncio
import os
import tempfile
import time
import unittest
from unittest import mock

from kern import konnektoren
from kern.dienste import snapchat as dienst, snapchat_wache as wache
from test.test_snapchat_wache import alt, ergebnis, lauf, protokoll_freigeben


class Funktion(unittest.TestCase):
    def setUp(self):
        wache._gesehen_texte.clear()

    def test_erst_ab_dem_fuenften_gleichen_text_innerhalb_des_fensters(self):
        self.assertEqual([wache._wiederholt(None, "Ja", 100.0 + i) for i in range(6)], [False, False, False, False, True, True])

    def test_verschiedene_texte_zaehlen_getrennt_und_ohne_satzzeichen_und_grossschreibung(self):
        self.assertFalse(wache._wiederholt(None, "Nein", 1.0))
        schreibweisen = ["Ja", "ja!", "JA", "ja ."]
        self.assertEqual([wache._wiederholt(None, t, 2.0 + i) for i, t in enumerate(schreibweisen)], [False] * 4)
        self.assertTrue(wache._wiederholt(None, "Ja?", 9.0))                     # das fünfte „ja“ in irgendeiner Schreibweise

    def test_nach_dem_fenster_beginnt_es_neu(self):
        for i in range(5):
            wache._wiederholt(None, "Ja", float(i))
        self.assertFalse(wache._wiederholt(None, "Ja", wache.WIEDERHOLT_FENSTER_S + 100.0))

    def test_jede_gruppe_fuer_sich(self):
        for i in range(5):
            wache._wiederholt("p3", "Ja", float(i))
        self.assertFalse(wache._wiederholt(None, "Ja", 5.0))
        self.assertFalse(wache._wiederholt("p4", "Ja", 5.0))


class Wache(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        konnektoren.setzen("snapchat", True)
        wache._zurueck.clear()
        wache._unbekannt_gemeldet.clear()
        wache._gesehen_texte.clear()
        wache._wiederholung_gemeldet.clear()

    def tearDown(self):
        protokoll_freigeben()
        self.env.stop()

    def neu(self, i):
        dienst._schreiben("eingang.json", dienst._lesen("eingang.json", []) + [alt("Ja", id=f"e{i}")])

    def test_die_fuenfte_gleiche_nachricht_bekommt_keine_antwort_mehr_und_eike_hoert_einmal_davon(self):
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis("Sir?"))) as m, \
             mock.patch.object(dienst, "auftrag_legen") as legen, \
             mock.patch("kern.dienste.benachrichtigungen.senden", return_value={}) as push:
            for i in range(8):
                self.neu(i)
                lauf(wache.einmal())
        self.assertEqual(m.await_count, 4)                          # nur die ersten vier bekamen eine Antwort
        self.assertEqual(legen.call_count, 4)
        self.assertEqual(dienst.eingang_neu(), [])                  # die übrigen gelten als bearbeitet (kein Dauerlauf)
        self.assertEqual(push.call_count, 1)                        # Eike erfährt es genau einmal
        self.assertIn("Ja", push.call_args.args[2])

    def test_verschiedene_nachrichten_werden_alle_beantwortet(self):
        with mock.patch.object(wache, "_antworten", mock.AsyncMock(return_value=ergebnis("ok"))) as m, \
             mock.patch.object(dienst, "auftrag_legen"):
            for i, t in enumerate(["Hey", "Wie spät?", "Danke", "Ok"]):
                dienst._schreiben("eingang.json", dienst._lesen("eingang.json", []) + [alt(t, id=f"e{i}")])
                lauf(wache.einmal())
        self.assertEqual(m.await_count, 4)
