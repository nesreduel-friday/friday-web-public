"""Personenstandort: Namenssuche, Sätze, Grenzen — ohne Netz (Container und Nominatim nachgestellt)."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

import httpx

from kern import konnektoren
from kern.dienste import personenstandort as dienst
from kern.dienste import standort
from kern.werkzeuge import katalog
from kern.werkzeuge import personenstandort as werkzeug


def _p(name="Kontakt P", kennung="mama", alter=120, genau=15.0, **kw):
    return {"kennung": kennung, "name": name, "zustand": "ok", "breite": 52.2, "laenge": 9.6,
            "genauigkeit_m": genau, "zeit": "2026-09-25T17:00:00Z", "alter_s": alter,
            "akku_prozent": 80, "regionen": []} | kw


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner,
                                                "PERSONENSTANDORT_SCHLUESSEL": "s" * 32})
        self.env.start()
        konnektoren.setzen("personenstandort", True)
        self.ort = mock.patch.object(standort, "ortsname", mock.AsyncMock(return_value="Bahnhofstraße, Musterhausen"))
        self.ort.start()

    def tearDown(self):
        self.ort.stop()
        self.env.stop()

    def liste(self, liste):
        return mock.patch.object(dienst, "personen", mock.AsyncMock(return_value=liste))


class FindenTest(unittest.TestCase):
    L = [_p("Kontakt P", "mama"), _p("Papa", "papa"), _p("Oma Ilse", "oma")]

    def test_genau_anfang_teilwort(self):
        self.assertEqual([p["kennung"] for p in dienst.finden("mama", self.L)], ["mama"])
        self.assertEqual([p["kennung"] for p in dienst.finden("Oma", self.L)], ["oma"])
        self.assertEqual([p["kennung"] for p in dienst.finden("ilse", self.L)], ["oma"])

    def test_unbekannt_und_leer(self):
        self.assertEqual(dienst.finden("Onkel", self.L), [])
        self.assertEqual(dienst.finden("", self.L), [])


class WerkzeugTest(Basis):
    def test_satz_mit_ort_alter_genauigkeit(self):
        with self.liste([_p()]):
            satz = lauf(werkzeug.wo_ist("Kontakt P"))
        self.assertIn("Kontakt P war vor 2 Minuten in der Nähe von Bahnhofstraße, Musterhausen", satz)
        self.assertIn("Genauigkeit rund 20 m", satz)

    def test_region_hat_vorrang(self):
        with self.liste([_p(regionen=["Zuhause"])]):
            self.assertIn("bei Zuhause", lauf(werkzeug.wo_ist("mama")))

    def test_grob_und_alt_werden_gekennzeichnet(self):
        with self.liste([_p(genau=2000.0)]):
            satz = lauf(werkzeug.wo_ist("Kontakt P"))
        self.assertIn("vermutlich", satz)
        self.assertIn("grob", satz)
        with self.liste([_p(alter=5 * 3600)]):
            self.assertIn("schon eine Weile her", lauf(werkzeug.wo_ist("Kontakt P")))

    def test_pausiert_und_noch_nie_ohne_koordinaten(self):
        with self.liste([{"kennung": "mama", "name": "Kontakt P", "zustand": "pausiert"},
                         {"kennung": "papa", "name": "Papa", "zustand": "noch_nie"}]):
            satz = lauf(werkzeug.wo_ist(""))
        self.assertIn("Kontakt P hat das Teilen gerade pausiert", satz)
        self.assertIn("Papa hat noch keinen Standort gemeldet", satz)

    def test_ohne_name_alle(self):
        with self.liste([_p(), _p("Papa", "papa")]):
            satz = lauf(werkzeug.wo_ist("alle"))
        self.assertIn("Kontakt P war", satz)
        self.assertIn("Papa war", satz)

    def test_unbekannter_name_nennt_die_eingerichteten(self):
        with self.liste([_p()]):
            self.assertIn("Eingerichtet sind: Kontakt P", lauf(werkzeug.wo_ist("Onkel")))

    def test_mehrdeutig_fragt_nach(self):
        with self.liste([_p("Oma Ilse", "ilse"), _p("Oma Rita", "rita")]):
            self.assertIn("Wen meinst du", lauf(werkzeug.wo_ist("Oma")))

    def test_fremder_text_bekommt_nichts(self):
        with self.liste([_p()]) as m:
            self.assertIn("fremdem Text", lauf(werkzeug.wo_ist("Kontakt P", fremde_inhalte=True)))
        m.assert_not_called()

    def test_container_aus_gibt_ehrliche_antwort(self):
        with mock.patch.object(dienst, "personen", mock.AsyncMock(side_effect=httpx.ConnectError("x"))):
            self.assertIn("nicht an die Standorte", lauf(werkzeug.wo_ist("Kontakt P")))

    def test_kein_ortsname_kein_absturz(self):
        with self.liste([_p()]), mock.patch.object(standort, "ortsname", mock.AsyncMock(side_effect=OSError)):
            self.assertIn("Namen ich gerade nicht nachschlagen", lauf(werkzeug.wo_ist("Kontakt P")))


class DienstTest(Basis):
    def test_ohne_schluessel_kein_aufruf(self):
        with mock.patch.dict(os.environ, {"PERSONENSTANDORT_SCHLUESSEL": ""}):
            with self.assertRaises(RuntimeError):
                lauf(dienst.personen())

    def test_schluessel_geht_im_kopf_mit(self):
        antwort = httpx.Response(200, json={"personen": [_p()]}, request=httpx.Request("GET", "http://x/"))
        with mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=antwort)) as get:
            self.assertEqual(len(lauf(dienst.personen())), 1)
        self.assertEqual(get.call_args.kwargs["headers"], {"X-Api-Key": "s" * 32})
        self.assertTrue(get.call_args.args[0].endswith("/api/personen"))


class KatalogTest(Basis):
    def _namen(self):
        return [e["name"] for t in katalog.gemini_tools() for e in t["functionDeclarations"]]

    def test_default_deny(self):
        konnektoren.setzen("personenstandort", False)
        self.assertNotIn("wo_ist", self._namen())
        self.assertIn("nicht freigegeben", lauf(katalog.ausfuehren("wo_ist", {"name": "Kontakt P"})).get("fehler", ""))

    def test_freigegeben_sichtbar_und_ausfuehrbar(self):
        self.assertIn("wo_ist", self._namen())
        with self.liste([_p()]):
            aus = lauf(katalog.ausfuehren("wo_ist", {"name": "Kontakt P"}))
        self.assertIn("Kontakt P war", aus["ergebnis"])

    def test_fremder_text_im_zug_wird_abgelehnt(self):
        with self.liste([_p()]):
            aus = lauf(katalog.ausfuehren("wo_ist", {"name": "Kontakt P"}, fremde_inhalte=True))
        self.assertIn("fremdem Text", aus["ergebnis"])


if __name__ == "__main__":
    unittest.main()
