import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from kern.werkbank import speicher
from kern.werkbank.speicher import Fach


class WerkbankSpeicherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fach = Fach(Path(self._tmp.name))

    def test_anlegen_und_lesen(self):
        r = self.fach.anlegen("Wecker klingelt doppelt", "fehler", "gespraech", beleg="am Montag", fingerabdruck="fp1")
        self.assertTrue(r["ok"])
        self.assertTrue(r["neu"])
        e = self.fach.eintrag(r["id"])
        self.assertEqual(e["titel"], "Wecker klingelt doppelt")
        self.assertEqual(e["status"], "vorgeschlagen")
        self.assertEqual(e["zaehler"], 1)
        self.assertFalse(e["fremd"])
        self.assertEqual([x["id"] for x in self.fach.offene()], [r["id"]])
        self.assertEqual(self.fach.treffer("fp1")["id"], r["id"])
        self.assertIsNone(self.fach.treffer("gibtsnicht"))
        self.assertIsNone(self.fach.eintrag(999))

    def test_denkrunde_hoechstens_zwei_neue_vorhaben_am_tag(self):
        a = self.fach.anlegen("Erstes Problem mit dem Wecker", "fehler", "denkrunde")
        b = self.fach.anlegen("Zweite Idee zum Stundenplan", "idee", "denkrunde")
        c = self.fach.anlegen("Dritter Wunsch zur Musik", "wunsch", "denkrunde")
        self.assertTrue(a["ok"] and b["ok"])
        self.assertFalse(c["ok"])
        self.assertIn("heute", c["grund"])
        self.assertTrue(self.fach.anlegen("Etwas aus dem Gespräch", "wunsch", "gespraech")["ok"])    # andere Quellen frei
        self.assertTrue(self.fach.anlegen("Erstes Problem mit dem Wecker", "fehler", "denkrunde")["ok"])   # Dublette zählt nicht neu

    def test_gleicher_fingerabdruck_gibt_dasselbe_vorhaben_und_zaehlt(self):
        a = self.fach.anlegen("Erster Titel", "fehler", "stoerung", fingerabdruck="fp")
        b = self.fach.anlegen("Ganz anderer Titel", "fehler", "stoerung", fingerabdruck="fp")
        self.assertTrue(b["ok"])
        self.assertIs(b["neu"], False)
        self.assertEqual(b["id"], a["id"])
        self.assertEqual(self.fach.eintrag(a["id"])["zaehler"], 2)

    def test_gleicher_sinn_im_titel_wird_zusammengefuehrt(self):
        a = self.fach.anlegen("Die Wetterkarte zeigt falsche Temperaturen an", "fehler", "gespraech")
        b = self.fach.anlegen("Wetterkarte zeigt falsche Temperaturen", "fehler", "gespraech")
        self.assertIs(b["neu"], False)
        self.assertEqual(b["id"], a["id"])
        self.assertEqual(self.fach.eintrag(a["id"])["zaehler"], 2)

    def test_verworfene_zaehlen_nicht_als_treffer(self):
        a = self.fach.anlegen("Etwas Kaputtes", "fehler", "gespraech", fingerabdruck="fp")
        self.fach.status_setzen(a["id"], "verworfen")
        self.assertIsNone(self.fach.treffer("fp"))
        b = self.fach.anlegen("Etwas Kaputtes", "fehler", "gespraech", fingerabdruck="fp")
        self.assertTrue(b["neu"])
        self.assertNotEqual(b["id"], a["id"])
        self.assertEqual(self.fach.eintrag(a["id"])["status"], "verworfen")

    def test_nichts_wird_geloescht(self):
        self.assertFalse(hasattr(self.fach, "loeschen"))
        a = self.fach.anlegen("Etwas", "idee", "denkrunde")
        self.fach.status_setzen(a["id"], "ruht")
        self.assertEqual(self.fach.eintrag(a["id"])["status"], "ruht")

    def test_status_unbekannt_wirft(self):
        a = self.fach.anlegen("Etwas", "idee", "denkrunde")
        with self.assertRaises(ValueError):
            self.fach.status_setzen(a["id"], "kaputt")
        with self.assertRaises(ValueError):
            self.fach.status_setzen(a["id"], "bereit", unbekannt="x")
        r = self.fach.status_setzen(a["id"], "bereit", branch="b", versuche=2, tests="ok")
        self.assertEqual((r["status"], r["branch"], r["versuche"]), ("bereit", "b", 2))

    def test_unbekannte_art_und_quelle_werden_abgelehnt(self):
        self.assertFalse(self.fach.anlegen("X", "quatsch", "gespraech")["ok"])
        self.assertFalse(self.fach.anlegen("X", "idee", "quatsch")["ok"])
        self.assertFalse(self.fach.anlegen("  ", "idee", "gespraech")["ok"])

    def test_titel_zu_lang_wird_abgelehnt(self):
        r = self.fach.anlegen("x" * (speicher.TITEL_MAX + 1), "idee", "gespraech")
        self.assertFalse(r["ok"])
        self.assertIn("Zu lang", r["grund"])
        self.assertEqual(self.fach.offene(), [])

    def test_beleg_wird_geschwaerzt_und_gekuerzt(self):
        r = self.fach.anlegen("Etwas", "fehler", "stoerung", beleg="a" * 3000)
        self.assertEqual(len(self.fach.eintrag(r["id"])["beleg"]), speicher.BELEG_MAX)
        r = self.fach.anlegen("Anderes", "fehler", "stoerung", beleg="key AIzaSyA1234567890abcdefghijklmnopqrstuvwxyz ende")
        self.assertNotIn("AIzaSyA1234567890", self.fach.eintrag(r["id"])["beleg"])

    def test_heute_gestartet_zaehlt_nur_heute(self):
        a = self.fach.anlegen("Eins", "idee", "gespraech")
        self.assertEqual(self.fach.heute_gestartet(), 0)
        self.fach.starten_notieren(a["id"])
        self.assertEqual(self.fach.heute_gestartet(), 1)
        self.fach.starten_notieren(a["id"], jetzt=speicher._jetzt() - timedelta(days=2))
        self.assertEqual(self.fach.heute_gestartet(), 1)

    def test_heute_gestartet_zaehlt_starts_nicht_vorhaben(self):
        a = self.fach.anlegen("Eins", "idee", "gespraech")
        for _ in range(3):
            self.fach.starten_notieren(a["id"])
        self.assertEqual(self.fach.heute_gestartet(), 3)

    def test_uebergang_nur_aus_dem_erwarteten_status(self):
        a = self.fach.anlegen("Eins", "idee", "gespraech")
        self.fach.status_setzen(a["id"], "laeuft")
        self.assertTrue(self.fach.uebergang(a["id"], "laeuft", "bereit", bericht="gut"))
        self.assertFalse(self.fach.uebergang(a["id"], "laeuft", "fehler"))
        self.assertEqual((self.fach.eintrag(a["id"])["status"], self.fach.eintrag(a["id"])["bericht"]), ("bereit", "gut"))

    def test_zaehlen_setzt_zuletzt(self):
        a = self.fach.anlegen("Eins", "idee", "gespraech")
        spaeter = speicher._jetzt() + timedelta(hours=1)
        e = self.fach.zaehlen(a["id"], spaeter)
        self.assertEqual(e["zaehler"], 2)
        self.assertEqual(e["zuletzt"], speicher._iso(spaeter))

    def test_verbindung_und_stoerungstabelle(self):
        with self.fach.verbindung() as db:
            self.assertEqual(db.row_factory, __import__("sqlite3").Row)
            db.execute("INSERT INTO stoerungen (fingerabdruck, ort, erstmals, zuletzt, anzahl, tage, vorhaben_id) "
                       "VALUES ('f','o','a','b',1,'[]',NULL)")
        with self.fach.verbindung() as db:
            self.assertEqual(db.execute("SELECT ort FROM stoerungen").fetchone()["ort"], "o")
        with self.assertRaises(RuntimeError):
            with self.fach.verbindung() as db:
                db.execute("DELETE FROM stoerungen")
                raise RuntimeError("zurück")
        with self.fach.verbindung() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM stoerungen").fetchone()["n"], 1)

    def test_standard_nutzt_daten_ordner_mit_rechten_600(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            alt = os.environ.get("FRIDAY_DATEN")
            os.environ["FRIDAY_DATEN"] = d
            try:
                speicher.standard()
                self.assertEqual((Path(d) / "werkbank.db").stat().st_mode & 0o777, 0o600)
            finally:
                if alt is None:
                    del os.environ["FRIDAY_DATEN"]
                else:
                    os.environ["FRIDAY_DATEN"] = alt
                speicher._instanzen.pop(d, None)


if __name__ == "__main__":
    unittest.main()
