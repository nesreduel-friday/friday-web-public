"""hphome-Wache: meldet Platte und ausgefallene Dienste — leise, wo es nichts zu melden gibt."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kern.dienste import benachrichtigungen, hphome_wache as w


def zustand(prozent=50.0, container=None, **extra):
    return {"helfer": True, "container_ok": True, "wartung_bis": None,
            "host": {"platten": [{"pfad": "/", "prozent": prozent, "gesamt_gb": 233.0, "belegt_gb": 214.0}]},
            "container": container if container is not None else [], **extra}


def c(name, zustand="running", gesundheit="", absichtlich=False):
    return {"name": name, "zustand": zustand, "gesundheit": gesundheit, "absichtlich_gestoppt": absichtlich}


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.st = w.stand_lesen()


class Platte(Basis):
    def test_meldet_ab_90_einmal(self):
        m = w.pruefen(zustand(92.0), 1000, self.st)
        self.assertEqual([x[0] for x in m], ["platte"])
        self.assertIn("92 %", m[0][2])
        self.assertEqual(w.pruefen(zustand(93.0), 1100, self.st), [])           # nicht wieder

    def test_erst_nach_entwarnung_wieder(self):
        w.pruefen(zustand(92.0), 1000, self.st)
        w.pruefen(zustand(88.0), 2000, self.st)                                 # zwischen 85 und 90: noch keine Entwarnung
        self.assertEqual(w.pruefen(zustand(91.0), 3000, self.st), [])
        w.pruefen(zustand(80.0), 4000, self.st)                                 # Entwarnung
        self.assertEqual([x[0] for x in w.pruefen(zustand(91.0), 5000, self.st)], ["platte"])

    def test_unter_90_still(self):
        self.assertEqual(w.pruefen(zustand(89.9), 1000, self.st), [])

    def test_fehlender_prozentwert(self):
        z = zustand(); z["host"]["platten"] = [{"pfad": "/"}]
        self.assertEqual(w.pruefen(z, 1000, self.st), [])


class Container(Basis):
    def test_ausgefallen_erst_nach_drei_minuten(self):
        w.pruefen(zustand(container=[c("jellyfin")]), 1000, self.st)              # gesehen, läuft
        z = zustand(container=[c("jellyfin", "exited")])
        self.assertEqual(w.pruefen(z, 1100, self.st), [])                         # gerade erst weg
        self.assertEqual(w.pruefen(z, 1170, self.st), [])                         # 70 s
        m = w.pruefen(z, 1290, self.st)                                           # 190 s
        self.assertEqual([x[0] for x in m], ["down:jellyfin"])
        self.assertIn("jellyfin", m[0][1])

    def test_nie_gelaufen_ist_kein_ausfall(self):
        z = zustand(container=[c("seminar-ki", "exited")])
        self.assertEqual(w.pruefen(z, 1000, self.st), [])
        self.assertEqual(w.pruefen(z, 9000, self.st), [])

    def test_absichtlich_gestoppt_schweigt(self):
        w.pruefen(zustand(container=[c("jellyfin")]), 1000, self.st)
        z = zustand(container=[c("jellyfin", "exited", absichtlich=True)])
        w.pruefen(z, 1100, self.st)
        self.assertEqual(w.pruefen(z, 9000, self.st), [])

    def test_wieder_da_setzt_den_zaehler_zurueck(self):
        w.pruefen(zustand(container=[c("jellyfin")]), 1000, self.st)
        w.pruefen(zustand(container=[c("jellyfin", "exited")]), 1100, self.st)
        w.pruefen(zustand(container=[c("jellyfin")]), 1200, self.st)
        self.assertEqual(w.pruefen(zustand(container=[c("jellyfin", "exited")]), 1300, self.st), [])   # neu ab 1300
        self.assertEqual([x[0] for x in w.pruefen(zustand(container=[c("jellyfin", "exited")]), 1500, self.st)], ["down:jellyfin"])

    def test_entfernter_container_ist_kein_ausfall(self):
        w.pruefen(zustand(container=[c("alt")]), 1000, self.st)
        self.assertEqual(w.pruefen(zustand(container=[]), 9000, self.st), [])
        self.assertNotIn("alt", self.st["gesehen"])

    def test_unhealthy(self):
        m = w.pruefen(zustand(container=[c("friday-graphiti", gesundheit="unhealthy")]), 1000, self.st)
        self.assertEqual([x[0] for x in m], ["unhealthy:friday-graphiti"])

    def test_gesund_und_startend_sind_still(self):
        z = zustand(container=[c("a", gesundheit="healthy"), c("b", gesundheit="starting"), c("d")])
        self.assertEqual(w.pruefen(z, 1000, self.st), [])

    def test_sperre_sechs_stunden_gegen_flattern(self):
        z_an, z_aus = zustand(container=[c("x", gesundheit="unhealthy")]), zustand(container=[c("x")])
        self.assertEqual(len(w.pruefen(z_an, 1000, self.st)), 1)
        for t in range(1060, 1060 + 5 * 3600, 600):                              # alle 10 min flattert es
            w.pruefen(z_aus, t, self.st)
            self.assertEqual(w.pruefen(z_an, t + 1, self.st), [], t)
        self.assertEqual(len(w.pruefen(z_an, 1000 + 6 * 3600 + 5, self.st)), 1)  # nach sechs Stunden wieder

    def test_docker_aus_meldet_nichts_ueber_container(self):
        w.pruefen(zustand(container=[c("jellyfin")]), 1000, self.st)
        z = zustand(container=[], container_ok=False)
        self.assertEqual(w.pruefen(z, 9000, self.st), [])
        self.assertIn("jellyfin", self.st["gesehen"])                             # nicht vergessen, nur nicht beurteilt


class Stille(Basis):
    def test_ohne_zustand_oder_helfer(self):
        self.assertEqual(w.pruefen(None, 1000, self.st), [])
        self.assertEqual(w.pruefen({"helfer": False}, 1000, self.st), [])

    def test_wartung_nach_dem_host_neustart(self):
        w.pruefen(zustand(container=[c("jellyfin")]), 1000, self.st)
        z = zustand(92.0, container=[c("jellyfin", "exited")], wartung_bis=2000)
        self.assertEqual(w.pruefen(z, 1900, self.st), [])
        self.assertEqual(w.pruefen(z, 1999, self.st), [])
        m = w.pruefen(zustand(92.0, container=[c("jellyfin", "exited")], wartung_bis=2000), 2100, self.st)
        self.assertIn("platte", [x[0] for x in m])


class Ablauf(Basis):
    def test_stand_bleibt_ueber_neustart_erhalten(self):
        w.pruefen(zustand(92.0, container=[c("a")]), 1000, self.st)
        w.stand_schreiben(self.st)
        st2 = w.stand_lesen()
        self.assertTrue(st2["platte"])
        self.assertEqual(st2["gesehen"], ["a"])
        self.assertEqual(oct((Path(self.tmp.name) / "hphome" / "wache.json").stat().st_mode & 0o777), "0o600")

    def test_kaputter_stand(self):
        (Path(self.tmp.name) / "hphome").mkdir()
        (Path(self.tmp.name) / "hphome" / "wache.json").write_text("{kaputt")
        self.assertEqual(w.stand_lesen(), {"platte": False, "gesehen": [], "down": {}, "gemeldet": {}})

    def test_einmal_sendet_ueber_benachrichtigungen_mit_art_server(self):
        senden = mock.Mock(return_value={})
        with mock.patch.object(w.hphome, "zustand_roh", return_value=zustand(95.0)), \
                mock.patch.object(w.benachrichtigungen, "senden", senden):
            asyncio.run(w.einmal(1000))
        senden.assert_called_once()
        self.assertEqual(senden.call_args[0][0], "server")

    def test_einmal_sendet_nicht_wenn_die_art_aus_ist(self):
        benachrichtigungen.stellen("server", False)
        senden = mock.Mock()
        with mock.patch.object(w.hphome, "zustand_roh", return_value=zustand(95.0)), \
                mock.patch.object(w.benachrichtigungen, "senden", senden):
            asyncio.run(w.einmal(1000))
        senden.assert_not_called()

    # ------------------------------------------------ Fix-Welle nach dem Gesamt-Review

    def _einmal(self, z, senden, jetzt):
        with mock.patch.object(w.hphome, "zustand_roh", return_value=z), \
                mock.patch.object(w.benachrichtigungen, "senden", senden):
            return asyncio.run(w.einmal(jetzt))

    def test_ruhezeit_haelt_den_alarm_nur_zurueck(self):
        still = mock.Mock(return_value={"gesendet": 0, "unterdrueckt": "ruhezeit"})
        self._einmal(zustand(95.0), still, 1000)
        st = w.stand_lesen()
        self.assertEqual(st["gemeldet"], {})                                     # nichts gilt als gemeldet
        self.assertFalse(st["platte"])
        laut = mock.Mock(return_value={"gesendet": 1, "unterdrueckt": ""})
        self._einmal(zustand(95.0), laut, 1060)                                 # nach der Ruhezeit
        laut.assert_called_once()
        self.assertIn("platte", w.stand_lesen()["gemeldet"])
        self.assertTrue(w.stand_lesen()["platte"])

    def test_ruhezeit_auch_fuer_container(self):
        still = mock.Mock(return_value={"unterdrueckt": "ruhezeit"})
        self._einmal(zustand(container=[c("x", gesundheit="unhealthy")]), still, 1000)
        self.assertEqual(w.stand_lesen()["gemeldet"], {})
        laut = mock.Mock(return_value={"unterdrueckt": ""})
        self._einmal(zustand(container=[c("x", gesundheit="unhealthy")]), laut, 1060)
        laut.assert_called_once()

    def test_senden_wirft_zustand_zurueck_und_weiter_mit_den_anderen(self):
        aufrufe = []

        def senden(art, titel, text, url="/", tag="", dringend=False):
            aufrufe.append(tag)
            if len(aufrufe) == 1:
                raise RuntimeError("Push kaputt")
            return {"unterdrueckt": ""}
        z = zustand(95.0, container=[c("x", gesundheit="unhealthy")])
        self._einmal(z, senden, 1000)                                            # kein Absturz
        self.assertEqual(len(aufrufe), 2)                                         # der zweite Befund kam trotzdem raus
        st = w.stand_lesen()
        self.assertEqual(list(st["gemeldet"]), ["unhealthy:x"])
        self.assertFalse(st["platte"])                                            # die Platte wird nächstes Mal neu versucht

    def test_art_aus_rueckt_den_zustand_vor(self):
        aus = mock.Mock(return_value={"unterdrueckt": "art_aus"})
        self._einmal(zustand(95.0), aus, 1000)
        self.assertIn("platte", w.stand_lesen()["gemeldet"])                     # abgeschaltet: nichts wird nachgeholt
        benachrichtigungen.stellen("server", False)
        nie = mock.Mock()
        self._einmal(zustand(container=[c("x", gesundheit="unhealthy")]), nie, 1000)
        nie.assert_not_called()
        self.assertIn("unhealthy:x", w.stand_lesen()["gemeldet"])

    def test_jeder_befund_hat_ein_eigenes_tag(self):
        senden = mock.Mock(return_value={"unterdrueckt": ""})
        self._einmal(zustand(95.0, container=[c("x", gesundheit="unhealthy"), c("y", gesundheit="unhealthy")]), senden, 1000)
        tags = [k.kwargs.get("tag") for k in senden.call_args_list]
        self.assertEqual(sorted(tags), ["server-platte", "server-unhealthy-x", "server-unhealthy-y"])

    def test_frei_ist_der_nutzbare_platz(self):
        z = zustand(97.0)
        z["host"]["platten"][0]["frei_gb"] = 7.6                                   # gesamt 233, belegt 214
        m = w.pruefen(z, 1000, self.st)
        self.assertIn("7.6", m[0][2])
        self.assertNotIn("19", m[0][2])                                            # nie gesamt − belegt
        st2 = w.stand_lesen()
        m = w.pruefen(zustand(97.0), 1000, st2)                                   # ohne frei_gb
        self.assertIn("?", m[0][2])
        self.assertNotIn("19", m[0][2])
        self.assertNotIn("None", m[0][2])

    def test_die_art_gibt_es_und_ist_standardmaessig_an(self):
        art = benachrichtigungen.art("server")
        self.assertTrue(art["standard"])
        self.assertTrue(benachrichtigungen.an("server"))


if __name__ == "__main__":
    unittest.main()
