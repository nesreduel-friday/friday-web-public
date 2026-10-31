"""Ausarbeitung als PDF: Titelseite, je Aufgabe die Blattseite als Bild und eine Textseite mit Lösung."""
import io
import os
import tempfile
import unittest
from unittest import mock

from pypdf import PdfReader

from kern.aufgabenblatt import ausarbeitung, speicher
from test.test_aufgabenblatt_speicher import pdf_b64


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.blatt = speicher.blatt_ablegen("Mathe Blatt 3.pdf", pdf_b64(2))

    def tearDown(self):
        self.env.stop()

    def fertig(self, loesungen=None):
        a = speicher.anlegen(self.blatt["id"])
        speicher.beginnen(a["id"])
        speicher.abschliessen(a["id"], loesungen if loesungen is not None else [
            {"nummer": "1", "aufgabe": "2+2", "seite": 1, "loesung": "4 € für Müller", "weg": "addieren", "sicher": True},
            {"nummer": "2", "aufgabe": "Fläche", "seite": 2, "loesung": "ungefähr 7", "weg": "geschätzt", "sicher": False, "quelle": "https://beispiel.de/x"}], "Aufgabe 3 fehlt")
        return a["id"]


class Bauen(Basis):
    def test_pdf_ist_gueltig_loesungen_kompakt_auf_einer_seite_dann_die_blattseiten_einmal(self):
        pdf = ausarbeitung.bauen(self.fertig())
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 1 + 2)        # 1 Textseite + Blattseite 1 und 2 als Anhang

    def test_text_enthaelt_loesung_unsicherheit_und_umlaute(self):
        pdf = ausarbeitung.bauen(self.fertig())
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
        for erwartet in ("Mathe Blatt 3.pdf", "Aufgabe 1", "addieren", "prüfen", "Aufgabe 3 fehlt", "Müller"):
            self.assertIn(erwartet, text)

    def test_seite_ausserhalb_des_blatts_faellt_auf_text_zurueck(self):
        a = self.fertig([{"nummer": "1", "seite": 99, "loesung": "x", "sicher": True}])
        self.assertEqual(len(PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages), 1)

    def test_aufgabe_ohne_seite_hat_nur_die_textseite(self):
        a = self.fertig([{"nummer": "1", "loesung": "x", "sicher": True}])
        self.assertEqual(len(PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages), 1)

    def test_lange_loesung_laeuft_auf_weitere_seiten(self):
        a = self.fertig([{"nummer": "1", "seite": 1, "loesung": "Satz. " * 600, "weg": "Weg. " * 700, "sicher": True}])
        self.assertGreater(len(PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages), 2)       # mehr als eine Textseite plus der Anhang

    def test_dieselbe_blattseite_kommt_nur_einmal_vor(self):
        a = self.fertig([{"nummer": str(i), "seite": 1, "loesung": f"L{i}", "sicher": True} for i in range(1, 4)])
        self.assertEqual(len(PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages), 1 + 1)

    def test_viele_kurze_aufgaben_fuellen_die_seite_statt_je_eine_zu_bekommen(self):
        a = self.fertig([{"nummer": str(i), "loesung": f"Ergebnis {i}", "weg": "kurz", "sicher": True} for i in range(1, 4)])
        self.assertEqual(len(PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages), 1)

    def test_alte_auftraege_mit_wort_aufgabe_in_der_nummer_werden_beim_setzen_bereinigt(self):
        self.assertEqual(ausarbeitung._nummer({"nummer": "Aufgabe 1"}), "1")
        self.assertEqual(ausarbeitung._nummer({"nummer": "2a"}), "2a")
        self.assertEqual(ausarbeitung._nummer({"nummer": "Aufgabe"}), "–")

    def test_kein_wort_aufgabe_doppelt(self):
        a = self.fertig([{"nummer": "Aufgabe 1", "loesung": "4", "sicher": True}])
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages)
        self.assertNotIn("Aufgabe Aufgabe", text)

    def test_nummerierte_schritte_im_weg_stehen_je_in_einer_zeile(self):
        _, items = ausarbeitung._karte_aufgabe({"nummer": "1", "aufgabe": "", "seite": 0, "loesung": "x", "sicher": True, "quelle": "",
                                                "weg": "1. Erst das. 2. Dann dies. 3. Zuletzt jenes."})
        zeilen = ["".join(r[1] for r in i["runs"]) for i in items if i["typ"] == "zeile"]
        self.assertEqual([z for z in zeilen if z[:2] in ("1.", "2.", "3.")], ["1. Erst das.", "2. Dann dies.", "3. Zuletzt jenes."])

    def test_offen_karte_wiederholt_das_wort_offen_nicht(self):
        a = self.fertig()
        text = PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages[0].extract_text()
        self.assertIn("NOCH OFFEN", text)
        self.assertNotIn("Offen: Aufgabe 3", text)
        self.assertIn("Aufgabe 3 fehlt", text)

    def test_mathe_zeichen_und_hochzahlen_landen_in_der_symbolschrift(self):
        a = self.fertig([{"nummer": "1", "loesung": r"x ≈ √2, also x^2 ≤ 3", "weg": r"\frac{3}{4} · π", "sicher": True}])
        inhalt = b"".join(p.get_contents().get_data() for p in PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages)
        self.assertIn(b"/F3", inhalt)
        self.assertIn(b"\xd6", inhalt)          # √
        self.assertIn(b"\xbb", inhalt)          # ≈
        self.assertIn(b"\xa3", inhalt)          # ≤

    def test_jede_seite_hat_eine_fusszeile_mit_seitenzahl(self):
        a = self.fertig([{"nummer": "1", "seite": 1, "loesung": "Satz. " * 600, "weg": "Weg. " * 700, "sicher": True}])
        seiten = PdfReader(io.BytesIO(ausarbeitung.bauen(a))).pages
        n = len(seiten)
        self.assertGreater(n, 2)
        self.assertIn(f"Seite 1 von {n}", seiten[0].extract_text())
        self.assertIn(f"Seite 2 von {n}", seiten[1].extract_text())

    def test_nicht_fertiger_auftrag_wird_abgelehnt(self):
        a = speicher.anlegen(self.blatt["id"])
        with self.assertRaisesRegex(speicher.AufgabenFehler, "nicht fertig"):
            ausarbeitung.bauen(a["id"])

    def test_unbekannter_auftrag(self):
        with self.assertRaises(speicher.AufgabenFehler):
            ausarbeitung.bauen(99)

    def test_ablegen_schreibt_mit_rechten_600(self):
        a = self.fertig()
        pfad = ausarbeitung.ablegen(a)
        self.assertTrue(pfad.exists())
        self.assertEqual(os.stat(pfad).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
