"""Fahrt ohne Linie/Uhrzeit finden: gemessene Fahrten am Tag zwischen Einstieg und Ausstieg — Messdaten sind Attrappen."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.garantie import ablage
from kern.werkzeuge import garantie_annahme as wga

B = ZoneInfo("Europe/Berlin")


def lauf(c):
    return asyncio.run(c)


def zeit(h, m):
    return datetime(2026, 10, 12, h, m, tzinfo=B)


def verlauf(*halte):
    """Halte als (Kennung, Name, geplante Abfahrt)."""
    return {"halte": [{"reference": k, "name": n, "ab_plan": z, "an_plan": z} for k, n, z in halte]}


REISEN = [{"id": "r1", "tag": "2026-10-12", "linie": "5"}, {"id": "r2", "tag": "2026-10-12", "linie": "7"},
          {"id": "r3", "tag": "2026-10-11", "linie": "5"}]
VERLAEUFE = {
    "r1": verlauf(("a", "Hauptbahnhof", zeit(7, 42)), ("b", "Empelde", zeit(8, 5))),
    "r2": verlauf(("a", "Hauptbahnhof", zeit(7, 50)), ("b", "Empelde", zeit(8, 12))),
    "r3": verlauf(("a", "Hauptbahnhof", zeit(7, 42)), ("b", "Empelde", zeit(8, 5))),
}
WERTE = {"datum": "2026-10-12", "start": "Hauptbahnhof", "ziel": "Empelde"}


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.a = ablage.standard()
        self.verlaeufe = dict(VERLAEUFE)

        async def hole(rid):
            v = self.verlaeufe[rid]
            if isinstance(v, Exception):
                raise v
            return v
        for m in (mock.patch.object(self.a, "reisen", return_value=list(REISEN)),
                  mock.patch("kern.werkzeuge.garantie_annahme.antrag.verlauf", side_effect=hole)):
            m.start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        self.env.stop()


class Suchen(Basis):
    def test_nur_einstieg_ausstieg_und_tag_liefert_alle_passenden_nach_zeit(self):
        t = lauf(wga.fahrten_suchen(WERTE))
        self.assertEqual([(x["reise"], x["linie"]) for x in t], [("r1", "5"), ("r2", "7")])      # r3 ist von einem anderen Tag
        self.assertEqual((t[0]["von"], t[0]["bis"]), ("a", "b"))
        self.assertEqual(t[0]["uhr"], "07:42")

    def test_uhrzeit_grenzt_ein_fuenf_minuten_toleranz(self):
        self.assertEqual([x["reise"] for x in lauf(wga.fahrten_suchen({**WERTE, "abfahrt": "07:44"}))], ["r1"])
        self.assertEqual([x["reise"] for x in lauf(wga.fahrten_suchen({**WERTE, "abfahrt": "07:55"}))], ["r2"])
        self.assertEqual(lauf(wga.fahrten_suchen({**WERTE, "abfahrt": "09:00"})), [])

    def test_linie_grenzt_ein(self):
        self.assertEqual([x["reise"] for x in lauf(wga.fahrten_suchen({**WERTE, "linie": "7"}))], ["r2"])

    def test_falsche_richtung_zaehlt_nicht(self):
        t = lauf(wga.fahrten_suchen({**WERTE, "start": "Empelde", "ziel": "Hauptbahnhof"}))
        self.assertEqual(t, [])

    def test_halt_nicht_auf_der_fahrt_oder_fehlerhafter_verlauf_wird_uebersprungen(self):
        self.verlaeufe["r1"] = verlauf(("a", "Hauptbahnhof", zeit(7, 42)), ("c", "Musterhausen", zeit(8, 5)))
        self.verlaeufe["r2"] = RuntimeError("Auskunft weg")
        self.assertEqual(lauf(wga.fahrten_suchen(WERTE)), [])

    def test_hoechstens_so_viele_verlaeufe_je_suche(self):
        viele = [{"id": f"x{i}", "tag": "2026-10-12", "linie": "5"} for i in range(wga.MAX_PRUEFEN + 10)]
        self.a.reisen.return_value = viele
        self.verlaeufe.update({r["id"]: VERLAEUFE["r1"] for r in viele})
        self.assertLessEqual(len(lauf(wga.fahrten_suchen(WERTE))), wga.MAX_PRUEFEN)

    def test_kaputte_uhrzeit_ist_keine_einschraenkung_sondern_wird_gemeldet(self):
        with self.assertRaises(ValueError):
            lauf(wga.fahrten_suchen({**WERTE, "abfahrt": "kurz nach sieben"}))
