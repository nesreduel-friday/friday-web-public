"""Standard-Skill des Moduls Garantie: nur mit wirksamem Modul, Eikes Skill hat Vorrang, nie in der Datenbank."""
import os
import tempfile
import unittest
from unittest import mock

from kern.personen import module, register
from kern.skills import fach, standard, zugriff


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.p = register.anlegen("Kontakt B Muster", snapchat="maya_x")
        self.frei = mock.patch("kern.konnektoren.benutzbar", return_value=True)       # die Garantie ist freigegeben
        self.frei.start()
        self.addCleanup(self.frei.stop)

    def tearDown(self):
        for i in fach._instanzen.values():
            i._db.close()
        fach._instanzen.clear()
        self.env.stop()

    def person(self):
        return register.holen(self.p["id"])


class Standard(Basis):
    def test_ohne_profil_kein_skill(self):
        self.assertIsNone(standard.fuer(self.person()))
        self.assertEqual(zugriff.skills_fuer(self.person()), [])

    def test_mit_profil_gibt_es_den_skill_mit_den_feldern_des_antrags(self):
        register.aendern(self.p["id"], quellen={"garantie": 3})
        s = standard.fuer(self.person())
        self.assertEqual(s["name"], "garantie_annahme")
        pflicht = {f["name"] for f in s["felder"] if f["pflicht"]}
        self.assertEqual(pflicht, {"datum", "start", "ziel"})              # Linie und Abfahrt findet das System selbst
        self.assertEqual({f["name"] for f in s["felder"]} & {"linie", "abfahrt"}, {"linie", "abfahrt"})
        self.assertEqual(s["optionen"], {"einreichen": "mit_freigabe"})
        self.assertTrue({"akte_erfassen", "akte_zusammenfassung", "ticket_lesen", "ticket_bestaetigen",
                         "garantie_antrag_aus_akte"} <= set(s["werkzeuge"]))
        self.assertEqual([x["name"] for x in zugriff.skills_fuer(self.person())], ["garantie_annahme"])

    def test_hebel_aus_oder_pausiert_kein_skill(self):
        register.aendern(self.p["id"], quellen={"garantie": 3}, module={"garantie": "aus"})
        self.assertIsNone(standard.fuer(self.person()))
        register.aendern(self.p["id"], module={"garantie": "auto"}, pausiert=True)
        self.assertEqual(zugriff.skills_fuer(self.person()), [])

    def test_garantie_nicht_freigegeben_kein_skill(self):
        register.aendern(self.p["id"], quellen={"garantie": 3})
        self.assertIsNotNone(standard.fuer(self.person()))
        with mock.patch("kern.konnektoren.benutzbar", return_value=False):
            self.assertIsNone(standard.fuer(self.person()))
            self.assertEqual(zugriff.skills_fuer(self.person()), [])

    def test_unbekannte_oder_leere_person_kein_skill(self):
        self.assertIsNone(standard.fuer(None))
        self.assertIsNone(standard.fuer({"id": "p999"}))
        self.assertIsNone(standard.fuer({"id": ""}))

    def test_werkzeuge_der_person_kommen_aus_dem_standard_skill(self):
        register.aendern(self.p["id"], quellen={"garantie": 3})
        with mock.patch.dict("kern.skills.primitive.PERSONENFAEHIG", {n: {} for n in standard.WERKZEUGE}):
            self.assertTrue(set(standard.WERKZEUGE) <= zugriff.werkzeuge_fuer(self.person()))

    def test_eikes_skill_mit_antragsprimitiv_hat_vorrang(self):
        register.aendern(self.p["id"], quellen={"garantie": 3})
        from kern.werkzeuge import akte  # registriert die echten Primitive im Verzeichnis
        akte._registrieren()
        e = fach.standard().anlegen(name="eigener_ablauf", zweck="x", ausloeser="y", zielgruppe=[self.p["id"]],
                                    anweisung="frag nach", werkzeuge=["akte_erfassen", "garantie_antrag_aus_akte"],
                                    optionen={"einreichen": "mit_freigabe"},
                                    felder=[{"name": "linie", "typ": "text", "pflicht": True}])
        self.assertTrue(e["ok"], e)
        fach.standard().aktivieren(e["id"])
        self.assertEqual([s["name"] for s in zugriff.skills_fuer(self.person())], ["eigener_ablauf"])

    def test_der_standard_skill_steht_nie_in_der_datenbank(self):
        register.aendern(self.p["id"], quellen={"garantie": 3})
        zugriff.skills_fuer(self.person())
        self.assertEqual(fach.standard().aktive(), [])

    def test_neue_werkzeuge_sind_bei_hebel_aus_gesperrt(self):
        p = {**self.person(), "module": {"garantie": "aus"}}
        for n in ("akte_zusammenfassung", "ticket_lesen", "ticket_bestaetigen", "garantie_antrag_aus_akte"):
            self.assertFalse(module.primitiv_erlaubt(p, n), n)
        self.assertTrue(module.primitiv_erlaubt(p, "akte_erfassen"))
