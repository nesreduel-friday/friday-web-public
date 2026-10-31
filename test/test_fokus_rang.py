"""Fokus-Personen: Vorrang bei der Verarbeitung."""
import os
import tempfile
import unittest
from unittest import mock

from kern.fokus import ablage, rang

U = "11111111-1111-1111-1111-111111111111"


class Namen(unittest.TestCase):
    def test_alle_woerter_muessen_vorkommen_ohne_gross_klein(self):
        self.assertTrue(rang.kommt_vor("Eike und summer muster besprechen Freitag.", "Summer Muster"))
        self.assertFalse(rang.kommt_vor("Eike und Summer besprechen Freitag.", "Summer Muster"))

    def test_einzelner_vorname_zaehlt_wenn_der_name_nur_ein_wort_hat(self):
        self.assertTrue(rang.kommt_vor("Gespräch über Kontakt L", "Kontakt L"))

    def test_teilwort_ist_kein_treffer(self):
        self.assertFalse(rang.kommt_vor("Timothy kommt", "Kontakt A"))

    def test_leerer_name_passt_nie(self):
        self.assertFalse(rang.kommt_vor("irgendwas", ""))


class Rang(unittest.TestCase):
    def test_rang_stufen(self):
        self.assertEqual(rang.rang_von("Eike und Summer Muster reden.", None, ["Summer Muster"]), 0)
        self.assertEqual(rang.rang_von("Eike plant die Schule.", None, ["Summer Muster"]), 1)
        self.assertEqual(rang.rang_von("Nele fragt nach Hilfe.", "p3", ["Summer Muster"]), 2)

    def test_ohne_fokus_bleibt_alles_wie_es_war(self):
        self.assertEqual(rang.rang_von("Summer Muster", None, []), 1)
        self.assertEqual(rang.rang_von("x", "p3", []), 2)


class Sortieren(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        ablage.markieren(U, "Summer Muster", True)

    def tearDown(self):
        self.env.stop()

    def _protokoll(self):
        koepfe = {1: {"person": "p3", "zusammenfassung": "Nele fragt nach Hilfe."},
                  2: {"person": None, "zusammenfassung": "Eike plant die Schule."},
                  3: {"person": None, "zusammenfassung": "Eike und Summer Muster reden."},
                  4: {"person": "p9", "zusammenfassung": "Gespräch"}}
        p = mock.Mock()
        p.kopf.side_effect = lambda i: koepfe.get(i)
        return p

    def test_fokus_dann_eike_dann_rest_stabil(self):
        with mock.patch.object(rang.register, "holen", side_effect=lambda pid: {"name": "Summer Muster"} if pid == "p9" else {"name": "Nele"}):
            self.assertEqual(rang.sortiert([1, 2, 3, 4], self._protokoll()), [3, 4, 2, 1])

    def test_ohne_markierte_personen_unveraendert_und_ohne_nachschlagen(self):
        ablage.markieren(U, "Summer Muster", False)
        p = self._protokoll()
        self.assertEqual(rang.sortiert([1, 2, 3], p), [1, 2, 3])
        p.kopf.assert_not_called()

    def test_unbekanntes_gespraech_wird_nicht_vorgezogen(self):
        self.assertEqual(rang.sortiert([99, 3], self._protokoll())[0], 3)

    def test_personen_fokus_zuerst(self):
        personen = [{"name": "Nele"}, {"name": "Summer Muster"}, {"name": "Kontakt A"}]
        self.assertEqual([p["name"] for p in rang.personen_sortiert(personen)], ["Summer Muster", "Nele", "Kontakt A"])


if __name__ == "__main__":
    unittest.main()
