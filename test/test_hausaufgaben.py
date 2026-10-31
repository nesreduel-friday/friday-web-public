"""Hausaufgaben verstehen (Extras → HA) — ohne Netz, ohne Gemini.

Die Einträge haben den Aufbau echter Schulmanager-Antworten vom 16.09.2026
(Kurskürzel als Text, Zeilenumbrüche im Text), Inhalte teils erfunden.
"""

import asyncio
import os
import re
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import schulmanager as dienst
from kern.main import app
from kern.schule import hausaufgaben as ha

MITTWOCH = datetime(2026, 9, 16, 15, 0, tzinfo=ZoneInfo("Europe/Berlin"))
ROH = [
    {"date": "2026-09-16", "subject": "po281", "homework": "S. 51-52 lesen - Fragen?\nS. 57 A1-2"},
    {"date": "2026-09-16", "subject": "BI281", "homework": "Versuchsergebnisse hochladen und die Aufgaben 1 bis 3 auf Seite 89 bearbeiten"},
    {"date": "2026-09-10", "subject": "CH281", "homework": "Reaktionsgleichung \nNatronlauge und Salzsäure"},
    {"date": "2026-09-09", "subject": "ma281", "homework": "S. 31 Nr. 10"},       # 7 Tage zurück: noch drin
    {"date": "2026-09-08", "subject": "en281", "homework": "Vokabeln lernen"},    # erst mit „7 Tage mehr“
    {"date": "2026-09-03", "subject": "de281", "homework": "   "},                # leer: nie
    {"date": "kaputt", "subject": "de281", "homework": "ohne Datum"},
]


def antwort(stapel_text: str) -> dict:
    """Wie Gemini: ein Eintrag je nr — der zweite behauptet fälschlich „ja“."""
    n = len(re.findall(r"^nr \d+", stapel_text, re.M))
    return {"eintraege": [{"nr": i, "kurz": f"Aufgabe {i}", "machbar": "ja", "grund": "",
                           "teile": [{"quelle": "buch", "seiten": [89, 89, -3], "aufgaben": ["1", " "],
                                      "was": "bearbeiten", "machbar": "ja", "grund": ""},
                                     {"quelle": "quatsch", "seiten": [], "aufgaben": [], "was": "hochladen",
                                      "machbar": "nein" if i == 2 else "ja", "grund": "praktisch"}]}
                          for i in range(1, n + 1)]}


class Mit_Ordner(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(),
                                                "ISERV_USER": "", "ISERV_PASS": ""})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=MITTWOCH)
        self.uhr.start()
        self.roh = mock.patch.object(dienst, "hausaufgaben", mock.AsyncMock(return_value=ROH))
        self.roh.start()
        ha.vergessen()

    def tearDown(self):
        self.roh.stop()
        self.uhr.stop()
        self.env.stop()
        ha.vergessen()


class Deutung(unittest.TestCase):
    def test_fach_aus_kuerzel(self):
        self.assertEqual(ha.fach("BI281"), "Biologie")
        self.assertEqual(ha.fach("po281"), "Politik")
        self.assertEqual(ha.fach("spa11"), "Spanisch")
        self.assertEqual(ha.fach("XY12"), "XY12")
        self.assertEqual(ha.fach(""), "?")

    def test_bereinigen_ohne_leere_und_kaputte_neueste_zuerst(self):
        liste = ha.bereinigen(ROH + [ROH[0]])
        self.assertEqual([e["datum"] for e in liste],
                         ["2026-09-16", "2026-09-16", "2026-09-10", "2026-09-09", "2026-09-08"])
        self.assertEqual(len({e["id"] for e in liste}), 5)          # doppelt geliefert → einmal

    def test_geaenderter_text_ist_neuer_eintrag(self):
        a = ha.kennung("2026-09-16", "BI281", "S. 89")
        self.assertEqual(a, ha.kennung("2026-09-16", "bi281 ", "S.  89"))
        self.assertNotEqual(a, ha.kennung("2026-09-16", "BI281", "S. 90"))

    def test_gesamtwertung_folgt_aus_den_teilen(self):
        r = ha.pruefen(antwort("nr 1\nnr 2")["eintraege"][1])
        self.assertEqual(r["machbar"], "teilweise")                  # Modell sagte „ja“
        self.assertEqual(r["teile"][0]["seiten"], [89])
        self.assertEqual(r["teile"][0]["aufgaben"], ["1"])
        self.assertEqual(r["teile"][1]["quelle"], "sonstiges")
        nur_nein = {"kurz": "", "machbar": "ja", "grund": "",
                    "teile": [{"quelle": "buch", "machbar": "nein"}]}
        self.assertEqual(ha.pruefen(nur_nein)["machbar"], "nein")
        self.assertIsNone(ha.pruefen(None))


class Auswerten(Mit_Ordner):
    def test_einmal_auswerten_dann_aus_der_ablage(self):
        gemini = mock.AsyncMock(side_effect=lambda teile, *_: (antwort(teile[0]["text"]), "probe"))
        with mock.patch("kern.schule.briefe._gemini", gemini):
            ids = [e["id"] for e in asyncio.run(ha.liste())]
            self.assertEqual(asyncio.run(ha.auswerten(ids)), 5)
            self.assertEqual(asyncio.run(ha.auswerten(ids)), 0)
        self.assertEqual(gemini.await_count, 1)                      # ein Stapel, danach nichts mehr
        gespeichert = ha.standard().eintraege(ids)
        self.assertTrue(all(g["status"] == "verarbeitet" for g in gespeichert.values()))

    def test_fehler_zaehlt_hoch_und_gibt_irgendwann_auf(self):
        gemini = mock.AsyncMock(side_effect=RuntimeError("Gemini hat den Brief nicht verarbeitet (x: 429)"))
        with mock.patch("kern.schule.briefe._gemini", gemini):
            ids = [e["id"] for e in asyncio.run(ha.liste())]
            for _ in range(ha.MAX_VERSUCHE + 2):
                asyncio.run(ha.auswerten(ids))
        self.assertEqual(gemini.await_count, ha.MAX_VERSUCHE)
        g = ha.standard().eintraege(ids)[ids[0]]
        self.assertEqual((g["status"], g["versuche"]), ("fehler", ha.MAX_VERSUCHE))
        self.assertIn("429", g["fehler"])


class Seite(Mit_Ordner):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)
        self.gemini = mock.patch("kern.schule.briefe._gemini", mock.AsyncMock(
            side_effect=lambda teile, *_: (antwort(teile[0]["text"]), "probe")))
        self.gemini.start()

    def tearDown(self):
        self.gemini.stop()
        super().tearDown()

    def test_ohne_freigabe_nichts_geholt(self):
        d = self.c.get("/api/hausaufgaben").json()
        self.assertFalse(d["aktiv"])
        self.assertEqual(d["eintraege"], [])
        dienst.hausaufgaben.assert_not_awaited()

    def test_sieben_tage_und_sieben_mehr(self):
        konnektoren.setzen("schulmanager", True)
        konnektoren.zugang_setzen("schulmanager", "eike", "geheim")
        d = self.c.get("/api/hausaufgaben").json()
        self.assertEqual(d["tage"], 7)
        self.assertEqual([e["datum"] for e in d["eintraege"]], ["2026-09-16", "2026-09-16", "2026-09-10", "2026-09-09"])
        self.assertTrue(d["weitere"])
        self.assertEqual(d["eintraege"][0]["fach"], "Politik")
        d = self.c.get("/api/hausaufgaben?tage=14").json()
        self.assertEqual(len(d["eintraege"]), 5)
        self.assertFalse(d["weitere"])
        # Die Auswertung läuft im Hintergrund an; spätestens beim nächsten Abruf ist sie da.
        asyncio.run(ha.auswerten([e["id"] for e in d["eintraege"]]))
        d = self.c.get("/api/hausaufgaben?tage=14").json()
        self.assertTrue(all(e["auswertung"] for e in d["eintraege"]))
        self.assertEqual(dienst.hausaufgaben.await_count, 1)         # Liste fünf Minuten zwischengespeichert

    def test_abgewiesene_anmeldung_wird_gemeldet(self):
        konnektoren.setzen("schulmanager", True)
        konnektoren.zugang_setzen("schulmanager", "eike", "falsch")
        dienst.hausaufgaben.side_effect = dienst.AnmeldeFehler("IServ hat diese Zugangsdaten abgewiesen.")
        r = self.c.get("/api/hausaufgaben")
        self.assertEqual(r.status_code, 502)
        self.assertIn("abgewiesen", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
