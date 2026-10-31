"""Home Assistant als Standortquelle — nur lesen, ohne Netz (HTTP nachgestellt)."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx

from kern import konnektoren
from kern.dienste import homeassistant as dienst
from kern.dienste import standort


def lauf(coro):
    return asyncio.run(coro)


def _state(alter_s=60, state="home", **attribute):
    a = {"latitude": 52.25, "longitude": 9.67, "gps_accuracy": 20}
    a.update(attribute)
    return {"entity_id": "person.eike", "state": state, "attributes": a,
            "last_updated": (datetime.now(timezone.utc) - timedelta(seconds=alter_s)).isoformat(timespec="seconds")}


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner, "HOMEASSISTANT_ENTITAET": ""})
        self.env.start()
        konnektoren.zugang_setzen("homeassistant", "http://homeassistant:8123/", "tok-123")
        konnektoren.setzen("homeassistant", True)

    def tearDown(self):
        self.env.stop()

    def _get(self, daten=None, code=200):
        antwort = httpx.Response(code, json=daten if daten is not None else _state(),
                                 request=httpx.Request("GET", "http://homeassistant:8123/api/states/person.eike"))
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=antwort))


class Lesen(Basis):
    def test_position_mit_genauigkeit_und_alter(self):
        with self._get(_state(alter_s=90)):
            p = lauf(dienst.position())
        self.assertEqual(p["quelle"], "Home Assistant")
        self.assertEqual((p["breite"], p["laenge"]), (52.25, 9.67))
        self.assertEqual(p["genau"], 20)
        self.assertAlmostEqual(p["alter_s"], 90, delta=2)

    def test_token_geht_als_bearer_und_nie_in_die_adresse(self):
        with self._get() as get:
            lauf(dienst.position())
        self.assertEqual(get.call_args.args[0], "http://homeassistant:8123/api/states/person.eike")
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer tok-123"})
        self.assertNotIn("tok-123", get.call_args.args[0])

    def test_entitaet_ist_einstellbar(self):
        with mock.patch.dict(os.environ, {"HOMEASSISTANT_ENTITAET": "device_tracker.eikes_iphone"}), self._get() as get:
            lauf(dienst.position())
        self.assertTrue(get.call_args.args[0].endswith("/api/states/device_tracker.eikes_iphone"))

    def test_kurzer_name_wird_zu_person(self):
        with mock.patch.dict(os.environ, {"HOMEASSISTANT_ENTITAET": "Eike"}), self._get() as get:
            lauf(dienst.position())
        self.assertTrue(get.call_args.args[0].endswith("/api/states/person.eike"))

    def test_zahlen_als_text_werden_gelesen(self):
        with self._get(_state(latitude="52.25", longitude="9.67", gps_accuracy="15")):
            p = lauf(dienst.position())
        self.assertEqual((p["breite"], p["laenge"], p["genau"]), (52.25, 9.67, 15))

    def test_unbekannte_oder_unbrauchbare_position_gibt_none(self):
        for daten in (_state(state="unavailable"), _state(state="unknown"),
                      _state(latitude=None), _state(longitude=None), {"state": "home", "attributes": {}}):
            with self.subTest(daten), self._get(daten):
                self.assertIsNone(lauf(dienst.position()))

    def test_ohne_genauigkeit_bleibt_sie_unbekannt(self):
        with self._get(_state(**{"gps_accuracy": None})):
            self.assertIsNone(lauf(dienst.position())["genau"])

    def test_fehlende_oder_kaputte_zeit_gibt_keine_position(self):
        daten = _state()
        daten["last_updated"] = "kaputt"
        with self._get(daten):
            self.assertIsNone(lauf(dienst.position()))


class Fehler(Basis):
    def test_ohne_zugang_kein_aufruf(self):
        konnektoren.zugang_loeschen("homeassistant")
        with self._get() as get, self.assertRaises(dienst.HomeAssistantFehler):
            lauf(dienst.position())
        get.assert_not_called()

    def test_falsches_token_und_unbekannte_entitaet_mit_klartext(self):
        for code, teil in ((401, "Token"), (403, "Token"), (404, "Entität")):
            with self.subTest(code), self._get({"message": "x"}, code), \
                    self.assertRaises(dienst.HomeAssistantFehler) as f:
                lauf(dienst.position())
            self.assertIn(teil, str(f.exception))

    def test_dienstfehler_und_netzfehler_werden_eigener_fehler(self):
        with self._get({}, 500), self.assertRaises(dienst.HomeAssistantFehler):
            lauf(dienst.position())
        with mock.patch("httpx.AsyncClient.get", mock.AsyncMock(side_effect=httpx.ConnectError("x"))), \
                self.assertRaises(dienst.HomeAssistantFehler):
            lauf(dienst.position())


class Pruefen(Basis):
    def test_sagt_verbunden_ohne_koordinaten(self):
        with self._get(_state(alter_s=120)):
            ok, text = lauf(dienst.pruefen())
        self.assertTrue(ok)
        self.assertIn("person.eike", text)
        self.assertIn("home", text)
        self.assertIn("2 Minuten", text)
        self.assertIn("20 m", text)
        for teil in ("52.25", "9.67", "52,25"):
            self.assertNotIn(teil, text)

    def test_ohne_position_sagt_es_das(self):
        with self._get(_state(state="unavailable")):
            ok, text = lauf(dienst.pruefen())
        self.assertFalse(ok)
        self.assertIn("unavailable", text)


class Kandidaten(Basis):
    """`eigene_kandidaten` ist die gemeinsame Quelle für Ankunft und Route."""

    def test_home_assistant_ist_eine_quelle(self):
        erwartet = {"quelle": "Home Assistant", "breite": 52.25, "laenge": 9.67,
                    "genau": 20, "alter_s": 60, "geraet": ""}
        with mock.patch("kern.konnektoren.benutzbar", side_effect=lambda k: k == "homeassistant"), \
                mock.patch.object(dienst, "position", mock.AsyncMock(return_value=erwartet)):
            self.assertEqual(lauf(standort.eigene_kandidaten(frisch=False)), [erwartet])

    def test_ein_fehler_in_home_assistant_kostet_die_anderen_quellen_nicht(self):
        with mock.patch("kern.konnektoren.benutzbar", side_effect=lambda k: k == "homeassistant"), \
                mock.patch.object(dienst, "position", mock.AsyncMock(side_effect=RuntimeError("weg"))):
            self.assertEqual(lauf(standort.eigene_kandidaten(frisch=False)), [])


class Konnektor(unittest.TestCase):
    def test_rein_lesend_mit_link_und_ohne_werkzeuge(self):
        k = konnektoren.KONNEKTOREN["homeassistant"]
        self.assertFalse(k.get("schreibbar"))
        self.assertEqual(k["werkzeuge"], {})
        self.assertFalse(k.get("ohne_zugang"))                   # Adresse + Token sind Pflicht
        self.assertTrue(konnektoren.hilfe_url("homeassistant").startswith("https://"))
        self.assertIn("Token", k["passwort_feld"])


if __name__ == "__main__":
    unittest.main()
