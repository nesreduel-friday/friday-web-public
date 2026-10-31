"""Snap-Bild im Hochformat: Querbilder bekommen einen unscharf aufgezogenen eigenen Hintergrund statt schwarzer Balken."""
import asyncio
import os
import tempfile
import unittest
from io import BytesIO
from unittest import mock

from PIL import Image

from kern.dienste import snapbild


def png(breite, hoehe, farbe=(200, 40, 40)):
    b = BytesIO()
    Image.new("RGB", (breite, hoehe), farbe).save(b, "PNG")
    return b.getvalue()


def ergebnis(daten):
    return Image.open(BytesIO(daten)).convert("RGB")


class Hochformat(unittest.TestCase):
    def test_querbild_wird_zu_neun_zu_sechzehn(self):
        bild = ergebnis(snapbild.vorbereiten(png(1600, 900), hochformat=True))
        self.assertEqual(bild.size, (1080, 1920))

    def test_vordergrund_bleibt_scharf_und_mittig_hintergrund_ist_vom_bild(self):
        # Oben links ein dunkles Bild mit hellem Mittelfeld: Hintergrund wird aus dem Bild gerechnet, nicht schwarz.
        roh = Image.new("RGB", (1600, 900), (30, 30, 200))
        roh.paste((250, 250, 20), (700, 350, 900, 550))
        b = BytesIO()
        roh.save(b, "PNG")
        bild = ergebnis(snapbild.vorbereiten(b.getvalue(), hochformat=True))
        mitte = bild.getpixel((540, 960))
        self.assertTrue(mitte[0] > 200 and mitte[1] > 200 and mitte[2] < 80, mitte)          # scharfes Mittelfeld
        oben = bild.getpixel((540, 40))
        self.assertNotEqual(oben, (0, 0, 0))
        self.assertTrue(oben[2] > oben[0], oben)                                               # bläulich wie das Bild, kein Schwarz

    def test_bild_im_neun_zu_sechzehn_bleibt_unveraendert_im_format(self):
        bild = ergebnis(snapbild.vorbereiten(png(900, 1600), hochformat=True))
        self.assertEqual(bild.size, (900, 1600))

    def test_schmales_hochbild_bekommt_seitenraender_aus_dem_bild(self):
        bild = ergebnis(snapbild.vorbereiten(png(500, 1500), hochformat=True))
        self.assertEqual(bild.size, (1080, 1920))
        links = bild.getpixel((10, 960))
        self.assertTrue(links[0] > 100 and links[0] > links[1], links)                        # rötlich wie das Bild

    def test_ohne_den_schalter_bleibt_alles_wie_vorher(self):
        self.assertEqual(ergebnis(snapbild.vorbereiten(png(1600, 900))).size, (1600, 900))

    def test_text_liegt_auf_dem_fertigen_hochformat(self):
        mit = ergebnis(snapbild.vorbereiten(png(1600, 900), "Hallo du da", hochformat=True))
        ohne = ergebnis(snapbild.vorbereiten(png(1600, 900), hochformat=True))
        self.assertEqual(mit.size, (1080, 1920))
        self.assertNotEqual(mit.tobytes(), ohne.tobytes())

    def test_quadrat_wird_ebenfalls_gefuellt(self):
        self.assertEqual(ergebnis(snapbild.vorbereiten(png(800, 800), hochformat=True)).size, (1080, 1920))

    def test_kaputtes_bild_bleibt_ein_fehler(self):
        with self.assertRaises(snapbild.SnapbildFehler):
            snapbild.vorbereiten(b"kein bild", hochformat=True)


class Verwendung(unittest.TestCase):
    """Die automatischen Snaps (Flammen, Wunsch-Snaps) bitten um das Hochformat — Eikes eigene Snaps nicht."""

    def test_flammen_und_wunsch_rufen_mit_hochformat_auf(self):
        import inspect
        from kern.personen import flammen
        from kern.werkzeuge import snapwunsch
        self.assertIn("hochformat=True", inspect.getsource(flammen.einmal))
        self.assertIn("hochformat=True", inspect.getsource(snapwunsch.snap_wunsch))

    def test_eikes_schicke_snap_bleibt_unveraendert(self):
        import inspect
        from kern.werkzeuge import snapchat
        self.assertNotIn("hochformat", inspect.getsource(snapchat.schicke_snap))
