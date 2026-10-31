"""Module (Hebel) einer Kontakt-Person — reine Logik, ohne Netz, im Temp-Ordner."""
import json
import os
import tempfile
import unittest
from unittest import mock

from kern import config
from kern.personen import PersonenFehler, module, rechte


def person(**kw):
    p = {"id": "p1", "name": "Kontakt B", "werkzeuge": ["uhrzeit"], "quellen": {}}
    p.update(kw)
    return p


class Ueberwachung(unittest.TestCase):
    def test_standard_aus_und_nicht_wirksam(self):
        self.assertFalse(module.von_person(person())["ueberwachung"])
        self.assertNotIn("ueberwachung", module.wirksam(person(module={"ueberwachung": True})))   # kein Werkzeug, nur der Push
        self.assertTrue(module.von_person(person(module={"ueberwachung": True}))["ueberwachung"])
        self.assertFalse(module.von_person(person(module={"ueberwachung": "ja"}))["ueberwachung"])


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Wirksam(Basis):
    def test_standard_ist_alles_aus_und_garantie_auto(self):
        m = module.von_person(person())
        self.assertEqual(m, {"plan": "aus", "garantie": "auto", "medien": False, "snap_wuensche": False, "flammen": False,
                         "ueberwachung": False})
        self.assertEqual(module.wirksam(person()), set())

    def test_garantie_auto_braucht_ein_profil(self):
        self.assertNotIn("garantie", module.wirksam(person()))
        self.assertIn("garantie", module.wirksam(person(quellen={"garantie": 4})))

    def test_garantie_aus_schlaegt_das_profil(self):
        p = person(quellen={"garantie": 4}, module={"garantie": "aus"})
        self.assertNotIn("garantie", module.wirksam(p))

    def test_garantie_an_ohne_profil_ist_nicht_wirksam(self):
        p = person(module={"garantie": "an"})
        self.assertNotIn("garantie", module.wirksam(p))

    def test_profil_null_ist_eike_und_zaehlt_nicht(self):
        self.assertFalse(module.hat_garantie_profil(person(quellen={"garantie": 0})))
        self.assertFalse(module.hat_garantie_profil(person(quellen={"garantie": "x"})))

    def test_muell_im_register_faellt_auf_standard(self):
        p = person(module={"flammen": "ja", "plan": "alles", "garantie": 7, "medien": 1, "unbekannt": True})
        self.assertEqual(module.von_person(p), module.von_person(person()))
        self.assertEqual(module.wirksam(p), set())

    def test_plan_wird_aus_alten_werkzeugen_abgeleitet(self):
        self.assertEqual(module.von_person(person(werkzeuge=["uhrzeit", "kalender"]))["plan"], "voll")
        self.assertEqual(module.von_person(person(werkzeuge=["uhrzeit", "stundenplan"]))["plan"], "voll")
        self.assertEqual(module.von_person(person(werkzeuge=["uhrzeit", "kalender_frei"]))["plan"], "frei")
        self.assertEqual(module.von_person(person(werkzeuge=["uhrzeit", "termin_wunsch"]))["plan"], "aus")   # allgemeines Recht
        self.assertEqual(module.von_person(person(werkzeuge=["uhrzeit"]))["plan"], "aus")


class Pruefen(Basis):
    def test_gueltige_werte(self):
        self.assertEqual(module.pruefen({"plan": "frei", "flammen": True}), {"plan": "frei", "flammen": True})

    def test_ungueltiges_wirft(self):
        for roh in ({"plan": "alles"}, {"garantie": "ja"}, {"medien": "an"}, {"flammen": 1}, {"nix": True}, "x", ["plan"]):
            with self.assertRaises(PersonenFehler, msg=roh):
                module.pruefen(roh)

    def test_garantie_an_ohne_profil_wird_abgelehnt(self):
        with self.assertRaises(PersonenFehler):
            module.zusammenfuehren(person(), {"garantie": "an"})
        neu = module.zusammenfuehren(person(quellen={"garantie": 3}), {"garantie": "an", "medien": True})
        self.assertEqual((neu["garantie"], neu["medien"]), ("an", True))


class Werkzeuge(Basis):
    def test_plan_stufen_oeffnen_die_richtigen_werkzeuge(self):
        self.assertEqual(set(module.werkzeuge_fuer(person(module={"plan": "aus"}))), set(module.BASIS))
        frei = module.werkzeuge_fuer(person(module={"plan": "frei"}))
        self.assertEqual(set(frei), set(module.BASIS) | {"kalender_frei"})
        voll = module.werkzeuge_fuer(person(module={"plan": "voll"}))
        self.assertEqual(set(voll), set(module.BASIS) | {"stundenplan", "kalender_frei", "kalender", "kalender_suchen"})

    def test_grundfunktionen_sind_immer_an_egal_was_im_alten_feld_steht(self):
        for roh in ([], ["uhrzeit"], ["uhrzeit", "suchen"]):
            w = module.werkzeuge_fuer(person(werkzeuge=roh, module={"plan": "aus"}))
            self.assertEqual(set(w), {"uhrzeit", "suchen", "nachrichten"})

    def test_nie_etwas_ausserhalb_der_positivliste(self):
        w = module.werkzeuge_fuer(person(werkzeuge=["uhrzeit", "mails", "kalender"], module={"plan": "voll"}))
        self.assertTrue(set(w) <= set(rechte.FREMDEN_ERLAUBT))
        self.assertNotIn("mails", w)

    def test_termin_wunsch_steckt_nicht_im_plan(self):
        self.assertNotIn("termin_wunsch", module.werkzeuge_fuer(person(module={"plan": "voll"})))

    def test_uhrzeit_ist_immer_an(self):
        self.assertIn("uhrzeit", module.werkzeuge_fuer(person(werkzeuge=[])))

    def test_primitive_brauchen_das_modul(self):
        self.assertTrue(module.primitiv_erlaubt(person(), "garantie_antrag_aus_akte"))           # auto: erreicht das Primitiv
        self.assertFalse(module.primitiv_erlaubt(person(module={"garantie": "aus"}), "garantie_antrag_aus_akte"))
        self.assertTrue(module.primitiv_erlaubt(person(module={"garantie": "aus"}), "akte_erfassen"))   # generisch
        self.assertFalse(module.primitiv_erlaubt(person(), "medien_wunsch_stellen"))
        self.assertTrue(module.primitiv_erlaubt(person(module={"medien": True}), "medien_wunsch_stellen"))
        self.assertTrue(module.primitiv_erlaubt(person(), "nachricht_an_personen"))     # nicht an ein Modul gebunden

    def test_snap_wunsch_nur_mit_modul(self):
        self.assertFalse(module.oeffnet(person(), "snap_wunsch"))
        self.assertTrue(module.oeffnet(person(module={"snap_wuensche": True}), "snap_wunsch"))
        self.assertFalse(module.oeffnet(person(module={"snap_wuensche": True}), "etwas_anderes"))


class Protokoll(Basis):
    def test_append_only_mit_herkunft(self):
        module.protokollieren("p1", "flammen", False, True, "app")
        module.protokollieren("p1", "flammen", True, False, "friday")
        zeilen = (config.daten_ordner() / module.PROTOKOLL).read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(z)["von"] for z in zeilen], ["app", "friday"])
        self.assertEqual(json.loads(zeilen[0])["modul"], "flammen")


class Migration(Basis):
    def _alt(self, name, **kw):
        from kern.personen import register
        a = register.anlegen(name, **kw)
        d = register._lesen()
        d["personen"][a["id"]].pop("module")        # Bestandsperson, wie vor dem Umbau
        register._speichern(d)
        return a

    def test_migriert_nur_personen_ohne_module_und_leitet_plan_ab(self):
        from kern.personen import register
        a = self._alt("Alt", werkzeuge=["uhrzeit", "stundenplan"])
        self.assertEqual(module.migrieren(), 1)
        self.assertEqual(register.holen(a["id"])["module"]["plan"], "voll")
        self.assertEqual(module.migrieren(), 0)       # idempotent

    def test_aktiver_medien_skill_oeffnet_das_modul(self):
        from kern.personen import register
        a = self._alt("Alt")
        with mock.patch("kern.skills.zugriff.werkzeuge_fuer", return_value={"medien_wunsch_stellen"}):
            module.migrieren()
        self.assertTrue(register.holen(a["id"])["module"]["medien"])

    def test_migration_wird_protokolliert(self):
        from kern.personen import register
        self._alt("Alt", werkzeuge=["uhrzeit", "kalender"])
        module.migrieren()
        zeilen = (config.daten_ordner() / module.PROTOKOLL).read_text(encoding="utf-8").splitlines()
        self.assertTrue(zeilen)
        self.assertTrue(all(json.loads(z)["von"] == "migration" for z in zeilen))
