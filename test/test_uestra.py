"""ÜSTRA-Echtzeit — ohne Netz.

Aufbau der Antworten wie am 14.09.2026 gemessen: Haltestellenliste als Array,
Abfahrten als `{stop, coordinates, departures}` mit `events[{plannedTime,
estimated_time}]`, Zeiten in UTC. Die Verspätungen der S5 sind die gemessenen.
"""

import asyncio
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import httpx

from kern.dienste import gvh, uestra
from kern.werkzeuge import fahrplan

JETZT = datetime(2026, 9, 14, 23, 0, tzinfo=ZoneInfo("Europe/Berlin"))       # = 21:00 UTC


def _halt(id_, name):
    return {"id": id_, "gid": f"de:03241:{id_[-4:]}", "name": name, "lat": 52.2, "lon": 9.6, "platforms": []}


HALTESTELLEN = [_halt("25005844", "Bennigsen Bahnhof"), _halt("25005845", "Bennigsen Hauptstraße"),
                _halt("25005848", "Bennigsen Mühle"), _halt("25000011", "Hannover Kröpcke"),
                _halt("25000001", "Hannover Kröpcke/Theaterstraße"), _halt("25000031", "Hannover Hauptbahnhof"),
                _halt("25005814", "Musterdorf Am Wehrturm"), _halt("25005816", "Musterdorf Denkmal"),
                _halt("25007711", "Musterdorf Wolfsberg")]


def _ev(plan, prognose=None, **mehr):
    e = {"plannedTime": f"2026-09-14T{plan}:00.000Z"}
    if prognose:
        e["estimated_time"] = f"2026-09-14T{prognose}:00.000Z"
    return {**e, **mehr}


BAUARBEITEN = {"type": "lineInfo", "titel": "S5: Bauarbeiten zwischen Weetzen und Musterhausen"}
TAFEL = {"stop": "Bahnhof", "coordinates": [], "departures": [
    {"line": "S-Bahn S5", "number": "S5", "destination": "Hannover Hauptbahnhof",
     "events": [_ev("21:04", "21:33"), _ev("21:41", "22:20")],
     "infos": [BAUARBEITEN, {"type": "infrastructure", "titel": "Aufzug zum Bahnsteig außer Betrieb"}]},
    {"line": "S-Bahn S5", "number": "S5", "destination": "Bad Pyrmont, Bahnhof",
     "events": [_ev("20:50", "20:50"), _ev("22:17", "22:20")], "infos": [BAUARBEITEN]},
    {"line": "Bus 500", "number": "500", "destination": "Musterhausen", "events": [_ev("21:10")]},       # ohne Prognose
    {"line": "Bus 501", "number": "501", "destination": "Musterhausen",
     "events": [_ev("21:15", "21:15", cancelled=True)]},
]}


def _netz(tafel=TAFEL, zaehler=None):
    async def get(self, url, params=None):
        if zaehler is not None:
            zaehler.append(url)
        daten = HALTESTELLEN if url == uestra.HALTESTELLEN_URL else tafel
        return httpx.Response(200, json=daten, request=httpx.Request("GET", url))
    return mock.patch("httpx.AsyncClient.get", get)


class Basis(unittest.TestCase):
    def setUp(self):
        uestra._liste, uestra._geladen = [], 0.0
        self.uhr = mock.patch("kern.config.jetzt", return_value=JETZT)
        self.uhr.start()

    def tearDown(self):
        self.uhr.stop()


class Haltestellenwahl(unittest.TestCase):
    LISTE = [{"id": h["id"], "name": h["name"]} for h in HALTESTELLEN]

    def _name(self, wunsch):
        return uestra.auswaehlen(wunsch, self.LISTE)["name"]

    def test_der_ort_meint_seinen_bahnhof(self):
        self.assertEqual(self._name("Bennigsen"), "Bennigsen Bahnhof")

    def test_wenigste_zusatzwoerter(self):
        self.assertEqual(self._name("Kröpcke"), "Hannover Kröpcke")

    def test_exakter_name_gewinnt(self):
        self.assertEqual(self._name("Hannover Kröpcke/Theaterstraße"), "Hannover Kröpcke/Theaterstraße")

    def test_hbf(self):
        self.assertEqual(self._name("Hannover Hbf"), "Hannover Hauptbahnhof")

    def test_gleichstand_wird_erfragt(self):
        with self.assertRaises(uestra.Mehrdeutig) as f:
            uestra.auswaehlen("Musterdorf", self.LISTE)
        self.assertIn("Musterdorf Denkmal", str(f.exception))
        self.assertIn("Musterdorf Wolfsberg", str(f.exception))

    def test_unbekannt_statt_irgendwas(self):
        with self.assertRaises(uestra.Unbekannt):
            uestra.auswaehlen("Ludwigshafen", self.LISTE)

    def test_linien_der_beiden_auskuenfte(self):
        self.assertTrue(uestra.gleiche_linie("S5", "S5"))
        self.assertTrue(uestra.gleiche_linie("500", "Bus 500"))
        self.assertFalse(uestra.gleiche_linie("5", "S5"))
        self.assertFalse(uestra.gleiche_linie("50", "Bus 500"))
        self.assertFalse(uestra.gleiche_linie("", "Bus 500"))


class Abfahrten(Basis):
    def _stand(self):
        with _netz():
            return asyncio.run(uestra.abfahrten("Bennigsen", anzahl=20))

    def test_verspaetung_aus_plan_und_prognose(self):
        stand = self._stand()
        self.assertEqual(stand["haltestelle"], "Bennigsen Bahnhof")          # nicht das verkürzte „Bahnhof“
        s5 = next(a for a in stand["abfahrten"] if a["plan"].strftime("%H:%M") == "23:04")
        self.assertEqual((s5["verspaetung"], f"{s5['prognose']:%H:%M}"), (29, "23:33"))

    def test_abgefahrenes_faellt_weg(self):
        self.assertNotIn("22:50", [f"{a['plan']:%H:%M}" for a in self._stand()["abfahrten"]])

    def test_fehlende_prognose_erfindet_keine_verspaetung(self):
        """Der Ansatz setzte bei fehlender Zeit „jetzt“ ein — daraus wäre eine
        erfundene Verspätung geworden. Hier: keine Prognose, keine Zahl."""
        bus = next(a for a in self._stand()["abfahrten"] if a["linie"] == "500")
        self.assertIsNone(bus["prognose"])
        self.assertIsNone(bus["verspaetung"])

    def test_ausfall_wenn_er_gemeldet_wird(self):
        bus = next(a for a in self._stand()["abfahrten"] if a["linie"] == "501")
        self.assertTrue(bus["faellt_aus"])

    def test_nach_voraussichtlicher_zeit_sortiert(self):
        self.assertEqual([a["linie"] for a in self._stand()["abfahrten"]][:3], ["500", "501", "S5"])

    def test_stoerungen_ohne_aufzuege_und_ohne_doppelte(self):
        self.assertEqual(self._stand()["meldungen"], ["S5: Bauarbeiten zwischen Weetzen und Musterhausen"])

    def test_haltestellenliste_nur_einmal_geladen(self):
        aufrufe = []
        with _netz(zaehler=aufrufe):
            asyncio.run(uestra.abfahrten("Bennigsen"))
            asyncio.run(uestra.abfahrten("Kröpcke"))
        self.assertEqual(aufrufe.count(uestra.HALTESTELLEN_URL), 1)

    def test_auskunft_weg(self):
        with mock.patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("weg")), \
                self.assertRaises(uestra.UestraFehler):
            asyncio.run(uestra.abfahrten("Bennigsen"))


class Werkzeug(Basis):
    def test_text_mit_verspaetung_ausfall_und_meldung(self):
        with _netz():
            text = asyncio.run(fahrplan.abfahrten("Bennigsen"))
        self.assertIn("Nächste Abfahrten ab Bennigsen Bahnhof:", text)
        # Vorne die voraussichtliche Zeit — danach ist sortiert —, die Planzeit in Klammern.
        self.assertIn("23:33  S5 Richtung Hannover Hauptbahnhof  — 29 Min später (Plan 23:04)", text)
        self.assertIn("23:10  500 Richtung Musterhausen  (keine Prognose)", text)
        self.assertIn("501 Richtung Musterhausen  — fällt aus", text)
        self.assertIn("Meldungen der ÜSTRA: S5: Bauarbeiten", text)
        self.assertNotIn("Aufzug", text)
        self.assertIn(fahrplan.MIT_ECHTZEIT, text)

    def test_mehrdeutig_fragt_und_weicht_nicht_auf_den_gvh_aus(self):
        with _netz(), mock.patch.object(gvh, "abfahrten", mock.AsyncMock()) as fahrplanweg:
            text = asyncio.run(fahrplan.abfahrten("Musterdorf"))
        self.assertIn("Welche meinst du?", text)
        fahrplanweg.assert_not_awaited()

    GVH_STAND = {"haltestelle": "Hameln, Bahnhof",
                 "abfahrten": [{"linie": "S5", "richtung": "Hannover", "zeit": "23:10", "faellt_aus": False}]}

    def test_ausserhalb_der_region_fahrplanzeiten(self):
        with mock.patch.object(uestra, "abfahrten", mock.AsyncMock(side_effect=uestra.Unbekannt("x"))), \
                mock.patch.object(gvh, "abfahrten", mock.AsyncMock(return_value=self.GVH_STAND)):
            text = asyncio.run(fahrplan.abfahrten("Hameln"))
        self.assertIn("23:10  S5 Richtung Hannover", text)
        self.assertIn(fahrplan.OHNE_ECHTZEIT, text)
        self.assertNotIn("nicht erreichbar", text)

    def test_uestra_weg_fahrplanzeiten_mit_hinweis(self):
        with mock.patch.object(uestra, "abfahrten", mock.AsyncMock(side_effect=uestra.UestraFehler("weg"))), \
                mock.patch.object(gvh, "abfahrten", mock.AsyncMock(return_value=self.GVH_STAND)):
            text = asyncio.run(fahrplan.abfahrten("Bennigsen"))
        self.assertTrue(text.startswith("Die Echtzeit der ÜSTRA ist gerade nicht erreichbar."))
        self.assertIn(fahrplan.OHNE_ECHTZEIT, text)


VERBINDUNG = {"von": "Bennigsen", "nach": "Hannover, Hauptbahnhof", "verbindungen": [
    {"ab": "23:04", "an": "23:26", "dauer": "22 Min", "umstiege": 0, "abschnitte": [
        {"linie": "S5", "richtung": "Hannover Hauptbahnhof", "ab": "23:04", "an": "23:26",
         "von": "Bennigsen", "nach": "Hannover, Hauptbahnhof", "zu_fuss": False}]}]}


class VerbindungMitPrognose(Basis):
    def test_erste_fahrt_bekommt_die_prognose(self):
        with _netz(), mock.patch.object(gvh, "verbindungen", mock.AsyncMock(return_value=VERBINDUNG)):
            text = asyncio.run(fahrplan.verbindung("Bennigsen", "Hannover Hbf"))
        self.assertIn("S5 Richtung Hannover Hauptbahnhof: 23:04 Bennigsen → 23:26 Hannover, Hauptbahnhof"
                      "  [ÜSTRA: 29 Min später, voraussichtlich 23:33]", text)
        self.assertIn("Umstiege und Ankunft sind Fahrplanzeiten", text)

    def test_ohne_echtzeit_bleibt_es_bei_fahrplanzeiten(self):
        with mock.patch.object(uestra, "abfahrten", mock.AsyncMock(side_effect=uestra.UestraFehler("weg"))), \
                mock.patch.object(gvh, "verbindungen", mock.AsyncMock(return_value=VERBINDUNG)):
            text = asyncio.run(fahrplan.verbindung("Bennigsen", "Hannover Hbf"))
        self.assertNotIn("[ÜSTRA", text)
        self.assertIn(fahrplan.OHNE_ECHTZEIT, text)


if __name__ == "__main__":
    unittest.main()
