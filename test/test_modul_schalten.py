"""modul_schalten: nur Eike, nie nach Fremdtext, Person nur aus dem Register, Person-Instanzen sehen es nie."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.personen import register
from kern.werkzeuge import katalog, module_schalten as ms


def lauf(c):
    return asyncio.run(c)


class Schalten(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.maya = register.anlegen("Kontakt B Muster", snapchat="maya_x")

    def tearDown(self):
        self.env.stop()

    def modul(self):
        return register.holen(self.maya["id"])["module"]

    def test_schaltet_flammen_an_und_protokolliert(self):
        r = ms.modul_schalten("Kontakt B Muster", "flammen", "an")
        self.assertTrue(r.startswith("Erledigt"), r)
        self.assertTrue(self.modul()["flammen"])

    def test_plan_und_garantie_nehmen_ihre_stufen(self):
        self.assertTrue(ms.modul_schalten("Kontakt B Muster", "plan", "voll").startswith("Erledigt"))
        self.assertEqual(self.modul()["plan"], "voll")
        r = ms.modul_schalten("Kontakt B Muster", "garantie", "an")
        self.assertIn("Profil", r)
        self.assertFalse(r.startswith("Erledigt"))
        self.assertEqual(self.modul()["garantie"], "auto")

    def test_unbekannte_person_oder_modul_oder_wert(self):
        self.assertIn("Nichts geändert", ms.modul_schalten("Niemand", "flammen", "an"))
        self.assertIn("Nichts geändert", ms.modul_schalten("Kontakt B Muster", "magie", "an"))
        self.assertIn("Nichts geändert", ms.modul_schalten("Kontakt B Muster", "flammen", "vielleicht"))
        self.assertIn("Nichts geändert", ms.modul_schalten("Kontakt B Muster", "plan", "alles"))
        self.assertFalse(self.modul()["flammen"])

    def test_geratener_name_aendert_nichts(self):
        register.anlegen("Kontakt A")
        self.assertIn("Nichts geändert", ms.modul_schalten("Tom Kontakt A", "flammen", "an"))

    def test_nach_fremdtext_nichts(self):
        r = lauf(katalog.ausfuehren("modul_schalten", {"person": "Kontakt B Muster", "modul": "flammen", "wert": "an"},
                                    fremde_inhalte=True))
        self.assertIn("fehler", r)
        self.assertFalse(self.modul()["flammen"])

    def test_ueber_den_katalog_klappt_es_ohne_fremdtext(self):
        r = lauf(katalog.ausfuehren("modul_schalten", {"person": "Kontakt B Muster", "modul": "flammen", "wert": "an"}))
        self.assertIn("Erledigt", r["ergebnis"])
        self.assertTrue(self.modul()["flammen"])

    def test_person_instanz_sieht_es_nie_eike_schon(self):
        self.assertFalse(katalog._person_darf(register.holen(self.maya["id"]), "modul_schalten"))
        self.assertTrue(katalog._person_darf(None, "modul_schalten"))

    def test_nicht_in_der_denkrunde(self):
        self.assertNotIn("modul_schalten", katalog.DENKEN_ERLAUBT)


class Gegenlesen(unittest.TestCase):
    def test_im_sprachkanal_erst_vorlesen_und_namen_pruefen(self):
        from kern.ehrlichkeit import gegenlesen
        self.assertIn("modul_schalten", gegenlesen.GEGENLESEN)
        self.assertTrue(gegenlesen.betrifft("modul_schalten", {"person": "Kontakt B Muster", "modul": "plan", "wert": "voll"}))
        wl = gegenlesen.wortlaut("modul_schalten", {"person": "Kontakt B Muster", "modul": "plan", "wert": "voll"}, "Kontakt B Muster")
        self.assertIn("Kontakt B Muster", wl)
        self.assertIn("plan", wl)
        self.assertIn("voll", wl)
