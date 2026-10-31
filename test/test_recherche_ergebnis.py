"""Ergebnis abliefern (Push, Karte, Gedanke) und der Planer — alles mit Mocks."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.dashboard import friday_karten
from kern.denken import buch
from kern.dienste import benachrichtigungen
from kern.recherche import ergebnis, lauf, planer, speicher

QUELLEN = [{"url": "https://www.uni-hannover.de/de/juniorstudium/", "titel": "LUH", "abruf": "2026-10-15"},
           {"url": "https://stundenplan.uni-hannover.de/x.pdf", "titel": "", "abruf": "2026-10-15"}]


def run(c):
    return asyncio.run(c)


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.push = mock.patch.object(benachrichtigungen, "senden", return_value={"gesendet": 1})
        self.m_push = self.push.start()

    def tearDown(self):
        self.push.stop()
        self.env.stop()

    def fertig(self, bericht="Die Vorlesungen beginnen am 12.10. " + "x" * 600, sicherheit="teilweise", frage="Wann sind die Vorlesungen der Informatik an der LUH?"):
        r = speicher.anlegen(frage)
        speicher.beginnen(r["id"])
        speicher.abschliessen(r["id"], bericht, QUELLEN, sicherheit, "Räume unklar")
        return r["id"]


class Abliefern(Basis):
    def test_push_karte_und_gedanke(self):
        rid = self.fertig()
        karten = mock.AsyncMock(return_value={"id": "f1"})
        with mock.patch.object(friday_karten, "legen", karten):
            run(ergebnis.abliefern(rid))
        titel, text = self.m_push.call_args.args[1], self.m_push.call_args.args[2]
        self.assertEqual((self.m_push.call_args.args[0], titel), ("friday", "Recherche fertig"))
        self.assertLessEqual(len(text), 160)
        self.assertNotIn("http", text)
        k = karten.await_args
        self.assertLessEqual(len(k.args[1]), 400)
        self.assertIn("Quellen prüfen", k.args[1])
        self.assertEqual(k.kwargs.get("minuten", k.args[3] if len(k.args) > 3 else None), 720)
        g = buch.standard().neueste(5)[0]
        self.assertEqual(g["art"], "erkenntnis")
        self.assertLessEqual(len(g["text"]), 400)
        self.assertIn(f"Recherche #{rid}", g["quellen"])
        self.assertTrue(g["fremd"])
        self.assertEqual(speicher.holen(rid)["gedanke_id"], g["id"])

    def test_langer_bericht_gedanke_und_karte_bleiben_in_den_grenzen(self):
        for frage, bericht in (("Erste Frage zum Wetter", "wort " * 300), ("Zweite Frage zu Bussen", "x" * 900),
                               ("Dritte Frage zu Tarifen", ("a" * 399 + " ") * 3)):
            rid = self.fertig(bericht=bericht, frage=frage)
            karten = mock.AsyncMock(return_value={"id": "f1"})
            with mock.patch.object(friday_karten, "legen", karten):
                run(ergebnis.abliefern(rid))
            self.assertLessEqual(len(karten.await_args.args[1]), 400)
            self.assertTrue(karten.await_args.args[1].endswith("Quellen prüfen."), karten.await_args.args[1][-40:])
            self.assertIsNotNone(speicher.holen(rid)["gedanke_id"], bericht[:10])

    def test_gedanke_zaehlt_nicht_als_denkrunde(self):
        b = buch.standard()
        vorher = b.runden_heute()
        rid = self.fertig()
        with mock.patch.object(friday_karten, "legen", mock.AsyncMock()):
            run(ergebnis.abliefern(rid))
        self.assertEqual(b.runden_heute(), vorher)
        self.assertIsNone(b.letzte_runde_start())

    def test_karte_ohne_adressen(self):
        rid = self.fertig(bericht="Beginn 12.10. (Quelle: https://www.uni-hannover.de/de/x). Mehr unter http://a.example/y.")
        karten = mock.AsyncMock(return_value={"id": "f1"})
        with mock.patch.object(friday_karten, "legen", karten):
            run(ergebnis.abliefern(rid))
        self.assertNotIn("http", karten.await_args.args[1])

    def test_fehler_bei_karte_und_gedanke_kippen_den_push_nicht(self):
        rid = self.fertig()
        with mock.patch.object(friday_karten, "legen", mock.AsyncMock(side_effect=RuntimeError("x"))), \
             mock.patch.object(buch, "standard", side_effect=RuntimeError("y")):
            run(ergebnis.abliefern(rid))
        self.m_push.assert_called_once()

    def test_gescheiterte_recherche_meldet_den_fehler(self):
        r = speicher.anlegen("Frage")
        speicher.scheitern(r["id"], "Netz weg")
        with mock.patch.object(friday_karten, "legen", mock.AsyncMock()) as karten:
            run(ergebnis.abliefern(r["id"]))
        self.assertEqual(self.m_push.call_args.args[1], "Recherche gescheitert")
        karten.assert_not_awaited()

    def test_abgebrochene_recherche_meldet_nichts(self):
        r = speicher.anlegen("Frage")
        speicher.abbrechen(r["id"])
        run(ergebnis.abliefern(r["id"]))
        self.m_push.assert_not_called()


class Planer(Basis):
    def test_nichts_wartet(self):
        self.assertIsNone(run(planer.schritt()))

    def test_ein_auftrag_pro_schritt_der_aelteste_zuerst(self):
        a, b = speicher.anlegen("Erste"), speicher.anlegen("Zweite")

        async def fake(rid, anfrage=None, jetzt=None):
            speicher.abschliessen(rid, "B", QUELLEN, "belegt", "")
            return {"status": "fertig"}

        with mock.patch.object(lauf, "recherchieren", side_effect=fake) as l, mock.patch.object(ergebnis, "abliefern", mock.AsyncMock()) as ab:
            self.assertEqual(run(planer.schritt()), "fertig")
            self.assertEqual(l.call_args.args[0], a["id"])
            ab.assert_awaited_once_with(a["id"])
            self.assertEqual(speicher.holen(b["id"])["status"], "wartend")
            self.assertEqual(run(planer.schritt()), "fertig")
            self.assertEqual(l.call_args.args[0], b["id"])

    def test_ausnahme_im_lauf_setzt_fehler_und_meldet(self):
        r = speicher.anlegen("Frage")
        with mock.patch.object(lauf, "recherchieren", mock.AsyncMock(side_effect=RuntimeError("Gemini aus"))), \
             mock.patch.object(friday_karten, "legen", mock.AsyncMock()):
            self.assertEqual(run(planer.schritt()), "fehler")
        h = speicher.holen(r["id"])
        self.assertEqual(h["status"], "fehler")
        self.assertIn("RuntimeError", h["fehler"])
        self.assertEqual(self.m_push.call_args.args[1], "Recherche gescheitert")

    def test_fehler_nach_abbruch_ueberschreibt_den_abbruch_nicht(self):
        r = speicher.anlegen("Frage")

        async def abbrechen_und_scheitern(rid, anfrage=None, jetzt=None):
            speicher.abbrechen(rid)
            raise RuntimeError("später")

        with mock.patch.object(lauf, "recherchieren", side_effect=abbrechen_und_scheitern):
            self.assertEqual(run(planer.schritt()), "abgebrochen")
        self.assertEqual(speicher.holen(r["id"])["status"], "abgebrochen")
        self.m_push.assert_not_called()

    def test_abgebrochener_lauf_wird_nicht_abgeliefert(self):
        speicher.anlegen("Frage")
        with mock.patch.object(lauf, "recherchieren", mock.AsyncMock(return_value={"status": "abgebrochen"})), \
             mock.patch.object(ergebnis, "abliefern", mock.AsyncMock()) as ab:
            self.assertEqual(run(planer.schritt()), "abgebrochen")
        ab.assert_not_awaited()

    def test_start_setzt_haengende_auftraege_zurueck(self):
        r = speicher.anlegen("Frage")
        speicher.beginnen(r["id"])
        self.assertEqual(planer.start_aufraeumen(), 1)
        self.assertEqual(speicher.holen(r["id"])["status"], "wartend")


if __name__ == "__main__":
    unittest.main()
