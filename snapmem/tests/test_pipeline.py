import csv
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import piexif
from PIL import Image

from snapmem.pipeline import run

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
U3 = "33333333-3333-3333-3333-333333333333"
U4 = "44444444-4444-4444-4444-444444444444"
U5 = "55555555-5555-5555-5555-555555555555"


def _jpg(color=(0, 0, 255)):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, "JPEG", quality=100)
    return buf.getvalue()


def _overlay():
    buf = io.BytesIO()
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(buf, "PNG")
    return buf.getvalue()


def _video(path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return Path(path).read_bytes()


def _eintrag(datum, typ="Image", ort="52.5, 9.7"):
    return {"Date": f"{datum} UTC", "Media Type": typ, "Location": f"Latitude, Longitude: {ort}",
            "Download Link": "", "Media Download Url": ""}


class FakePruefer:
    def __init__(self, personen=(), einbrand=()):
        self._p, self._e = personen, einbrand

    def gesichter(self, d):
        return 0

    def personen(self, d):
        return 1 if d in self._p else 0

    def einbrand(self, d, name):
        return d in self._e, "Test"


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bilder = {U1: _jpg((0, 0, 40)), U2: _jpg((0, 200, 0)), U3: _jpg((200, 200, 0)),
                       U4: _jpg((200, 0, 200)), U5: _jpg((120, 120, 120))}
        self.video = _video(self.tmp / "v.mp4")

    def _zip(self, name="export.zip", with_video=True):
        pfad = self.tmp / name
        eintraege = [
            _eintrag("2024-01-01 10:00:00"),                        # U1: einzige Datei des Tages → eindeutig
            _eintrag("2024-01-02 08:00:00", ort="1.0, 2.0"),        # U2 + U3: zwei Bilder, zwei Einträge → mehrdeutig
            _eintrag("2024-01-02 09:00:00", ort="3.0, 4.0"),
            _eintrag("2024-01-03 11:00:00"),                        # U4
            _eintrag("2024-01-04 12:00:00"),                        # U5
        ]
        if with_video:
            eintraege.append(_eintrag("2024-01-05 13:00:00", "Video"))
        with zipfile.ZipFile(pfad, "w") as z:
            z.writestr("json/memories_history.json", json.dumps({"Saved Media": eintraege}))
            z.writestr("json/chat_history.json", "{}")
            z.writestr(f"memories/2024-01-01_{U1}-main.jpg", self.bilder[U1])
            z.writestr(f"memories/2024-01-01_{U1}-overlay.png", _overlay())      # Overlay → verschmelzen
            z.writestr(f"memories/2024-01-02_{U2}-main.jpg", self.bilder[U2])
            z.writestr(f"memories/2024-01-02_{U3}-main.jpg", self.bilder[U3])
            z.writestr(f"memories/2024-01-03_{U4}-main.jpg", self.bilder[U4])
            z.writestr(f"memories/2024-01-04_{U5}-main.jpg", self.bilder[U5])
            if with_video:
                z.writestr(f"memories/2024-01-05_{U1[:-1]}9-main.mp4", self.video)
                z.writestr(f"memories/2024-01-05_{U1[:-1]}9-overlay.png", _overlay())
            z.writestr("memories/memories.html", "<html></html>")
        return pfad

    def _lesen(self, ziel):
        z = zipfile.ZipFile(ziel)
        zeilen = list(csv.DictReader(io.StringIO(z.read("memories_metadaten.csv").decode("utf-8-sig")), delimiter=";"))
        return z, zeilen

    def test_alles_verschmilzt_und_schreibt_metadaten(self):
        ziel = self.tmp / "out.zip"
        zaehler = run([self._zip()], "alles", ziel)
        z, zeilen = self._lesen(ziel)
        namen = set(z.namelist())
        self.assertIn(f"memories/2024-01-01_{U1}.jpg", namen)
        self.assertFalse([n for n in namen if "overlay" in n or n.endswith(".html")])
        r, _, b = Image.open(io.BytesIO(z.read(f"memories/2024-01-01_{U1}.jpg"))).convert("RGB").getpixel((8, 8))
        self.assertGreater(r, 240)                                   # Overlay eingebrannt
        exif = piexif.load(z.read(f"memories/2024-01-01_{U1}.jpg"))
        self.assertEqual(exif["Exif"][piexif.ExifIFD.DateTimeOriginal], b"2024:01:01 10:00:00")
        self.assertIn(piexif.GPSIFD.GPSLatitude, exif["GPS"])
        self.assertIn(f"memories/2024-01-05_{U1[:-1]}9.mp4", namen)
        self.assertEqual(zaehler["ausgegeben"], 6)

    def test_mehrdeutig_bekommt_nur_das_datum(self):
        ziel = self.tmp / "out.zip"
        run([self._zip()], "alles", ziel)
        z, zeilen = self._lesen(ziel)
        exif = piexif.load(z.read(f"memories/2024-01-02_{U2}.jpg"))
        self.assertEqual(exif["Exif"][piexif.ExifIFD.DateTimeOriginal], b"2024:01:02 00:00:00")
        self.assertEqual(exif["GPS"], {})
        zeile = next(r for r in zeilen if r["datei"].endswith(f"{U2}-main.jpg"))
        self.assertEqual(zeile["status"], "mehrdeutig")
        self.assertIn("08:00:00", zeile["kandidaten"])
        self.assertIn("09:00:00", zeile["kandidaten"])
        self.assertEqual(zeile["aufnahme_utc"], "2024-01-02")

    def test_sauber_laesst_overlay_und_eingebranntes_weg(self):
        ziel = self.tmp / "out.zip"
        p = FakePruefer(einbrand={self.bilder[U3], self.bilder[U4]})
        zaehler = run([self._zip()], "sauber", ziel, pruefer=p)
        z, _ = self._lesen(ziel)
        namen = {n for n in z.namelist() if n.endswith(".jpg")}
        self.assertEqual(namen, {f"memories/2024-01-02_{U2}.jpg", f"memories/2024-01-04_{U5}.jpg"})
        self.assertEqual(zaehler["ausgeschlossen: Overlay-Datei"], 1)
        self.assertEqual(zaehler["ausgeschlossen: eingebrannt (Sticker/Schrift/Linse)"], 2)
        self.assertFalse([n for n in z.namelist() if n.endswith(".mp4")])

    def test_personen_nimmt_nur_saubere_mit_person(self):
        ziel = self.tmp / "out.zip"
        # U1 hat Person, aber ein Overlay → raus. U5 hat Person, ist sauber → drin. U2 ohne Person → raus.
        p = FakePruefer(personen={self.bilder[U1], self.bilder[U5]})
        run([self._zip()], "personen", ziel, pruefer=p)
        z, _ = self._lesen(ziel)
        self.assertEqual({n for n in z.namelist() if n.endswith(".jpg")}, {f"memories/2024-01-04_{U5}.jpg"})

    def test_dubletten_ueber_mehrere_zips_nur_einmal(self):
        ziel = self.tmp / "out.zip"
        run([self._zip("a.zip", with_video=False), self._zip("b.zip", with_video=False)], "alles", ziel)
        z, zeilen = self._lesen(ziel)
        jpgs = [n for n in z.namelist() if n.endswith(".jpg")]
        self.assertEqual(len(jpgs), len(set(jpgs)))
        self.assertEqual(len(jpgs), 5)
        self.assertEqual(sum(1 for r in zeilen if r["datei"].endswith(f"{U1}-main.jpg")), 1)

    def test_kaputtes_bild_wird_vermerkt_und_lauf_geht_weiter(self):
        pfad = self._zip(with_video=False)
        mit_defekt = self.tmp / "defekt.zip"
        with zipfile.ZipFile(pfad) as src, zipfile.ZipFile(mit_defekt, "w") as dst:
            for info in src.infolist():
                daten = src.read(info)
                if info.filename.endswith(f"{U1}-overlay.png"):
                    daten = b"kaputt"
                dst.writestr(info.filename, daten)
        ziel = self.tmp / "out.zip"
        zaehler = run([mit_defekt], "alles", ziel)
        z, zeilen = self._lesen(ziel)
        self.assertEqual(zaehler["Fehler"], 1)
        zeile = next(r for r in zeilen if r["datei"].endswith(f"{U1}-main.jpg"))
        self.assertTrue(zeile["hinweis"].startswith("Fehler"))
        self.assertIn(f"memories/2024-01-04_{U5}.jpg", z.namelist())

    def _zip_chat(self):
        pfad = self.tmp / "chat.zip"
        self.chat_bilder = {"exakt": _jpg((250, 10, 10)), "anderes": _jpg((10, 250, 10))}
        chats = {
            "freund1": [{"Media Type": "MEDIA", "Created": "2024-01-02 09:30:00 UTC", "Media IDs": "b~EXAKT",
                         "Conversation Title": None},
                        {"Media Type": "TEXT", "Created": "2024-01-03 12:00:00 UTC", "Media IDs": "", "Conversation Title": None}],
            "aaaa-bbbb": [{"Media Type": "TEXT", "Created": "2024-01-03 13:00:00 UTC", "Media IDs": "",
                           "Conversation Title": "Gruppe Ü"}],
        }
        with zipfile.ZipFile(pfad, "w") as z:
            z.writestr("json/memories_history.json", json.dumps({"Saved Media": [_eintrag("2024-01-04 12:00:00")]}))
            z.writestr("json/chat_history.json", json.dumps(chats))
            z.writestr(f"memories/2024-01-04_{U5}-main.jpg", self.bilder[U5])
            z.writestr("chat_media/2024-01-02_b~EXAKT.jpg", self.chat_bilder["exakt"])
            z.writestr("chat_media/2024-01-03_b~ANDERES.jpg", self.chat_bilder["anderes"])
        return pfad

    def test_chat_medien_nennen_den_chat_nur_wenn_der_export_ihn_kennt(self):
        ziel = self.tmp / "out.zip"
        run([self._zip_chat()], "alles", ziel)
        z, zeilen = self._lesen(ziel)
        exakt = piexif.load(z.read("chat_media/2024-01-02_b~EXAKT.jpg"))
        self.assertEqual(exakt["0th"][piexif.ImageIFD.ImageDescription], b"Snapchat-Chat: freund1")
        self.assertEqual(exakt["Exif"][piexif.ExifIFD.DateTimeOriginal], b"2024:01:02 09:30:00")
        zeile = next(r for r in zeilen if r["datei"].endswith("EXAKT.jpg"))
        self.assertEqual((zeile["chat"], zeile["moegliche_chats"]), ("freund1", ""))
        anderes = piexif.load(z.read("chat_media/2024-01-03_b~ANDERES.jpg"))
        self.assertNotIn(piexif.ImageIFD.ImageDescription, anderes["0th"])     # nichts geraten
        zeile = next(r for r in zeilen if r["datei"].endswith("ANDERES.jpg"))
        self.assertEqual((zeile["chat"], zeile["moegliche_chats"]), ("", "Gruppe Ü | freund1"))

    def test_sauber_nimmt_chat_bilder_mit_und_prueft_sie_wie_memories(self):
        ziel = self.tmp / "out.zip"
        zaehler = run([self._zip_chat()], "sauber", ziel, pruefer=FakePruefer(einbrand={self.chat_bilder["anderes"]}))
        z, _ = self._lesen(ziel)
        self.assertEqual({n for n in z.namelist() if n.endswith(".jpg")},
                         {"chat_media/2024-01-02_b~EXAKT.jpg", f"memories/2024-01-04_{U5}.jpg"})
        self.assertEqual(zaehler["ausgeschlossen: eingebrannt (Sticker/Schrift/Linse)"], 1)

    def test_personen_nimmt_auch_chat_bilder_mit_person(self):
        ziel = self.tmp / "out.zip"
        run([self._zip_chat()], "personen", ziel, pruefer=FakePruefer(personen={self.chat_bilder["exakt"]}))
        z, _ = self._lesen(ziel)
        self.assertEqual({n for n in z.namelist() if n.endswith(".jpg")}, {"chat_media/2024-01-02_b~EXAKT.jpg"})

    def test_fortschritt_wird_gemeldet(self):
        meldungen = []
        run([self._zip(with_video=False)], "alles", self.tmp / "out.zip", lambda a, b, c: meldungen.append((a, b)))
        self.assertEqual(meldungen[0], (0, 5))
        self.assertEqual(meldungen[-1][0], meldungen[-1][1])

    def test_unbekannter_modus_und_leere_zip(self):
        with self.assertRaises(ValueError):
            run([self._zip()], "quatsch", self.tmp / "o.zip")
        leer = self.tmp / "leer.zip"
        with zipfile.ZipFile(leer, "w") as z:
            z.writestr("x.txt", "x")
        with self.assertRaises(ValueError):
            run([leer], "alles", self.tmp / "o2.zip")


if __name__ == "__main__":
    unittest.main()
