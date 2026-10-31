"""Seitensatz der Ausarbeitung: Mathe-Zeichen, hoch/tief, LaTeX-Reste, Umbruch — ohne Schriftdateien (PDF-Standardschriften Helvetica + Symbol)."""
import io
import unittest

from pypdf import PdfReader, PdfWriter

from kern.aufgabenblatt import satz


def text_von(runs) -> str:
    return "".join(r[1] for r in runs)


def fonts_von(runs) -> set:
    return {r[0] for r in runs}


class Normalisieren(unittest.TestCase):
    def test_latex_reste_werden_zu_zeichen(self):
        self.assertEqual(satz.normalisieren(r"$\frac{3}{4} \cdot \pi$"), "3/4 · π")
        self.assertEqual(satz.normalisieren(r"\sqrt{16} = 4"), "√(16) = 4")
        self.assertEqual(satz.normalisieren(r"x \leq 5 \Rightarrow x \neq 6"), "x ≤ 5 ⇒ x ≠ 6")
        self.assertEqual(satz.normalisieren(r"\text{Fläche} \approx 7"), "Fläche ≈ 7")

    def test_ascii_schreibweisen(self):
        self.assertEqual(satz.normalisieren("a <= b und c >= d"), "a ≤ b und c ≥ d")
        self.assertEqual(satz.normalisieren("3x = 15 => x = 5"), "3x = 15 ⇒ x = 5")
        self.assertEqual(satz.normalisieren("A <=> B"), "A ⇔ B")
        self.assertEqual(satz.normalisieren("17 * 23"), "17 · 23")
        self.assertEqual(satz.normalisieren("5+-2"), "5±2")
        self.assertEqual(satz.normalisieren("sqrt(2)"), "√(2)")
        self.assertEqual(satz.normalisieren("a != b"), "a ≠ b")

    def test_normaler_text_bleibt_unveraendert(self):
        t = "Berechne 17 mal 23 – das ist (fast) einfach; Müller zahlt 4 €."
        self.assertEqual(satz.normalisieren(t), t)


class Sonderfaelle(unittest.TestCase):
    def test_haekchen_und_kreuz_werden_woerter(self):
        self.assertEqual(satz.normalisieren("Probe stimmt ✓"), "Probe stimmt ok")
        self.assertEqual(satz.normalisieren("Weg ✗"), "Weg falsch")

    def test_dezimalkomma_aus_latex(self):
        self.assertEqual(satz.normalisieren(r"28{,}27 cm"), "28,27 cm")

    def test_url_mit_unterstrich_wird_nicht_tiefgestellt(self):
        r = satz.laeufe("Quelle: https://de.wikipedia.org/wiki/Eulersche_Zahl und a_1")
        self.assertEqual([x[1] for x in r if x[3] < 0], ["1"])
        self.assertIn("Eulersche_Zahl", text_von(r))

    def test_wort_mit_unterstrich_bleibt_wort(self):
        self.assertEqual([x for x in satz.laeufe("my_variable") if x[3] != 0], [])

    def test_tiefgestellte_buchstaben(self):
        r = satz.laeufe("Σ aᵢ + xₙ")
        self.assertNotIn("?", text_von(r))
        self.assertEqual([x[1] for x in r if x[3] < 0], ["i", "n"])


class Mengenlehre(unittest.TestCase):
    def test_in_wird_zum_elementzeichen_wenn_es_mathe_ist(self):
        for roh, soll in (("x in N", "x ∈ N"), ("∃n in N, n <= 10", "∃n ∈ N, n ≤ 10"), ("{x} in M", "{x} ∈ M"), ("y in {x}", "y ∈ {x}"),
                          ("i, j in {1, 2, 3}", "i, j ∈ {1, 2, 3}"), ("z' in Z.", "z' ∈ Z."), ("(x) in M", "(x) ∈ M"), ("∀m in N ∃n in N", "∀m ∈ N ∃n ∈ N")):
            self.assertEqual(satz.normalisieren(roh), soll, roh)

    def test_deutsches_in_bleibt(self):
        for t in ("Drücken Sie die Aussage in Worte aus", "Das steht in Mathe", "Der Wert in A liegt tief", "Zahlen in N geschrieben", "in Ordnung"):
            self.assertEqual(satz.normalisieren(t), t, t)

    def test_zahlbereiche_und_seltsame_zeichen(self):
        self.assertEqual(satz.normalisieren("x ∈ ℕ, y ∈ ℤ, q ∈ ℚ, r ∈ ℝ"), "x ∈ N, y ∈ Z, q ∈ Q, r ∈ R")
        self.assertEqual(satz.normalisieren("A ⬟ B"), "A ⇔ B")


class MengenlehreMehr(unittest.TestCase):
    def test_nicht_element_und_leere_menge(self):
        self.assertEqual(satz.normalisieren("1 not in M"), "1 ∉ M")
        self.assertEqual(satz.normalisieren("{x} ∩ M != O"), "{x} ∩ M ≠ ∅")
        self.assertEqual(satz.normalisieren("A ≠ O und B = O"), "A ≠ ∅ und B = O")          # nur nach ≠ ist O die leere Menge
        self.assertEqual(satz.normalisieren("Es ist not in Ordnung"), "Es ist not in Ordnung")


class Quantoren(unittest.TestCase):
    def test_ascii_quantoren_werden_zeichen(self):
        self.assertEqual(satz.normalisieren("Vm in N En in N : m = 2n"), "∀m ∈ N ∃n ∈ N : m = 2n")
        self.assertEqual(satz.normalisieren("Vz in Z Ez' in Z : z' > z"), "∀z ∈ Z ∃z' ∈ Z : z' > z")
        self.assertEqual(satz.normalisieren("Vx ∈ R"), "∀x ∈ R")

    def test_woerter_mit_v_oder_e_bleiben(self):
        for t in ("Vielen Dank in Z", "Eine Menge in N", "Ve in Zeit", "Er in Ordnung"):
            self.assertEqual(satz.normalisieren(t), t, t)


class Schritte(unittest.TestCase):
    def test_nummerierte_schritte_bekommen_eigene_zeilen(self):
        t = "1. Für (a): Sei m = 1. Dann ist n = 1/2. 2. Für (b): wähle z' = z + 1. 3. Fertig."
        self.assertEqual(satz.schritte_trennen(t), "1. Für (a): Sei m = 1. Dann ist n = 1/2.\n2. Für (b): wähle z' = z + 1.\n3. Fertig.")

    def test_teilschritte_in_klammern(self):
        self.assertEqual(satz.schritte_trennen("(1) Sei x in M. (2) Dann gilt y = x."), "(1) Sei x in M.\n(2) Dann gilt y = x.")

    def test_normaler_text_und_zahlen_bleiben(self):
        for t in ("Das sind 2. Dann kommt es drauf an.", "Version 1.2 ist 3.5 mal besser.", "Rechne 4 + 2. 3 mal 5 ist 15.", "Eine Zeile ohne alles"):
            self.assertEqual(satz.schritte_trennen(t), t, t)

    def test_vorhandene_zeilenumbrueche_bleiben(self):
        self.assertEqual(satz.schritte_trennen("1. a.\n2. b."), "1. a.\n2. b.")


class Laeufe(unittest.TestCase):
    def test_mathe_zeichen_kommen_aus_der_symbolschrift(self):
        r = satz.laeufe("x ≈ √2 ≤ π")
        self.assertIn("F3", fonts_von(r))
        self.assertEqual(text_von(r).count("\xd6"), 1)           # √ = Symbol-Code D6
        self.assertEqual(text_von(r).count("\xbb"), 1)           # ≈ = Symbol-Code BB
        self.assertNotIn("≈", text_von(r))

    def test_umlaute_und_euro_bleiben_in_helvetica(self):
        r = satz.laeufe("Müller zahlt 4 € für ß")
        self.assertEqual(fonts_von(r), {"F1"})
        self.assertEqual(text_von(r), "Müller zahlt 4 € für ß")

    def test_hochgestellt_und_tiefgestellt(self):
        r = satz.laeufe("x^2 + a_1 + y^{n+1} + z²")
        hoch = [x for x in r if x[3] > 0]
        tief = [x for x in r if x[3] < 0]
        self.assertEqual([x[1] for x in hoch], ["2", "n+1", "2"])
        self.assertEqual([x[1] for x in tief], ["1"])
        self.assertTrue(all(x[2] < 1 for x in hoch + tief))       # kleiner gesetzt

    def test_unbekanntes_zeichen_stuerzt_nicht_ab(self):
        self.assertIn("?", text_von(satz.laeufe("ok 😀 ok")))

    def test_fett_bleibt_fett_auch_bei_symbolen(self):
        r = satz.laeufe("Lösung: x ≈ 5", fett=True)
        self.assertEqual(fonts_von(r), {"F2", "F3"})


class Breite(unittest.TestCase):
    def test_fett_ist_breiter_und_breite_waechst_mit_der_schrift(self):
        n = satz.breite(satz.laeufe("Hallo Welt"), 10)
        self.assertGreater(satz.breite(satz.laeufe("Hallo Welt", fett=True), 10), n)
        self.assertAlmostEqual(satz.breite(satz.laeufe("Hallo Welt"), 20), 2 * n, places=3)
        self.assertGreater(satz.breite(satz.laeufe("WWW"), 10), satz.breite(satz.laeufe("iii"), 10))


class Umbruch(unittest.TestCase):
    def test_zeilen_sind_nie_breiter_als_erlaubt_und_woerter_bleiben_ganz(self):
        t = "Dies ist ein längerer Satz, der mehrere Zeilen braucht, weil er nicht in eine einzige passt. " * 3
        zeilen = satz.umbrechen(t, 11, 200)
        self.assertGreater(len(zeilen), 3)
        for z in zeilen:
            self.assertLessEqual(satz.breite(z, 11), 200 + 0.01)
        self.assertEqual(" ".join(text_von(z) for z in zeilen).split(), t.split())

    def test_absaetze_und_leerzeilen_bleiben(self):
        zeilen = satz.umbrechen("Schritt 1\nSchritt 2\n\nSchritt 3", 11, 300)
        self.assertEqual([text_von(z) for z in zeilen], ["Schritt 1", "Schritt 2", "", "Schritt 3"])

    def test_zu_langes_wort_wird_hart_getrennt(self):
        zeilen = satz.umbrechen("A" * 200, 11, 100)
        self.assertGreater(len(zeilen), 1)
        for z in zeilen:
            self.assertLessEqual(satz.breite(z, 11), 100 + 0.01)

    def test_hoch_im_wort_wird_nicht_getrennt(self):
        zeilen = satz.umbrechen("x^{10} + y", 11, 300)
        self.assertEqual(len(zeilen), 1)


class Seite(unittest.TestCase):
    def test_seite_wird_zu_gueltigem_pdf_mit_symbolschrift(self):
        s = satz.Seite()
        s.rechteck(10, 10, 100, 40, "#1F3A5F", radius=6)
        s.text(20, 700, satz.laeufe("Ergebnis √16 = 4"), 12, "#222222")
        w = PdfWriter()
        satz.schreibe_seiten(w, [s])
        puffer = io.BytesIO()
        w.write(puffer)
        r = PdfReader(io.BytesIO(puffer.getvalue()))
        self.assertEqual(len(r.pages), 1)
        inhalt = r.pages[0].get_contents().get_data()
        self.assertIn(b"/F3", inhalt)
        self.assertIn(b"\xd6", inhalt)
        self.assertIn("Ergebnis", r.pages[0].extract_text())

    def test_gemessene_und_gezeichnete_breite_stimmen_ueberein(self):
        for t in ("a ⇔ b ⇒ c", "x ∈ M, y ≤ 5", "Müller zahlt 4 €", "x^2 + a_1"):
            runs = satz.laeufe(t)
            self.assertAlmostEqual(satz.Seite().text(0, 0, runs, 10, "#000000"), satz.breite(runs, 10), places=3, msg=t)

    def test_symbole_haben_luft_zu_den_nachbarn(self):
        # Betrachter zeichnen die Symbol-Glyphen unterschiedlich breit: ein Pfeil bekommt mehr als seine Tabellenbreite, damit nichts klebt.
        self.assertGreater(satz.breite(satz.laeufe("⇒"), 10), 10.5)
        self.assertGreater(satz.breite(satz.laeufe("≤"), 10), 5.49)

    def test_klammern_und_backslash_im_text_werden_maskiert(self):
        s = satz.Seite()
        s.text(10, 10, satz.laeufe("a (b) \\ c"), 10, "#000000")
        w = PdfWriter()
        satz.schreibe_seiten(w, [s])
        puffer = io.BytesIO()
        w.write(puffer)
        self.assertIn("a (b)", PdfReader(io.BytesIO(puffer.getvalue())).pages[0].extract_text())


if __name__ == "__main__":
    unittest.main()
