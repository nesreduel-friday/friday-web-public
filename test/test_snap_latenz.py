import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host-helfer"))
import snap_latenz  # noqa: E402


class LatenzTest(unittest.TestCase):
    def test_auswertung(self):
        zeilen = [{"art": "text", "ergebnis": "gesendet", "zeit": 130.0, "gelegt": 120.0, "ausloeser": 100.0},
                  {"art": "text", "ergebnis": "gesendet", "zeit": 250.0, "gelegt": 235.0, "ausloeser": 200.0},
                  {"art": "text", "ergebnis": "gesendet", "zeit": 300.0, "gelegt": 290.0, "ausloeser": 280.0},
                  {"art": "text", "ergebnis": "verworfen", "zeit": 400.0, "gelegt": 390.0, "ausloeser": 380.0},
                  {"art": "text", "ergebnis": "gesendet", "zeit": 500.0}]          # ohne Ausloeser: zählt nicht
        r = snap_latenz.auswerten(zeilen)
        self.assertEqual(r["anzahl"], 3)
        self.assertEqual(r["median_s"], 30.0)         # 30, 50, 20 → Median 30
        self.assertEqual(r["sende_median_s"], 10.0)   # 10, 15, 10 → Median 10
        self.assertEqual(snap_latenz.auswerten([])["anzahl"], 0)

    def test_sende_zeit_auch_ohne_ausloeser(self):
        zeilen = [{"art": "text", "ergebnis": "gesendet", "zeit": 130.0, "gelegt": 120.0},
                  {"art": "text", "ergebnis": "gesendet", "zeit": 250.0, "gelegt": 230.0}]
        r = snap_latenz.sende_zeiten(zeilen)
        self.assertEqual(r["anzahl"], 2)
        self.assertEqual(r["median_s"], 15.0)
