"""Takt, Schalter und Graphiti-Brücke der Denkrunde — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from kern.denken import brucke, buch, lauf
from kern.planer import briefing

BERLIN = ZoneInfo("Europe/Berlin")
MITTWOCH = datetime(2026, 10, 7, 12, 0, tzinfo=BERLIN)


class MitLauf(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.jetzt = MITTWOCH
        uhr = mock.patch("kern.config.jetzt", side_effect=lambda: self.jetzt)
        uhr.start()
        self.addCleanup(uhr.stop)
        self.b = buch.standard()


class Faellig(MitLauf):
    def setUp(self):
        super().setUp()
        n = mock.patch("kern.denken.neues.seit", return_value=["gespraeche"])   # Gate hier offen; eigene Tests unten
        n.start()
        self.addCleanup(n.stop)

    def f(self, jetzt=None, an=True, aktiv=None):
        return lauf.faellig(self.b, jetzt or self.jetzt, an, aktiv)

    def test_ohne_vorherige_runde_ist_es_faellig(self):
        self.assertEqual(self.f(), "")

    def test_aus(self):
        self.assertEqual(self.f(an=False), "aus")

    def test_nachts_nicht(self):
        for stunde in (0, 3, 6, 23):
            self.assertEqual(self.f(self.jetzt.replace(hour=stunde)), "nacht", stunde)
        for stunde in (7, 12, 22):
            self.assertEqual(self.f(self.jetzt.replace(hour=stunde)), "", stunde)

    def test_takt_kommt_aus_der_datenbank_nicht_aus_dem_speicher(self):
        self.b.runde_beginnen()
        self.assertEqual(self.f(), "takt")
        self.assertEqual(self.f(self.jetzt + timedelta(minutes=120)), "")
        self.assertEqual(self.f(self.jetzt + timedelta(minutes=60)), "takt")

    def test_takt_ist_einstellbar(self):
        self.b.runde_beginnen()
        with mock.patch.dict(os.environ, {"NACHDENKEN_TAKT_MIN": "60"}):
            self.assertEqual(self.f(self.jetzt + timedelta(minutes=61)), "")
        with mock.patch.dict(os.environ, {"NACHDENKEN_TAKT_MIN": "quatsch"}):
            self.assertEqual(lauf.takt_min(), 120)

    def test_tageslimit(self):
        for _ in range(lauf.TAGESLIMIT):
            self.b.runde_beginnen()
        self.assertEqual(self.f(self.jetzt + timedelta(hours=3)), "limit")

    def test_nicht_waehrend_eike_gerade_spricht(self):
        self.assertEqual(self.f(aktiv=self.jetzt - timedelta(minutes=2)), "gespraech")
        self.assertEqual(self.f(aktiv=self.jetzt - timedelta(minutes=lauf.RUHE_NACH_GESPRAECH_MIN + 1)), "")


class Gate(MitLauf):
    def test_nichts_neu_ueberspringt_die_runde(self):
        self.b.runde_beginnen()
        spaeter = self.jetzt + timedelta(minutes=120)
        with mock.patch("kern.denken.neues.seit", return_value=[]):
            self.assertEqual(lauf.faellig(self.b, spaeter, True, None), "nichts_neu")
        with mock.patch("kern.denken.neues.seit", return_value=["gespraeche"]):
            self.assertEqual(lauf.faellig(self.b, spaeter, True, None), "")

    def test_gescheiterte_runde_zaehlt_nicht_als_gesehen(self):
        gut = self.b.runde_beginnen()
        self.b._schreiben("UPDATE runden SET start='2026-10-07T07:00:00+02:00' WHERE id=?", (gut,))
        kaputt = self.b.runde_beginnen()
        self.b.runde_beenden(kaputt, "", "m", "", fehler="Gemini überlastet")
        gesehen = []
        with mock.patch("kern.denken.neues.seit", side_effect=lambda b, s: gesehen.append(s) or []):
            lauf.faellig(self.b, self.jetzt + timedelta(minutes=120), True, None)
        self.assertEqual(gesehen[0].hour, 7)     # ab der letzten GUTEN Runde, nicht ab der gescheiterten

    def test_knopf_ignoriert_das_gate(self):
        async def durchlauf():
            with mock.patch("kern.denken.neues.seit", return_value=[]), \
                 mock.patch("kern.denken.runde.runde", new=mock.AsyncMock(return_value={"ok": True})) as r:
                await lauf.jetzt_nachdenken(self.b)
                return r.await_count
        self.assertEqual(asyncio.run(durchlauf()), 1)


class Einstellung(MitLauf):
    def test_standard_ist_an(self):
        self.assertTrue(lauf.ist_an())

    def test_ausschalten_beruehrt_andere_einstellungen_nicht(self):
        briefing.einstellen(an=True)
        lauf.einstellen(False)
        self.assertFalse(lauf.ist_an())
        self.assertTrue(briefing.ist_an())
        lauf.einstellen(True)
        self.assertTrue(lauf.ist_an())


class Schritt(MitLauf):
    def setUp(self):
        super().setUp()
        p = mock.patch("kern.denken.lauf.denkrunde.runde", new=mock.AsyncMock(return_value={"ok": True}))
        self.runde = p.start()
        self.addCleanup(p.stop)
        for name in ("einspeisen_offene", "nachsehen"):
            q = mock.patch(f"kern.denken.lauf.brucke.{name}", new=mock.AsyncMock())
            q.start()
            self.addCleanup(q.stop)
        a = mock.patch("kern.denken.lauf._letzte_aktivitaet", return_value=None)
        a.start()
        self.addCleanup(a.stop)

    def schritt(self):
        return asyncio.run(lauf.schritt(self.b))

    def test_faellig_startet_eine_runde(self):
        self.assertEqual(self.schritt(), "")
        self.runde.assert_awaited_once()

    def test_neustart_mitten_am_tag_macht_keine_doppelrunde(self):
        self.b.runde_beginnen()
        self.assertEqual(self.schritt(), "takt")
        self.runde.assert_not_awaited()

    def test_fehler_in_der_bruecke_stoert_den_schritt_nicht(self):
        with mock.patch("kern.denken.lauf.brucke.einspeisen_offene", new=mock.AsyncMock(side_effect=RuntimeError("x"))):
            self.assertEqual(self.schritt(), "")

    def test_zweite_runde_waehrend_eine_laeuft_wird_abgewiesen(self):
        async def zwei():
            lauf._laeuft = True
            try:
                return await lauf.jetzt_nachdenken(self.b)
            finally:
                lauf._laeuft = False
        self.assertFalse(asyncio.run(zwei())["ok"])
        self.runde.assert_not_awaited()

    def test_ist_in_main_gestartet(self):
        quelle = (Path(__file__).resolve().parents[1] / "kern" / "main.py").read_text(encoding="utf-8")
        self.assertIn('_starten("denken"', quelle)

    def test_schritt_laesst_alte_faeden_ruhen(self):
        f = self.b.faden_anlegen("Mathe Abitur", "Wann beginnt die Abiturvorbereitung", quelle="eike")["id"]
        self.jetzt = MITTWOCH + timedelta(days=15)
        self.schritt()
        self.assertEqual(self.b.faden(f)["status"], "ruht")

    def test_fehler_beim_ruhenlassen_stoert_den_schritt_nicht(self):
        with mock.patch.object(self.b, "faeden_ruhen_lassen", side_effect=RuntimeError("x")):
            self.assertEqual(self.schritt(), "")


class Bruecke(MitLauf):
    def setUp(self):
        super().setUp()
        self.r = self.b.runde_beginnen()
        self.b.gedanke_festhalten(self.r, "erkenntnis", "Mathe läuft gut", ["Noten"], "belegt")
        self.b.gedanke_festhalten(self.r, "luecke", "Mail nennt anderen Termin", ["Mail", "Kalender"], fremd=True)
        self.b.runde_beenden(self.r, "", "m", "", "")
        p = mock.patch("kern.gedaechtnis.graph.pausiert", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.beschaeftigt = mock.patch("kern.denken.brucke._graph_beschaeftigt", return_value=False)
        self.beschaeftigt_mock = self.beschaeftigt.start()
        self.addCleanup(self.beschaeftigt.stop)

    def einspeisen(self, ok=True):
        with mock.patch("kern.gedaechtnis.graph.einspeisen", new=mock.AsyncMock(return_value=ok)) as e:
            asyncio.run(brucke.einspeisen_offene(self.b))
        return e

    def test_episode_traegt_kennzeichnung_und_eigene_anweisungen(self):
        e = self.einspeisen()
        name, text, beschreibung, zeit = e.await_args.args[:4]
        self.assertEqual(name, f"gedanken-runde-{self.r}")
        self.assertIn("Mail nennt anderen Termin", text)
        self.assertNotIn("Mathe läuft gut", text)
        self.assertIn("aus fremdem Text", text)
        self.assertIn("Friday", beschreibung)
        self.assertIn("keine Aussage von Eike", beschreibung)
        self.assertIn("Friday", e.await_args.kwargs["anweisungen"])
        self.assertNotEqual(e.await_args.kwargs["anweisungen"], __import__("kern.gedaechtnis.graph", fromlist=["x"]).ANWEISUNGEN)
        self.assertEqual(self.b.runden_fuer_graph(), [])

    def test_abgelehnt_bleibt_fuer_spaeter_offen(self):
        self.einspeisen(ok=False)
        self.assertEqual([r["id"] for r in self.b.runden_fuer_graph()], [self.r])

    def test_pause_von_graphiti_schickt_nichts(self):
        with mock.patch("kern.gedaechtnis.graph.pausiert", return_value=True), \
             mock.patch("kern.gedaechtnis.graph.einspeisen", new=mock.AsyncMock()) as e:
            asyncio.run(brucke.einspeisen_offene(self.b))
        e.assert_not_awaited()

    def test_solange_gespraeche_auf_graphiti_warten_schickt_die_bruecke_nichts(self):
        # Am 08.10.2026 warteten 161 Gespräche und Graphiti war am Tageslimit: Gedanken dürfen sie nicht überholen.
        self.beschaeftigt_mock.return_value = True
        with mock.patch("kern.gedaechtnis.graph.einspeisen", new=mock.AsyncMock()) as e:
            asyncio.run(brucke.einspeisen_offene(self.b))
        e.assert_not_awaited()
        self.assertEqual([r["id"] for r in self.b.runden_fuer_graph()], [self.r])

    def test_nur_graphwuerdige_gedanken_stehen_in_der_episode(self):
        e = self.einspeisen()
        text = e.await_args.args[1]
        self.assertIn("Mail nennt anderen Termin", text)      # luecke
        self.assertNotIn("Mathe läuft gut", text)             # reine Erkenntnis bleibt lokal

    def test_runde_nur_mit_erkenntnissen_wird_nicht_gesendet(self):
        rid = self.b.runde_beginnen()
        self.b.gedanke_festhalten(rid, "erkenntnis", "Nur lokal über Chemie", ["Notiz"])
        self.b.runde_beenden(rid, "", "m", "", "")
        self.b.graph_setzen(self.r, "angekommen")             # die erste (graphwürdige) Runde ist erledigt
        with mock.patch("kern.gedaechtnis.graph.einspeisen", new=mock.AsyncMock()) as e:
            asyncio.run(brucke.einspeisen_offene(self.b))
        e.assert_not_awaited()

    def nachsehen(self, antwort, versuche=1):
        self.b.graph_setzen(self.r, "gesendet")
        for _ in range(versuche - 1):
            self.b.graph_setzen(self.r, "gesendet")
        self.jetzt = MITTWOCH + timedelta(minutes=11)
        with mock.patch("kern.gedaechtnis.graph.angekommen", new=mock.AsyncMock(return_value=antwort)):
            asyncio.run(brucke.nachsehen(self.b))
        return self.b._alle("SELECT graph_status FROM runden WHERE id=?", (self.r,))[0]["graph_status"]

    def test_angekommen(self):
        self.assertEqual(self.nachsehen(True), "angekommen")

    def test_verloren_wird_erneut_geschickt(self):
        self.assertIsNone(self.nachsehen(False))

    def test_nach_drei_versuchen_aufgegeben(self):
        self.assertEqual(self.nachsehen(False, versuche=3), "aufgegeben")

    def test_graphiti_stumm_laesst_alles_wie_es_ist(self):
        self.assertEqual(self.nachsehen(None), "gesendet")


if __name__ == "__main__":
    unittest.main()
