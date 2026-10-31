"""Hinweise der Denkrunde: Grenzen, Dubletten, Karte oder Push — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.denken import buch, hinweis
from kern.dienste import benachrichtigungen

MITTWOCH = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))


class MitHinweisen(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        uhr = mock.patch("kern.config.jetzt", return_value=MITTWOCH)
        uhr.start()
        self.addCleanup(uhr.stop)
        self.karte = mock.patch("kern.dashboard.friday_karten.legen", new=mock.AsyncMock(return_value={"id": "f1"}))
        self.karte_mock = self.karte.start()
        self.addCleanup(self.karte.stop)
        self.push = mock.patch.object(benachrichtigungen, "senden",
                                      return_value={"gesendet": 1, "geraete": 1, "abgemeldet": 0, "unterdrueckt": ""})
        self.push_mock = self.push.start()
        self.addCleanup(self.push.stop)
        self.b = buch.standard()
        self.r = self.b.runde_beginnen()
        self.auto = True
        self.vorbereiten(self.r)

    def vorbereiten(self, runde, luecke=False):
        """Wie eine echte Runde: erst im Gedächtnis nachgesehen, ggf. eine belegte Lücke festgehalten."""
        self.b.aufruf_merken(runde, "rueckblick")
        if luecke:
            self.b.gedanke_festhalten(runde, "luecke", f"Widerspruch{runde}zwischen Kalender{runde} und Erinnerungen{runde}",
                                      ["Kalender", "Erinnerungen"])

    def geben(self, titel="Termin kollidiert", text="Mathe-Klausur und Zahnarzt am Montag um 10", w="normal", runde=None):
        rid = self.r if runde is None else runde
        if self.auto and not self.b.gedaechtnis_geprueft(rid):      # neue Runden wie eine echte: erst im Gedächtnis nachgesehen
            self.vorbereiten(rid)
        return asyncio.run(hinweis.geben(self.b, rid, titel, text, w))


class Wege(MitHinweisen):
    def test_normal_ist_nur_eine_karte(self):
        e = self.geben()
        self.assertEqual((e["ok"], e["weg"]), (True, "karte"))
        self.karte_mock.assert_awaited_once()
        self.assertEqual(self.karte_mock.await_args.args[0], "Gedanke: Termin kollidiert")
        self.push_mock.assert_not_called()

    def test_hoch_geht_als_push_mit_der_art_nachdenken(self):
        self.vorbereiten(self.r, luecke=True)
        e = self.geben(w="hoch")
        self.assertEqual((e["ok"], e["weg"]), (True, "push"))
        self.push_mock.assert_called_once()
        self.assertEqual(self.push_mock.call_args.args[0], "nachdenken")
        self.assertFalse(self.push_mock.call_args.kwargs.get("dringend", False))
        self.karte_mock.assert_not_awaited()

    def test_unbekannte_wichtigkeit_gilt_als_normal(self):
        self.assertEqual(self.geben(w="sehr dringend")["weg"], "karte")

    def test_art_nachdenken_ist_bekannt_und_standardmaessig_an(self):
        art = next(a for a in benachrichtigungen.ARTEN if a["id"] == "nachdenken")
        self.assertTrue(art["standard"])
        self.assertIn("nachdenken", benachrichtigungen.KENNT)


    def test_normaler_hinweis_wird_auch_ein_anliegen(self):
        from kern.anliegen import fach
        self.geben("Frist Anmeldung", "Die Anmeldung endet übermorgen um Mitternacht")
        a = fach.standard().offene("eike")
        self.assertEqual((a[0]["quelle"], a[0]["dringlichkeit"], a[0]["titel"]), ("hinweis", "normal", "Frist Anmeldung"))

    def test_hoher_hinweis_wird_ein_hohes_anliegen(self):
        from kern.anliegen import fach
        self.vorbereiten(self.r, luecke=True)
        self.geben("Terminkollision", "Klausur und Zahnarzt am selben Tag um zehn", "hoch")
        self.assertEqual(fach.standard().offene("eike")[0]["dringlichkeit"], "hoch")

    def test_abgelehnter_hinweis_legt_kein_anliegen_an(self):
        from kern.anliegen import fach
        self.auto = False                                    # Runde ohne Gedächtnis-Abfrage → abgelehnt
        e = self.geben(runde=self.b.runde_beginnen())
        self.assertFalse(e["ok"])
        self.assertEqual(fach.standard().offene("eike"), [])

    def test_kaputtes_fach_laesst_den_hinweis_gelingen(self):
        with mock.patch("kern.anliegen.fach.standard", side_effect=RuntimeError("kaputt")):
            self.assertTrue(self.geben()["ok"])


class Grenzen(MitHinweisen):
    def test_nur_ein_hinweis_je_runde(self):
        self.assertTrue(self.geben()["ok"])
        zweiter = self.geben("Frist", "Anmeldung endet übermorgen um Mitternacht")
        self.assertFalse(zweiter["ok"])
        self.assertIn("Runde", zweiter["grund"])
        self.assertEqual(self.karte_mock.await_count, 1)

    def test_hoechstens_drei_je_tag(self):
        themen = [("Frist Anmeldung", "Die Anmeldung zur Studienfahrt endet übermorgen"),
                  ("Wetter Ausflug", "Am Samstag regnet es den ganzen Tag stark"),
                  ("Geburtstag Kontakt P", "Kontakt P hat nächste Woche Dienstag Geburtstag"),
                  ("Bus fällt aus", "Linie 100 fällt morgen früh wegen Bauarbeiten komplett aus")]
        erg = [self.geben(t, x, runde=self.b.runde_beginnen()) for t, x in themen]
        self.assertEqual([e["ok"] for e in erg], [True, True, True, False])
        self.assertIn("Heute", erg[-1]["grund"])

    def test_dublette_binnen_48_stunden_wird_verworfen(self):
        self.assertTrue(self.geben()["ok"])
        e = self.geben(runde=self.b.runde_beginnen())
        self.assertFalse(e["ok"])
        self.assertIn("schon", e["grund"])

    def test_dieselbe_kollision_in_anderen_worten_ist_eine_dublette(self):
        # Echter Fall (08.10.2026): zwei Pushes zu „Pizza essen“, Titel und Text nur anders formuliert.
        self.vorbereiten(self.r, luecke=True)
        r2 = self.b.runde_beginnen()
        self.vorbereiten(r2, luecke=True)
        self.assertTrue(self.geben("Terminkollision: Pizza essen",
                                   "Deine Erinnerung 'Pizza essen' ist heute um 11:08 Uhr eingestellt – genau während deiner "
                                   "Deutsch-Arbeit in der 4. Stunde (bis 11:35 Uhr).", "hoch")["ok"])
        e = self.geben("Terminkollision: Pizza essen während Deutsch-Arbeit",
                       "Die offene Erinnerung für 'Pizza essen' ist heute um 11:08 Uhr angesetzt und kollidiert direkt mit der "
                       "laufenden Deutsch-Arbeit in der 4. Stunde (bis 11:35 Uhr).", "hoch", runde=r2)
        self.assertFalse(e["ok"])
        self.assertEqual(self.push_mock.call_count, 1)

    def test_andere_kollision_mit_gleichem_titelanfang_ist_keine_dublette(self):
        self.assertTrue(self.geben("Terminkollision: Mathe-Klausur", "Klausur am Montag um 10 und Zahnarzt zur selben Zeit")["ok"])
        self.assertTrue(self.geben("Terminkollision: Konzert", "Das Konzert am Samstag fällt auf den Geburtstag von Kontakt P",
                                   runde=self.b.runde_beginnen())["ok"])

    def test_zu_langer_hinweis_wird_abgelehnt_nicht_abgeschnitten(self):
        e = self.geben("Titel " * 30, "Kurzer Text")
        self.assertFalse(e["ok"])
        self.assertIn("Zu lang", e["grund"])
        e = self.geben("Kurzer Titel", "wort " * 100)
        self.assertFalse(e["ok"])
        self.assertEqual(self.push_mock.call_count + self.karte_mock.await_count, 0)

    def test_leerer_titel_wird_abgelehnt(self):
        self.assertFalse(self.geben("  ", "x")["ok"])

    def test_fehler_bei_der_zustellung_zaehlt_nicht(self):
        self.karte_mock.side_effect = RuntimeError("kaputt")
        e = self.geben()
        self.assertFalse(e["ok"])
        self.assertEqual(self.b.hinweise_heute(), 0)


class Gegenpruefung(MitHinweisen):
    def setUp(self):
        super().setUp()
        self.auto = False
        self.r2 = self.b.runde_beginnen()          # eine Runde OHNE Gedächtnis-Abfrage

    def test_ohne_gedaechtnisabfrage_kein_hinweis(self):
        e = self.geben(runde=self.r2)
        self.assertFalse(e["ok"])
        self.assertIn("Gedächtnis", e["grund"])
        self.karte_mock.assert_not_awaited()
        self.push_mock.assert_not_called()

    def test_kalenderabfrage_zaehlt_nicht_als_gegenpruefung(self):
        self.b.aufruf_merken(self.r2, "kalender")
        self.assertFalse(self.geben(runde=self.r2)["ok"])

    def test_nach_der_abfrage_geht_es(self):
        self.vorbereiten(self.r2)
        self.assertTrue(self.geben(runde=self.r2)["ok"])

    def test_hoch_ohne_belegte_luecke_wird_zur_karte(self):
        e = self.geben(w="hoch")                   # setUp: geprüft, aber keine Lücke
        self.assertEqual((e["ok"], e["weg"]), (True, "karte"))
        self.push_mock.assert_not_called()

    def test_hoch_mit_belegter_luecke_bleibt_ein_push(self):
        self.vorbereiten(self.r2, luecke=True)
        self.assertEqual(self.geben(w="hoch", runde=self.r2)["weg"], "push")

    def test_echter_pizza_fall_ohne_pruefung_waere_abgelehnt_worden(self):
        # 08.10.2026: Eike hatte die Erinnerung selbst bestellt; die Runde hat das nie nachgesehen.
        e = self.geben("Terminkollision: Pizza essen", "Erinnerung um 11:08 kollidiert mit der Deutsch-Arbeit",
                       "hoch", runde=self.r2)
        self.assertFalse(e["ok"])
        self.push_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
