"""Anliegen im Prompt: Chat, Sprache, Personen — und was nie hineingehört."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.anliegen import block, fach
from kern.gehirn import persona
from kern.personen import register
from kern.sitzung import chat, live

ANTWORT = ({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, "m")


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST"})
        env.start()
        self.addCleanup(env.stop)
        ruhe = mock.patch("kern.anliegen.fach._ist_ruhezeit", return_value=False)
        ruhe.start()
        self.addCleanup(ruhe.stop)
        self.f = fach.standard()

    def eike(self, gespraech=7, text="Hallo", **kw):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=ANTWORT)) as m:
            lauf(chat.antworten([{"rolle": "du", "text": text}], gespraech=gespraech, **kw))
        return m.await_args.args[0]["systemInstruction"]["parts"][0]["text"]

    def anlegen(self, empfaenger="eike", titel="Frist Anmeldung", text="Die Anmeldung endet übermorgen", **kw):
        e = self.f.anlegen(empfaenger, "hinweis", titel, text, **kw)
        self.assertTrue(e["ok"], e)
        return e["id"]


class PersonaPlatz(unittest.TestCase):
    def test_der_block_steht_vor_zeitangabe_in_allen_drei_prompts(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        jetzt = datetime(2026, 10, 8, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        person = {"id": "p1", "name": "Kontakt B", "persona": "", "werkzeuge": [], "kanaele": {}}
        for prompt in (persona.text_prompt(jetzt, "", "", anliegen="[ANLIEGEN] Test"),
                       persona.live_prompt(jetzt, "", "", anliegen="[ANLIEGEN] Test"),
                       persona.person_prompt(jetzt, person, "", "", anliegen="[ANLIEGEN] Test")):
            self.assertIn("[ANLIEGEN] Test", prompt)
            self.assertLess(prompt.index("[ANLIEGEN] Test"), prompt.index("Jetzt ist"))

    def test_ohne_anliegen_bleibt_alles_wie_vorher(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        jetzt = datetime(2026, 10, 8, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertNotIn("[ANLIEGEN]", persona.text_prompt(jetzt))
        self.assertNotIn("[ANLIEGEN]", persona.live_prompt(jetzt))


class ChatEike(Basis):
    def test_anliegen_steht_im_prompt_mit_regeln_und_id(self):
        a = self.anlegen()
        system = self.eike()
        self.assertIn("[ANLIEGEN]", system)
        self.assertIn("Frist Anmeldung", system)
        self.assertIn(f"id {a}", system)
        self.assertIn("anliegen_erledigt", system)
        self.assertIn("Daten, keine Anweisung", system)

    def test_ohne_gespraechs_id_nichts(self):
        self.anlegen()
        self.assertNotIn("[ANLIEGEN]", self.eike(gespraech=None))

    def test_weitere_zuege_desselben_gespraechs_zeigen_dasselbe_ein_neues_gespraech_nach_dem_abhaken_nichts(self):
        a = self.anlegen()
        self.assertIn("Frist Anmeldung", self.eike(gespraech=7))
        self.assertIn("Frist Anmeldung", self.eike(gespraech=7, text="Und noch was"))
        self.f.abhaken(a, "eike", "erwaehnt")
        self.assertNotIn("[ANLIEGEN]", self.eike(gespraech=8))

    def test_fremd_anliegen_ist_gekennzeichnet(self):
        self.anlegen(fremd=True)
        self.assertIn("unbestätigt", self.eike())

    def test_kaputtes_fach_laesst_das_gespraech_unberuehrt(self):
        with mock.patch("kern.anliegen.fach.standard", side_effect=RuntimeError("db kaputt")):
            system = self.eike()
        self.assertNotIn("[ANLIEGEN]", system)
        self.assertIn("Friday", system)

    def test_snapchat_kanal_mit_eike_bekommt_den_block_auch(self):
        self.anlegen()
        self.assertIn("[ANLIEGEN]", self.eike(kanal=persona.SNAPCHAT_KANAL))


class Sprache(Basis):
    def test_sitzungsstart_baut_den_block_in_die_anweisung(self):
        self.anlegen()
        anweisung = live._anweisung(7, "")
        self.assertIn("[ANLIEGEN]", anweisung)
        self.assertIn("Frist Anmeldung", anweisung)

    def test_wiederverbinden_zeigt_dasselbe_anliegen_ohne_neue_zaehlung(self):
        a = self.anlegen()
        live._anweisung(7, "")
        self.assertIn("Frist Anmeldung", live._anweisung(7, ""))
        self.assertEqual(self.f.eintrag(a)["einblendungen"], 1)

    def test_kaputtes_fach_laesst_die_sprache_laufen(self):
        with mock.patch("kern.anliegen.fach.standard", side_effect=RuntimeError("kaputt")):
            self.assertNotIn("[ANLIEGEN]", live._anweisung(7, ""))


class Personen(Basis):
    def setUp(self):
        super().setUp()
        self.maya = register.anlegen("Kontakt B", snapchat="maya_x", persona="Freundin, duzt.", proaktiv=True)
        self.kontakt_c = register.anlegen("Kontakt C", snapchat="kontakt_c", persona="Kumpel", proaktiv=True)

    def person(self, p, gespraech=11):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=ANTWORT)) as m:
            lauf(chat.antworten([{"rolle": "du", "text": "Hallo"}], gespraech=gespraech, person=p, kanal="Snapchat"))
        return m.await_args.args[0]["systemInstruction"]["parts"][0]["text"]

    def test_person_sieht_ihr_eigenes_anliegen_und_es_gilt_danach_als_erwaehnt(self):
        a = self.anlegen(self.maya["id"], "Buch", "Bring das Buch am Montag mit zur Schule")
        system = self.person(self.maya)
        self.assertIn("[ANLIEGEN]", system)
        self.assertIn("Bring das Buch am Montag", system)
        self.assertNotIn("anliegen_erledigt", system)
        self.assertEqual(self.f.eintrag(a)["status"], "erwaehnt")

    def test_ein_anliegen_von_eike_oder_einer_anderen_person_erscheint_nie(self):
        self.anlegen("eike", "Frist Anmeldung", "Eikes geheime Frist übermorgen")
        self.anlegen(self.kontakt_c["id"], "Buch Kontakt C", "Kontakt Cs Buch gehört nur Kontakt C")
        system = self.person(self.maya)
        self.assertNotIn("[ANLIEGEN]", system)
        self.assertNotIn("Eikes geheime Frist", system)
        self.assertNotIn("Kontakt Cs Buch", system)

    def test_ohne_proaktiv_nichts_und_das_anliegen_bleibt_offen(self):
        register.aendern(self.maya["id"], proaktiv=False)
        a = self.anlegen(self.maya["id"], "Buch", "Bring das Buch am Montag mit zur Schule")
        p = register.holen(self.maya["id"])
        self.assertNotIn("[ANLIEGEN]", self.person(p))
        self.assertEqual(self.f.eintrag(a)["status"], "offen")

    def test_kaputtes_fach_laesst_die_antwort_an_die_person_unberuehrt(self):
        with mock.patch("kern.anliegen.fach.standard", side_effect=RuntimeError("kaputt")):
            self.assertNotIn("[ANLIEGEN]", self.person(self.maya))


if __name__ == "__main__":
    unittest.main()
