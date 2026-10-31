"""Routen des Gedanken-Reiters — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.denken import buch, lauf
from kern.main import app


class Routen(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "FRIDAY_HINTERGRUND": "aus"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app)
        self.b = buch.standard()

    def test_liste_leer(self):
        d = self.client.get("/api/gedanken").json()
        self.assertEqual((d["an"], d["gedanken"], d["runden"]), (True, [], []))

    def test_liste_zeigt_gedanken_und_runden(self):
        r = self.b.runde_beginnen()
        self.b.gedanke_festhalten(r, "frage", "Wann ist die Physikarbeit?")
        self.b.runde_beenden(r, "fertig", "m", "wissen", "")
        d = self.client.get("/api/gedanken").json()
        self.assertEqual(d["gedanken"][0]["text"], "Wann ist die Physikarbeit?")
        self.assertEqual((d["runden"][0]["gedanken"], d["runden"][0]["ergebnis"]), (1, "fertig"))

    def test_antwort_wird_nicht_zwischengespeichert(self):
        self.assertIn("no-store", self.client.get("/api/gedanken").headers.get("cache-control", ""))

    def test_ausschalten_und_einschalten(self):
        self.assertFalse(self.client.post("/api/gedanken/einstellung", json={"an": False}).json()["an"])
        self.assertFalse(self.client.get("/api/gedanken").json()["an"])
        self.assertTrue(self.client.post("/api/gedanken/einstellung", json={"an": True}).json()["an"])

    def test_einstellung_verlangt_einen_wahrheitswert(self):
        self.assertEqual(self.client.post("/api/gedanken/einstellung", json={"an": "vielleicht"}).status_code, 422)

    def test_jetzt_startet_eine_runde(self):
        with mock.patch.object(lauf, "jetzt_nachdenken", new=mock.AsyncMock(return_value={"ok": True})) as m:
            d = self.client.post("/api/gedanken/jetzt").json()
        self.assertTrue(d["gestartet"])
        m.assert_awaited_once()

    def test_jetzt_waehrend_eine_laeuft_startet_nichts(self):
        lauf._laeuft = True
        self.addCleanup(setattr, lauf, "_laeuft", False)
        with mock.patch.object(lauf, "jetzt_nachdenken", new=mock.AsyncMock()) as m:
            d = self.client.post("/api/gedanken/jetzt").json()
        self.assertFalse(d["gestartet"])
        m.assert_not_awaited()

    def test_liste_enthaelt_die_faeden(self):
        self.b.faden_anlegen("Mathe Abitur", "Wann beginnt die Abiturvorbereitung", quelle="eike")
        d = self.client.get("/api/gedanken").json()
        self.assertEqual([f["titel"] for f in d["faeden"]], ["Mathe Abitur"])
        self.assertEqual(d["faeden"][0]["status"], "offen")

    def test_faden_anlegen_durch_eike(self):
        d = self.client.post("/api/gedanken/faeden", json={"titel": "Konzert Dezember", "aufgabe": "Welche Karten gibt es"}).json()
        self.assertTrue(d["ok"])
        self.assertEqual(self.b.faden(d["id"])["quelle"], "eike")

    def test_faden_anlegen_zu_viele_und_unsinn(self):
        for n, t in enumerate(["Mathe Abitur", "Fahrrad Reparatur", "Konzert Dezember", "Führerschein Anmeldung", "Urlaub Sommer"]):
            self.assertTrue(self.client.post("/api/gedanken/faeden",
                                             json={"titel": t, "aufgabe": f"Aufgabe{n} zu {t} mit eigenen Worten"}).json()["ok"])
        d = self.client.post("/api/gedanken/faeden", json={"titel": "Praktikum Bewerbung", "aufgabe": "Welche Unterlagen fehlen"}).json()
        self.assertFalse(d["ok"])
        self.assertIn("offen", d["grund"])
        self.assertEqual(self.client.post("/api/gedanken/faeden", json={"titel": "", "aufgabe": "x"}).status_code, 422)
        self.assertEqual(self.client.post("/api/gedanken/faeden", json={"titel": "x" * 81, "aufgabe": "y"}).status_code, 422)
        self.assertEqual(self.client.post("/api/gedanken/faeden", json={"titel": "x", "aufgabe": "y" * 401}).status_code, 422)

    def test_status_aendern(self):
        f = self.b.faden_anlegen("Mathe Abitur", "Wann beginnt die Abiturvorbereitung", quelle="eike")["id"]
        self.assertTrue(self.client.post(f"/api/gedanken/faeden/{f}", json={"status": "ruht"}).json()["ok"])
        self.assertEqual(self.b.faden(f)["status"], "ruht")
        self.assertTrue(self.client.post(f"/api/gedanken/faeden/{f}", json={"status": "offen"}).json()["ok"])
        self.assertTrue(self.client.post(f"/api/gedanken/faeden/{f}", json={"status": "erledigt"}).json()["ok"])

    def test_status_unsinn_und_unbekannter_faden(self):
        f = self.b.faden_anlegen("Mathe Abitur", "Wann beginnt die Abiturvorbereitung", quelle="eike")["id"]
        self.assertEqual(self.client.post(f"/api/gedanken/faeden/{f}", json={"status": "geloescht"}).status_code, 422)
        self.assertFalse(self.client.post("/api/gedanken/faeden/9999", json={"status": "ruht"}).json()["ok"])
        self.assertEqual(self.b.faden(f)["status"], "offen")


if __name__ == "__main__":
    unittest.main()
