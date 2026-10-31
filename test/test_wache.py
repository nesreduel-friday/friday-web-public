"""Abfahrten im Blick: merken, nachsehen, einmal melden — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import uestra, wache
from kern.main import app
from kern.werkzeuge import fahrplan

BERLIN = ZoneInfo("Europe/Berlin")
MONTAG = datetime(2026, 9, 21, 7, 0, tzinfo=BERLIN)      # ein Montag
SONNTAG = datetime(2026, 9, 20, 7, 0, tzinfo=BERLIN)


def abfahrt(stunde=7, minute=20, linie="300", richtung="Hannover", verspaetung=None,
            faellt_aus=False, tag=None):
    plan = (tag or MONTAG).replace(hour=stunde, minute=minute)
    return {"linie": linie, "art": f"Bus {linie}", "richtung": richtung, "plan": plan,
            "prognose": plan + timedelta(minutes=verspaetung) if verspaetung is not None else None,
            "verspaetung": verspaetung, "faellt_aus": faellt_aus}


class Mit_Wache(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        konnektoren.setzen("gvh", True)
        wache._gemeldet.clear()
        self.geschickt = mock.patch("kern.dienste.benachrichtigungen.senden",
                                    return_value={"gesendet": 1}).start()
        self.client = TestClient(app)

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()

    def _tafel(self, abfahrten, halt="Bennigsen"):
        return mock.patch("kern.dienste.uestra.abfahrten",
                          mock.AsyncMock(return_value={"haltestelle": halt, "abfahrten": abfahrten,
                                                       "meldungen": []}))

    def _anlegen(self, **anders):
        angaben = {"halt": "Bennigsen", "linie": "300", "richtung": "Hannover",
                   "ab": "07:10", "bis": "07:40", "tage": [0, 1, 2, 3, 4]}
        angaben.update(anders)
        with self._tafel([]):
            return asyncio.run(wache.anlegen(**angaben))


class Anlegen(Mit_Wache):
    def test_eine_wache_landet_auf_der_platte(self):
        w = self._anlegen()
        self.assertEqual(wache.alle()[0]["id"], w["id"])
        rechte = (Path(self.ordner) / "wachen.json").stat().st_mode & 0o777
        self.assertEqual(rechte, 0o600)

    def test_der_name_kommt_von_der_uestra_nicht_von_eike(self):
        # So heißt die Haltestelle später genau so wie auf der Tafel.
        with self._tafel([], halt="Bennigsen/Bahnhof"):
            w = asyncio.run(wache.anlegen("bennigsen", "300", ab="07:10", bis="07:40"))
        self.assertEqual(w["halt"], "Bennigsen/Bahnhof")

    def test_haltestelle_ohne_echtzeit_wird_abgelehnt(self):
        """Eine Wache auf Fahrplanzeiten meldete nie etwas — das wäre ein leeres
        Versprechen und schlimmer als keine Wache."""
        with mock.patch("kern.dienste.uestra.abfahrten",
                        mock.AsyncMock(side_effect=uestra.Unbekannt("weg"))):
            with self.assertRaises(wache.WachenFehler) as fehler:
                asyncio.run(wache.anlegen("Kleinkleckersdorf", ab="07:00", bis="08:00"))
        self.assertIn("Echtzeit", str(fehler.exception))
        self.assertEqual(wache.alle(), [])

    def test_mehrdeutige_haltestelle_fragt_nach(self):
        with mock.patch("kern.dienste.uestra.abfahrten",
                        mock.AsyncMock(side_effect=uestra.Mehrdeutig("Welche denn: A oder B?"))):
            with self.assertRaises(wache.WachenFehler) as fehler:
                asyncio.run(wache.anlegen("Bahnhof", ab="07:00", bis="08:00"))
        self.assertIn("A oder B", str(fehler.exception))

    def test_rueckwaerts_laufendes_fenster_wird_abgelehnt(self):
        with self.assertRaises(wache.WachenFehler):
            asyncio.run(wache.anlegen("Bennigsen", ab="08:00", bis="07:00"))

    def test_nicht_mehr_als_die_hoechstzahl(self):
        for i in range(wache.MAX_WACHEN):
            self._anlegen(linie=str(i))
        with self.assertRaises(wache.WachenFehler):
            self._anlegen(linie="zuviel")
        self.assertEqual(len(wache.alle()), wache.MAX_WACHEN)

    def test_tage_in_worten(self):
        self.assertEqual(wache._tage_deuten("werktags"), [0, 1, 2, 3, 4])
        self.assertEqual(wache._tage_deuten("am Wochenende"), [5, 6])
        self.assertEqual(wache._tage_deuten("täglich"), [])
        self.assertEqual(wache._tage_deuten("Mo Di Do"), [0, 1, 3])
        self.assertEqual(wache._tage_deuten("Blumenkohl"), [])

    def test_entfernen_und_stellen(self):
        w = self._anlegen()
        self.assertFalse(wache.stellen(w["id"], False)["aktiv"])
        self.assertIsNotNone(wache.entfernen(w["id"]))
        self.assertEqual(wache.alle(), [])
        self.assertIsNone(wache.entfernen(w["id"]))


class Fenster(Mit_Wache):
    def test_im_fenster_und_im_vorlauf(self):
        w = self._anlegen()
        self.assertTrue(wache.offen(w, MONTAG.replace(hour=7, minute=20)))
        self.assertTrue(wache.offen(w, MONTAG.replace(hour=6, minute=45)))   # Vorlauf
        self.assertFalse(wache.offen(w, MONTAG.replace(hour=6, minute=30)))

    def test_nach_dem_fenster_ist_zu(self):
        w = self._anlegen()
        self.assertTrue(wache.offen(w, MONTAG.replace(hour=7, minute=44)))   # Nachlauf
        self.assertFalse(wache.offen(w, MONTAG.replace(hour=8, minute=0)))

    def test_am_falschen_wochentag_bleibt_es_zu(self):
        w = self._anlegen()
        self.assertFalse(wache.offen(w, SONNTAG.replace(hour=7, minute=20)))

    def test_ohne_tage_gilt_jeder_tag(self):
        w = self._anlegen(tage=[])
        self.assertTrue(wache.offen(w, SONNTAG.replace(hour=7, minute=20)))

    def test_pausiert_heisst_zu(self):
        w = self._anlegen()
        wache.stellen(w["id"], False)
        self.assertFalse(wache.offen(wache.alle()[0], MONTAG.replace(hour=7, minute=20)))


class Treffer(Mit_Wache):
    def test_verspaetung_ueber_der_schwelle(self):
        w = self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        self.assertEqual(len(wache.betroffen(w, [abfahrt(verspaetung=8)], jetzt)), 1)
        self.assertEqual(wache.betroffen(w, [abfahrt(verspaetung=2)], jetzt), [])

    def test_ohne_prognose_wird_nichts_behauptet(self):
        w = self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        self.assertEqual(wache.betroffen(w, [abfahrt(verspaetung=None)], jetzt), [])

    def test_ausfall_zaehlt_immer(self):
        w = self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        self.assertEqual(len(wache.betroffen(w, [abfahrt(faellt_aus=True)], jetzt)), 1)

    def test_andere_linie_geht_uns_nichts_an(self):
        w = self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        self.assertEqual(wache.betroffen(w, [abfahrt(linie="301", verspaetung=20)], jetzt), [])

    def test_ohne_linie_zaehlt_alles(self):
        w = self._anlegen(linie="")
        jetzt = MONTAG.replace(hour=7, minute=5)
        self.assertEqual(len(wache.betroffen(w, [abfahrt(linie="301", verspaetung=20)], jetzt)), 1)

    def test_andere_richtung_geht_uns_nichts_an(self):
        w = self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        self.assertEqual(wache.betroffen(w, [abfahrt(richtung="Musterhausen", verspaetung=20)], jetzt), [])

    def test_ausserhalb_des_fensters_geplant(self):
        w = self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        self.assertEqual(wache.betroffen(w, [abfahrt(stunde=9, verspaetung=20)], jetzt), [])

    def test_die_planzeit_entscheidet_nicht_die_prognose(self):
        """Die Bahn von 7:20, die erst um 8:05 kommt, ist GENAU der Fall für eine
        Meldung. Nach der Prognose sortiert fiele sie aus dem Fenster heraus."""
        w = self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        self.assertEqual(len(wache.betroffen(w, [abfahrt(minute=20, verspaetung=45)], jetzt)), 1)


class Melden(Mit_Wache):
    def test_meldet_eine_verspaetung(self):
        self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        with self._tafel([abfahrt(verspaetung=12)]):
            ergebnis = asyncio.run(wache.pruefen(jetzt))
        self.assertEqual(ergebnis["gemeldet"], 1)
        args = self.geschickt.call_args.args
        self.assertEqual(args[0], "fahrt")
        self.assertIn("300", args[1])
        self.assertIn("12 Min", args[1])
        self.assertIn("Bennigsen", args[2])

    def test_geht_durch_die_benachrichtigungen_und_zwar_dringend(self):
        # Eine Warnung nach der Ruhezeit ist keine Warnung mehr.
        self._anlegen()
        with self._tafel([abfahrt(verspaetung=12)]):
            asyncio.run(wache.pruefen(MONTAG.replace(hour=7, minute=5)))
        self.assertIs(self.geschickt.call_args.args[5], True)

    def test_zweimal_nachsehen_meldet_nur_einmal(self):
        """Alle drei Minuten dieselbe Nachricht machte das Handy unbenutzbar."""
        self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        with self._tafel([abfahrt(verspaetung=12)]):
            asyncio.run(wache.pruefen(jetzt))
            asyncio.run(wache.pruefen(jetzt + timedelta(minutes=3)))
        self.assertEqual(self.geschickt.call_count, 1)

    def test_deutlich_schlimmer_meldet_noch_einmal(self):
        self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        with self._tafel([abfahrt(verspaetung=10)]):
            asyncio.run(wache.pruefen(jetzt))
        with self._tafel([abfahrt(verspaetung=10 + wache.NACHSCHLAG_MIN)]):
            asyncio.run(wache.pruefen(jetzt + timedelta(minutes=3)))
        self.assertEqual(self.geschickt.call_count, 2)

    def test_ein_bisschen_schlimmer_meldet_nicht(self):
        self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        with self._tafel([abfahrt(verspaetung=10)]):
            asyncio.run(wache.pruefen(jetzt))
        with self._tafel([abfahrt(verspaetung=12)]):
            asyncio.run(wache.pruefen(jetzt + timedelta(minutes=3)))
        self.assertEqual(self.geschickt.call_count, 1)

    def test_aus_verspaetung_wird_ausfall_und_das_kommt_durch(self):
        self._anlegen()
        jetzt = MONTAG.replace(hour=7, minute=5)
        with self._tafel([abfahrt(verspaetung=10)]):
            asyncio.run(wache.pruefen(jetzt))
        with self._tafel([abfahrt(faellt_aus=True)]):
            asyncio.run(wache.pruefen(jetzt + timedelta(minutes=3)))
        self.assertEqual(self.geschickt.call_count, 2)
        self.assertIn("fällt aus", self.geschickt.call_args.args[1])

    def test_geschlossene_wachen_werden_gar_nicht_erst_abgefragt(self):
        self._anlegen()
        with self._tafel([abfahrt(verspaetung=30)]) as tafel:
            ergebnis = asyncio.run(wache.pruefen(MONTAG.replace(hour=12, minute=0)))
        self.assertEqual(tafel.call_count, 0)
        self.assertEqual(ergebnis["wachen"], 0)

    def test_eine_haltestelle_wird_nur_einmal_abgefragt(self):
        self._anlegen(linie="300")
        self._anlegen(linie="500")
        with self._tafel([abfahrt(verspaetung=12), abfahrt(linie="500", verspaetung=9)]) as tafel:
            asyncio.run(wache.pruefen(MONTAG.replace(hour=7, minute=5)))
        self.assertEqual(tafel.call_count, 1)
        self.assertEqual(self.geschickt.call_count, 2)

    def test_eine_tote_tafel_bricht_nichts_ab(self):
        self._anlegen()
        with mock.patch("kern.dienste.uestra.abfahrten", mock.AsyncMock(side_effect=OSError("weg"))):
            ergebnis = asyncio.run(wache.pruefen(MONTAG.replace(hour=7, minute=5)))
        self.assertIn("Bennigsen", ergebnis["fehler"])
        self.assertEqual(self.geschickt.call_count, 0)

    def test_am_naechsten_tag_wird_wieder_gemeldet(self):
        self._anlegen()
        with self._tafel([abfahrt(verspaetung=12)]):
            asyncio.run(wache.pruefen(MONTAG.replace(hour=7, minute=5)))
        morgen = MONTAG + timedelta(days=1)
        with self._tafel([abfahrt(verspaetung=12, tag=morgen)]):
            asyncio.run(wache.pruefen(morgen.replace(hour=7, minute=5)))
        self.assertEqual(self.geschickt.call_count, 2)


class Werkzeuge(Mit_Wache):
    def test_merken_und_aufzaehlen(self):
        with self._tafel([]):
            text = asyncio.run(fahrplan.fahrt_merken("Bennigsen", "300", "Hannover", "07:10", "07:40",
                                                     "werktags"))
        self.assertIn("Bennigsen", text)
        self.assertIn("werktags", text)
        self.assertIn("Bennigsen", asyncio.run(fahrplan.fahrten_gemerkt()))

    def test_warnt_wenn_die_art_aus_ist(self):
        # Sonst sagt Friday zu, was sie nicht halten kann.
        from kern.dienste import benachrichtigungen
        benachrichtigungen.stellen("fahrt", False)
        with self._tafel([]):
            text = asyncio.run(fahrplan.fahrt_merken("Bennigsen", "300", ab="07:10", bis="07:40"))
        self.assertIn("aus", text)

    def test_ohne_wachen_sagt_es_das(self):
        self.assertIn("keine Fahrt", asyncio.run(fahrplan.fahrten_gemerkt()))

    def test_vergessen_ueber_die_haltestelle(self):
        self._anlegen()
        self.assertIn("Nicht mehr im Blick", asyncio.run(fahrplan.fahrt_vergessen("Bennigsen")))
        self.assertEqual(wache.alle(), [])

    def test_vergessen_ueber_die_nummer(self):
        w = self._anlegen()
        asyncio.run(fahrplan.fahrt_vergessen(w["id"]))
        self.assertEqual(wache.alle(), [])

    def test_bei_mehreren_wird_gefragt_statt_geraten(self):
        self._anlegen(linie="300")
        self._anlegen(linie="500")
        text = asyncio.run(fahrplan.fahrt_vergessen("Bennigsen"))
        self.assertIn("Welche", text)
        self.assertEqual(len(wache.alle()), 2)

    def test_unbekanntes_vergessen_aendert_nichts(self):
        self._anlegen()
        asyncio.run(fahrplan.fahrt_vergessen("Kröpcke"))
        self.assertEqual(len(wache.alle()), 1)

    def test_die_werkzeuge_brauchen_den_gvh(self):
        konnektoren.setzen("gvh", False)
        from kern.werkzeuge import katalog
        namen = {f["name"] for f in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("fahrt_merken", namen)


class Schnittstelle(Mit_Wache):
    def test_liste_und_anlegen(self):
        with self._tafel([]):
            antwort = self.client.post("/api/wachen", json={"haltestelle": "Bennigsen", "linie": "300",
                                                            "ab": "07:10", "bis": "07:40"})
        self.assertEqual(antwort.status_code, 200)
        d = self.client.get("/api/wachen").json()
        self.assertEqual(len(d["wachen"]), 1)
        self.assertIn("300", d["wachen"][0]["text"])

    def test_ohne_gvh_freigabe_geht_nichts(self):
        konnektoren.setzen("gvh", False)
        with self._tafel([]):
            antwort = self.client.post("/api/wachen", json={"haltestelle": "Bennigsen"})
        self.assertEqual(antwort.status_code, 403)

    def test_haltestelle_ohne_echtzeit_gibt_400_mit_grund(self):
        with mock.patch("kern.dienste.uestra.abfahrten",
                        mock.AsyncMock(side_effect=uestra.Unbekannt("weg"))):
            antwort = self.client.post("/api/wachen", json={"haltestelle": "Nirgendwo"})
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("Echtzeit", antwort.json()["detail"])

    def test_pausieren_und_loeschen(self):
        w = self._anlegen()
        self.assertFalse(self.client.post(f"/api/wachen/{w['id']}", json={"aktiv": False}).json()["aktiv"])
        self.assertEqual(self.client.delete(f"/api/wachen/{w['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/wachen/{w['id']}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
