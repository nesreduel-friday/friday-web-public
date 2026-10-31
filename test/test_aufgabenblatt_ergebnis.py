"""Ergebnis (Push + Karte) und Planer des Aufgabenblatt-Laufs."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.aufgabenblatt import ergebnis, lauf, planer, speicher
from test.test_aufgabenblatt_speicher import pdf_b64


def run(c):
    return asyncio.run(c)


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.push = mock.patch.object(ergebnis.benachrichtigungen, "senden")
        self.karte = mock.patch.object(ergebnis.friday_karten, "legen_datei", new_callable=mock.AsyncMock)
        self.m_push, self.m_karte = self.push.start(), self.karte.start()
        self.blatt = speicher.blatt_ablegen("Mathe.pdf", pdf_b64())

    def tearDown(self):
        self.push.stop()
        self.karte.stop()
        self.env.stop()

    def fertig(self):
        a = speicher.anlegen(self.blatt["id"])
        speicher.beginnen(a["id"])
        speicher.abschliessen(a["id"], [{"nummer": "1", "loesung": "42", "weg": "x", "sicher": True}], "")
        return a["id"]


class Abliefern(Basis):
    def test_fertig_gibt_push_und_karte_ohne_gedanken(self):
        run(ergebnis.abliefern(self.fertig()))
        self.assertEqual(self.m_push.call_args[0][:2], ("friday", "Aufgaben fertig"))
        self.assertIn("Mathe.pdf", self.m_push.call_args[0][2])
        self.assertTrue(self.m_karte.await_args[0][0].startswith("Aufgaben:"))
        self.assertIn("42", self.m_karte.await_args[0][1])
        aid = speicher.liste()[0]["id"]
        self.assertEqual(self.m_karte.await_args[0][2], f"/api/aufgabenblatt/{aid}/ausarbeitung")       # die Karte trägt den Download

    def test_fehler_gibt_nur_einen_fehlerpush(self):
        a = speicher.anlegen(self.blatt["id"])
        speicher.beginnen(a["id"])
        speicher.scheitern(a["id"], "kaputt")
        run(ergebnis.abliefern(a["id"]))
        self.assertEqual(self.m_push.call_args[0][1], "Aufgaben gescheitert")
        self.m_karte.assert_not_awaited()

    def test_abgebrochen_meldet_nichts(self):
        a = speicher.anlegen(self.blatt["id"])
        speicher.abbrechen(a["id"])
        run(ergebnis.abliefern(a["id"]))
        self.m_push.assert_not_called()

    def test_push_fehler_kippt_die_karte_nicht(self):
        self.m_push.side_effect = RuntimeError("push kaputt")
        run(ergebnis.abliefern(self.fertig()))
        self.m_karte.assert_awaited()


class Planer(Basis):
    def test_kein_wartender_auftrag(self):
        self.assertIsNone(run(planer.schritt()))

    def test_schritt_loest_und_liefert(self):
        a = speicher.anlegen(self.blatt["id"])
        gelaufen = mock.AsyncMock(side_effect=lambda aid, anfrage=None: speicher.abschliessen(aid, [{"nummer": "1", "loesung": "ok", "sicher": True}], "") and {"status": "fertig"})
        with mock.patch.object(lauf, "aufgaben_loesen", gelaufen):
            self.assertEqual(run(planer.schritt()), "fertig")
        self.assertEqual(speicher.holen(a["id"])["status"], "fertig")
        self.m_push.assert_called_once()

    def test_modellfehler_wird_zu_fehlerstatus_und_planer_lebt(self):
        a = speicher.anlegen(self.blatt["id"])
        with mock.patch.object(lauf, "aufgaben_loesen", mock.AsyncMock(side_effect=RuntimeError("boom"))):
            self.assertEqual(run(planer.schritt()), "fehler")
        self.assertEqual(speicher.holen(a["id"])["status"], "fehler")

    def test_abbruch_waehrend_des_laufs_ist_kein_fehlerpush(self):
        a = speicher.anlegen(self.blatt["id"])

        async def abbrechen(aid, anfrage=None):
            speicher.abbrechen(aid)
            raise RuntimeError("egal")
        with mock.patch.object(lauf, "aufgaben_loesen", abbrechen):
            self.assertEqual(run(planer.schritt()), "abgebrochen")
        self.m_push.assert_not_called()

    def test_start_aufraeumen_setzt_laufende_zurueck(self):
        a = speicher.anlegen(self.blatt["id"])
        speicher.beginnen(a["id"])
        self.assertEqual(planer.start_aufraeumen(), 1)

    def test_sperre_haelt_recherche_und_aufgaben_auseinander(self):
        from kern.dienste import hintergrund
        speicher.anlegen(self.blatt["id"])

        async def test():
            async with hintergrund.SPERRE:
                t = asyncio.create_task(planer.schritt())
                await asyncio.sleep(0.05)
                self.assertFalse(t.done())          # wartet auf die Sperre
            with mock.patch.object(lauf, "aufgaben_loesen", mock.AsyncMock(return_value={"status": "fertig"})):
                await t
        run(test())


if __name__ == "__main__":
    unittest.main()
