"""Uni-Tab unter Bildung zeigt das aktuelle Datum (im Browser berechnet, nur textContent)."""
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class UniDatum(unittest.TestCase):
    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.js = (WEB / "app.js").read_text(encoding="utf-8")

    def test_datumsfeld_im_uni_tab(self):
        uni = self.html.split('id="bildung-uni"', 1)[1].split('id="bildung-schule"', 1)[0]
        self.assertIn('id="uni-datum"', uni)

    def test_datum_wird_deutsch_formatiert_und_per_textcontent_gesetzt(self):
        self.assertIn("uni-datum", self.js)
        self.assertIn("toLocaleDateString('de-DE'", self.js)
        self.assertIn("weekday: 'long'", self.js)
        self.assertNotIn("uni-datum').innerHTML", self.js)


if __name__ == "__main__":
    unittest.main()
