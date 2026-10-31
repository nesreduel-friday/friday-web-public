import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from kern.dienste import snapbild


def png(farbe=(20, 80, 160), groesse=(400, 800), modus="RGB") -> bytes:
    aus = BytesIO()
    Image.new(modus, groesse, farbe if modus == "RGB" else farbe + (255,)).save(aus, "PNG")
    return aus.getvalue()


class VorbereitenTest(unittest.TestCase):
    def test_wird_ein_jpeg_ohne_alphakanal(self):
        jpeg = snapbild.vorbereiten(png(modus="RGBA"))
        self.assertEqual(jpeg[:2], b"\xff\xd8")
        self.assertEqual(Image.open(BytesIO(jpeg)).mode, "RGB")

    def test_grosse_bilder_werden_verkleinert(self):
        jpeg = snapbild.vorbereiten(png(groesse=(4000, 6000)))
        breite, hoehe = Image.open(BytesIO(jpeg)).size
        self.assertLessEqual(max(breite, hoehe), snapbild.MAX_SEITE)

    def test_kein_bild_wirft(self):
        with self.assertRaises(snapbild.SnapbildFehler):
            snapbild.vorbereiten(b"das ist kein bild")

    def test_text_liegt_als_band_im_unteren_teil_und_veraendert_nur_dort_pixel(self):
        ohne = Image.open(BytesIO(snapbild.vorbereiten(png()))).convert("RGB")
        mit = Image.open(BytesIO(snapbild.vorbereiten(png(), "Alles Gute zum Geburtstag!"))).convert("RGB")
        self.assertEqual(ohne.size, mit.size)
        breite, hoehe = mit.size
        oben = (10, 10, breite - 10, hoehe // 2)
        self.assertEqual(ohne.crop(oben).tobytes(), mit.crop(oben).tobytes())   # oben bleibt das Bild wie es war
        unten = (0, int(hoehe * 0.6), breite, hoehe)
        self.assertNotEqual(ohne.crop(unten).tobytes(), mit.crop(unten).tobytes())

    def test_text_mit_umlauten_und_langer_zeile_bricht_nicht(self):
        text = "Grüße aus dem schönen Hannover, schön dass es dich gibt und viel Spaß heute Abend! " * 3
        self.assertEqual(snapbild.vorbereiten(png(), text)[:2], b"\xff\xd8")

    def test_leerer_text_veraendert_das_bild_nicht(self):
        a = snapbild.vorbereiten(png())
        b = snapbild.vorbereiten(png(), "   ")
        self.assertEqual(a, b)


class AblegenTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_ablegen_gibt_pfad_relativ_zum_datenordner(self):
        rel = snapbild.ablegen(b"\xff\xd8daten")
        self.assertTrue(rel.startswith("snapchat/ausgang/") and rel.endswith(".jpg"))
        self.assertEqual((Path(self.ordner) / rel).read_bytes(), b"\xff\xd8daten")

    def test_alte_dateien_werden_beim_ablegen_aufgeraeumt(self):
        alt = Path(self.ordner) / "snapchat" / "ausgang" / ("a" * 32 + ".jpg")
        alt.parent.mkdir(parents=True)
        alt.write_bytes(b"alt")
        os.utime(alt, (1.0, 1.0))
        snapbild.ablegen(b"neu")
        self.assertFalse(alt.exists())
