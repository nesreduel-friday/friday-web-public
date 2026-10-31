"""Module wirken auf die Werkzeugrechte einer Person (katalog._person_darf) — ohne Netz."""
import os
import tempfile
import unittest
from unittest import mock

from kern.personen import register
from kern.werkzeuge import katalog


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.p = register.anlegen("Kontakt B")

    def tearDown(self):
        self.env.stop()

    def person(self):
        return register.holen(self.p["id"])


class Rechte(Basis):
    def test_plan_stufe_oeffnet_kalender(self):
        self.assertFalse(katalog._person_darf(self.person(), "kalender"))
        register.aendern(self.p["id"], module={"plan": "voll"})
        self.assertTrue(katalog._person_darf(self.person(), "kalender"))
        register.aendern(self.p["id"], module={"plan": "frei"})
        self.assertFalse(katalog._person_darf(self.person(), "kalender"))
        self.assertTrue(katalog._person_darf(self.person(), "kalender_frei"))

    def test_manipuliertes_werkzeuge_feld_oeffnet_den_plan_nicht(self):
        register.aendern(self.p["id"], module={"plan": "aus"})
        manipuliert = {**self.person(), "werkzeuge": ["uhrzeit", "kalender"]}
        self.assertFalse(katalog._person_darf(manipuliert, "kalender"))

    def test_snap_wunsch_nur_mit_modul_und_nie_fuer_eike(self):
        self.assertFalse(katalog._person_darf(self.person(), "snap_wunsch"))
        register.aendern(self.p["id"], module={"snap_wuensche": True})
        self.assertTrue(katalog._person_darf(self.person(), "snap_wunsch"))
        self.assertFalse(katalog._person_darf(None, "snap_wunsch"))

    def test_snap_wunsch_prueft_das_register_nicht_das_uebergebene_dict(self):
        gefaelscht = {**self.person(), "module": {"snap_wuensche": True}}
        self.assertFalse(katalog._person_darf(gefaelscht, "snap_wunsch"))

    def test_skill_primitiv_braucht_skill_und_modul(self):
        register.aendern(self.p["id"], module={"medien": False})
        with mock.patch("kern.werkzeuge.katalog.skillzugriff.erlaubt", return_value=True), \
                mock.patch("kern.werkzeuge.katalog.skillprimitive.PERSONENFAEHIG", {"medien_wunsch_stellen": {}}):
            self.assertFalse(katalog._person_darf(self.person(), "medien_wunsch_stellen"))
            register.aendern(self.p["id"], module={"medien": True})
            self.assertTrue(katalog._person_darf(self.person(), "medien_wunsch_stellen"))

    def test_gemini_werkzeugliste_folgt_den_modulen(self):
        with mock.patch("kern.werkzeuge.katalog.konnektoren.benutzbar", return_value=True):
            namen = {e["name"] for t in katalog.gemini_tools(self.person()) for e in t["functionDeclarations"]}
            self.assertNotIn("kalender", namen)
            self.assertIn("uhrzeit", namen)
            register.aendern(self.p["id"], module={"plan": "voll"})
            namen = {e["name"] for t in katalog.gemini_tools(self.person()) for e in t["functionDeclarations"]}
            self.assertIn("kalender", namen)


class SkillBlock(Basis):
    """Der Skill-Block im Prompt nennt nur Primitive, die die Hebel auch erlauben (Review 14.10.2026)."""

    def skill(self, werkzeuge):
        return {"name": "wunsch", "zweck": "Wünsche annehmen", "ausloeser": "sie wünscht sich etwas", "anweisung": "frag nach",
                "werkzeuge": werkzeuge, "optionen": {}, "felder": []}

    def test_gesperrtes_primitiv_steht_nicht_im_block(self):
        from kern.skills import zugriff
        register.aendern(self.p["id"], module={"medien": False})
        block = zugriff.person_block(self.person(), [self.skill(["medien_wunsch_stellen"])])
        self.assertNotIn("medien_wunsch_stellen", block)
        self.assertNotIn("wunsch —", block)               # Skill, der nichts mehr kann, entfällt ganz
        register.aendern(self.p["id"], module={"medien": True})
        self.assertIn("medien_wunsch_stellen", zugriff.person_block(self.person(), [self.skill(["medien_wunsch_stellen"])]))

    def test_skill_mit_mehreren_werkzeugen_behaelt_die_erlaubten(self):
        from kern.skills import zugriff
        register.aendern(self.p["id"], module={"garantie": "aus"})
        block = zugriff.person_block(self.person(), [self.skill(["akte_erfassen", "garantie_antrag_aus_akte"])])
        self.assertIn("akte_erfassen", block)
        self.assertNotIn("garantie_antrag_aus_akte", block)
