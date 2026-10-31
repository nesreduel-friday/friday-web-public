import os
import shutil
import tempfile
import unittest
from unittest import mock

from kern.verbrauch import tokens


class Tokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        u = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        u.start()
        self.addCleanup(u.stop)

    def antwort(self, e, a, d=0):
        return {"usageMetadata": {"promptTokenCount": e, "candidatesTokenCount": a, "thoughtsTokenCount": d}}

    def test_addiert_je_modell_und_bereich(self):
        tokens.zaehlen("m1", self.antwort(100, 10, 5), "chat")
        tokens.zaehlen("m1", self.antwort(50, 5), "chat")
        tokens.zaehlen("m2", self.antwort(7, 1), "denken")
        u = tokens.uebersicht()
        self.assertEqual(u["summe"]["aufrufe"], 3)
        self.assertEqual(u["summe"]["eingabe"], 157)
        chat = [e for e in u["eintraege"] if e["bereich"] == "chat"][0]
        self.assertEqual((chat["aufrufe"], chat["eingabe"], chat["ausgabe"], chat["denken"]), (2, 150, 15, 5))

    def test_ohne_usage_wird_nichts_gezaehlt(self):
        tokens.zaehlen("m1", {}, "chat")
        tokens.zaehlen("m1", None, "chat")
        self.assertEqual(tokens.uebersicht()["summe"]["aufrufe"], 0)

    def test_kaputte_antwort_wirft_nie(self):
        tokens.zaehlen("m1", {"usageMetadata": {"promptTokenCount": "x"}}, "chat")
        tokens.zaehlen("m1", "kein dict", "chat")

    def test_text(self):
        self.assertIn("Noch keine", tokens.als_text())
        tokens.zaehlen("m1", self.antwort(100, 10), "chat")
        self.assertIn("chat / m1", tokens.als_text())

    def test_bereich_des_aufrufers(self):
        self.assertEqual(tokens.bereich_des_aufrufers(1), "test_verbrauch_tokens")


class Anbindung(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        u = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        u.start()
        self.addCleanup(u.stop)

    def test_werkzeug_und_route(self):
        from fastapi.testclient import TestClient
        from kern.main import app
        from kern.werkzeuge import verbrauch
        tokens.zaehlen("m1", {"usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 2}}, "chat")
        self.assertIn("chat / m1", verbrauch.token_verbrauch("kaputt"))
        r = TestClient(app).get("/api/tokens?tage=3").json()
        self.assertEqual(r["summe"]["eingabe"], 40)

    def test_chat_anfrage_zaehlt_mit_bereich_des_aufrufers(self):
        import asyncio
        from kern.sitzung import chat

        class Antwort:
            status_code = 200
            def json(self):
                return {"usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 3}}

        async def post(*a, **k):
            return Antwort()
        with mock.patch.object(chat, "_post", post):
            asyncio.run(chat._gemini_anfrage({"contents": []}, schluessel_liste=[""]))
        e = tokens.uebersicht()["eintraege"][0]
        self.assertEqual((e["aufrufe"], e["eingabe"], e["ausgabe"]), (1, 9, 3))
