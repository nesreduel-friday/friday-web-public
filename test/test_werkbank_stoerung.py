import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from kern.anliegen.fach import Fach as AnliegenFach
from kern.werkbank import stoerung
from kern.werkbank.speicher import Fach

START = datetime(2026, 10, 1, 12, 0).astimezone()


class StoerungTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fach = Fach(Path(self._tmp.name) / "w")
        self.anl = AnliegenFach(Path(self._tmp.name) / "a")

    def melden(self, tag=0, text="Zeitüberschreitung", fp="fp"):
        return stoerung.melden("Wetter-Abruf", fp, text, START + timedelta(days=tag), fach=self.fach, anliegen=self.anl)

    def test_unter_schwelle_kein_vorhaben(self):
        self.melden(0)
        r = self.melden(1)
        self.assertEqual(r, {"vorhaben": None, "gemeldet": False})
        self.assertEqual(self.fach.offene(), [])

    def test_drei_tage_legen_ein_vorhaben_und_ein_anliegen_an(self):
        self.melden(0), self.melden(1)
        r = self.melden(2)
        self.assertTrue(r["gemeldet"])
        v = self.fach.eintrag(r["vorhaben"])
        self.assertEqual((v["art"], v["quelle"]), ("fehler", "stoerung"))
        a = self.anl.offene("eike")
        self.assertEqual(len(a), 1)
        self.assertIn("Wetter-Abruf", a[0]["titel"])
        self.assertIn("3 Tagen", a[0]["text"])

    def test_fuenf_vorkommen_am_selben_tag_reichen(self):
        for _ in range(4):
            self.assertFalse(self.melden(0)["gemeldet"])
        self.assertTrue(self.melden(0)["gemeldet"])

    def test_weitere_vorkommen_erzeugen_kein_zweites_anliegen(self):
        for _ in range(5):
            self.melden(0)
        for _ in range(4):
            self.assertFalse(self.melden(0)["gemeldet"])
        self.assertEqual(len(self.anl.offene("eike")), 1)
        self.assertEqual(len(self.fach.offene()), 1)

    def test_zehntes_vorkommen_meldet_erneut_nur_wenn_noch_vorgeschlagen(self):
        for _ in range(9):
            self.melden(0)
        # erstes Anliegen erledigt, sonst blockt dessen Duplikat-Schutz
        self.anl.abhaken(self.anl.offene("eike")[0]["id"], "eike", "erwaehnt")
        r = self.melden(0)
        self.assertTrue(r["gemeldet"])
        self.assertEqual(len(self.fach.offene()), 1)
        # zweiter Fall: Vorhaben nicht mehr vorgeschlagen
        self.fach.status_setzen(r["vorhaben"], "freigegeben")
        self.anl.abhaken(self.anl.offene("eike")[0]["id"], "eike", "erwaehnt")
        for _ in range(9):
            self.melden(0)
        self.assertFalse(self.melden(0)["gemeldet"])
        self.assertEqual(self.anl.offene("eike"), [])

    def test_erledigt_setzt_zaehler_zurueck_und_laesst_vorhaben_offen(self):
        for _ in range(5):
            r = self.melden(0)
        stoerung.erledigt("fp", fach=self.fach)
        self.assertEqual(self.fach.eintrag(r["vorhaben"])["status"], "vorgeschlagen")
        self.assertEqual(len(self.fach.offene()), 1)
        self.assertEqual(self.melden(1), {"vorhaben": None, "gemeldet": False})
        with self.fach.verbindung() as db:
            z = db.execute("SELECT anzahl, tage FROM stoerungen WHERE fingerabdruck = 'fp'").fetchone()
        self.assertEqual(z["anzahl"], 1)
        self.assertEqual(z["tage"], '["2026-10-02"]')

    def test_nach_verworfen_und_uebernommen_kann_dieselbe_stoerung_neu_vorgeschlagen_werden(self):
        for status in ("verworfen", "uebernommen"):
            with self.subTest(status=status):
                for _ in range(5):
                    r = self.melden(0)
                self.fach.status_setzen(r["vorhaben"], status)
                self.anl.abhaken(self.anl.offene("eike")[0]["id"], "eike", "erwaehnt")
                for _ in range(4):                                    # Schwelle gilt neu: unter 5 und unter 3 Tagen nichts
                    self.assertFalse(self.melden(0)["gemeldet"])
                neu = self.melden(0)
                self.assertTrue(neu["gemeldet"])
                self.assertNotEqual(neu["vorhaben"], r["vorhaben"])
                self.assertEqual(len(self.fach.offene()), 1)
                self.assertFalse(self.melden(0)["gemeldet"])          # keine Dauermeldung
                self.fach.status_setzen(neu["vorhaben"], "verworfen")
                self.anl.abhaken(self.anl.offene("eike")[0]["id"], "eike", "erwaehnt")
                stoerung.erledigt("fp", fach=self.fach)

    def test_text_wird_geschwaerzt(self):
        geheim = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef"
        for _ in range(5):
            r = self.melden(0, text=f"Fehler mit Schlüssel {geheim}")
        self.assertNotIn(geheim, self.fach.eintrag(r["vorhaben"])["beleg"])


if __name__ == "__main__":
    unittest.main()
