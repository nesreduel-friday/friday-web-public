"""Werkbank-Karte (Extras → hphome): nur textContent, keine Inline-Skripte, eingebunden, kein toter Schalter."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class WerkbankWeb(unittest.TestCase):
    def setUp(self):
        self.js = (WEB / "werkbank.js").read_text(encoding="utf-8")
        self.html = (WEB / "index.html").read_text(encoding="utf-8")

    def test_kein_html_aus_fremdtext(self):
        for verboten in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(verboten, self.js)
        self.assertIn("textContent", self.js)

    def test_eingebunden_ohne_inline_skript(self):
        self.assertIn('<script src="/werkbank.js"></script>', self.html)
        self.assertIn('id="hphome-werkbank"', self.html)
        for tag in re.findall(r"<script\b[^>]*>", self.html):
            self.assertIn("src=", tag)
        self.assertNotRegex(self.html, r"<script(?![^>]*\bsrc=)[^>]*>")

    def test_nur_eigene_pfade_und_keine_alten_schalter(self):
        self.assertNotIn("geschuetzte_pfade_ok", self.js)
        for url in re.findall(r"""['"`](/api/[^'"`]*|https?://[^'"`]*)""", self.js):
            self.assertTrue(url.startswith("/api/werkbank"), url)

    def test_uebernehmen_mit_bestaetigung_und_zurueckgerollt(self):
        self.assertIn("zurueckgerollt", self.js)
        self.assertIn("Zurückgerollt – Branch nicht noch einmal übernehmbar", self.js)
        self.assertIn("/uebernehmen", self.js)
        self.assertIn("Wirklich übernehmen", self.js)

    def test_zweiter_tipp_hat_sperre_und_ablauf(self):
        self.assertRegex(self.js, r"FREI_MS = 1500;")
        self.assertRegex(self.js, r"ABLAUF_MS = 10000;")
        self.assertIn("setTimeout(() => { z.frei = true", self.js)
        self.assertIn("b.disabled = true", self.js)

    def test_aktion_setzt_beschaeftigt_immer_zurueck(self):
        m = re.search(r"async function aktion\(.*?\n  }\n", self.js, re.S)
        self.assertTrue(m)
        for stueck in ("try {", "catch (_)", "finally {", "beschaeftigt = false"):
            self.assertIn(stueck, m.group(0))

    def test_verhalten_im_dom_stub(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node nicht vorhanden")
        r = subprocess.run([node, str(WEB.parent / "test" / "js" / "werkbank_verhalten.js"), str(WEB / "werkbank.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
