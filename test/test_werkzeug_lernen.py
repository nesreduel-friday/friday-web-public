"""Werkzeuge `lernen` und `schulbuecher`: Friday kann bei „was kommt in der Arbeit dran“ und
„welche Bücher habe ich hochgeladen“ nachsehen (22.09.2026 wusste er von beidem nichts)."""
import unittest
from unittest import mock

from kern.schule import buecher as buecherablage, lernen as lernendaten
from kern.werkzeuge import buecher, katalog, lernen

FAECHER = [{"id": 1, "name": "Mathematik", "arbeiten": 1, "muster": 1, "material": 2, "goodnotes": 1},
           {"id": 2, "name": "Chemie", "arbeiten": 0, "muster": 0, "material": 0, "goodnotes": 0}]

FACH = {
    "fach": {"id": 1, "name": "Mathematik", "semester": {"12/1": {"wert": 11.3, "eintraege": [{"x": 1}]},
                                                          "12/2": {"wert": None, "eintraege": []}}},
    "arbeiten": [{"titel": "Klausur Vektorrechnung", "person": "Kontakt B", "datum": "2026-01-15", "status": "gelesen"}],
    "muster": [{"person": "", "text": "Vorzeichenfehler beim Ableiten", "anzahl": 2}],
    "material": [{"dateiname": "Integral.pdf"}, {"dateiname": "Formeln.pdf"}],
    "goodnotes": [{"name": "Mathe eA"}],
    "aktuell": {"material": [{"dateiname": "Integral.pdf", "zusammenfassung": "Einführung Integralrechnung",
                              "wichtiges": ["A = ∫ f(x) dx"]}],
                "seiten": [{"titel": "Funktionenscharen", "name": "Mathe eA", "datum": "2026-09-07",
                            "text": "Ein Term mit Parameter " + "x" * 600}]},
}


class Lernen(unittest.TestCase):
    def frage(self, fach):
        with mock.patch.object(lernendaten, "liste", return_value=FAECHER), \
                mock.patch.object(lernendaten, "fach", return_value=FACH):
            return lernen.lernen(fach)

    def test_zeigt_aktuelles_muster_arbeiten_und_material(self):
        t = self.frage("mathe")
        self.assertIn("Mathematik", t)
        self.assertIn("Einführung Integralrechnung", t)
        self.assertIn("A = ∫ f(x) dx", t)                      # Formel wörtlich
        self.assertIn("Funktionenscharen", t)
        self.assertIn("Vorzeichenfehler beim Ableiten", t)
        self.assertIn("Klausur Vektorrechnung", t)
        self.assertIn("Integral.pdf", t)
        self.assertIn("Schnitt 11.3", t)

    def test_seitentext_ist_gekuerzt(self):
        self.assertLess(len(self.frage("Mathematik")), 2500)

    def test_unbekanntes_fach_nennt_die_moeglichen(self):
        with mock.patch.object(lernendaten, "liste", return_value=FAECHER):
            t = lernen.lernen("Kunst")
        self.assertIn("Mathematik", t)
        self.assertIn("Chemie", t)

    def test_ohne_fach_listet_alle_faecher_mit_zahlen(self):
        with mock.patch.object(lernendaten, "liste", return_value=FAECHER):
            t = lernen.lernen("")
        self.assertIn("Mathematik", t)
        self.assertIn("2 Material", t)

    def test_ist_fremder_text_und_am_konnektor(self):
        self.assertIn("lernen", lernen.FREMDE_WERKZEUGE)
        self.assertEqual(lernen.KONNEKTOR, "iserv_gruppenordner")
        self.assertIn("lernen", lernen.WERKZEUGE)
        self.assertIn(lernen, katalog._MODULE)


class Schulbuecher(unittest.TestCase):
    def test_listet_titel_fach_und_seiten(self):
        b = {"id": 1, "titel": "AbiBox Englisch", "fach": "Englisch", "status": "fertig", "fortschritt": 3,
             "gesamt": 3, "fehler": None, "dateien": [], "eintraege": [{"seite": s} for s in (18, 19, 20)],
             "indexiert": True}
        ablage = mock.Mock()
        ablage.alle.return_value = [b, {**b, "id": 2, "titel": "Alt", "entfernt": "2026-09-01"}]
        with mock.patch.object(buecherablage, "standard", return_value=ablage):
            t = buecher.schulbuecher()
        self.assertIn("AbiBox Englisch", t)
        self.assertIn("Englisch", t)
        self.assertIn("18", t)
        self.assertNotIn("Alt", t)                              # entfernte Bücher zählen nicht

    def test_ohne_buecher(self):
        ablage = mock.Mock()
        ablage.alle.return_value = []
        with mock.patch.object(buecherablage, "standard", return_value=ablage):
            self.assertIn("keine", buecher.schulbuecher().lower())

    def test_am_konnektor_hausaufgaben(self):
        self.assertEqual(buecher.KONNEKTOR, "hausaufgaben")
        self.assertIn(buecher, katalog._MODULE)


if __name__ == "__main__":
    unittest.main()
