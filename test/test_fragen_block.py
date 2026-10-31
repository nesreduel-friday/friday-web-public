"""Prompt-Block [FRAGEN]: offene und kürzlich beantwortete Fragen — nie ein Gesprächsbruch — ohne Netz."""

import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.denken import buch, kontext
from kern.fragen import block, fach

JETZT = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))


class Block(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        uhr = mock.patch("kern.config.jetzt", return_value=JETZT)
        uhr.start()
        self.addCleanup(uhr.stop)
        self.f = fach.standard()

    def test_block_leer_ohne_fragen(self):
        self.assertEqual(block.bauen_sicher(), "")

    def test_block_zeigt_offene_und_beantwortete(self):
        a = self.f.stellen("chat", "Briefing um 7 oder 8?", "Beides passt.", ["07:00", "08:00"])["id"]
        b = self.f.stellen("denkrunde", "Wecker laut oder leise?", "Text", ["laut", "leise"])["id"]
        self.f.beantworten(b, option=2)
        t = block.bauen_sicher()
        self.assertIn("[FRAGEN]", t)
        self.assertIn(f"#{a} Briefing um 7 oder 8? — 1 = 07:00, 2 = 08:00", t)
        self.assertIn(f"#{b} Wecker laut oder leise? — Antwort: leise", t)
        self.assertIn("frage_beantworten", t)
        self.assertIn("NICHT selbst", t)           # Regel: nach einer offenen Frage nicht selbst entscheiden

    def test_verfallene_stehen_in_der_runde_mit_der_erlaubnis_weiterzumachen(self):
        a = self.f.stellen("chat", "Briefing um 7 oder 8?", "Text", ["07:00"], gueltig_stunden=1)["id"]
        from datetime import timedelta
        with mock.patch("kern.config.jetzt", return_value=JETZT + timedelta(hours=2)):
            t = block.antworten_fuer_runde()
        self.assertIn("Ohne Antwort verfallen", t)
        self.assertIn(f"#{a}", t)

    def test_faden_mit_frischer_antwort_wird_fokus(self):
        b = buch.standard()
        eike = b.faden_anlegen("Mathe Abitur", "Wann beginnt die Vorbereitung", quelle="eike")["id"]
        fr = b.faden_anlegen("Wecker Lautstärke", "Wie laut soll der Wecker sein", quelle="friday")["id"]
        self.assertEqual(b.faden_fokus()["id"], eike)
        from datetime import timedelta
        from kern.denken import runde
        q = self.f.stellen("denkrunde", "Wecker laut oder leise?", "Text", ["laut", "leise"], bezug=f"faden:{fr}")["id"]
        with mock.patch("kern.config.jetzt", return_value=JETZT + timedelta(hours=1)):
            self.f.beantworten(q, option=2)
            self.assertEqual(runde.fokus_waehlen(b)["id"], fr)
        with mock.patch("kern.config.jetzt", return_value=JETZT + timedelta(hours=2)):
            b.faden_weiter(fr, "Leise gewählt")
            self.assertEqual(runde.fokus_waehlen(b)["id"], eike)       # Antwort ist eingearbeitet, nicht noch einmal Fokus

    def test_fremd_frage_ist_markiert(self):
        self.f.stellen("denkrunde", "Briefing um 7 oder 8?", "Text", ["a"], fremd=True)
        self.assertIn("gelesen und unbestätigt", block.bauen_sicher())

    def test_fehler_im_fach_bricht_kein_gespraech(self):
        with mock.patch("kern.fragen.fach.standard", side_effect=RuntimeError("weg")):
            self.assertEqual(block.bauen_sicher(), "")

    def test_denkrunde_sieht_antworten_als_eikes_text_nicht_als_fremdtext(self):
        a = self.f.stellen("chat", "Briefing um 7 oder 8?", "Text", ["07:00", "08:00"], bezug="faden:3")["id"]
        self.f.beantworten(a, option=2)
        b = buch.standard()
        k = kontext.baue(b)
        self.assertIn("Antworten von Eike", k)
        self.assertIn("08:00", k)
        self.assertIn("faden:3", k)
        c = self.f.stellen("chat", "Wecker laut oder leise?", "Text", ["laut"])["id"]
        self.assertIn("Offene Fragen an Eike", kontext.baue(b))
        self.assertIn(f"#{c}", kontext.baue(b))
        # auch die fremdfreie OpenCode-Runde bekommt Eikes eigene Antworten, aber keine Fragen aus Fremdtext
        self.f.stellen("denkrunde", "Konzert Karten kaufen?", "Aus Mail", ["ja"], fremd=True)
        k2 = kontext.baue(b, ohne_fremd=True)
        self.assertIn("08:00", k2)
        self.assertNotIn("Konzert", k2)


class Einbau(unittest.TestCase):
    """Chat, Sprache und Personen: Eike sieht den Block, eine Person nie."""

    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST"})
        env.start()
        self.addCleanup(env.stop)
        self.f = fach.standard()
        self.f.stellen("chat", "Briefing um 7 oder 8?", "Beides passt.", ["07:00", "08:00"])

    def prompt(self, **kw):
        import asyncio
        from kern.sitzung import chat
        antwort = ({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, "m")
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=antwort)) as m:
            asyncio.run(chat.antworten([{"rolle": "du", "text": "Hallo"}], gespraech=7, **kw))
        return m.await_args.args[0]["systemInstruction"]["parts"][0]["text"]

    def test_chat_mit_eike_zeigt_den_block(self):
        self.assertIn("Briefing um 7 oder 8?", self.prompt())

    def test_person_sieht_den_block_nie(self):
        from kern.personen import register
        maya = register.anlegen("Kontakt B", snapchat="maya_x", persona="Freundin", proaktiv=True)
        self.assertNotIn("[FRAGEN]", self.prompt(person=maya, kanal="Snapchat"))

    def test_sprache_zeigt_den_block(self):
        from kern.sitzung import live
        self.assertIn("Briefing um 7 oder 8?", live._anweisung(7, ""))

    def test_kaputtes_fach_laesst_chat_und_sprache_laufen(self):
        from kern.sitzung import live
        with mock.patch("kern.fragen.fach.standard", side_effect=RuntimeError("weg")):
            self.assertNotIn("[FRAGEN]", self.prompt())
            self.assertNotIn("[FRAGEN]", live._anweisung(7, ""))


if __name__ == "__main__":
    unittest.main()
