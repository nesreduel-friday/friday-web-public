"""Web Push: Schlüssel, Geräte, Senden — ohne Netz."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from kern.dienste import push
from kern.main import app

ANMELDUNG = {"endpoint": "https://web.push.apple.com/abc",
             "keys": {"p256dh": "BPk...", "auth": "xyz"}}
ZWEITE = {"endpoint": "https://web.push.apple.com/zwei",
          "keys": {"p256dh": "BPk...", "auth": "abc"}}


class Schluessel(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_einmal_erzeugt_dann_wiederverwendet(self):
        erst = push.oeffentlicher_schluessel()
        self.assertGreater(len(erst), 80)
        datei = Path(self.ordner) / "push_schluessel.pem"
        self.assertTrue(datei.exists())
        self.assertEqual(stat.S_IMODE(datei.stat().st_mode), 0o600)   # privat bleibt privat
        self.assertEqual(push.oeffentlicher_schluessel(), erst)        # kein neuer Schlüssel


class Geraete(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_anmelden_ohne_dubletten_und_ohne_schluessel_nach_aussen(self):
        push.anmelden(ANMELDUNG, "iPhone")
        push.anmelden(ANMELDUNG, "iPhone")                 # dieselbe Adresse: ersetzt
        self.assertEqual(push.anmelden(ZWEITE, "iPad"), 2)
        aussen = json.dumps(push.geraete())
        self.assertNotIn("auth", aussen)
        self.assertNotIn("xyz", aussen)
        self.assertIn("web.push.apple.com", aussen)
        datei = Path(self.ordner) / "push_geraete.json"
        self.assertEqual(stat.S_IMODE(datei.stat().st_mode), 0o600)

    def test_unvollstaendige_anmeldung(self):
        with self.assertRaises(ValueError):
            push.anmelden({"endpoint": "https://x/1"})

    def test_abmelden(self):
        push.anmelden(ANMELDUNG)
        self.assertEqual(push.abmelden(ANMELDUNG["endpoint"]), 1)
        self.assertEqual(push.geraete(), [])


class Senden(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        push.anmelden(ANMELDUNG, "iPhone")
        push.anmelden(ZWEITE, "iPad")

    def tearDown(self):
        self.env.stop()

    def test_an_alle_geraete_mit_inhalt(self):
        with mock.patch("pywebpush.webpush") as w:
            r = push.senden("Friday", "Zeit für Mathe", url="/#chat", kennung="erinnerung")
        self.assertEqual((r["gesendet"], r["geraete"], r["abgemeldet"]), (2, 2, 0))
        inhalt = json.loads(w.call_args.kwargs["data"])
        self.assertEqual(inhalt["titel"], "Friday")
        self.assertEqual(inhalt["text"], "Zeit für Mathe")
        self.assertEqual(inhalt["tag"], "erinnerung")
        self.assertEqual(w.call_args.kwargs["vapid_claims"], {"sub": push.KONTAKT})

    def test_verschwundenes_geraet_wird_ausgetragen(self):
        from pywebpush import WebPushException
        fehler = WebPushException("weg", response=mock.Mock(status_code=410))

        def antwort(**kw):
            if kw["subscription_info"]["endpoint"] == ANMELDUNG["endpoint"]:
                raise fehler
            return None

        with mock.patch("pywebpush.webpush", side_effect=antwort):
            r = push.senden("Friday", "Hallo")
        self.assertEqual((r["gesendet"], r["abgemeldet"]), (1, 1))
        self.assertEqual([g["geraet"] for g in push.geraete()], ["iPad"])

    def test_anderer_fehler_traegt_nicht_aus(self):
        from pywebpush import WebPushException
        with mock.patch("pywebpush.webpush",
                        side_effect=WebPushException("kaputt", response=mock.Mock(status_code=500))):
            r = push.senden("Friday", "Hallo")
        self.assertEqual((r["gesendet"], r["abgemeldet"]), (0, 0))
        self.assertEqual(len(push.geraete()), 2)                      # bleiben angemeldet

    def test_ohne_geraete_passiert_nichts(self):
        for g in (ANMELDUNG, ZWEITE):
            push.abmelden(g["endpoint"])
        with mock.patch("pywebpush.webpush") as w:
            self.assertEqual(push.senden("x", "y")["gesendet"], 0)
        w.assert_not_called()


class Seite(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_schluessel_anmelden_test_abmelden(self):
        s = self.c.get("/api/push/schluessel").json()["schluessel"]
        self.assertGreater(len(s), 80)
        self.assertEqual(self.c.post("/api/push/anmelden",
                                     json={"anmeldung": ANMELDUNG, "geraet": "iPhone"}).json()["geraete"], 1)
        with mock.patch("pywebpush.webpush") as w:
            r = self.c.post("/api/push/test").json()
        self.assertEqual(r["gesendet"], 1)
        w.assert_called_once()
        self.assertEqual(self.c.get("/api/push/geraete").json()[0]["geraet"], "iPhone")
        self.assertEqual(self.c.post("/api/push/abmelden",
                                     json={"endpoint": ANMELDUNG["endpoint"]}).json()["entfernt"], 1)

    def test_unvollstaendige_anmeldung_wird_abgelehnt(self):
        r = self.c.post("/api/push/anmelden", json={"anmeldung": {"endpoint": "https://x/1"}})
        self.assertEqual(r.status_code, 400)


class Kontakt(unittest.TestCase):
    """Apple wies alles mit ungültiger Domain ab — der Standardkontakt muss taugen."""

    def test_gueltige_mailto_adresse(self):
        self.assertTrue(push.KONTAKT.startswith("mailto:"))
        adresse = push.KONTAKT.removeprefix("mailto:")
        self.assertIn("@", adresse)
        endung = adresse.rsplit(".", 1)[-1]
        self.assertNotIn(endung, ("local", "invalid", "localhost", "test"))


class GrundMelden(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        push.anmelden(ANMELDUNG, "iPhone")

    def tearDown(self):
        self.env.stop()

    def test_grund_wird_durchgereicht_statt_verschluckt(self):
        from pywebpush import WebPushException
        antwort = mock.Mock(status_code=403, text='{"reason":"BadJwtToken"}')
        with mock.patch("pywebpush.webpush", side_effect=WebPushException("nein", response=antwort)):
            r = push.senden("Friday", "Hallo")
        self.assertEqual(r["gesendet"], 0)
        self.assertIn("403", r["grund"])
        self.assertIn("BadJwtToken", r["grund"])

    def test_endpunkt_meldet_den_grund_im_klartext(self):
        from pywebpush import WebPushException
        c = TestClient(app)
        antwort = mock.Mock(status_code=403, text='{"reason":"BadJwtToken"}')
        with mock.patch("pywebpush.webpush", side_effect=WebPushException("nein", response=antwort)):
            r = c.post("/api/push/test", json={"verzoegerung": 0}).json()
        self.assertIn("abgelehnt", r["text"])
        self.assertIn("BadJwtToken", r["text"])

    def test_verzoegerter_test_sendet_nicht_sofort(self):
        c = TestClient(app)
        with mock.patch("pywebpush.webpush") as w:
            r = c.post("/api/push/test", json={"verzoegerung": 10}).json()
        self.assertEqual(r["geplant"], 10)
        self.assertIn("10 Sekunden", r["text"])
        w.assert_not_called()          # kommt erst später, App kann geschlossen werden


class Zustandsseite(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "k"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_zeigt_den_echten_stand(self):
        d = TestClient(app).get("/api/zustand").json()
        from kern import config
        self.assertEqual(d["version"], config.VERSION)
        self.assertEqual(d["phase"], config.PHASE)
        self.assertGreaterEqual(d["schluessel"]["gespraech"], 1)
        self.assertIn("konnektoren", d)
        self.assertIn("gedaechtnis", d)
        self.assertEqual(d["push"]["geraete"], 0)
        self.assertTrue(d["push"]["kontakt"].startswith("mailto:"))
