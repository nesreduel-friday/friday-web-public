"""Deutung kurzer Rufe: Wort-Treffer und Reihung nach Aktivität (04.10.2026)."""
import os
import tempfile
import unittest
from unittest import mock

from kern.personen import register
from kern.protokoll import speicher as pspeicher
from kern.werkzeuge import gedaechtnis as gwerkzeug


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Deutung(Basis):
    def test_exakt_geht_vor(self):
        register.anlegen("Kontakt A", snapchat="kontakt_a_kudla")
        profil, verstanden, vorschlaege = gwerkzeug._person_deuten("kontakt_a_kudla")
        self.assertEqual(profil["name"], "Kontakt A")
        self.assertEqual(verstanden, "")

    def test_einzelnes_wort_wird_verstanden(self):
        register.anlegen("Kontakt A", snapchat="kontakt_a_kudla")
        profil, verstanden, _ = gwerkzeug._person_deuten("Kontakt A")
        self.assertEqual(profil["name"], "Kontakt A")
        self.assertEqual(verstanden, "")      # ein ganzes Namenswort ist nicht geraten: keine Rückfrage (15.10.2026)
        profil, verstanden, _ = gwerkzeug._person_deuten("Lea Kontakt A")
        self.assertEqual(profil["name"], "Kontakt A")
        self.assertIn("verstanden", verstanden)   # „Lea“ steht nicht im Namen: weiter geraten

    def test_mehrere_werden_nach_aktivitaet_gereiht(self):
        a = register.anlegen("Kontakt A Muster", snapchat="kontakt_a_kudla")["id"]
        b = register.anlegen("Kontakt A Meier", snapchat="kontakt_a_w")["id"]
        # Kontakt A Meier war gerade Thema — er steht oben.
        g = pspeicher.standard().gespraech_beginnen("chat", person=b)
        pspeicher.standard().beitrag(g, "du", "Kontakt A Meier kommt mit")
        profil, _, vorschlaege = gwerkzeug._person_deuten("Kontakt A")
        self.assertIsNone(profil)
        self.assertEqual(vorschlaege[0], "Kontakt A Meier")
        self.assertIn("Kontakt A Muster", vorschlaege)

    def test_ohne_aktivitaet_kein_absturz(self):
        register.anlegen("Kontakt A Muster", snapchat="kontakt_a_kudla")
        register.anlegen("Kontakt A Meier", snapchat="kontakt_a_w")
        _, _, vorschlaege = gwerkzeug._person_deuten("Kontakt A")
        self.assertEqual(len(vorschlaege), 2)


if __name__ == "__main__":
    unittest.main()
