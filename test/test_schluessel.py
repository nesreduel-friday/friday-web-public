"""Schlüssel-Übersicht: Limits merken, ablaufen lassen, nie einen Schlüssel preisgeben."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from kern import config, schluessel
from kern.main import app

GEHEIM = "AIzaGEHEIM-haupt"
ENV = {"GEMINI_API_KEY": GEHEIM, "HINTERGRUND_GEMINI_API_KEY": "AIzaGEHEIM-hg",
       "RESERVE_GEMINI_API_KEY": "", "GRAPH_GEMINI_API_KEY": "", "GRAPHITI_GEMINI_API_KEY": "",
       "HAUSAUFGABEN_GEMINI_API_KEY": "", "BUECHER_GEMINI_API_KEY": "", "LOESEN_GEMINI_API_KEY": "",
       "LOESEN_RESERVE_GEMINI_API_KEY": "", "GOODNOTES_GEMINI_API_KEY": ""}


class SchluesselTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {**ENV, "FRIDAY_DATEN": self.ordner.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.ordner.cleanup)
        schluessel._stand = None
        schluessel._zaehler = None

    def _schl(self, name):
        return next(s for s in schluessel.uebersicht()["schluessel"] if s["name"] == name)

    def test_zaehler_zaehlt_je_tag_schluessel_und_modell_ohne_inhalt(self):
        haupt = os.environ["GEMINI_API_KEY"]
        schluessel.erfolg_melden(haupt, "m1")
        schluessel.erfolg_melden(haupt, "m1")
        schluessel.limit_melden(haupt, "m1", "quota perday")
        schluessel.fehler_melden(haupt, "m2")
        schluessel.erfolg_melden("fremder-schluessel", "m1")
        heute = schluessel.zaehler_tage(7)
        [tag] = heute.values()
        self.assertEqual(tag["Haupt"]["m1"], {"ok": 2, "limit": 1})
        self.assertEqual(tag["Haupt"]["m2"], {"fehler": 1})
        self.assertEqual(tag["unbekannt"]["m1"], {"ok": 1})
        self.assertIn("zaehler", schluessel.uebersicht())
        schluessel._zaehler = None                       # neu aus der Datei geladen
        self.assertEqual(schluessel.zaehler_tage(7), heute)
        self.assertNotIn(haupt, (schluessel._zaehler_datei().read_text(encoding="utf-8")))

    def test_ohne_limit_ist_alles_frei_und_zwecke_stehen_da(self):
        d = schluessel.uebersicht()
        self.assertEqual(d["am_limit"], 0)
        self.assertEqual([s["name"] for s in d["schluessel"]], ["Haupt", "Hintergrund"])
        self.assertIn("Gespräch und Chat", [z["zweck"] for z in self._schl("Haupt")["zwecke"]])
        self.assertTrue(all(z["verfuegbar"] for z in d["zwecke"]))
        self.assertIn("Gespräch und Chat", [z["zweck"] for z in d["zwecke"]])

    def test_zweck_bleibt_verfuegbar_solange_ein_schluessel_der_kette_frei_ist(self):
        with mock.patch.dict(os.environ, {"RESERVE_GEMINI_API_KEY": "AIzaGEHEIM-reserve"}):
            schluessel.limit_melden(GEHEIM, "m", "PerDay")
            gespraech = next(z for z in schluessel.uebersicht()["zwecke"] if z["zweck"] == "Gespräch und Chat")
            self.assertTrue(gespraech["verfuegbar"])

    def test_zweck_ist_am_limit_wenn_alle_schluessel_der_kette_limitiert_sind(self):
        # Nur ein Schlüssel ("Haupt") ist in diesem Setup für den Chat da (Reserve leer).
        schluessel.limit_melden(GEHEIM, "m", "PerDay")
        gespraech = next(z for z in schluessel.uebersicht()["zwecke"] if z["zweck"] == "Gespräch und Chat")
        self.assertFalse(gespraech["verfuegbar"])

    def test_tageslimit_wird_gemerkt_und_bei_erfolg_gestrichen(self):
        schluessel.limit_melden(GEHEIM, "gemini-3.5-flash", "Quota exceeded ... PerDay ...")
        s = self._schl("Haupt")
        self.assertEqual(s["limits"][0]["art"], "tag")
        self.assertEqual(s["limits"][0]["modell"], "gemini-3.5-flash")
        schluessel.erfolg_melden(GEHEIM, "gemini-3.5-flash")
        self.assertEqual(self._schl("Haupt")["limits"], [])

    def test_minutenlimit_laeuft_ab(self):
        schluessel.limit_melden(GEHEIM, "m", "PerMinute")
        self.assertEqual(self._schl("Haupt")["limits"][0]["art"], "minute")
        schluessel._stand["Haupt"]["m"]["seit"] = time.time() - schluessel.MINUTENLIMIT_S - 5
        self.assertEqual(self._schl("Haupt")["limits"], [])

    def test_limit_ueberlebt_neustart(self):
        schluessel.limit_melden(GEHEIM, "m", "PerDay")
        schluessel._stand = None
        self.assertEqual(len(self._schl("Haupt")["limits"]), 1)

    def test_unbekannter_schluessel_wird_ignoriert(self):
        schluessel.limit_melden("fremd", "m", "PerDay")
        self.assertEqual(schluessel.uebersicht()["am_limit"], 0)

    def test_api_nennt_nie_einen_schluessel(self):
        schluessel.limit_melden(GEHEIM, "m", "PerDay")
        text = TestClient(app).get("/api/schluessel").text
        self.assertNotIn("GEHEIM", text)
        self.assertIn("Haupt", text)


class PoolEinbindungTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {**ENV, "FRIDAY_DATEN": self.ordner.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.ordner.cleanup)
        schluessel._stand = None

    def _pool_setzen(self, anzahl: int, automatik: bool = False):
        pfad = Path(self.ordner.name) / "gemini_pool.json"
        eintraege = [{"name": f"Pool {i+1}", "wert": f"AIzaPOOL{i+1}", "projekt_id": f"friday-pool-{i+1}",
                      "angelegt_am": "2026-09-22T00:00:00+02:00"} for i in range(anzahl)]
        pfad.write_text(json.dumps({"schluessel": eintraege, "automatik": automatik, "letzte_uebernahme": 0}),
                         encoding="utf-8")
        return [e["wert"] for e in eintraege]

    def test_gespraech_bekommt_den_ganzen_pool(self):
        werte = self._pool_setzen(3)
        liste = config.gespraech_schluessel_liste()
        for w in werte:
            self.assertIn(w, liste)

    def test_hintergrund_reserviert_die_letzten_zwei(self):
        werte = self._pool_setzen(3)
        liste = config.hintergrund_schluessel_liste()
        self.assertIn(werte[0], liste)
        self.assertNotIn(werte[1], liste)
        self.assertNotIn(werte[2], liste)

    def test_hintergrund_bekommt_nichts_wenn_pool_klein_ist(self):
        werte = self._pool_setzen(2)
        liste = config.hintergrund_schluessel_liste()
        for w in werte:
            self.assertNotIn(w, liste)

    def test_pool_schluessel_erscheint_in_der_uebersicht(self):
        self._pool_setzen(1)
        namen = [s["name"] for s in schluessel.uebersicht()["schluessel"]]
        self.assertIn("Pool 1", namen)

    def test_limit_auf_pool_schluessel_wird_erkannt(self):
        werte = self._pool_setzen(1)
        schluessel.limit_melden(werte[0], "gemini-3.5-flash", "PerDay")
        s = next(s for s in schluessel.uebersicht()["schluessel"] if s["name"] == "Pool 1")
        self.assertEqual(s["limits"][0]["art"], "tag")

    def test_uebersicht_zeigt_pool_stand(self):
        self._pool_setzen(2, automatik=True)
        p = schluessel.uebersicht()["pool"]
        self.assertEqual(p["anzahl"], 2)
        self.assertTrue(p["automatik"])

    def test_automatik_wird_bei_knapper_reserve_ausgeloest(self):
        werte = self._pool_setzen(2, automatik=True)
        o = Path(self.ordner.name) / "schluessel_auftraege"
        o.mkdir(parents=True, exist_ok=True)
        (o / "zustand.json").write_text(json.dumps({"aktualisiert": time.time(), "letzte_antwort": {}}),
                                         encoding="utf-8")
        schluessel.limit_melden(werte[0], "gemini-3.5-flash", "PerDay")
        self.assertTrue((o / "auftrag.json").exists())

    def test_schwaerzen_entfernt_pool_schluessel(self):
        werte = self._pool_setzen(1)
        self.assertNotIn(werte[0], config.schwaerzen(f"geheim: {werte[0]}"))

    def test_pool_haelt_hintergrund_zweck_verfuegbar_wenn_eigene_schluessel_limitiert_sind(self):
        werte = self._pool_setzen(3)  # Reserve 2 -> 1 Pool-Schlüssel bleibt dem Hintergrund
        schluessel.limit_melden(os.environ["HINTERGRUND_GEMINI_API_KEY"], "m", "PerDay")
        d = schluessel.uebersicht()
        z = next(z for z in d["zwecke"] if z["zweck"] == "Briefe, Mails, Chats auswerten")
        self.assertTrue(z["verfuegbar"])

    def test_graph_schluessel_liste_bekommt_pool_ohne_die_letzten_zwei(self):
        werte = self._pool_setzen(3)
        with mock.patch.dict(os.environ, {"GRAPH_GEMINI_API_KEY": "AIzaGRAPH"}):
            liste = config.graph_schluessel_liste()
        self.assertEqual(liste, ["AIzaGRAPH", werte[0]])

    def test_frei_ist_wahr_fuer_unbekannten_schluessel(self):
        self.assertTrue(schluessel.frei("nie gesehen"))

    def test_frei_wird_falsch_nach_limit_melden(self):
        self.assertTrue(schluessel.frei(GEHEIM))
        schluessel.limit_melden(GEHEIM, "m", "PerDay")
        self.assertFalse(schluessel.frei(GEHEIM))


class SchluesselRoutenTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {**ENV, "FRIDAY_DATEN": self.ordner.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.ordner.cleanup)
        schluessel._stand = None
        self.client = TestClient(app)

    def test_automatik_route_prueft_herkunft(self):
        r = self.client.post("/api/schluessel/automatik", json={"an": True},
                              headers={"origin": "https://boese.example"})
        self.assertEqual(r.status_code, 403)

    def test_automatik_route_schaltet(self):
        r = self.client.post("/api/schluessel/automatik", json={"an": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["pool"]["automatik"])

    def test_anlegen_route_prueft_herkunft(self):
        r = self.client.post("/api/schluessel/anlegen", headers={"origin": "https://boese.example"})
        self.assertEqual(r.status_code, 403)

    def test_anlegen_route_ohne_helfer_meldet_das(self):
        r = self.client.post("/api/schluessel/anlegen")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertIn("Helfer", r.json()["text"])


if __name__ == "__main__":
    unittest.main()
