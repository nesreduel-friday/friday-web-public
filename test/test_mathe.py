"""Formeln im Chat: eigener kleiner LaTeX-Zeichner (web/mathe.js), nie HTML aus fremdem Text."""
import shutil
import subprocess
import unittest
from pathlib import Path

from kern.gehirn import persona

WEB = Path(__file__).resolve().parent.parent / "web"


class MatheImChat(unittest.TestCase):
    def setUp(self):
        self.mathe = (WEB / "mathe.js").read_text(encoding="utf-8")
        self.app = (WEB / "app.js").read_text(encoding="utf-8")
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_skript_ist_eingebunden_und_vor_app_js(self):
        self.assertIn('<script src="/mathe.js"></script>', self.html)
        self.assertLess(self.html.index("/mathe.js"), self.html.index("/app.js"))

    def test_baut_dom_statt_html_und_kennt_die_befehle(self):
        for verboten in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            with self.subTest(verboten):
                self.assertNotIn(verboten, self.mathe)
        for teil in ("createElement", "createTextNode", "frac", "binom", "sqrt", "teile", "zeichne"):
            with self.subTest(teil):
                self.assertIn(teil, self.mathe)
        for klasse in ("mathe-bruch", "mathe-zaehler", "mathe-nenner", "mathe-binom", "mathe-wurzel", "mathe-block"):
            with self.subTest(klasse):
                self.assertIn(klasse, self.mathe)

    def test_app_zeichnet_mathe_und_behaelt_links_und_fett(self):
        for teil in ("FridayMathe", ".teile(", ".zeichne(", "_schreibeFliesstext", "_LINK_MUSTER", "_FETT_MUSTER"):
            with self.subTest(teil):
                self.assertIn(teil, self.app)
        # Nur Kommentare nennen das Wort; gesetzt wird nie HTML.
        self.assertNotRegex(self.app, r"\.(?:inner|outer)HTML\s*=|insertAdjacentHTML|document\.write|eval\(|new Function")

    def test_css_kennt_die_formelteile(self):
        for klasse in (".mathe", ".mathe-block", ".mathe-bruch", ".mathe-binom", ".mathe-wurzel"):
            with self.subTest(klasse):
                self.assertIn(klasse, self.css)

    def test_persona_laesst_formeln_wieder_zu(self):
        # Statt Formeln zu verbieten (14.09.2026), soll die Oberfläche sie darstellen.
        self.assertIn("Dollarzeichen", persona.TEXT_REGELN)
        self.assertIn("stellt sie dar", persona.TEXT_REGELN)
        self.assertIn("$\\frac", persona.TEXT_REGELN)
        self.assertNotIn("KEIN LaTeX", persona.TEXT_REGELN)

    def test_verhalten_im_dom_stub(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node nicht vorhanden")
        r = subprocess.run([node, str(WEB.parent / "test" / "js" / "mathe_verhalten.js"), str(WEB / "mathe.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
