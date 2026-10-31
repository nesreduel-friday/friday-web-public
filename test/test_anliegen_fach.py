"""Anliegen-Fach: Auswahl, Grenzen, Verfall — ohne Netz."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from kern.anliegen import fach

BERLIN = ZoneInfo("Europe/Berlin")
MITTWOCH = datetime(2026, 10, 7, 12, 0, tzinfo=BERLIN)


class MitFach(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.jetzt = MITTWOCH
        uhr = mock.patch("kern.config.jetzt", side_effect=lambda: self.jetzt)
        uhr.start()
        self.addCleanup(uhr.stop)
        ruhe = mock.patch("kern.anliegen.fach._ist_ruhezeit", return_value=False)
        self.ruhe = ruhe.start()
        self.addCleanup(ruhe.stop)
        self.f = fach.standard()

    def neu(self, titel="Frist Anmeldung", text="Die Anmeldung endet übermorgen um Mitternacht", d="normal",
            empfaenger="eike", **kw):
        e = self.f.anlegen(empfaenger, "hinweis", titel, text, d, **kw)
        self.assertTrue(e["ok"], e)
        return e["id"]


class Anlegen(MitFach):
    def test_anlegen_und_lesen(self):
        a = self.f.eintrag(self.neu())
        self.assertEqual((a["empfaenger"], a["status"], a["dringlichkeit"], a["einblendungen"], a["fremd"]),
                         ("eike", "offen", "normal", 0, False))

    def test_leerer_titel_text_oder_empfaenger_wird_abgelehnt(self):
        self.assertFalse(self.f.anlegen("eike", "hinweis", " ", "Text")["ok"])
        self.assertFalse(self.f.anlegen("eike", "hinweis", "Titel", "")["ok"])
        self.assertFalse(self.f.anlegen("", "hinweis", "Titel", "Text")["ok"])

    def test_zu_lange_texte_werden_abgelehnt_nicht_abgeschnitten(self):
        e = self.f.anlegen("eike", "hinweis", "Titel", "wort " * 100)
        self.assertFalse(e["ok"])
        self.assertIn("Zu lang", e["grund"])
        self.assertFalse(self.f.anlegen("eike", "hinweis", "T" * 100, "Text")["ok"])
        self.assertEqual(self.f.offene("eike"), [])

    def test_dublette_desselben_empfaengers_wird_abgelehnt_anderer_empfaenger_nicht(self):
        self.neu()
        d = self.f.anlegen("eike", "hinweis", "Frist Anmeldung Studienfahrt", "Die Anmeldung endet übermorgen um Mitternacht")
        self.assertFalse(d["ok"])
        self.assertTrue(self.f.anlegen("p1", "erinnerung", "Frist Anmeldung", "Die Anmeldung endet übermorgen um Mitternacht")["ok"])

    def test_unbekannte_dringlichkeit_gilt_als_normal(self):
        self.assertEqual(self.f.eintrag(self.neu(d="sehr dringend"))["dringlichkeit"], "normal")

    def test_fremd_flag(self):
        self.assertTrue(self.f.eintrag(self.neu(fremd=True))["fremd"])


class Auswahl(MitFach):
    def test_ohne_anliegen_nichts(self):
        self.assertIsNone(self.f.auswaehlen("eike", 1))

    def test_ohne_gespraechs_id_nichts_und_nichts_wird_gezaehlt(self):
        a = self.neu()
        self.assertIsNone(self.f.auswaehlen("eike", None))
        self.assertEqual(self.f.eintrag(a)["einblendungen"], 0)

    def test_auswahl_zaehlt_die_einblendung(self):
        a = self.neu()
        g = self.f.auswaehlen("eike", 7)
        self.assertEqual((g["id"], g["einblendungen"], g["eingeblendet_in"]), (a, 1, 7))

    def test_im_selben_gespraech_bleibt_es_dasselbe_ohne_neue_zaehlung(self):
        a = self.neu()
        self.neu("Geburtstag Kontakt P", "Kontakt P hat nächste Woche Dienstag Geburtstag")
        self.assertEqual(self.f.auswaehlen("eike", 7)["id"], a)
        self.assertEqual(self.f.auswaehlen("eike", 7)["id"], a)
        self.assertEqual(self.f.eintrag(a)["einblendungen"], 1)

    def test_hoch_vor_normal_dann_das_aelteste(self):
        n1 = self.neu("Mathe Klausur", "Die Mathe Klausur steht am Montag an")
        self.neu("Zahnarzt", "Der Zahnarzt Termin liegt am Mittwoch früh")
        h = self.neu("Hoch", "Dringendes Anliegen über Bahn", "hoch")
        self.assertEqual(self.f.auswaehlen("eike", 7)["id"], h)
        self.f.abhaken(h, "eike", "erwaehnt")
        self.assertEqual(self.f.auswaehlen("eike", 8)["id"], n1)

    def test_nach_erwaehnung_kommt_es_in_keinem_weiteren_gespraech(self):
        a = self.neu()
        self.f.auswaehlen("eike", 7)
        self.assertTrue(self.f.abhaken(a, "eike", "erwaehnt")["ok"])
        self.assertIsNone(self.f.auswaehlen("eike", 7))
        self.assertIsNone(self.f.auswaehlen("eike", 8))
        self.assertEqual(self.f.eintrag(a)["status"], "erwaehnt")

    def test_tagesgrenze_zwei_normale_hohe_bleiben_moeglich(self):
        themen = [("Mathe Klausur", "Die Mathe Klausur steht am Montag an"),
                  ("Zahnarzt", "Der Zahnarzt Termin liegt am Mittwoch früh"),
                  ("Konzert", "Konzertkarten gibt es nur noch bis Freitag")]
        ids = [self.neu(t, x) for t, x in themen]
        for g, a in zip((7, 8), ids):
            self.assertEqual(self.f.auswaehlen("eike", g)["id"], a)
            self.f.abhaken(a, "eike", "erwaehnt")
        self.assertIsNone(self.f.auswaehlen("eike", 9))                      # normal: heute genug
        h = self.neu("Hoch", "Dringendes Anliegen über Bahn", "hoch")
        self.assertEqual(self.f.auswaehlen("eike", 9)["id"], h)
        self.f.abhaken(h, "eike", "erwaehnt")
        self.jetzt = MITTWOCH + timedelta(days=1)                            # nächster Tag: wieder erlaubt
        self.assertEqual(self.f.auswaehlen("eike", 10)["id"], ids[2])

    def test_in_der_ruhezeit_nur_hohe(self):
        n = self.neu()
        self.ruhe.return_value = True
        self.assertIsNone(self.f.auswaehlen("eike", 7))
        h = self.neu("Hoch", "Dringendes Anliegen über Bahn", "hoch")
        self.assertEqual(self.f.auswaehlen("eike", 7)["id"], h)
        self.ruhe.return_value = False
        self.f.abhaken(h, "eike", "erwaehnt")
        self.assertEqual(self.f.auswaehlen("eike", 8)["id"], n)

    def test_vergisst_das_modell_das_abhaken_verfaellt_es_nach_drei_gespraechen(self):
        a = self.neu()
        for g in (7, 8, 9):
            self.assertEqual(self.f.auswaehlen("eike", g)["id"], a)
        self.assertEqual(self.f.eintrag(a)["einblendungen"], 3)
        self.assertEqual(self.f.auswaehlen("eike", 9)["id"], a)              # im 3. Gespräch bleibt es bis zum Ende
        self.assertIsNone(self.f.auswaehlen("eike", 10))
        self.assertEqual(self.f.eintrag(a)["status"], "verfallen")

    def test_abgelaufenes_anliegen_verfaellt(self):
        a = self.neu()
        self.jetzt = MITTWOCH + timedelta(hours=49)
        self.assertIsNone(self.f.auswaehlen("eike", 7))
        self.assertEqual(self.f.eintrag(a)["status"], "verfallen")

    def test_empfaenger_sehen_nur_ihre_eigenen(self):
        e = self.neu(empfaenger="eike")
        p = self.neu("Buch", "Bring das Buch am Montag mit zur Schule", empfaenger="p1")
        self.assertEqual(self.f.auswaehlen("p1", 7)["id"], p)
        self.assertEqual(self.f.auswaehlen("eike", 8)["id"], e)
        self.assertIsNone(self.f.auswaehlen("p2", 9))


class Abhaken(MitFach):
    def test_spaeter_stellt_vier_stunden_zurueck(self):
        a = self.neu()
        self.f.auswaehlen("eike", 7)
        self.assertTrue(self.f.abhaken(a, "eike", "spaeter")["ok"])
        self.assertIsNone(self.f.auswaehlen("eike", 7))
        self.jetzt = MITTWOCH + timedelta(hours=3)
        self.assertIsNone(self.f.auswaehlen("eike", 8))
        self.jetzt = MITTWOCH + timedelta(hours=5)
        self.assertEqual(self.f.auswaehlen("eike", 9)["id"], a)

    def test_nicht_mehr_verwirft(self):
        a = self.neu()
        self.assertTrue(self.f.abhaken(a, "eike", "nicht_mehr")["ok"])
        self.assertEqual(self.f.eintrag(a)["status"], "verfallen")
        self.assertIsNone(self.f.auswaehlen("eike", 7))

    def test_fremdes_unbekanntes_oder_erledigtes_anliegen_und_unsinn_werden_abgelehnt(self):
        a = self.neu()
        self.assertFalse(self.f.abhaken(a, "p1", "erwaehnt")["ok"])          # gehört Eike
        self.assertFalse(self.f.abhaken(9999, "eike", "erwaehnt")["ok"])
        self.assertFalse(self.f.abhaken(a, "eike", "geloescht")["ok"])
        self.assertTrue(self.f.abhaken(a, "eike", "erwaehnt")["ok"])
        self.assertFalse(self.f.abhaken(a, "eike", "erwaehnt")["ok"])
        self.assertEqual(self.f.eintrag(a)["status"], "erwaehnt")


class Kuerzen(unittest.TestCase):
    def test_kurzer_text_bleibt(self):
        self.assertEqual(fach.kuerzen_satz("Ein Satz."), "Ein Satz.")

    def test_langer_text_endet_an_einer_satzgrenze_mit_verweis(self):
        text = ("Die Info-Veranstaltung ist am Freitag um 16:30 Uhr. " * 12).strip()
        k = fach.kuerzen_satz(text)
        self.assertLessEqual(len(k), fach.TEXT_MAX)
        self.assertTrue(k.endswith("(Mehr im Reiter Gedanken.)"))
        self.assertIn("Uhr. (Mehr", k)

    def test_ein_einziger_endloser_satz_wird_am_wort_gekuerzt(self):
        k = fach.kuerzen_satz("wort " * 200)
        self.assertLessEqual(len(k), fach.TEXT_MAX)
        self.assertIn("…", k)


if __name__ == "__main__":
    unittest.main()
