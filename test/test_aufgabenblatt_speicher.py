"""Aufgabenblatt-Speicher: PDF ablegen und prüfen, Aufträge, Limits, Neustart."""
import base64
import io
import os
import tempfile
import unittest
from unittest import mock

from pypdf import PdfWriter

from kern.aufgabenblatt import speicher


def pdf_b64(seiten=1) -> str:
    w = PdfWriter()
    for _ in range(seiten):
        w.add_blank_page(width=200, height=200)
    b = io.BytesIO()
    w.write(b)
    return base64.b64encode(b.getvalue()).decode()


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Blatt(Basis):
    def test_ablegen_speichert_datei_mit_rechten_600(self):
        b = speicher.blatt_ablegen("Blatt 1.pdf", pdf_b64(2))
        self.assertEqual((b["name"], b["seiten"], b["neu"]), ("Blatt 1.pdf", 2, True))
        pfad = os.path.join(self.ordner, "aufgabenblatt", f"blatt-{b['id']}.pdf")
        self.assertTrue(os.path.exists(pfad))
        self.assertEqual(os.stat(pfad).st_mode & 0o777, 0o600)
        self.assertTrue(speicher.blatt_bytes(b["id"]).startswith(b"%PDF-"))

    def test_dasselbe_pdf_zweimal_ist_dasselbe_blatt(self):
        a = speicher.blatt_ablegen("a.pdf", pdf_b64())
        b = speicher.blatt_ablegen("b.pdf", pdf_b64())
        self.assertEqual(a["id"], b["id"])
        self.assertFalse(b["neu"])

    def test_kein_pdf_wird_abgelehnt_und_nichts_abgelegt(self):
        for daten in (base64.b64encode(b"hallo welt").decode(), "%%%kein-base64%%%", ""):
            with self.assertRaises(speicher.AufgabenFehler):
                speicher.blatt_ablegen("x.pdf", daten)
        self.assertFalse(os.path.exists(os.path.join(self.ordner, "aufgabenblatt")) and os.listdir(os.path.join(self.ordner, "aufgabenblatt")))

    def test_defektes_pdf_wird_abgelehnt(self):
        with self.assertRaises(speicher.AufgabenFehler):
            speicher.blatt_ablegen("x.pdf", base64.b64encode(b"%PDF-1.4 kaputt").decode())

    def test_zu_gross_und_zu_viele_seiten(self):
        with mock.patch.object(speicher, "PDF_MAX", 100), self.assertRaisesRegex(speicher.AufgabenFehler, "MB"):
            speicher.blatt_ablegen("x.pdf", pdf_b64())
        with mock.patch.object(speicher, "SEITEN_MAX", 2), self.assertRaisesRegex(speicher.AufgabenFehler, "Seiten"):
            speicher.blatt_ablegen("x.pdf", pdf_b64(3))

    def test_neuestes_blatt_gilt_nur_24_stunden(self):
        self.assertIsNone(speicher.neuestes_blatt())
        b = speicher.blatt_ablegen("a.pdf", pdf_b64())
        self.assertEqual(speicher.neuestes_blatt()["id"], b["id"])
        with mock.patch.object(speicher, "BLATT_FRIST_H", 0):
            self.assertIsNone(speicher.neuestes_blatt())


class Auftraege(Basis):
    def setUp(self):
        super().setUp()
        self.blatt = speicher.blatt_ablegen("a.pdf", pdf_b64())

    def test_anlegen_ohne_blatt_sagt_was_fehlt(self):
        with mock.patch.object(speicher, "BLATT_FRIST_H", 0), self.assertRaisesRegex(speicher.AufgabenFehler, "PDF"):
            speicher.anlegen(None)

    def test_anlegen_nimmt_das_neueste_blatt(self):
        a = speicher.anlegen(None, "  nur kurz  ")
        self.assertEqual((a["blatt"], a["hinweise"], a["status"]), (self.blatt["id"], "nur kurz", "wartend"))

    def test_unbekanntes_blatt_wird_abgelehnt(self):
        with self.assertRaises(speicher.AufgabenFehler):
            speicher.anlegen(999)

    def test_limits(self):
        with mock.patch.object(speicher, "MAX_OFFEN", 2):
            speicher.anlegen(None)
            speicher.anlegen(None)
            with self.assertRaisesRegex(speicher.AufgabenFehler, "warten"):
                speicher.anlegen(None)
        with mock.patch.object(speicher, "TAGESLIMIT", 1), mock.patch.object(speicher, "MAX_OFFEN", 9):
            with self.assertRaisesRegex(speicher.AufgabenFehler, "Tag"):
                speicher.anlegen(None)

    def test_abschliessen_nur_fuer_laufende_und_sauber(self):
        a = speicher.anlegen(None)
        speicher.beginnen(a["id"])
        self.assertTrue(speicher.abschliessen(a["id"], [
            {"nummer": "1", "aufgabe": "2+2", "seite": 1, "loesung": "4", "weg": "addieren", "sicher": True, "quelle": ""},
            {"nummer": "2", "loesung": "vielleicht 7", "sicher": False}], "Aufgabe 3 fehlt"))
        h = speicher.holen(a["id"])
        self.assertEqual((h["status"], h["sicherheit"], h["offen"]), ("fertig", "teilweise", "Aufgabe 3 fehlt"))
        self.assertEqual(h["loesungen"][1]["seite"], 0)
        self.assertIn("Aufgabe 1", speicher.loesungstext(h))
        self.assertIn("unsicher", speicher.loesungstext(h))
        self.assertFalse(speicher.abschliessen(a["id"], [], ""))      # nicht doppelt

    def test_hoechstens_kappt_die_sicherheit(self):
        a = speicher.anlegen(None)
        speicher.beginnen(a["id"])
        speicher.abschliessen(a["id"], [{"nummer": "1", "loesung": "x", "sicher": True}], "", hoechstens="teilweise")
        self.assertEqual(speicher.holen(a["id"])["sicherheit"], "teilweise")

    def test_abgebrochen_bleibt_abgebrochen(self):
        a = speicher.anlegen(None)
        speicher.beginnen(a["id"])
        self.assertTrue(speicher.abbrechen(a["id"]))
        self.assertFalse(speicher.abschliessen(a["id"], [{"nummer": "1", "loesung": "x", "sicher": True}], ""))
        self.assertEqual(speicher.holen(a["id"])["status"], "abgebrochen")

    def test_neustart_zuruecksetzen_dann_fehler(self):
        a = speicher.anlegen(None)
        speicher.beginnen(a["id"])
        self.assertEqual(speicher.laufende_zuruecksetzen(), 1)
        self.assertEqual(speicher.holen(a["id"])["status"], "wartend")
        speicher.beginnen(a["id"])
        speicher.laufende_zuruecksetzen()
        self.assertEqual(speicher.holen(a["id"])["status"], "fehler")

    def test_nummer_ohne_wort_aufgabe(self):
        for roh, soll in (("Aufgabe 1", "1"), ("aufgabe 2a", "2a"), ("Aufgabe: 3", "3"), ("4b", "4b"), ("", "–")):
            self.assertEqual(speicher.loesung_sauber({"nummer": roh, "loesung": "x"})["nummer"], soll, roh)

    def test_sicherheit_von(self):
        s = speicher.sicherheit_von
        self.assertEqual(s([{"sicher": True}, {"sicher": True}]), "belegt")
        self.assertEqual(s([{"sicher": True}, {"sicher": False}]), "teilweise")
        self.assertEqual(s([{"sicher": False}]), "unsicher")
        self.assertEqual(s([]), "unsicher")


if __name__ == "__main__":
    unittest.main()
