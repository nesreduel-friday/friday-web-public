"""Snapchat-Memories: Upload in Teilen, Aufträge, Status, Aufräumen (Friday-Seite)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kern.snapmem import auftraege as a


def _zip_bytes():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("json/memories_history.json", "{}")
    return buf.getvalue()


ZIP = _zip_bytes()


class Auftraege(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.klein = mock.patch.object(a, "TEIL", 4)       # winzige Teile für den Test
        self.klein.start()
        self.addCleanup(self.klein.stop)

    def _hochladen(self, inhalt=ZIP, name="export.zip"):
        uid = a.upload_start(name, len(inhalt))["id"]
        for nr in range((len(inhalt) + a.TEIL - 1) // a.TEIL):
            a.upload_teil(uid, nr, inhalt[nr * a.TEIL:(nr + 1) * a.TEIL])
        return uid, a.upload_fertig(uid)

    def test_upload_in_teilen_ergibt_die_datei(self):
        inhalt = ZIP
        uid, info = self._hochladen(inhalt)
        self.assertEqual((a.ordner() / "uploads" / info["datei"]).read_bytes(), inhalt)
        self.assertEqual(info["groesse"], len(inhalt))
        self.assertFalse(list((a.ordner() / "uploads").glob("*.teil")))

    def test_unvollstaendiger_upload_wird_abgelehnt(self):
        uid = a.upload_start("x.zip", 12)["id"]
        a.upload_teil(uid, 0, b"abcd")
        with self.assertRaises(a.SnapmemFehler):
            a.upload_fertig(uid)

    def test_falsche_teilgroesse_und_nummer(self):
        uid = a.upload_start("x.zip", 12)["id"]
        with self.assertRaises(a.SnapmemFehler):
            a.upload_teil(uid, 0, b"abcde")           # größer als TEIL
        with self.assertRaises(a.SnapmemFehler):
            a.upload_teil(uid, 9, b"abcd")            # hinter dem Ende
        with self.assertRaises(a.SnapmemFehler):
            a.upload_teil("../../etc", 0, b"abcd")    # keine gültige ID

    def test_nur_zip_und_saubere_namen(self):
        with self.assertRaises(a.SnapmemFehler):
            a.upload_start("foto.jpg", 10)
        datei = self._hochladen(name="../../böse name.zip")[1]["datei"]
        self.assertNotIn("/", datei)
        self.assertTrue(datei.endswith(".zip"))

    def test_keine_zip_nach_dem_hochladen_wird_verworfen(self):
        uid, _ = None, None
        uid = a.upload_start("x.zip", 4)["id"]
        a.upload_teil(uid, 0, b"nope")
        with self.assertRaises(a.SnapmemFehler):
            a.upload_fertig(uid)
        self.assertEqual(a.uploads(), [])

    def test_zu_gross_und_zu_wenig_platz(self):
        with mock.patch.object(a, "ZIP_MAX", 10):
            with self.assertRaises(a.SnapmemFehler):
                a.upload_start("x.zip", 11)
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=10)):
            with self.assertRaises(a.SnapmemFehler) as ctx:
                a.upload_start("x.zip", 100)
        self.assertIn("Platz", str(ctx.exception))

    def test_auftrag_status_und_ergebnis(self):
        _, info = self._hochladen()
        aid = a.auftrag_anlegen([info["datei"]], "personen")
        auftrag = json.loads((a.ordner() / "auftraege" / f"{aid}.json").read_text())
        self.assertEqual((auftrag["zips"], auftrag["modus"]), ([info["datei"]], "personen"))
        self.assertEqual(a.status(aid)["zustand"], "wartet")
        (a.ordner() / "ergebnisse").mkdir(exist_ok=True)
        (a.ordner() / "ergebnisse" / f"{aid}.status.json").write_text(json.dumps({"zustand": "fertig", "groesse": 5}))
        self.assertEqual(a.status(aid)["zustand"], "fertig")
        self.assertIsNone(a.ergebnis_pfad(aid))
        (a.ordner() / "ergebnisse" / f"{aid}.zip").write_bytes(b"12345")
        self.assertEqual(a.ergebnis_pfad(aid).name, f"{aid}.zip")

    def test_auftrag_prueft_modus_und_dateien(self):
        with self.assertRaises(a.SnapmemFehler):
            a.auftrag_anlegen(["gibt_es_nicht.zip"], "alles")
        _, info = self._hochladen()
        with self.assertRaises(a.SnapmemFehler):
            a.auftrag_anlegen([info["datei"]], "quatsch")
        with self.assertRaises(a.SnapmemFehler):
            a.auftrag_anlegen([], "alles")

    def test_id_wird_streng_geprueft(self):
        for schlecht in ("../x", "a/b", "", "x" * 40):
            with self.assertRaises(a.SnapmemFehler):
                a.status(schlecht)

    def test_loeschen_entfernt_nur_den_lauf(self):
        _, info = self._hochladen()
        aid = a.auftrag_anlegen([info["datei"]], "alles")
        (a.ordner() / "ergebnisse").mkdir(exist_ok=True)
        (a.ordner() / "ergebnisse" / f"{aid}.zip").write_bytes(b"x")
        (a.ordner() / "ergebnisse" / f"{aid}.status.json").write_text("{}")
        a.loeschen(aid)
        self.assertEqual(list((a.ordner() / "ergebnisse").iterdir()), [])
        self.assertFalse((a.ordner() / "auftraege" / f"{aid}.json").exists())
        self.assertEqual(len(a.uploads()), 1)                 # Upload bleibt, bis Eike ihn löscht
        a.upload_loeschen(info["id"])
        self.assertEqual(a.uploads(), [])

    def test_aufraeumen_nach_sieben_tagen(self):
        _, info = self._hochladen()
        aid = a.auftrag_anlegen([info["datei"]], "alles")
        (a.ordner() / "ergebnisse").mkdir(exist_ok=True)
        (a.ordner() / "ergebnisse" / f"{aid}.zip").write_bytes(b"x")
        alt = time.time() - 8 * 86400
        for p in (a.ordner() / "ergebnisse" / f"{aid}.zip", a.ordner() / "auftraege" / f"{aid}.json",
                  a.ordner() / "uploads" / info["datei"]):
            os.utime(p, (alt, alt))
        a.aufraeumen()
        self.assertEqual(a.uploads(), [])
        self.assertEqual(a.laeufe(), [])

    def test_laeufe_zeigen_neueste_zuerst(self):
        _, info = self._hochladen()
        a1 = a.auftrag_anlegen([info["datei"]], "alles")
        time.sleep(0.02)
        a2 = a.auftrag_anlegen([info["datei"]], "sauber")
        self.assertEqual([x["id"] for x in a.laeufe()], [a2, a1])


if __name__ == "__main__":
    unittest.main()
