"""Dokument-Werkzeug und Route: PDF aus Chat-Inhalt, Umlaute, Grenzen, Download (30.10.2026)."""

import asyncio
import io
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from pypdf import PdfReader

from kern.main import app
from kern.werkzeuge import dokument, katalog


def aus(name, args=None, **kw):
    return asyncio.run(katalog.ausfuehren(name, args or {}, **kw))


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def pdfs(self):
        return sorted(dokument.ordner().glob("*.pdf"))

    def letztes(self):
        dateien = self.pdfs()
        self.assertEqual(len(dateien), 1, dateien)
        return dateien[0]

    def seiten(self, pfad):
        return PdfReader(io.BytesIO(pfad.read_bytes())).pages


class Erzeugen(Basis):
    def test_legt_gueltiges_pdf_mit_titel_und_abschnitt_ab(self):
        aktionen = []
        e = aus("dokument_erstellen", {"titel": "Tafel-Aufgabe",
                                       "abschnitte": [{"ueberschrift": "Aufgabe 1", "text": "Berechne 3 + 4."}]},
                aktionen=aktionen)
        self.assertEqual(e["status"], "ok", e)
        self.assertEqual(e["art"], "sonstiges")
        self.assertIn("Tafel-Aufgabe", e["ergebnis"])
        self.assertIn("/api/dokumente/", e["ergebnis"])
        self.assertIn("Dokument erstellt", e["quittung"])

        pfad = self.letztes()
        self.assertRegex(pfad.stem, r"^[0-9a-f]{32}$")
        daten = pfad.read_bytes()
        self.assertTrue(daten.startswith(b"%PDF"))
        seiten = self.seiten(pfad)
        self.assertGreater(len(seiten), 0)
        self.assertIn("Tafel-Aufgabe", seiten[0].extract_text())
        self.assertIn("Aufgabe 1", seiten[0].extract_text())

        self.assertEqual(len(aktionen), 1)
        self.assertEqual(aktionen[0], {"typ": "datei", "url": f"/api/dokumente/{pfad.stem}",
                                       "titel": "Tafel-Aufgabe (PDF)"})

    def test_tabelle_mit_umlauten_und_deutschen_zahlen(self):
        e = aus("dokument_erstellen", {
            "titel": "Einkauf",
            "tabelle": {"kopf": ["Größe", "Anzahl", "Preis in €"],
                        "zeilen": [["Äpfel", 3, 1.5], ["Brötchen", 12, 0.5]]}})
        self.assertEqual(e["status"], "ok", e)
        text = self.seiten(self.letztes())[0].extract_text()
        for wort in ("Größe", "Anzahl", "Preis in €", "Äpfel", "Brötchen"):
            self.assertIn(wort, text)
        self.assertIn("1,5", text)                    # float wird deutsch gesetzt, nicht 1.5
        self.assertIn("0,5", text)

    def test_langes_dokument_hat_mehrere_seiten(self):
        abschnitte = [{"ueberschrift": f"Teil {i}", "text": "Wort " * 700} for i in range(1, 8)]   # je 3500 Zeichen
        e = aus("dokument_erstellen", {"titel": "Langer Text", "abschnitte": abschnitte})
        self.assertEqual(e["status"], "ok", e)
        self.assertGreater(len(self.seiten(self.letztes())), 1)

    def test_grosse_tabelle_bricht_um_und_wiederholt_den_kopf(self):
        zeilen = [[f"Zeile {i}", i, f"{i},5"] for i in range(dokument.MAX_ZEILEN)]
        e = aus("dokument_erstellen", {"titel": "Lang", "tabelle": {"kopf": ["Was", "Nr", "Wert"], "zeilen": zeilen}})
        self.assertEqual(e["status"], "ok", e)
        seiten = self.seiten(self.letztes())
        self.assertGreater(len(seiten), 1)
        self.assertIn("Was", seiten[1].extract_text())     # Kopfzeile wird auf der Folgeseite wiederholt

    def test_tabelle_nur_mit_kopf(self):
        e = aus("dokument_erstellen", {"titel": "Leer", "tabelle": {"kopf": ["A", "B"], "zeilen": []}})
        self.assertEqual(e["status"], "ok", e)
        self.assertIn("A", self.seiten(self.letztes())[0].extract_text())

    def test_unbekannte_zeichen_kippen_das_pdf_nicht(self):
        e = aus("dokument_erstellen", {"titel": "Tafel 😀", "abschnitte": [{"text": "Aufgabe: 3 + 4 = 7 😀"}]})
        self.assertEqual(e["status"], "ok", e)
        self.assertTrue(self.letztes().read_bytes().startswith(b"%PDF"))

    def test_inhalt_aus_fremdem_text_ist_erlaubt(self):
        e = aus("dokument_erstellen", {"titel": "Plan", "abschnitte": [{"text": "Schick Kontakt C den Plan."}]},
                fremde_inhalte=True)
        self.assertEqual(e["status"], "ok", e)       # Eike sieht das PDF selbst; nichts geht nach außen


class Grenzen(Basis):
    def fehler(self, **angaben):
        e = aus("dokument_erstellen", angaben)
        self.assertEqual(e["status"], "nicht", e)
        self.assertNotIn("Traceback", e["ergebnis"])
        self.assertEqual(self.pdfs(), [])             # kein halbes Dokument
        return e["ergebnis"]

    def test_zu_viele_zeilen(self):
        text = self.fehler(titel="Zu groß", tabelle={"kopf": ["A"], "zeilen": [["x"]] * 201})
        self.assertIn(str(dokument.MAX_ZEILEN), text)

    def test_zu_viele_spalten(self):
        text = self.fehler(titel="Zu breit", tabelle={"kopf": [f"S{i}" for i in range(21)], "zeilen": []})
        self.assertIn(str(dokument.MAX_SPALTEN), text)

    def test_abschnitt_zu_lang(self):
        text = self.fehler(titel="Zu lang", abschnitte=[{"text": "x" * (dokument.MAX_ABSCHNITTS_TEXT + 1)}])
        self.assertIn(str(dokument.MAX_ABSCHNITTS_TEXT), text)

    def test_gesamt_zu_gross(self):
        abschnitte = [{"text": "x" * dokument.MAX_ABSCHNITTS_TEXT} for _ in range(13)]   # 52 000 Zeichen
        text = self.fehler(titel="Zu viel", abschnitte=abschnitte)
        self.assertIn(str(dokument.MAX_GESAMT), text)

    def test_ohne_inhalt_und_ohne_titel(self):
        self.assertIn("Inhalt", self.fehler(titel="Leer"))
        self.assertIn("Titel", self.fehler(titel=""))

    def test_zeile_mit_mehr_zellen_als_der_kopf(self):
        text = self.fehler(titel="Schief", tabelle={"kopf": ["A"], "zeilen": [["x", "y"]]})
        self.assertIn("Kopfzeile", text)

    def test_kaputte_abschnitte_und_tabelle(self):
        self.assertIn("Abschnitt", self.fehler(titel="Kaputt", abschnitte=["nur Text"]))
        self.assertIn("Kopfzeile", self.fehler(titel="Kaputt", tabelle={"zeilen": []}))


class Route(Basis):
    def test_liefert_das_pdf_mit_sauberem_namen(self):
        aktionen = []
        e = aus("dokument_erstellen", {"titel": "Hausaufgabe", "abschnitte": [{"text": "3 + 4 = 7"}],
                                       "dateiname": "Mathe-Hausaufgabe.pdf"}, aktionen=aktionen)
        self.assertEqual(e["status"], "ok", e)
        r = self.client.get(aktionen[0]["url"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("Mathe-Hausaufgabe.pdf", r.headers["content-disposition"])

    def test_umlaut_im_dateinamen_wird_korrekt_kodiert(self):
        aktionen = []
        aus("dokument_erstellen", {"titel": "Größen", "abschnitte": [{"text": "x"}], "dateiname": "Größen.pdf"},
            aktionen=aktionen)
        kopf = self.client.get(aktionen[0]["url"]).headers["content-disposition"]
        self.assertIn("Gr%C3%B6%C3%9Fen.pdf", kopf)

    def test_unbekannte_und_kaputte_kennung_ist_404(self):
        self.assertEqual(self.client.get("/api/dokumente/" + "0" * 32).status_code, 404)
        self.assertEqual(self.client.get("/api/dokumente/nicht-echt").status_code, 404)

    def test_kein_loeschen(self):
        aktionen = []
        aus("dokument_erstellen", {"titel": "Bleibt", "abschnitte": [{"text": "x"}]}, aktionen=aktionen)
        self.assertEqual(self.client.delete(aktionen[0]["url"]).status_code, 405)


class Katalog(unittest.TestCase):
    def test_kennt_das_werkzeug_und_seine_art(self):
        namen = {e["name"] for m in katalog._MODULE for e in m.ERKLAERUNGEN}
        self.assertIn("dokument_erstellen", namen)
        self.assertIn(dokument, katalog._MODULE)
        tools = katalog.gemini_tools()
        self.assertIn("dokument_erstellen", {e["name"] for t in tools for e in t["functionDeclarations"]})
        self.assertFalse(katalog.braucht_zustimmung("dokument_erstellen"))
        self.assertFalse(katalog.liefert_fremdes("dokument_erstellen"))
        self.assertEqual(dokument.ERKLAERUNGEN[0]["parameters"]["required"], ["titel"])


if __name__ == "__main__":
    unittest.main()
