"""Flammen: Fälligkeit, Zustand, Schalter — ohne Netz, im Temp-Ordner."""
import json
import os
import stat
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from kern import config
from kern.personen import flammen

B = ZoneInfo("Europe/Berlin")


def zeit(h, m=0, tag=14):
    return datetime(2026, 10, tag, h, m, tzinfo=B)


def neu(**kw):
    z = {"letzter": None, "letzter_tag": None, "auftrag": None, "fehler": 0, "pause_bis": None, "bilder": [], "probe_tag": None}
    z.update(kw)
    return z


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Faellig(Basis):
    def fruehestens(self, pid="p1"):
        return zeit(7) + timedelta(minutes=flammen.versatz(pid, date(2026, 10, 14)))

    def test_versatz_ist_stabil_und_im_bereich(self):
        a = flammen.versatz("p1", date(2026, 10, 14))
        self.assertEqual(a, flammen.versatz("p1", date(2026, 10, 14)))
        self.assertTrue(0 <= a <= flammen.VERSATZ_MAX_MIN)
        self.assertGreater(len({flammen.versatz(f"p{i}", date(2026, 10, 14)) for i in range(1, 30)}), 5)

    def test_vor_dem_fenster_nicht(self):
        self.assertFalse(flammen.faellig(neu(), "p1", zeit(6, 59)))
        self.assertFalse(flammen.faellig(neu(), "p1", self.fruehestens() - timedelta(minutes=1)))
        self.assertTrue(flammen.faellig(neu(), "p1", self.fruehestens()))

    def test_im_fenster_ja_und_ab_21_30_nicht(self):
        self.assertTrue(flammen.faellig(neu(), "p1", zeit(21, 29)))
        self.assertFalse(flammen.faellig(neu(), "p1", zeit(21, 30)))
        self.assertFalse(flammen.faellig(neu(), "p1", zeit(22, 45)))

    def test_ruhezeit_kante_morgens(self):
        self.assertFalse(flammen.faellig(neu(), "p1", zeit(6, 30)))

    def test_nur_ein_snap_je_kalendertag(self):
        self.assertFalse(flammen.faellig(neu(letzter_tag="2026-10-14"), "p1", zeit(15)))

    def test_zwanzig_stunden_abstand(self):
        z = neu(letzter=(zeit(15) - timedelta(hours=19, minutes=59)).isoformat(), letzter_tag="2026-10-13")
        self.assertFalse(flammen.faellig(z, "p1", zeit(15)))
        z = neu(letzter=(zeit(15) - timedelta(hours=20)).isoformat(), letzter_tag="2026-10-13")
        self.assertTrue(flammen.faellig(z, "p1", zeit(15)))

    def test_offener_auftrag_blockiert(self):
        self.assertFalse(flammen.faellig(neu(auftrag="abc"), "p1", zeit(15)))

    def test_pause_blockiert_bis_sie_vorbei_ist(self):
        z = neu(pause_bis=(zeit(15) + timedelta(hours=1)).isoformat())
        self.assertFalse(flammen.faellig(z, "p1", zeit(15)))
        self.assertTrue(flammen.faellig(z, "p1", zeit(16, 1)))

    def test_kaputte_zeit_im_zustand_blockiert_nicht_und_stuerzt_nicht(self):
        self.assertTrue(flammen.faellig(neu(letzter="kaputt", pause_bis="auch kaputt"), "p1", zeit(15)))


    def test_zeit_ohne_zeitzone_stuerzt_nicht(self):
        self.assertTrue(flammen.faellig(neu(letzter="2026-10-13T10:00:00", pause_bis="2026-10-14T23:00:00"), "p1", zeit(15)))


class Zustand(Basis):
    def test_roundtrip_mit_rechten_600(self):
        flammen.zustand_schreiben({"personen": {"p1": neu(fehler=2)}})
        self.assertEqual(flammen.zustand_lesen()["personen"]["p1"]["fehler"], 2)
        modus = os.stat(config.daten_ordner() / flammen.DATEI).st_mode
        self.assertEqual(stat.S_IMODE(modus), 0o600)

    def test_kaputte_datei_liest_sich_leer(self):
        (config.daten_ordner() / flammen.DATEI).write_text("{kaputt", encoding="utf-8")
        self.assertEqual(flammen.zustand_lesen(), {"personen": {}})

    def test_modus_standard_aus_und_nur_bekannte_werte(self):
        self.assertEqual(flammen.modus(), "aus")
        ordner = config.daten_ordner() / "snapchat"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "einstellungen.json").write_text(json.dumps({"flammen": "trocken"}), encoding="utf-8")
        self.assertEqual(flammen.modus(), "trocken")
        (ordner / "einstellungen.json").write_text(json.dumps({"flammen": "vielleicht"}), encoding="utf-8")
        self.assertEqual(flammen.modus(), "aus")
