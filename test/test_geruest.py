"""Das Gerüst: Seiten, Zustand, und die eine harte Regel.

Laufen lassen:
    docker compose run --rm friday python -m unittest discover -s test -t . -v
"""

import importlib
import json
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern import config
from kern.main import app


class Geruest(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_health_meldet_zustand(self):
        r = self.c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["phase"], config.PHASE)
        self.assertIn("textmodell", d)
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_schluessel_verlaesst_nie_den_server(self):
        geheim = "AIzaGEHEIM_darf_nie_im_browser_landen_123"
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": geheim}):
            r = self.c.get("/api/health")
        self.assertTrue(r.json()["geminiSchluessel"])
        self.assertNotIn(geheim, r.text)
        self.assertNotIn("AIza", r.text)

    def test_ohne_schluessel_sagt_health_das_ehrlich(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.assertFalse(self.c.get("/api/health").json()["geminiSchluessel"])

    def test_startseite_mit_allen_seiten_und_bedienelementen(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        # Seit dem 24.09.2026 ist die Übersicht die Startseite. Der Kreis im Kopf ist der
        # Gesprächsknopf, Transkript und Eingabe wohnen im Chat (siehe test_seite.Aufbau).
        for id_ in ["uebersicht", "chat", "gedaechtnis", "freigaben", "einstellungen",
                    "kreis", "halten", "ptt", "oeffnen-hier", "karten",
                    "chat-verlauf", "chat-form", "chat-text", "chat-bild-datei",
                    "u-hier", "u-sitzungen", "studio", "studio-liste"]:
            self.assertIn(f'id="{id_}"', r.text, id_)
        # Textfeld, keine einzeilige Eingabe: eingefügte Absätze behalten Zeilenumbrüche.
        self.assertIn('<textarea id="chat-text"', r.text)

    def test_skripte_erreichbar(self):
        for pfad in ["/app.js", "/sprechen.js", "/chat.js", "/pcm-processor.js", "/sw.js",
                     "/umgebung.js", "/studio.js", "/uebersicht.js"]:
            r = self.c.get(pfad)
            self.assertEqual(r.status_code, 200, pfad)
            self.assertIn("javascript", r.headers["content-type"], pfad)

    def test_manifest_mit_richtigem_typ_und_erreichbaren_symbolen(self):
        r = self.c.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("application/manifest+json"))
        for icon in json.loads(r.text)["icons"]:
            ri = self.c.get(icon["src"])
            self.assertEqual(ri.status_code, 200, icon["src"])
            self.assertTrue(ri.content.startswith(b"\x89PNG"))

    def test_keine_oeffentlichen_api_docs(self):
        self.assertEqual(self.c.get("/docs").status_code, 404)
        self.assertEqual(self.c.get("/openapi.json").status_code, 404)

    def test_alle_bausteine_da_und_beschrieben(self):
        for name in ["sitzung", "gehirn", "gedaechtnis", "werkzeuge", "dienste", "planer", "protokoll"]:
            modul = importlib.import_module(f"kern.{name}")
            self.assertTrue((modul.__doc__ or "").strip(), f"kern.{name} ohne Zweck")
            self.assertIn("Phase", modul.__doc__, f"kern.{name} ohne Phase")


if __name__ == "__main__":
    unittest.main()
