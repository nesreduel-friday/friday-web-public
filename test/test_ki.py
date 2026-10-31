"""Gemini-Anfragen der Schule: 503 wird wiederholt, Fehler bekommen eine verständliche Erklärung."""
import asyncio
import unittest
from unittest import mock

from kern.schule import ki


class Antwort:
    def __init__(self, code, daten=None):
        self.status_code = code
        self._daten = daten or {}
        self.text = str(self._daten)

    def json(self):
        return self._daten


OK = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


def client(*antworten):
    c = mock.Mock()
    c.post = mock.AsyncMock(side_effect=[Antwort(*a) for a in antworten])
    return c


class Wiederholen(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(ki, "PAUSEN_503", (0, 0))
        p.start()
        self.addCleanup(p.stop)

    def senden(self, c):
        return asyncio.run(ki.senden(c, "m", "schl", {}))

    def test_503_wird_wiederholt_und_dann_gelingt_es(self):
        c = client((503,), (503,), (200, OK))
        self.assertEqual(self.senden(c)["content"]["parts"][0]["text"], "ok")
        self.assertEqual(c.post.await_count, 3)

    def test_503_bleibt_ein_fehler_nach_den_wiederholungen(self):
        c = client((503,), (503,), (503,))
        with self.assertRaises(ki.KiFehler) as ctx:
            self.senden(c)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(c.post.await_count, 3)

    def test_andere_fehler_werden_nicht_wiederholt(self):
        c = client((429, {"error": "quota"}))
        with self.assertRaises(ki.KiFehler):
            self.senden(c)
        self.assertEqual(c.post.await_count, 1)


class Verstaendlich(unittest.TestCase):
    def test_ueberlastet(self):
        t = ki.verstaendlich(ki.KiFehler("Nicht bearbeitet (gemini-3.5-flash: 503)"))
        self.assertIn("überlastet", t)
        self.assertIn("503", t)

    def test_kontingent(self):
        t = ki.verstaendlich(ki.KiFehler("Gemini hat nicht geantwortet (gemini-3.5-flash: 429)"))
        self.assertIn("Kontingent", t)
        self.assertIn("429", t)

    def test_unbekannter_fehler_bleibt_wie_er_war(self):
        self.assertEqual(ki.verstaendlich(ValueError("kaputt")), "ValueError: kaputt")
        self.assertEqual(ki.verstaendlich(ki.KiFehler("x: 404")), "KiFehler: x: 404")


if __name__ == "__main__":
    unittest.main()
