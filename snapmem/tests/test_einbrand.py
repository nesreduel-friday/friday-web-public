"""Erkenner für eingebrannte Schrift/Aufkleber/Linsen (Fotos aus dem echten Export, verkleinert)."""
import unittest

from snapmem import einbrand
from snapmem.pruefer import grosse_uuid
from snapmem.tests.fotos import foto

lies = lambda n: foto(n).read_bytes()


class GeoText(unittest.TestCase):
    def test_geo_beschriftung_wird_erkannt(self):
        self.assertTrue(einbrand.geo_text(lies("eingebrannt_geo.jpg")))

    def test_normale_fotos_haben_keine(self):
        for name in ("sauber_person.jpg", "sauber_rose.jpg", "sauber_gruppe.jpg", "kein_mensch_laub.jpg", "person_ruecken.jpg"):
            self.assertFalse(einbrand.geo_text(lies(name)), name)

    def test_kaputte_datei(self):
        self.assertFalse(einbrand.geo_text(b"kein bild"))


@unittest.skipUnless(einbrand.CLIP.exists() and einbrand.GEWICHTE.exists(), "CLIP-Modell oder Gewichte fehlen")
class Modell(unittest.TestCase):
    def test_sticker_und_linsen_werden_erkannt(self):
        for name in ("eingebrannt_dino.jpg", "eingebrannt_banane.jpg", "eingebrannt_geo.jpg"):
            erkannt, grund = einbrand.pruefen(lies(name), grosse_uuid=True)
            self.assertTrue(erkannt, f"{name}: {grund}")

    def test_saubere_fotos_bleiben_drin(self):
        for name in ("sauber_person.jpg", "sauber_rose.jpg", "sauber_gruppe.jpg"):
            erkannt, grund = einbrand.pruefen(lies(name), grosse_uuid=False)
            self.assertFalse(erkannt, f"{name}: {grund}")

    def test_unlesbares_bild_gilt_als_nicht_eingebrannt(self):
        self.assertEqual(einbrand.pruefen(b"kein bild", grosse_uuid=True)[0], False)

    def test_chat_bilder_haben_eine_eigene_milde_schwelle(self):
        _, grund = einbrand.pruefen(lies("sauber_person.jpg"), grosse_uuid=False, chat=True)
        self.assertIn(f"Schwelle {einbrand.SCHWELLE_CHAT:.2f}", grund)
        self.assertGreater(einbrand.SCHWELLE_CHAT, float(einbrand._gewichte()["schwelle_gross"]))

    def test_grosse_uuid_prueft_strenger(self):
        g = einbrand._gewichte()
        self.assertLess(float(g["schwelle_gross"]), float(g["schwelle_klein"]))


class Dateiname(unittest.TestCase):
    def test_grosse_und_kleine_uuid(self):
        self.assertTrue(grosse_uuid("memories/2025-10-04_C6483E6D-B397-424E-9ABC-0123456789AB-main.jpg"))
        self.assertFalse(grosse_uuid("memories/2023-07-29_1962b298-422e-a978-b60b-072a5949ebed-main.jpg"))
        self.assertFalse(grosse_uuid("memories/ohne_uuid.jpg"))
        self.assertFalse(grosse_uuid("chat_media/2026-07-06_b~EiASFVVWZjFZZDBBTzY3VkJkSEEzU3lzbTIBCUgCUARgAQ.jpg"))


if __name__ == "__main__":
    unittest.main()
