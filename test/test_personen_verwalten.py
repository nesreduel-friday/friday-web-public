"""Personen für OwnTracks in der App verwalten (Freigaben → Standort der Familie) — Container nachgestellt, kein Netz."""

import asyncio
import base64
import json
import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import personenstandort as dienst
from kern.main import app

WEB = __import__("pathlib").Path(__file__).resolve().parent.parent / "web"
VERW = "v" * 40


def _antwort(code, daten):
    return httpx.Response(code, json=daten, request=httpx.Request("POST", "http://friday-personenstandort:8131/x"))


ANGELEGT = {"kennung": "eike", "name": "Eike", "passwort": "geheim-passwort-123", "empfangs_url": "http://192.0.2.10:8130/pub"}


def _p(**kw):
    return {"kennung": "mama", "name": "Kontakt P", "zustand": "ok", "breite": 52.2, "laenge": 9.6, "genauigkeit_m": 15.0,
            "zeit": "2026-09-25T17:00:00Z", "alter_s": 120, "akku_prozent": 80, "regionen": ["Zuhause"]} | kw


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "PERSONENSTANDORT_SCHLUESSEL": "s" * 32,
                                                "PERSONENSTANDORT_VERWALTUNGS_SCHLUESSEL": VERW})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()


class Dienst(Basis):
    def test_anlegen_schickt_den_verwaltungsschluessel_nicht_den_lese_schluessel(self):
        ruf = mock.AsyncMock(return_value=_antwort(201, ANGELEGT))
        with mock.patch("httpx.AsyncClient.request", ruf):
            d = asyncio.run(dienst.anlegen("eike", "Eike"))
        methode, url = ruf.await_args.args[:2]
        self.assertEqual((methode, url), ("POST", "http://friday-personenstandort:8131/api/verwaltung/personen"))
        self.assertEqual(ruf.await_args.kwargs["headers"], {"X-Api-Key": VERW})
        self.assertEqual(ruf.await_args.kwargs["json"], {"kennung": "eike", "name": "Eike"})
        self.assertEqual(d["passwort"], ANGELEGT["passwort"])

    def test_ohne_verwaltungsschluessel_geht_nichts_raus(self):
        with mock.patch.dict(os.environ, {"PERSONENSTANDORT_VERWALTUNGS_SCHLUESSEL": ""}), \
             mock.patch("httpx.AsyncClient.request", mock.AsyncMock()) as ruf:
            with self.assertRaises(dienst.PersonenFehler) as f:
                asyncio.run(dienst.anlegen("eike", "Eike"))
        self.assertIn("nicht eingerichtet", str(f.exception))
        ruf.assert_not_awaited()

    def test_fehler_des_containers_kommen_als_text(self):
        for code, daten, teil in ((409, {"fehler": "Die Kennung „eike“ gibt es schon."}, "gibt es schon"),
                                  (401, {"fehler": "x"}, "schlüssel"), (404, {"fehler": "unbekannt"}, "unbekannt"),
                                  (500, {}, "Fehler")):
            with self.subTest(code), mock.patch("httpx.AsyncClient.request", mock.AsyncMock(return_value=_antwort(code, daten))):
                with self.assertRaises(dienst.PersonenFehler) as f:
                    asyncio.run(dienst.anlegen("eike", "Eike"))
                self.assertIn(teil.lower(), str(f.exception).lower())
        with mock.patch("httpx.AsyncClient.request", mock.AsyncMock(side_effect=httpx.ConnectError("aus"))):
            with self.assertRaises(dienst.PersonenFehler) as f:
                asyncio.run(dienst.pausieren("mama", True))
        self.assertIn("antwortet nicht", str(f.exception))

    def test_die_kennung_entsteht_aus_dem_namen(self):
        for name, erwartet in (("Eike", "eike"), ("Kontakt P Müller", "kontakt_p_mueller"), ("  Opa Ärzte ", "opa_aerzte"),
                               ("Anna-Lena", "anna-lena"), ("Bob!!!", "bob")):
            self.assertEqual(dienst.kennung_aus_namen(name), erwartet, name)
        for schlecht in ("", "   ", "123", "!!!", "x"):
            with self.assertRaises(ValueError):
                dienst.kennung_aus_namen(schlecht)
        self.assertLessEqual(len(dienst.kennung_aus_namen("a" * 80)), 24)

    def test_owntracks_link_enthaelt_alles_fuer_die_einrichtung_mit_einem_tipp(self):
        link = dienst.owntracks_link(ANGELEGT)
        teile = urlsplit(link)
        self.assertEqual((teile.scheme, teile.path), ("owntracks", "/config"))
        cfg = json.loads(base64.b64decode(parse_qs(teile.query)["inline"][0]))
        self.assertEqual((cfg["_type"], cfg["mode"], cfg["url"], cfg["username"], cfg["password"], cfg["auth"]),
                         ("configuration", 3, ANGELEGT["empfangs_url"], "eike", ANGELEGT["passwort"], True))
        self.assertEqual(cfg["deviceId"], "handy")

    def test_die_liste_traegt_nie_koordinaten_zum_browser(self):
        with mock.patch.object(dienst, "personen", mock.AsyncMock(return_value=[_p(), _p(kennung="pa", name="Papa", zustand="pausiert")])):
            liste = asyncio.run(dienst.verwaltungsliste())
        text = json.dumps(liste)
        for verboten in ("breite", "laenge", "52.2", "9.6", "Zuhause"):
            self.assertNotIn(verboten, text)
        self.assertEqual([(x["kennung"], x["zustand"]) for x in liste], [("mama", "ok"), ("pa", "pausiert")])
        self.assertEqual(liste[0]["alter_s"], 120)


class Routen(Basis):
    def _mit(self, **antworten):
        """Ersetzt Dienstfunktionen durch AsyncMocks; `as m` liefert sie nach Namen (patch.multiple tut das nur für DEFAULT)."""
        import contextlib

        @contextlib.contextmanager
        def gepatcht():
            mocks = {k: mock.AsyncMock(**v) for k, v in antworten.items()}
            with mock.patch.multiple(dienst, **mocks):
                yield mocks
        return gepatcht()

    def test_liste(self):
        with self._mit(verwaltungsliste={"return_value": [{"kennung": "mama", "name": "Kontakt P", "zustand": "ok"}]}):
            r = self.c.get("/api/personenstandort/personen")
        self.assertEqual((r.status_code, r.json()["personen"][0]["kennung"]), (200, "mama"))

    def test_liste_ohne_container_ist_ein_text_kein_absturz(self):
        with self._mit(verwaltungsliste={"side_effect": dienst.PersonenFehler("Der Container antwortet nicht.")}):
            r = self.c.get("/api/personenstandort/personen")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertIn("antwortet nicht", r.json()["text"])

    def test_anlegen_gibt_die_einrichtung_einmal_zurueck(self):
        with self._mit(anlegen={"return_value": ANGELEGT}) as m:
            r = self.c.post("/api/personenstandort/personen", json={"name": "Eike"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual((d["ok"], d["einrichtung"]["benutzer"], d["einrichtung"]["passwort"]), (True, "eike", ANGELEGT["passwort"]))
        self.assertTrue(d["einrichtung"]["link"].startswith("owntracks:///config?inline="))
        self.assertEqual(d["einrichtung"]["url"], ANGELEGT["empfangs_url"])
        m["anlegen"].assert_awaited_once_with("eike", "Eike")                 # Kennung aus dem Namen

    def test_anlegen_mit_eigener_kennung_und_schlechten_eingaben(self):
        with self._mit(anlegen={"return_value": ANGELEGT}) as m:
            self.assertEqual(self.c.post("/api/personenstandort/personen", json={"name": "Eike", "kennung": "ek"}).status_code, 200)
            m["anlegen"].assert_awaited_with("ek", "Eike")
            for schlecht in ({"name": ""}, {"name": "   "}, {"name": "x" * 100}, {}, {"name": "Eike", "kennung": "A b"}, {"name": "!!!"}):
                r = self.c.post("/api/personenstandort/personen", json=schlecht)
                self.assertIn(r.status_code, (200, 400, 422), schlecht)
                if r.status_code == 200:
                    self.assertFalse(r.json()["ok"], schlecht)

    def test_neues_passwort_pausieren_entfernen(self):
        with self._mit(neues_passwort={"return_value": {"kennung": "mama", "passwort": "neu-neu-neu-123", "empfangs_url": ANGELEGT["empfangs_url"]}},
                       pausieren={"return_value": {"ok": True}}, entfernen={"return_value": {"ok": True}}) as m:
            r = self.c.post("/api/personenstandort/personen/mama/token")
            self.assertEqual(r.json()["einrichtung"]["passwort"], "neu-neu-neu-123")
            self.assertEqual(self.c.post("/api/personenstandort/personen/mama/pause", json={"pause": True}).status_code, 200)
            m["pausieren"].assert_awaited_with("mama", True)
            self.assertEqual(self.c.delete("/api/personenstandort/personen/mama").status_code, 200)
            m["entfernen"].assert_awaited_with("mama")

    def test_fremde_herkunft_und_schlechte_kennung(self):
        boese = {"Origin": "https://boese.example"}
        with self._mit(anlegen={"return_value": ANGELEGT}, entfernen={"return_value": {"ok": True}}) as m:
            self.assertEqual(self.c.post("/api/personenstandort/personen", json={"name": "Eike"}, headers=boese).status_code, 403)
            self.assertEqual(self.c.delete("/api/personenstandort/personen/mama", headers=boese).status_code, 403)
            self.assertEqual(self.c.post("/api/personenstandort/personen/mama/pause", json={"pause": True}, headers=boese).status_code, 403)
            self.assertEqual(self.c.post("/api/personenstandort/personen/mama/token", headers=boese).status_code, 403)
            for k in ("A b", "..%2Fx", "x" * 40):
                self.assertIn(self.c.delete(f"/api/personenstandort/personen/{k}").status_code, (400, 404, 405, 422), k)
            m["anlegen"].assert_not_awaited()
            m["entfernen"].assert_not_awaited()

    def test_das_passwort_steht_nur_in_der_antwort_und_nie_im_protokoll(self):
        with self._mit(anlegen={"return_value": ANGELEGT}), self.assertLogs(level="DEBUG") as log:
            import logging
            logging.getLogger("test").debug("start")
            self.c.post("/api/personenstandort/personen", json={"name": "Eike"})
        self.assertNotIn(ANGELEGT["passwort"], "\n".join(log.output))
        with self._mit(verwaltungsliste={"return_value": [{"kennung": "eike", "name": "Eike", "zustand": "noch_nie"}]}):
            self.assertNotIn(ANGELEGT["passwort"], self.c.get("/api/personenstandort/personen").text)


class Seite(unittest.TestCase):
    def test_personen_seite(self):
        js = (WEB / "personen.js").read_text(encoding="utf-8")
        html = (WEB / "index.html").read_text(encoding="utf-8")
        frei = (WEB / "freigaben.js").read_text(encoding="utf-8")
        self.assertIn('<script src="/personen.js"></script>', html)
        self.assertIn("FridayPersonen", frei)
        self.assertIn("k.id === 'personenstandort'", frei)
        self.assertNotIn("innerHTML", js)
        self.assertIn("owntracks:///config?inline=", js)                     # der Link wird vor dem Benutzen geprüft
        for weg in ("/api/personenstandort/personen", "'DELETE'", "/pause", "/token"):
            self.assertIn(weg, js)
        self.assertIn("Endgültig entfernen", js)                              # erst fragen, dann löschen
        self.assertIn("nur jetzt", js.lower())                                # das Passwort gibt es nur einmal
        self.assertIn("Allow external configuration", js)                     # OwnTracks lehnt den Link sonst ab (25.09.2026 gemeldet)


if __name__ == "__main__":
    unittest.main()
