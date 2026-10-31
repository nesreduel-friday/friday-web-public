"""Flamme mit Eike selbst (14.10.2026): Schalter, Lauf, Prompt-Hinweis, Route — Netz und Handy sind Attrappen."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern import config
from kern.dienste import benachrichtigungen
from kern.main import app
from kern.personen import flammen, register

B = ZoneInfo("Europe/Berlin")
JETZT = datetime(2026, 10, 14, 15, 0, tzinfo=B)


def lauf(c):
    return asyncio.run(c)


async def bild(pid, name):
    return (b"JPEG", "https://beispiel.test/e.jpg", "Katze")


def einstellungen(**kw):
    ordner = config.daten_ordner() / "snapchat"
    ordner.mkdir(parents=True, exist_ok=True)
    p = ordner / "einstellungen.json"
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    d.update(kw)
    p.write_text(json.dumps(d), encoding="utf-8")


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        einstellungen(flammen="an", ticket_annahme="an")
        self.auftraege = []

        def auftrag(art, **kw):
            self.auftraege.append((art, kw))
            return f"id{len(self.auftraege)}"
        self.patches = [
            mock.patch("kern.personen.flammen.snapchat.snap_wartet", return_value=False),
            mock.patch("kern.personen.flammen.snapchat.auftrag_legen", side_effect=auftrag),
            mock.patch("kern.personen.flammen.snapbild.vorbereiten", return_value=b"J"),
            mock.patch("kern.personen.flammen.snapbild.ablegen", return_value="snapchat/ausgang/x.jpg"),
            mock.patch("kern.dienste.benachrichtigungen.senden", return_value={}),
            mock.patch("kern.personen.flammen.konnektoren.benutzbar", return_value=True),
            mock.patch("kern.personen.flammen.konnektoren.schreiben_erlaubt", return_value=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()


class Schalter(Basis):
    def test_standard_aus_und_setzen_behaelt_die_anderen_einstellungen(self):
        self.assertFalse(flammen.eike_an())
        flammen.eike_setzen(True)
        self.assertTrue(flammen.eike_an())
        d = json.loads((config.daten_ordner() / "snapchat" / "einstellungen.json").read_text(encoding="utf-8"))
        self.assertEqual((d["flammen"], d["ticket_annahme"], d["flammen_eike"]), ("an", "an", True))
        flammen.eike_setzen(False)
        self.assertFalse(flammen.eike_an())

    def test_nur_echtes_true_zaehlt(self):
        einstellungen(flammen_eike="ja")
        self.assertFalse(flammen.eike_an())


class Lauf(Basis):
    def test_snap_an_eike_ohne_person_id(self):
        flammen.eike_setzen(True)
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), ["eike"])
        art, kw = self.auftraege[0]
        self.assertEqual(art, "snap")
        self.assertIsNone(kw["person"])                                   # kein Register-Eintrag: der Dienst nimmt Eikes Konto
        z = flammen.zustand_lesen()["personen"]["eike"]
        self.assertEqual(z["auftrag"], "id1")
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])           # Auftrag offen

    def test_hebel_aus_nichts(self):
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        self.assertEqual(self.auftraege, [])

    def test_trocken_schreibt_nur_das_protokoll(self):
        einstellungen(flammen="trocken")
        flammen.eike_setzen(True)
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), [])
        probe = (config.daten_ordner() / "snapchat" / "flammen_probe.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(probe[0])["person"], "eike")
        self.assertEqual(self.auftraege, [])

    def test_eike_kommt_vor_den_personen(self):
        p = register.anlegen("Kontakt B Muster", snapchat="maya_x")
        register.aendern(p["id"], module={"flammen": True})
        flammen.eike_setzen(True)
        self.assertEqual(lauf(flammen.einmal(JETZT, bild)), ["eike"])
        with mock.patch("kern.personen.flammen.snapchat.snap_wartet", return_value=True):    # sein Snap liegt noch beim Helfer
            self.assertEqual(lauf(flammen.einmal(JETZT + timedelta(minutes=1), bild)), [])
        self.assertEqual(lauf(flammen.einmal(JETZT + timedelta(minutes=2), bild)), [p["id"]])      # danach ist sie dran

    def test_nach_dem_senden_ist_heute_erledigt(self):
        flammen.eike_setzen(True)
        lauf(flammen.einmal(JETZT, bild))
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "gesendet"}):
            self.assertEqual(lauf(flammen.einmal(JETZT + timedelta(minutes=3), bild)), [])
        z = flammen.zustand_lesen()["personen"]["eike"]
        self.assertEqual(z["letzter_tag"], "2026-10-14")

    def test_hebel_aus_keine_stoerung(self):
        flammen.zustand_schreiben({"personen": {"eike": {**flammen._neu(), "auftrag": "x", "fehler": 2}}})
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "aufgegeben"}), \
                mock.patch("kern.personen.flammen.stoerung.melden") as stoerung_melden:
            flammen.abrechnen(flammen.zustand_lesen(), JETZT, melden=True)
        stoerung_melden.assert_not_called()

    def test_pause_meldet_stoerung_und_einmal_aufs_handy(self):
        flammen.eike_setzen(True)
        flammen.zustand_schreiben({"personen": {"eike": {**flammen._neu(), "auftrag": "x", "fehler": 2}}})
        with mock.patch("kern.personen.flammen.snapchat.ergebnis_fuer", return_value={"ergebnis": "aufgegeben"}), \
                mock.patch("kern.personen.flammen.stoerung.melden") as stoerung_melden:
            z = flammen.zustand_lesen()
            flammen.abrechnen(z, JETZT, melden=True)
        benachrichtigungen.senden.assert_called_once()
        self.assertEqual(stoerung_melden.call_args.args[:2], ("Flammen-Snap an Eike", "flamme:eike"))
        self.assertIsNotNone(z["personen"]["eike"]["pause_bis"])


class Hinweis(Basis):
    def test_prompt_hinweis_nur_wenn_flamme_wirklich_laeuft(self):
        self.assertEqual(flammen.eike_kontext(), "")
        flammen.eike_setzen(True)
        self.assertIn("Flamme", flammen.eike_kontext())
        einstellungen(flammen="trocken")
        self.assertEqual(flammen.eike_kontext(), "")
        einstellungen(flammen="aus")
        self.assertEqual(flammen.eike_kontext(), "")


class Route(Basis):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)
        self.kopf = {}

    def test_setzen_und_lesen(self):
        r = self.c.post("/api/flamme-eike", json={"an": True})
        self.assertTrue(r.json()["ok"], r.text)
        d = self.c.get("/api/kontakte").json()
        self.assertTrue(d["flammen_eike"])
        self.assertEqual(d["flammen_modus"], "an")
        self.c.post("/api/flamme-eike", json={"an": False})
        self.assertFalse(self.c.get("/api/kontakte").json()["flammen_eike"])

    def test_fremder_ursprung_wird_abgelehnt(self):
        r = self.c.post("/api/flamme-eike", json={"an": True}, headers={"Origin": "http://boese.example"})
        self.assertGreaterEqual(r.status_code, 400)
        self.assertFalse(flammen.eike_an())

    def test_kein_werkzeug_fuer_das_modell(self):
        from kern.werkzeuge import katalog
        self.assertNotIn("flamme_eike", {n for m in katalog._MODULE for n in getattr(m, "WERKZEUGE", {})})


class ModusRoute(Basis):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)

    def test_modus_setzen_behaelt_andere_schluessel(self):
        einstellungen(flammen="aus", ticket_annahme="an")
        for m in ("trocken", "an", "aus"):
            r = self.c.post("/api/flammen-modus", json={"modus": m})
            self.assertEqual(r.json()["modus"], m, r.text)
            self.assertEqual(self.c.get("/api/kontakte").json()["flammen_modus"], m)
        self.assertEqual(flammen.modus(), "aus")
        e = json.loads((flammen._einstellungen_pfad()).read_text(encoding="utf-8"))
        self.assertEqual(e["ticket_annahme"], "an")

    def test_ungueltig_und_fremder_ursprung(self):
        self.assertEqual(self.c.post("/api/flammen-modus", json={"modus": "x"}).status_code, 400)
        r = self.c.post("/api/flammen-modus", json={"modus": "an"}, headers={"Origin": "http://boese.example"})
        self.assertGreaterEqual(r.status_code, 400)
