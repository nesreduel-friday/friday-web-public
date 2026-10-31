import io
import unittest

from PIL import Image

from snapmem.overlay import merge_image


def _png(size, color):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def _jpg(size, color, exif=b""):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=100, exif=exif)
    return buf.getvalue()


class MergeImageTest(unittest.TestCase):
    def test_opaque_overlay_covers_pixel(self):
        out = merge_image(_jpg((8, 8), (0, 0, 255)), _png((8, 8), (255, 0, 0, 255)))
        r, g, b = Image.open(io.BytesIO(out)).convert("RGB").getpixel((4, 4))
        self.assertGreater(r, 240)
        self.assertLess(b, 15)

    def test_transparent_overlay_keeps_main(self):
        out = merge_image(_jpg((8, 8), (0, 0, 255)), _png((8, 8), (255, 0, 0, 0)))
        r, g, b = Image.open(io.BytesIO(out)).convert("RGB").getpixel((4, 4))
        self.assertLess(r, 15)
        self.assertGreater(b, 240)

    def test_half_transparent_mixes(self):
        out = merge_image(_jpg((8, 8), (0, 0, 255)), _png((8, 8), (255, 0, 0, 128)))
        r, _, b = Image.open(io.BytesIO(out)).convert("RGB").getpixel((4, 4))
        self.assertTrue(100 < r < 160 and 100 < b < 160)

    def test_overlay_of_other_size_is_scaled(self):
        out = merge_image(_jpg((16, 32), (0, 0, 255)), _png((4, 8), (255, 0, 0, 255)))
        img = Image.open(io.BytesIO(out))
        self.assertEqual(img.size, (16, 32))
        self.assertGreater(img.convert("RGB").getpixel((15, 31))[0], 240)

    def test_exif_is_kept(self):
        import piexif
        exif = piexif.dump({"0th": {piexif.ImageIFD.Make: b"Test"}})
        out = merge_image(_jpg((8, 8), (0, 0, 255), exif), _png((8, 8), (0, 0, 0, 0)))
        self.assertEqual(piexif.load(out)["0th"][piexif.ImageIFD.Make], b"Test")

    def test_without_overlay_returns_main_unchanged(self):
        main = _jpg((8, 8), (1, 2, 3))
        self.assertEqual(merge_image(main, None), main)

    def test_broken_overlay_raises_value_error(self):
        with self.assertRaises(ValueError):
            merge_image(_jpg((8, 8), (1, 2, 3)), b"kein bild")


if __name__ == "__main__":
    unittest.main()
