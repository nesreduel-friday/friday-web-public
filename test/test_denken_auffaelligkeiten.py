"""Abschnitt „Auffälligkeiten“: nur Zahlen und Statuswörter, jede Quelle einzeln — ohne Netz."""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from kern import schluessel
from kern.denken import auffaelligkeiten, buch, kontext
from kern.werkbank import speicher

MITTWOCH = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))


class Auffaelligkeiten(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        env.start()
        self.addCleanup(env.stop)
        uhr = mock.patch("kern.config.jetzt", return_value=MITTWOCH)
        uhr.start()
        self.addCleanup(uhr.stop)
        schluessel._zaehler = None
        self.addCleanup(setattr, schluessel, "_zaehler", None)

    def test_leer_wenn_alles_ruhig(self):
        self.assertEqual(auffaelligkeiten.text(), "")

    def test_gemini_limits_als_satz(self):
        with mock.patch("kern.schluessel.zaehler_tage", return_value={"2026-10-07": {"Haupt": {
                "m1": {"ok": 40, "limit": 3}, "m2": {"limit": 2, "fehler": 1}}, "Ruhig": {"m1": {"ok": 5}}}}):
            t = auffaelligkeiten.text()
        self.assertIn("Schlüssel Haupt: 5 Limit-Antworten (429), 1 Serverfehler", t)
        self.assertNotIn("Ruhig", t)

    def test_nur_zahlen_und_statuswoerter(self):
        f = speicher.standard()
        v = f.anlegen("Geheimer Titel Kontakt F Mustermann", "fehler", "denkrunde", beleg="Mailtext vom Chef")
        f.status_setzen(v["id"], "fehler")
        d = Path(self.ordner) / "snapchat"
        d.mkdir(parents=True, exist_ok=True)
        (d / "ergebnisse.jsonl").write_text(json.dumps(
            {"zeit": time.time(), "ergebnis": "aufgegeben", "empfaenger": "Kontakt F Mustermann", "grund": "Text vom Handy"}) + "\n")
        t = auffaelligkeiten.text()
        self.assertIn("1 Vorhaben im Zustand „fehler“", t)
        self.assertIn("1 Sendeaufträge in 24 Stunden aufgegeben", t)
        for verboten in ("Geheimer", "Mustermann", "Mailtext", "Chef", "Handy"):
            self.assertNotIn(verboten, t)

    def test_eine_quelle_fehlt_der_rest_bleibt(self):
        with mock.patch("kern.schluessel.zaehler_tage", side_effect=RuntimeError("weg")), \
             mock.patch("kern.werkbank.speicher.standard", return_value=mock.Mock(
                 offene=lambda: [{"status": "fehler"}])):
            t = auffaelligkeiten.text()
        self.assertIn("1 Vorhaben", t)

    def test_hoechstens_zehn_zeilen(self):
        viele = {"2026-10-07": {f"S{n}": {"m": {"limit": 1}} for n in range(15)}}
        with mock.patch("kern.schluessel.zaehler_tage", return_value=viele):
            t = auffaelligkeiten.text()
        self.assertEqual(t.count("\n- "), 10 - 1 + 1)

    def test_steht_im_kontext(self):
        with mock.patch("kern.denken.auffaelligkeiten.text", return_value="Auffälligkeiten an Friday selbst:\n- X"):
            self.assertIn("Auffälligkeiten an Friday selbst", kontext.baue(buch.standard()))

    def test_nicht_im_kontext_fuer_fremdfreie_runde(self):
        with mock.patch("kern.denken.auffaelligkeiten.text", return_value="Auffälligkeiten an Friday selbst:\n- X"):
            self.assertIn("Auffälligkeiten", kontext.baue(buch.standard(), ohne_fremd=True))


if __name__ == "__main__":
    unittest.main()
