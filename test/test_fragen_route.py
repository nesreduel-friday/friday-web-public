"""Routen /api/fragen und der Fragen-Block im Reiter Gedanken — ohne Netz."""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from kern.fragen import fach
from kern.main import app

WEB = Path(__file__).resolve().parent.parent / "web"


class Routen(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "FRIDAY_HINTERGRUND": "aus"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app)
        self.f = fach.standard()
        self.fid = self.f.stellen("chat", "Briefing um 7 oder 8?", "Beides passt.", ["07:00", "08:00"])["id"]

    def antwort(self, fid=None, **daten):
        return self.client.post(f"/api/fragen/{fid or self.fid}/antwort", json=daten)

    def test_liste(self):
        d = self.client.get("/api/fragen").json()
        self.assertEqual([f["id"] for f in d["offen"]], [self.fid])
        self.assertEqual(d["beantwortet"], [])
        self.assertEqual(d["offen"][0]["optionen"], ["07:00", "08:00"])

    def test_antwort_per_option(self):
        r = self.antwort(option=2)
        self.assertEqual((r.status_code, r.json()["antwort"]), (200, "08:00"))
        d = self.client.get("/api/fragen").json()
        self.assertEqual((d["offen"], d["beantwortet"][0]["antwort"]), ([], "08:00"))

    def test_antwort_frei(self):
        r = self.antwort(text="  Lieber um halb acht ")
        self.assertEqual((r.status_code, r.json()["art"]), (200, "frei"))

    def test_beides_oder_nichts_ist_422(self):
        self.assertEqual(self.antwort(option=1, text="auch").status_code, 422)
        self.assertEqual(self.antwort().status_code, 422)
        self.assertEqual(self.antwort(option=3).status_code, 422)
        self.assertEqual(self.f.eintrag(self.fid)["status"], "offen")

    def test_zweite_antwort_ist_409_und_unbekannt_404(self):
        self.assertEqual(self.antwort(option=1).status_code, 200)
        self.assertEqual(self.antwort(option=2).status_code, 409)
        self.assertEqual(self.f.eintrag(self.fid)["antwort"], "07:00")
        self.assertEqual(self.antwort(fid=999, option=1).status_code, 404)

    def test_fremder_origin_wird_abgelehnt(self):
        r = self.client.post(f"/api/fragen/{self.fid}/antwort", json={"option": 1}, headers={"Origin": "http://boese.example"})
        self.assertGreaterEqual(r.status_code, 400)
        self.assertEqual(self.f.eintrag(self.fid)["status"], "offen")


class Seite(unittest.TestCase):
    def setUp(self):
        self.js = (WEB / "gedaechtnis.js").read_text(encoding="utf-8")
        self.html = (WEB / "index.html").read_text(encoding="utf-8")

    def test_block_ist_da_und_nur_textcontent(self):
        self.assertIn('id="gd-fragen"', self.html)
        i = self.js.index("function fragenZeigen")
        block = self.js[i:self.js.index("function gedankenZeigen")]
        for verboten in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
            self.assertNotIn(verboten, block)

    def test_optionen_knoepfe_freitext_und_kein_doppeltes_absenden(self):
        block = self.js[self.js.index("function fragenZeigen"):self.js.index("function gedankenZeigen")]
        self.assertIn("/api/fragen/${f.id}/antwort", block)
        self.assertIn("option: i + 1", block)
        self.assertIn("Eigene Antwort", block)
        self.assertIn("disabled = true", block)

    def test_formular_wird_nicht_neu_gezeichnet_waehrend_getippt_wird(self):
        block = self.js[self.js.index("function fragenZeigen"):self.js.index("function gedankenZeigen")]
        self.assertIn("dataset.sig", block)
        self.assertIn("activeElement", block)

    def test_nur_eigene_pfade(self):
        block = self.js[self.js.index("function fragenZeigen"):self.js.index("function gedankenZeigen")]
        for url in re.findall(r"""['"`](/api/[^'"`]*|https?://[^'"`]*)""", block):
            self.assertTrue(url.startswith("/api/fragen"), url)


if __name__ == "__main__":
    unittest.main()
