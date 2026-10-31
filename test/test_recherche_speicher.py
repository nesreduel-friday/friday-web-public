"""Recherche-Speicher (`data/recherchen.db`) — reine Logik, im Temp-Ordner."""
import os
import tempfile
import unittest
from unittest import mock

from kern.recherche import speicher


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Auftraege(Basis):
    def test_anlegen_und_holen(self):
        r = speicher.anlegen("Wann sind die Vorlesungen an der LUH?", "nur Informatik")
        self.assertEqual((r["status"], r["versuche"], r["notizen"], r["quellen"]), ("wartend", 0, [], []))
        self.assertEqual(speicher.holen(r["id"])["hinweise"], "nur Informatik")
        self.assertIsNone(speicher.holen(999))

    def test_ungueltige_frage_wird_abgelehnt(self):
        for f in ("", "   ", "x" * (speicher.FRAGE_MAX + 1)):
            with self.assertRaises(speicher.RechercheFehler, msg=f[:10]):
                speicher.anlegen(f)

    def test_tageslimit_und_offene_auftraege(self):
        for i in range(speicher.TAGESLIMIT):
            r = speicher.anlegen(f"Frage {i}")
            speicher.abschliessen(r["id"], "Bericht", [], "belegt", "")
        with self.assertRaises(speicher.RechercheFehler):
            speicher.anlegen("noch eine")

    def test_hoechstens_fuenf_offene(self):
        with mock.patch.object(speicher, "TAGESLIMIT", 99):
            for i in range(speicher.MAX_OFFEN):
                speicher.anlegen(f"Frage {i}")
            with self.assertRaises(speicher.RechercheFehler):
                speicher.anlegen("zu viel")

    def test_naechster_wartender_ist_der_aelteste(self):
        a, b = speicher.anlegen("Erste"), speicher.anlegen("Zweite")
        self.assertEqual(speicher.naechster_wartender()["id"], a["id"])
        speicher.beginnen(a["id"])
        self.assertEqual(speicher.naechster_wartender()["id"], b["id"])
        speicher.abbrechen(b["id"])
        self.assertIsNone(speicher.naechster_wartender())

    def test_beginnen_notiz_zaehlen_abschliessen(self):
        r = speicher.anlegen("Frage")
        self.assertEqual(speicher.beginnen(r["id"])["status"], "laeuft")
        speicher.notiz(r["id"], "Vorlesungen ab 12.10.", "https://uni-hannover.de/x")
        speicher.zaehlen(r["id"], "suchen")
        speicher.zaehlen(r["id"], "suchen")
        speicher.zaehlen(r["id"], "seiten")
        h = speicher.holen(r["id"])
        self.assertEqual(h["notizen"], [{"text": "Vorlesungen ab 12.10.", "quelle": "https://uni-hannover.de/x"}])
        self.assertEqual(h["schritte"], {"suchen": 2, "seiten": 1})
        speicher.abschliessen(r["id"], "Bericht", [{"url": "https://uni-hannover.de/x", "titel": "LUH", "abruf": "2026-10-15"}], "belegt", "Raum unklar")
        h = speicher.holen(r["id"])
        self.assertEqual((h["status"], h["bericht"], h["sicherheit"], h["offen"]), ("fertig", "Bericht", "belegt", "Raum unklar"))
        self.assertEqual(h["quellen"][0]["titel"], "LUH")
        self.assertTrue(h["ende"])

    def test_notizen_sind_begrenzt(self):
        r = speicher.anlegen("Frage")
        for i in range(speicher.MAX_NOTIZEN + 5):
            speicher.notiz(r["id"], "n" * 900, "q")
        h = speicher.holen(r["id"])
        self.assertEqual(len(h["notizen"]), speicher.MAX_NOTIZEN)
        self.assertEqual(len(h["notizen"][0]["text"]), speicher.NOTIZ_MAX)

    def test_scheitern_und_abbrechen(self):
        a, b, c = speicher.anlegen("A"), speicher.anlegen("B"), speicher.anlegen("C")
        speicher.scheitern(a["id"], "Netz weg")
        self.assertEqual((speicher.holen(a["id"])["status"], speicher.holen(a["id"])["fehler"]), ("fehler", "Netz weg"))
        self.assertTrue(speicher.abbrechen(b["id"]))
        self.assertEqual(speicher.holen(b["id"])["status"], "abgebrochen")
        speicher.abschliessen(c["id"], "x", [], "unsicher", "")
        self.assertFalse(speicher.abbrechen(c["id"]))
        self.assertFalse(speicher.abbrechen(999))

    def test_neustart_setzt_laufende_zurueck_und_gibt_nach_zwei_versuchen_auf(self):
        r = speicher.anlegen("Frage")
        speicher.beginnen(r["id"])
        self.assertEqual(speicher.laufende_zuruecksetzen(), 1)
        h = speicher.holen(r["id"])
        self.assertEqual((h["status"], h["versuche"]), ("wartend", 1))
        speicher.beginnen(r["id"])
        speicher.laufende_zuruecksetzen()
        h = speicher.holen(r["id"])
        self.assertEqual((h["status"], h["versuche"]), ("fehler", 2))
        self.assertEqual(speicher.laufende_zuruecksetzen(), 0)

    def test_nichts_wird_geloescht(self):
        r = speicher.anlegen("Frage")
        speicher.abbrechen(r["id"])
        self.assertEqual(len(speicher.liste()), 1)

    def test_scheitern_und_abschliessen_ueberschreiben_keinen_abbruch(self):
        r = speicher.anlegen("Frage")
        speicher.beginnen(r["id"])
        speicher.abbrechen(r["id"])
        self.assertFalse(speicher.scheitern(r["id"], "zu spät"))
        self.assertFalse(speicher.abschliessen(r["id"], "B", [], "belegt", ""))
        self.assertEqual(speicher.holen(r["id"])["status"], "abgebrochen")
        r2 = speicher.anlegen("Zwei")
        speicher.beginnen(r2["id"])
        self.assertTrue(speicher.abschliessen(r2["id"], "B", [], "belegt", ""))

    def test_tageslimit_zaehlt_nach_deutscher_zeit(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        spaet = datetime(2026, 10, 15, 23, 30, tzinfo=ZoneInfo("Europe/Berlin"))       # = 21:30 UTC
        frueh = datetime(2026, 10, 16, 0, 30, tzinfo=ZoneInfo("Europe/Berlin"))        # = 22:30 UTC, deutscher Tag danach
        with mock.patch.object(speicher.config, "jetzt", return_value=spaet):
            for i in range(speicher.TAGESLIMIT):
                speicher.abschliessen(speicher.anlegen(f"F{i}")["id"], "B", [], "belegt", "")
        with mock.patch.object(speicher.config, "jetzt", return_value=frueh):
            speicher.anlegen("Neuer deutscher Tag")                                  # darf nicht am Vortag hängen

    def test_gedanke_setzen(self):
        r = speicher.anlegen("Frage")
        speicher.gedanke_setzen(r["id"], 42)
        self.assertEqual(speicher.holen(r["id"])["gedanke_id"], 42)


if __name__ == "__main__":
    unittest.main()
