import json
import shutil
import tempfile
import unittest
from pathlib import Path

from snapmem import arbeiter
from tests.test_pipeline import PipelineTest


class ArbeiterTest(unittest.TestCase):
    def setUp(self):
        self.ordner = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.ordner, True)
        for u in ("auftraege", "uploads", "ergebnisse"):
            (self.ordner / u).mkdir()
        # Mini-ZIP aus dem Pipeline-Test wiederverwenden
        helfer = PipelineTest("test_fortschritt_wird_gemeldet")
        helfer.setUp()
        self.addCleanup(shutil.rmtree, helfer.tmp, True)
        shutil.copy(helfer._zip(with_video=False), self.ordner / "uploads" / "export.zip")

    def _auftrag(self, auftrag_id="a1", **felder):
        daten = {"id": auftrag_id, "zips": ["export.zip"], "modus": "alles"} | felder
        (self.ordner / "auftraege" / f"{auftrag_id}.json").write_text(json.dumps(daten))

    def test_auftrag_wird_gerechnet(self):
        self._auftrag()
        pfad = arbeiter.naechster(self.ordner)
        self.assertEqual(pfad.stem, "a1")
        arbeiter.bearbeiten(self.ordner, pfad)
        st = arbeiter.status_lesen(self.ordner, "a1")
        self.assertEqual(st["zustand"], "fertig")
        self.assertEqual(st["zaehler"]["ausgegeben"], 5)
        self.assertEqual(st["fertig"], st["gesamt"])
        self.assertTrue((self.ordner / "ergebnisse" / "a1.zip").is_file())
        self.assertIsNone(arbeiter.naechster(self.ordner))   # erledigt → nicht nochmal

    def test_fehlende_zip_wird_als_fehler_gemeldet(self):
        self._auftrag(zips=["gibt_es_nicht.zip"])
        arbeiter.bearbeiten(self.ordner, arbeiter.naechster(self.ordner))
        st = arbeiter.status_lesen(self.ordner, "a1")
        self.assertEqual(st["zustand"], "fehler")
        self.assertIn("nicht gefunden", st["meldung"])

    def test_pfadtricks_und_modus_werden_abgelehnt(self):
        for felder in ({"zips": ["../x.zip"]}, {"zips": ["a/b.zip"]}, {"modus": "quatsch"}, {"zips": []}):
            self._auftrag("t", **felder)
            arbeiter.bearbeiten(self.ordner, self.ordner / "auftraege" / "t.json")
            self.assertEqual(arbeiter.status_lesen(self.ordner, "t")["zustand"], "fehler", felder)

    def test_kaputte_zip_gibt_fehler_statt_absturz(self):
        (self.ordner / "uploads" / "kaputt.zip").write_bytes(b"kein zip")
        self._auftrag(zips=["kaputt.zip"])
        arbeiter.bearbeiten(self.ordner, arbeiter.naechster(self.ordner))
        st = arbeiter.status_lesen(self.ordner, "a1")
        self.assertEqual(st["zustand"], "fehler")
        self.assertFalse((self.ordner / "ergebnisse" / "a1.zip").exists())

    def test_zu_wenig_platz_wird_vorher_gemeldet(self):
        self._auftrag()
        alt = arbeiter.PLATZ_FAKTOR
        arbeiter.PLATZ_FAKTOR = 10**12
        self.addCleanup(setattr, arbeiter, "PLATZ_FAKTOR", alt)
        arbeiter.bearbeiten(self.ordner, arbeiter.naechster(self.ordner))
        self.assertIn("Zu wenig Platz", arbeiter.status_lesen(self.ordner, "a1")["meldung"])

    def test_unterbrochener_lauf_wird_als_fehler_markiert(self):
        self._auftrag()
        arbeiter._status(self.ordner, "a1", zustand="laeuft")
        arbeiter.aufraeumen_nach_absturz(self.ordner)
        st = arbeiter.status_lesen(self.ordner, "a1")
        self.assertEqual(st["zustand"], "fehler")
        self.assertIn("unterbrochen", st["meldung"])


if __name__ == "__main__":
    unittest.main()
