"""Gate „nur bei Neuem“: Ändert sich seit der letzten Runde etwas? Ohne Netz, ohne Modell."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from kern.denken import buch, neues

JETZT = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
SEIT = JETZT - timedelta(hours=2)


class Neues(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        uhr = mock.patch("kern.config.jetzt", return_value=JETZT)
        uhr.start()
        self.addCleanup(uhr.stop)
        self.b = buch.standard()
        self.gespraeche = []
        self.fakten = []
        self.offene = []
        neues._gesehen = ""
        for ziel, wert in (("kern.protokoll.speicher.standard", mock.Mock(gespraeche=lambda limit=10: self.gespraeche)),
                           ("kern.gedaechtnis.speicher.standard", mock.Mock(fakten=lambda limit=None: self.fakten)),
                           ("kern.erinnerungen.standard", mock.Mock(offene=lambda n=20: self.offene))):
            p = mock.patch(ziel, return_value=wert)
            p.start()
            self.addCleanup(p.stop)

    def test_ohne_aenderung_keine_runde(self):
        self.assertEqual(neues.seit(self.b, SEIT), [])

    def test_neues_gespraech_loest_runde_aus(self):
        self.gespraeche = [{"person": None, "zuletzt": (SEIT + timedelta(minutes=30)).isoformat(), "beginn": SEIT.isoformat()}]
        self.assertEqual(neues.seit(self.b, SEIT), ["gespraeche"])

    def test_gespraech_einer_person_zaehlt_nicht(self):
        self.gespraeche = [{"person": "p1", "zuletzt": JETZT.isoformat(), "beginn": JETZT.isoformat()}]
        self.assertEqual(neues.seit(self.b, SEIT), [])

    def test_neue_notiz_und_faellige_erinnerung(self):
        self.fakten = [{"gueltig_ab": (SEIT + timedelta(minutes=5)).isoformat()}]
        self.offene = [{"faellig": (SEIT + timedelta(minutes=10)).isoformat()}]
        self.assertEqual(neues.seit(self.b, SEIT), ["notizen", "erinnerungen"])

    def test_offener_fokus_faden_loest_immer_aus(self):
        self.b.faden_anlegen("Mathe Abitur", "Wann beginnt die Vorbereitung", quelle="eike")
        self.assertEqual(neues.seit(self.b, SEIT), ["faeden"])

    def test_lesefehler_gilt_als_neu(self):
        with mock.patch("kern.protokoll.speicher.standard", side_effect=RuntimeError("weg")):
            self.assertEqual(neues.seit(self.b, SEIT), ["gespraeche"])

    def test_lange_pause_laeuft_trotzdem(self):
        self.assertEqual(neues.seit(self.b, JETZT - timedelta(hours=neues.MAX_PAUSE_H)), ["pause"])

    def test_neue_auffaelligkeit_loest_aus_gleiche_nicht_nochmal(self):
        neues._gesehen = None
        with mock.patch("kern.denken.auffaelligkeiten.text", return_value="A1"):
            self.assertEqual(neues.seit(self.b, SEIT), ["auffaelligkeiten"])
            self.assertEqual(neues.seit(self.b, SEIT), [])
        with mock.patch("kern.denken.auffaelligkeiten.text", return_value="A2"):
            self.assertEqual(neues.seit(self.b, SEIT), ["auffaelligkeiten"])

    def test_erste_runde_ohne_vorgaenger(self):
        self.assertEqual(neues.seit(self.b, None), ["erste_runde"])


if __name__ == "__main__":
    unittest.main()
