"""Fragen-Fach: Grenzen im Code, löscht nie — ohne Netz."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from kern.fragen import fach

JETZT = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))


class Fragen(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.jetzt = JETZT
        uhr = mock.patch("kern.config.jetzt", side_effect=lambda: self.jetzt)
        uhr.start()
        self.addCleanup(uhr.stop)
        self.f = fach.standard()

    def stellen(self, titel="Welche Uhrzeit fürs Briefing?", optionen=("07:00", "08:00"), **kw):
        return self.f.stellen(kw.pop("quelle", "denkrunde"), titel, kw.pop("text", "Es gibt zwei Wege."), list(optionen), **kw)

    def test_stellen_prueft_laengen_und_optionen(self):
        self.assertTrue(self.stellen()["ok"])
        self.assertFalse(self.stellen("x" * 81)["ok"])
        self.assertFalse(self.stellen("Anderes Thema Musik", text="y" * 301)["ok"])
        self.assertFalse(self.stellen("Drittes Thema Wetter", optionen=("a", "b", "c", "d", "e"))["ok"])
        self.assertFalse(self.stellen("Viertes Thema Schule", optionen=("z" * 41,))["ok"])
        self.assertFalse(self.stellen("Thema Quelle falsch", quelle="mail")["ok"])
        self.assertFalse(self.stellen("Thema Bezug falsch", bezug="irgendwas")["ok"])
        self.assertTrue(self.stellen("Thema Bezug Faden", bezug="faden:7", optionen=())["ok"])

    def test_drittes_offene_ueberschritten(self):
        for t in ("Briefing Uhrzeit wählen", "Wecker Lautstärke wählen", "Musik Anbieter wählen"):
            self.assertTrue(self.stellen(t)["ok"], t)
        r = self.stellen("Vierte ganz andere Sache")
        self.assertFalse(r["ok"])
        self.assertIn("offen", r["grund"])

    def test_drei_neue_je_tag(self):
        for t in ("Briefing Uhrzeit wählen", "Wecker Lautstärke wählen", "Musik Anbieter wählen"):
            fid = self.stellen(t)["id"]
            self.f.beantworten(fid, option=1)
        r = self.stellen("Vierte ganz andere Sache")
        self.assertFalse(r["ok"])
        self.assertIn("Heute", r["grund"])
        self.jetzt += timedelta(days=1)
        self.assertTrue(self.stellen("Vierte ganz andere Sache")["ok"])

    def test_dublette_wird_abgelehnt(self):
        self.assertTrue(self.stellen("Briefing Uhrzeit wählen")["ok"])
        self.assertFalse(self.stellen("Uhrzeit Briefing wählen bitte")["ok"])

    def test_dublette_gegen_gestern_beantwortete(self):
        fid = self.stellen("Briefing Uhrzeit wählen")["id"]
        self.f.beantworten(fid, option=1)
        self.jetzt += timedelta(hours=20)
        self.assertFalse(self.stellen("Briefing Uhrzeit wählen")["ok"])

    def test_beantworten_per_option_und_frei(self):
        a = self.stellen("Briefing Uhrzeit wählen")["id"]
        b = self.stellen("Wecker Lautstärke wählen")["id"]
        r = self.f.beantworten(a, option=2)
        self.assertEqual((r["ok"], r["antwort"], r["art"]), (True, "08:00", "option"))
        r = self.f.beantworten(b, text="Lieber leise")
        self.assertEqual((r["antwort"], r["art"]), ("Lieber leise", "frei"))
        self.assertEqual(self.f.eintrag(a)["status"], "beantwortet")

    def test_beantworten_genau_eins_von_beidem(self):
        a = self.stellen()["id"]
        self.assertFalse(self.f.beantworten(a)["ok"])
        self.assertFalse(self.f.beantworten(a, option=1, text="auch frei")["ok"])
        self.assertFalse(self.f.beantworten(a, option=3)["ok"])
        self.assertFalse(self.f.beantworten(a, text="z" * 301)["ok"])
        self.assertEqual(self.f.eintrag(a)["status"], "offen")

    def test_zweite_antwort_wird_abgelehnt(self):
        a = self.stellen()["id"]
        self.assertTrue(self.f.beantworten(a, option=1)["ok"])
        r = self.f.beantworten(a, option=2)
        self.assertFalse(r["ok"])
        self.assertEqual(self.f.eintrag(a)["antwort"], "07:00")

    def test_verfall_loescht_nie(self):
        a = self.stellen(gueltig_stunden=1)["id"]
        self.jetzt += timedelta(hours=2)
        self.assertEqual(self.f.verfallen_markieren(), 1)
        self.assertEqual(self.f.eintrag(a)["status"], "verfallen")
        self.assertEqual(self.f.offene(), [])
        self.assertFalse(self.f.beantworten(a, option=1)["ok"])

    def test_dublette_gegen_kuerzlich_verfallene(self):
        a = self.stellen("Briefing Uhrzeit wählen", gueltig_stunden=1)["id"]
        self.jetzt += timedelta(hours=2)
        self.f.verfallen_markieren()
        self.assertEqual(self.f.eintrag(a)["status"], "verfallen")
        self.assertFalse(self.stellen("Briefing Uhrzeit wählen")["ok"])
        self.jetzt += timedelta(hours=60)
        self.assertTrue(self.stellen("Briefing Uhrzeit wählen")["ok"])

    def test_verfallene_seit(self):
        a = self.stellen("Briefing Uhrzeit wählen", gueltig_stunden=1)["id"]
        self.jetzt += timedelta(hours=2)
        self.assertEqual([x["id"] for x in self.f.verfallene_seit(48)], [a])

    def test_parallel_stellen_haelt_das_offen_limit(self):
        import threading
        ergebnisse = []
        def los(n):
            ergebnisse.append(self.f.stellen("chat", f"Eigenes Thema Nummer{n} Wort{n}x", "Text", [])["ok"])
        ts = [threading.Thread(target=los, args=(n,)) for n in range(8)]
        orig = self.f.offene
        def langsam():
            r = orig()
            import time; time.sleep(0.05)
            return r
        self.f.offene = langsam
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(ergebnisse.count(True), fach.OFFEN_MAX)
        self.assertEqual(len(orig()), fach.OFFEN_MAX)

    def test_beantwortet_seit(self):
        a = self.stellen()["id"]
        self.f.beantworten(a, option=1)
        self.assertEqual([x["id"] for x in self.f.beantwortet_seit(48)], [a])
        self.jetzt += timedelta(hours=49)
        self.assertEqual(self.f.beantwortet_seit(48), [])
        self.assertIsNotNone(self.f.eintrag(a))

    def test_fremd_bleibt_markiert(self):
        a = self.stellen(fremd=True)["id"]
        self.assertTrue(self.f.eintrag(a)["fremd"])


if __name__ == "__main__":
    unittest.main()
