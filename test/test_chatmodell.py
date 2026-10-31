"""Schalter Gemini/DeepSeek im Chat (29.10.2026): nur Eikes Chat, nie mit Datei, Rückfall auf Gemini."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.denken import deepseek
from kern.sitzung import chat, chatmodell

TEXT = {"contents": [{"role": "user", "parts": [{"text": "Hallo"}]}]}
DATEI = {"contents": [{"role": "user", "parts": [{"text": "Was ist das?"}, {"inlineData": {"mimeType": "image/png", "data": "AA"}}]}]}


class Chatmodell(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.gemini_gerufen = []

        async def gemini(inhalt, bevorzugt=None):
            self.gemini_gerufen.append(bevorzugt)
            return {"g": 1}, "gemini-x"
        self.gemini = gemini

    def lauf(self, inhalt=TEXT, person=None, bevorzugt="gemini-x"):
        return asyncio.run(chatmodell.anfrage(self.gemini, inhalt, bevorzugt, person))

    def test_standard_ist_gemini(self):
        self.assertEqual(chatmodell.backend(), "gemini")
        self.assertEqual(self.lauf()[1], "gemini-x")

    def test_unbekanntes_backend_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            chatmodell.einstellen("gpt")
        self.assertEqual(chatmodell.backend(), "gemini")

    def test_deepseek_antwortet_wenn_gewaehlt(self):
        chatmodell.einstellen("deepseek")
        ds = mock.AsyncMock(return_value=({"d": 1}, "deepseek-flash"))
        with mock.patch.object(deepseek, "anfrage", ds):
            self.assertEqual(self.lauf(), ({"d": 1}, "deepseek-flash"))
        self.assertEqual(self.gemini_gerufen, [])

    def test_mit_datei_oder_person_bleibt_gemini(self):
        chatmodell.einstellen("deepseek")
        ds = mock.AsyncMock(return_value=({"d": 1}, "deepseek-flash"))
        with mock.patch.object(deepseek, "anfrage", ds):
            self.lauf(DATEI)
            self.lauf(TEXT, person={"id": "p1"})
        ds.assert_not_called()
        self.assertEqual(len(self.gemini_gerufen), 2)

    def test_ausfall_faellt_auf_gemini_zurueck_ohne_deepseek_modellnamen(self):
        chatmodell.einstellen("deepseek")
        ds = mock.AsyncMock(side_effect=chat.ChatFehler("Kein Schlüssel"))
        with mock.patch.object(deepseek, "anfrage", ds):
            antwort, modell = self.lauf(bevorzugt="deepseek-flash")
        self.assertEqual(modell, "gemini-x")
        self.assertEqual(self.gemini_gerufen, [None])


if __name__ == "__main__":
    unittest.main()
