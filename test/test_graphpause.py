"""Wissensgraph aussetzen, wenn das Gemini-Tageslimit erschöpft ist."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import httpx

from kern import schluessel
from kern.gedaechtnis import graph, rueckblick
from kern.protokoll import speicher as pspeicher


class Erkennen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(),
                                                "GRAPH_GEMINI_API_KEY": "k"})
        self.env.start()
        # tageslimit_erschoepft() meldet einen erkannten 429 jetzt an schluessel.py —
        # der Zwischenspeicher dort darf keinen Stand aus einem anderen Testordner haben.
        schluessel._stand = None

    def tearDown(self):
        self.env.stop()

    def _mit(self, code, text=""):
        antwort = httpx.Response(code, text=text, request=httpx.Request("POST", "https://x/"))
        return mock.patch("httpx.AsyncClient.post", mock.AsyncMock(return_value=antwort))

    def test_tageslimit_wird_erkannt(self):
        with self._mit(429, '{"error":{"details":[{"quotaId":"GenerateRequestsPerDayPerProject"}]}}'):
            self.assertTrue(asyncio.run(graph.tageslimit_erschoepft()))

    def test_minutenlimit_ist_kein_grund_zum_aussetzen(self):
        with self._mit(429, '{"error":{"details":[{"quotaId":"GenerateRequestsPerMinute"}]}}'):
            self.assertFalse(asyncio.run(graph.tageslimit_erschoepft()))

    def _url_der_pruefung(self):
        post = mock.AsyncMock(return_value=httpx.Response(200, text="{}", request=httpx.Request("POST", "https://x/")))
        with mock.patch("httpx.AsyncClient.post", post):
            asyncio.run(graph.tageslimit_erschoepft())
        return post.call_args.args[0]

    def test_pruefung_nimmt_standardmaessig_das_leichte_graphmodell(self):
        # gemini-3.5-flash hat nur 20 Anfragen/Tag (gemessen 05.10.2026) — die Prüfung auf ein anderes Modell als das
        # des Graphen sperrte den Graphen zu Unrecht (und umgekehrt).
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MODEL_NAME", None)
            self.assertIn("gemini-3.5-flash-lite:generateContent", self._url_der_pruefung())

    def test_pruefung_folgt_model_name(self):
        with mock.patch.dict(os.environ, {"MODEL_NAME": "gemini-x"}):
            self.assertIn("gemini-x:generateContent", self._url_der_pruefung())

    def test_alles_frei(self):
        with self._mit(200, "{}"):
            self.assertFalse(asyncio.run(graph.tageslimit_erschoepft()))

    def test_netzfehler_setzt_nicht_aus(self):
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=httpx.ConnectError("weg"))):
            self.assertFalse(asyncio.run(graph.tageslimit_erschoepft()))


class Pause(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_setzen_pruefen_aufheben(self):
        self.assertFalse(graph.pausiert())
        graph.pause_setzen(datetime.now(timezone.utc) + timedelta(hours=2))
        self.assertTrue(graph.pausiert())
        self.assertTrue(graph.pause_bis())
        graph.pause_aufheben()
        self.assertFalse(graph.pausiert())

    def test_abgelaufene_pause_gilt_nicht_mehr(self):
        graph.pause_setzen(datetime.now(timezone.utc) - timedelta(minutes=1))
        self.assertFalse(graph.pausiert())
        self.assertTrue(graph.pause_bis())        # der Vermerk bleibt, bis aufgeräumt wird

    def test_pause_pruefen_nur_bei_tageslimit(self):
        with mock.patch.object(graph, "tageslimit_erschoepft", mock.AsyncMock(return_value=False)):
            self.assertFalse(asyncio.run(graph.pause_pruefen()))
        self.assertFalse(graph.pausiert())
        with mock.patch.object(graph, "tageslimit_erschoepft", mock.AsyncMock(return_value=True)):
            self.assertTrue(asyncio.run(graph.pause_pruefen()))
        self.assertTrue(graph.pausiert())


JETZT = datetime(2026, 9, 20, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))


class Umschalten(unittest.TestCase):
    """Am Tageslimit: auf einen freien Schlüssel aus dem Pool umschalten statt nur zu pausieren.

    Feste Uhrzeit (Mittag) statt der echten Wanduhr — sonst wird die Unterscheidung
    „kurze Pause" gegen „Pause bis 9:05" in der Minute vor dem echten Reset flackerig."""

    def setUp(self):
        self.daten = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.daten, "GRAPH_GEMINI_API_KEY": "k-alt"})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=JETZT)
        self.uhr.start()
        schluessel._stand = None

    def tearDown(self):
        self.env.stop()
        self.uhr.stop()

    def _pool_schreiben(self, *werte):
        import json as jsonmod
        eintraege = [{"name": f"Pool {i+1}", "wert": w, "projekt_id": f"p{i}", "angelegt_am": "x"}
                     for i, w in enumerate(werte)]
        with open(os.path.join(self.daten, "gemini_pool.json"), "w", encoding="utf-8") as f:
            jsonmod.dump({"schluessel": eintraege, "automatik": False, "letzte_uebernahme": 0}, f)

    def test_schaltet_um_wenn_ein_kandidat_frei_ist(self):
        self._pool_schreiben("pool-1", "pool-2", "pool-3")  # Reserve 2 -> "pool-1" bleibt Graphiti
        with mock.patch.object(graph, "tageslimit_erschoepft", mock.AsyncMock(return_value=True)), \
             mock.patch("kern.dienste.schluessel_pool.graph_umschalten_anstossen",
                         return_value=(True, "Umschalten liegt bereit.")) as anstossen:
            self.assertTrue(asyncio.run(graph.pause_pruefen()))
        anstossen.assert_called_once_with("pool-1")
        self.assertEqual(graph.aktueller_schluessel(), "pool-1")
        self.assertTrue(graph.pausiert())
        # Kurze Pause fürs Neustarten — läuft über die echte Uhr (wie pause_setzen/
        # pausiert() sie nutzen), nicht über die gestellte JETZT (nur für den Reset-Fall unten).
        bis = datetime.fromisoformat(graph.pause_bis())
        self.assertLess(bis, datetime.now(timezone.utc) + timedelta(minutes=graph.UMSCHALT_PAUSE_MIN + 1))

    def test_ohne_kandidat_faellt_zurueck_auf_die_normale_pause(self):
        with mock.patch.object(graph, "tageslimit_erschoepft", mock.AsyncMock(return_value=True)):
            self.assertTrue(asyncio.run(graph.pause_pruefen()))
        self.assertEqual(graph.aktueller_schluessel(), "k-alt")   # nicht umgeschaltet
        bis = datetime.fromisoformat(graph.pause_bis())
        self.assertGreater(bis, JETZT.astimezone(timezone.utc) + timedelta(hours=1))   # bis zum Reset, nicht kurz

    def test_wenn_umschalten_fehlschlaegt_faellt_es_auf_die_normale_pause_zurueck(self):
        self._pool_schreiben("pool-1", "pool-2", "pool-3")
        with mock.patch.object(graph, "tageslimit_erschoepft", mock.AsyncMock(return_value=True)), \
             mock.patch("kern.dienste.schluessel_pool.graph_umschalten_anstossen",
                         return_value=(False, "Der Helfer auf hphome läuft nicht.")):
            self.assertTrue(asyncio.run(graph.pause_pruefen()))
        self.assertEqual(graph.aktueller_schluessel(), "k-alt")
        bis = datetime.fromisoformat(graph.pause_bis())
        self.assertGreater(bis, JETZT.astimezone(timezone.utc) + timedelta(hours=1))


class Reset(unittest.TestCase):
    """Das Tageslimit der kostenlosen Gemini-Stufe fällt um 00:00 UTC (Google nannte „retry in …“ bis dorthin, gemessen
    05.10.2026 für flash und flash-lite) — nicht um 9 Uhr deutscher Zeit, wie vorher angenommen."""

    def _reset_bei(self, jetzt):
        with mock.patch("kern.config.jetzt", return_value=jetzt):
            return graph._naechster_reset()

    def test_mittags_ist_der_reset_kurz_nach_mitternacht_utc(self):
        reset = self._reset_bei(datetime(2026, 10, 5, 18, 33, tzinfo=ZoneInfo("Europe/Berlin")))
        self.assertEqual(reset.astimezone(timezone.utc), datetime(2026, 10, 6, 0, 5, tzinfo=timezone.utc))

    def test_kurz_vor_dem_reset_gilt_der_von_heute(self):
        reset = self._reset_bei(datetime(2026, 10, 6, 1, 30, tzinfo=ZoneInfo("Europe/Berlin")))   # 23:30 UTC
        self.assertEqual(reset.astimezone(timezone.utc), datetime(2026, 10, 6, 0, 5, tzinfo=timezone.utc))

    def test_nach_dem_reset_gilt_der_naechste_tag(self):
        reset = self._reset_bei(datetime(2026, 10, 6, 3, 0, tzinfo=ZoneInfo("Europe/Berlin")))    # 01:00 UTC
        self.assertEqual(reset.astimezone(timezone.utc), datetime(2026, 10, 7, 0, 5, tzinfo=timezone.utc))


class VorzeitigBeenden(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        graph._pause_zuletzt_geprueft = 0.0

    def tearDown(self):
        self.env.stop()

    def _lange_pause(self, stunden=20):
        graph.pause_setzen(datetime.now(timezone.utc) + timedelta(hours=stunden))

    def test_freies_limit_beendet_die_lange_pause(self):
        self._lange_pause()
        with mock.patch.object(graph, "tageslimit_erschoepft", mock.AsyncMock(return_value=False)):
            self.assertTrue(asyncio.run(graph.pause_vorzeitig_beenden()))
        self.assertFalse(graph.pausiert())

    def test_erschoepftes_limit_laesst_die_pause_stehen(self):
        self._lange_pause()
        with mock.patch.object(graph, "tageslimit_erschoepft", mock.AsyncMock(return_value=True)):
            self.assertFalse(asyncio.run(graph.pause_vorzeitig_beenden()))
        self.assertTrue(graph.pausiert())

    def test_kurze_pausen_werden_nicht_angefasst(self):
        graph.pause_setzen(datetime.now(timezone.utc) + timedelta(minutes=3))      # Schlüsselwechsel / Container-Neustart
        pruef = mock.AsyncMock(return_value=False)
        with mock.patch.object(graph, "tageslimit_erschoepft", pruef):
            self.assertFalse(asyncio.run(graph.pause_vorzeitig_beenden()))
        pruef.assert_not_awaited()
        self.assertTrue(graph.pausiert())

    def test_hoechstens_alle_30_minuten_ein_aufruf(self):
        self._lange_pause()
        pruef = mock.AsyncMock(return_value=True)
        with mock.patch.object(graph, "tageslimit_erschoepft", pruef):
            asyncio.run(graph.pause_vorzeitig_beenden())
            asyncio.run(graph.pause_vorzeitig_beenden())
        self.assertEqual(pruef.await_count, 1)

    def test_ohne_pause_nichts_zu_tun(self):
        pruef = mock.AsyncMock(return_value=False)
        with mock.patch.object(graph, "tageslimit_erschoepft", pruef):
            self.assertFalse(asyncio.run(graph.pause_vorzeitig_beenden()))
        pruef.assert_not_awaited()

    def test_durchgang_hebt_die_pause_auf_und_arbeitet_wieder(self):
        self._lange_pause()
        p = pspeicher.standard()
        with mock.patch.object(graph, "tageslimit_erschoepft", mock.AsyncMock(return_value=False)), \
             mock.patch.object(pspeicher, "standard", return_value=p), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "nachsehen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "zusammenfassen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "sichern"), \
             mock.patch.object(rueckblick, "_gelegentlich_verdichten", mock.AsyncMock()), \
             mock.patch.object(p, "graph_offen", return_value=[7]), \
             mock.patch.object(p, "graph_zu_pruefen", return_value=[]), \
             mock.patch.object(p, "graph_in_arbeit", return_value=0):
            asyncio.run(rueckblick.durchgang())
            self.assertFalse(graph.pausiert())
            rueckblick.einspeisen.assert_awaited()


class Durchgang(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.p = pspeicher.standard()

    def tearDown(self):
        self.env.stop()

    def test_pausiert_schickt_nichts_an_den_graphen(self):
        graph.pause_setzen(datetime.now(timezone.utc) + timedelta(hours=2))
        with mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()) as ein, \
             mock.patch.object(rueckblick, "nachsehen", mock.AsyncMock()) as nach, \
             mock.patch.object(rueckblick, "zusammenfassen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "sichern"), \
             mock.patch.object(rueckblick, "_gelegentlich_verdichten", mock.AsyncMock()):
            asyncio.run(rueckblick.durchgang())
        ein.assert_not_awaited()
        nach.assert_not_awaited()

    def test_nach_der_pause_wird_sofort_nachgeholt(self):
        graph.pause_setzen(datetime.now(timezone.utc) - timedelta(minutes=1))   # abgelaufen
        with mock.patch.object(self.p, "graph_wieder_versuchen", return_value=3) as neu, \
             mock.patch.object(pspeicher, "standard", return_value=self.p), \
             mock.patch.object(rueckblick, "einspeisen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "nachsehen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "zusammenfassen", mock.AsyncMock()), \
             mock.patch.object(rueckblick, "sichern"), \
             mock.patch.object(rueckblick, "_gelegentlich_verdichten", mock.AsyncMock()):
            asyncio.run(rueckblick.durchgang())
        self.assertEqual(neu.call_args.kwargs.get("stunden"), 0)   # ohne 12 Stunden Wartezeit
        self.assertFalse(graph.pausiert())
        self.assertEqual(graph.pause_bis(), "")                    # Vermerk aufgeräumt
