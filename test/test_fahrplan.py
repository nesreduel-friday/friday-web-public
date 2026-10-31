"""GVH — Bus und Bahn. Erster Konnektor ohne Anmeldung.

Die Antwortbeispiele stammen aus echten Messungen an Eikes Netz (13.09.2026),
nicht aus der Vorstellung: HAFAS-Zeiten sind HHMMSS, Orte und Linien stehen
nicht im Abschnitt, sondern als Index in `common.locL` / `common.prodL`.
"""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import httpx

from kern import konnektoren
from kern.dienste import gvh, standort as standortdienst, uestra
from kern.werkzeuge import fahrplan, katalog

HALT = {"svcResL": [{"meth": "LocMatch", "err": "OK", "res": {"match": {"locL": [
    {"lid": "de:03241:11", "extId": "de:03241:11", "name": "Hannover, Kröpcke", "type": "S"}]}}}]}

TAFEL = {"svcResL": [{"meth": "StationBoard", "err": "OK", "res": {
    "common": {"prodL": [{"name": "3"}, {"name": "13"}]},
    "jnyL": [
        {"prodX": 0, "dirTxt": "Hannover/Wettbergen", "stbStop": {"dTimeS": "212300"}},
        {"prodX": 1, "dirTxt": "Hannover/Fasanenkrug", "stbStop": {"dTimeS": "212500", "dCncl": True}},
    ]}}]}

FAHRT = {"svcResL": [{"meth": "TripSearch", "err": "OK", "res": {
    "common": {"locL": [{"name": "Hannover, Kröpcke"}, {"name": "Hannover, Hauptbahnhof"}],
               "prodL": [{"name": "Fußweg"}, {"name": "S5"}]},
    "outConL": [{"dep": {"dTimeS": "212300"}, "arr": {"aTimeS": "214536"}, "dur": "002236", "chg": 0,
                 "secL": [{"dep": {"dTimeS": "212300", "locX": 0}, "arr": {"aTimeS": "214536", "locX": 1},
                           "jny": {"prodX": 1, "dirTxt": "Hameln"}}]}]}}]}


def setUpModule():
    # Diese Tests prüfen den GVH-Weg. Seit 14.09.2026 fragt abfahrten zuerst die
    # ÜSTRA-Echtzeit — ohne diesen Riegel ginge jeder Test ins echte Netz und fiele
    # je nach Uhrzeit anders aus. Die ÜSTRA hat eigene Tests (test_uestra.py).
    global _ohne_uestra
    _ohne_uestra = mock.patch.object(uestra, "abfahrten",
                                     mock.AsyncMock(side_effect=uestra.Unbekannt("nicht in der Liste")))
    _ohne_uestra.start()


def tearDownModule():
    _ohne_uestra.stop()


def _antwort(daten):
    return mock.patch("httpx.AsyncClient.post", mock.AsyncMock(return_value=httpx.Response(
        200, json=daten, request=httpx.Request("POST", gvh.URL))))


class Zeiten(unittest.TestCase):
    """HAFAS liefert HHMMSS am Stück; führende Ziffern sind Tage."""

    def test_uhrzeit(self):
        self.assertEqual(gvh._uhr("212300"), "21:23")
        self.assertEqual(gvh._uhr("000500"), "00:05")

    def test_ueber_mitternacht(self):
        self.assertEqual(gvh._uhr("1002300"), "00:23 (+1 Tag)")

    def test_unsinn_ergibt_leer(self):
        for wert in ("", None, "12", "keine Zeit"):
            self.assertEqual(gvh._uhr(wert), "")

    def test_dauer(self):
        self.assertEqual(gvh._dauer("002236"), "22 Min")
        self.assertEqual(gvh._dauer("010100"), "1 Std 1 Min")


class Abfragen(unittest.TestCase):
    def test_haltestelle_finden(self):
        with _antwort(HALT):
            treffer = asyncio.run(gvh.haltestellen("Kröpcke"))
        self.assertEqual(treffer[0]["name"], "Hannover, Kröpcke")

    def test_leere_eingabe_fragt_gar_nicht_erst(self):
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock()) as p:
            self.assertEqual(asyncio.run(gvh.haltestellen("   ")), [])
        p.assert_not_awaited()

    def test_unbekannte_haltestelle_wird_gesagt(self):
        leer = {"svcResL": [{"err": "OK", "res": {"match": {"locL": []}}}]}
        with _antwort(leer):
            text = asyncio.run(fahrplan.abfahrten("Gibtsnicht"))
        self.assertIn("finde ich im GVH nicht", text)

    def test_fehler_der_auskunft_wird_weitergegeben(self):
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=httpx.ConnectError("weg"))):
            self.assertIn("antwortet gerade nicht", asyncio.run(fahrplan.abfahrten("Kröpcke")))


class Treffergenauigkeit(unittest.TestCase):
    """Die GVH-Suche antwortet deutschlandweit. „Nimm den ersten Treffer" schickte
    Eike am 13.09.2026 von LUDWIGSHAFEN nach Musterhausen, statt von Wennigsen."""

    def test_falsche_stadt_wird_verworfen(self):
        # Die Straße stimmt, die Stadt nicht — ein Wort Übereinstimmung genügt NICHT.
        self.assertFalse(gvh._passt("Beispielweg Musterdorf", "Lübeck, Lübeck Beispielweg"))
        self.assertFalse(gvh._passt("Wennigsen KGS", "Ludwigshafen, Bgm.-Krafft-Pl."))

    def test_richtiger_treffer_wird_genommen(self):
        self.assertTrue(gvh._passt("KGS Wennigsen", "Wennigsen (Deister), KGS"))
        self.assertTrue(gvh._passt("Kröpcke", "Hannover, Kröpcke"))

    def test_hbf_ist_der_hauptbahnhof(self):
        """Eike sagt „Hbf", der GVH schreibt „Hauptbahnhof"."""
        self.assertTrue(gvh._passt("Hannover Hbf", "Hannover, Hauptbahnhof"))

    def test_adresse_statt_haltestelle_fuehrt_zur_rueckfrage(self):
        """„Beispielweg 1, Musterhausen" ist eine Adresse. Lieber nachfragen als
        stillschweigend „Steintor" nehmen."""
        self.assertFalse(gvh._passt("Musterhausen Steinweg", "Musterhausen (Han), Steintor"))

    def test_bei_zweifel_wird_gefragt_statt_geraten(self):
        fremd = {"svcResL": [{"err": "OK", "res": {"match": {"locL": [
            {"lid": "1", "name": "Lübeck, Lübeck Beispielweg"},
            {"lid": "2", "name": "Lüneburg, Beispielweg"}]}}}]}
        with _antwort(fremd):
            text = asyncio.run(fahrplan.abfahrten("Beispielweg Musterdorf"))
        self.assertIn("keine passende Haltestelle", text)
        self.assertIn("Lübeck", text)          # nennt die Alternativen, statt eine zu nehmen


class Abfahrtstafel(unittest.TestCase):
    def test_linie_richtung_und_zeit(self):
        # Erst die Haltestellensuche, dann die Tafel — zwei Aufrufe nacheinander.
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[
                httpx.Response(200, json=HALT, request=httpx.Request("POST", gvh.URL)),
                httpx.Response(200, json=TAFEL, request=httpx.Request("POST", gvh.URL))])):
            text = asyncio.run(fahrplan.abfahrten("Kröpcke"))
        self.assertIn("21:23  3 Richtung Hannover/Wettbergen", text)
        self.assertIn("fällt aus", text)                       # dCncl wird nicht verschwiegen

    def test_sagt_dass_es_fahrplanzeiten_sind(self):
        """Ohne dTimeR keine Aussage: Fahrplanzeiten, nie „pünktlich“."""
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[
                httpx.Response(200, json=HALT, request=httpx.Request("POST", gvh.URL)),
                httpx.Response(200, json=TAFEL, request=httpx.Request("POST", gvh.URL))])):
            text = asyncio.run(fahrplan.abfahrten("Kröpcke"))
        self.assertIn(fahrplan.OHNE_ECHTZEIT, text)
        self.assertNotIn("pünktlich", text)
        self.assertNotIn("Min später", text)

    def test_keine_pauschale_behauptung_mehr(self):
        """Seit 15.09.2026 liefert der GVH Echtzeit. „Verspätungen kennt der GVH hier
        nicht“ war damit eine falsche technische Behauptung."""
        self.assertNotIn("kennt der GVH", fahrplan.OHNE_ECHTZEIT)


# Aufgebaut wie die Messung vom 15.09.2026 (Bennigsen): zeitnahe Fahrten mit dTimeR,
# gleich oder später als geplant, eine spätere ohne.
TAFEL_ECHT = {"svcResL": [{"meth": "StationBoard", "err": "OK", "res": {
    "common": {"prodL": [{"name": "S5"}, {"name": "Bus 500"}]},
    "jnyL": [
        {"prodX": 0, "date": "20260915", "dirTxt": "Hannover Flughafen",
         "stbStop": {"dTimeS": "194700", "dTimeR": "200300"}},
        {"prodX": 1, "date": "20260915", "dirTxt": "Musterhausen", "stbStop": {"dTimeS": "195200", "dTimeR": "195200"}},
        {"prodX": 1, "date": "20260915", "dirTxt": "Musterhausen", "stbStop": {"dTimeS": "211100"}},
    ]}}]}


def _gvh_netz(*antworten):
    return mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[
        httpx.Response(200, json=a, request=httpx.Request("POST", gvh.URL)) for a in antworten]))


class EchtzeitDesGvh(unittest.TestCase):
    """Seit 15.09.2026 kommt dTimeR/aTimeR — aber nicht für jede Fahrt."""

    def test_abweichung_in_minuten(self):
        self.assertEqual(gvh._echt("20260915", "194700", "200300"), ("20:03", 16))
        self.assertEqual(gvh._echt("20260915", "195200", "195200"), ("19:52", 0))

    def test_ueber_mitternacht(self):
        self.assertEqual(gvh._echt("20260915", "235800", "1000500"), ("00:05 (+1 Tag)", 7))

    def test_fehlende_echtzeit_ist_keine_aussage(self):
        for fehlt in (None, "", "12"):
            self.assertEqual(gvh._echt("20260915", "211100", fehlt), ("", None))

    def test_tafel_uebernimmt_die_echtzeit(self):
        with _gvh_netz(HALT, TAFEL_ECHT):
            stand = asyncio.run(gvh.abfahrten("Kröpcke"))
        s5, bus, spaeter = stand["abfahrten"]
        self.assertEqual((s5["echt"], s5["verspaetung"]), ("20:03", 16))
        self.assertEqual(bus["verspaetung"], 0)
        self.assertIsNone(spaeter["verspaetung"])            # nicht 0 — nichts geraten

    def test_text_mit_echtzeit(self):
        with _gvh_netz(HALT, TAFEL_ECHT):
            text = asyncio.run(fahrplan.abfahrten("Kröpcke"))
        self.assertIn("19:47  S5 Richtung Hannover Flughafen  — 16 Min später, voraussichtlich 20:03", text)
        self.assertIn("19:52  Bus 500 Richtung Musterhausen  — pünktlich", text)
        self.assertIn("21:11  Bus 500 Richtung Musterhausen  (keine Echtzeit)", text)
        self.assertIn(fahrplan.GVH_ECHTZEIT, text)
        self.assertNotIn(fahrplan.OHNE_ECHTZEIT, text)


ZIEL = {"svcResL": [{"meth": "LocMatch", "err": "OK", "res": {"match": {"locL": [
    {"lid": "de:03241:31", "extId": "de:03241:31", "name": "Hannover, Hauptbahnhof", "type": "S"}]}}}]}

# Wie TripSearch Bennigsen → Hbf am 15.09.2026: Echtzeit in dep/arr und in secL.
FAHRT_ECHT = {"svcResL": [{"meth": "TripSearch", "err": "OK", "res": {
    "common": {"locL": [{"name": "Hannover, Kröpcke"}, {"name": "Hannover, Hauptbahnhof"}],
               "prodL": [{"name": "S5"}]},
    "outConL": [{"date": "20260915", "dep": {"dTimeS": "194100", "dTimeR": "194200"},
                 "arr": {"aTimeS": "200300", "aTimeR": "200400"}, "dur": "002200", "chg": 0,
                 "secL": [{"dep": {"dTimeS": "194100", "dTimeR": "194200", "locX": 0},
                           "arr": {"aTimeS": "200300", "aTimeR": "200400", "locX": 1},
                           "jny": {"prodX": 0, "dirTxt": "Hannover Flughafen"}}]}]}}]}

# Mit Umstieg: der erste Zug meldet +3, der zweite nichts — also auch keine Ankunftsaussage.
FAHRT_UMSTIEG = {"svcResL": [{"meth": "TripSearch", "err": "OK", "res": {
    "common": {"locL": [{"name": "Hannover, Kröpcke"}, {"name": "Hannover, Aegidientorplatz"},
                        {"name": "Hannover, Hauptbahnhof"}],
               "prodL": [{"name": "3"}, {"name": "Bus 100"}]},
    "outConL": [{"date": "20260915", "dep": {"dTimeS": "194100", "dTimeR": "194400"},
                 "arr": {"aTimeS": "200300"}, "dur": "002200", "chg": 1,
                 "secL": [{"dep": {"dTimeS": "194100", "dTimeR": "194400", "locX": 0},
                           "arr": {"aTimeS": "194500", "aTimeR": "194800", "locX": 1},
                           "jny": {"prodX": 0, "dirTxt": "Wettbergen"}},
                          {"dep": {"dTimeS": "195500", "locX": 1}, "arr": {"aTimeS": "200300", "locX": 2},
                           "jny": {"prodX": 1, "dirTxt": "Hauptbahnhof"}}]}]}}]}


class VerbindungMitGvhEchtzeit(unittest.TestCase):
    def test_abfahrt_und_ankunft_mit_echtzeit(self):
        with _gvh_netz(HALT, ZIEL, FAHRT_ECHT), \
                mock.patch.object(uestra, "abfahrten", mock.AsyncMock()) as ustra:
            text = asyncio.run(fahrplan.verbindung("Kröpcke", "Hannover Hbf"))
        self.assertIn("19:41 – 20:03 (22 Min, ohne Umstieg)  — Abfahrt 1 Min später, voraussichtlich 19:42; "
                      "Ankunft 1 Min später, voraussichtlich 20:04", text)
        self.assertIn(fahrplan.GVH_ECHTZEIT, text)
        self.assertNotIn(fahrplan.OHNE_ECHTZEIT, text)
        self.assertNotIn("Umstiege und Ankunft sind Fahrplanzeiten", text)   # stimmte nicht mehr
        ustra.assert_not_awaited()                   # GVH hat die Echtzeit — kein Zusatzaufruf

    def test_fehlende_ankunft_wird_nicht_geraten(self):
        with _gvh_netz(HALT, ZIEL, FAHRT_UMSTIEG):
            text = asyncio.run(fahrplan.verbindung("Kröpcke", "Hannover Hbf"))
        self.assertIn("Abfahrt 3 Min später, voraussichtlich 19:44; Ankunft ohne Echtzeit", text)
        self.assertIn("3 Richtung Wettbergen: 19:41 Hannover, Kröpcke → 19:45 Hannover, Aegidientorplatz"
                      "  [ab 3 Min später, voraussichtlich 19:44; an 3 Min später, voraussichtlich 19:48]", text)
        bus = next(z for z in text.splitlines() if "Bus 100" in z)
        self.assertNotIn("[", bus)                   # keine Echtzeit, keine Aussage
        self.assertNotIn("pünktlich", text)

    def test_ausfall_wird_genannt(self):
        ausfall = {"svcResL": [{**FAHRT_ECHT["svcResL"][0], "res": {
            **FAHRT_ECHT["svcResL"][0]["res"],
            "outConL": [{**FAHRT_ECHT["svcResL"][0]["res"]["outConL"][0], "secL": [{
                **FAHRT_ECHT["svcResL"][0]["res"]["outConL"][0]["secL"][0],
                "dep": {"dTimeS": "194100", "dCncl": True, "locX": 0}}]}]}}]}
        with _gvh_netz(HALT, ZIEL, ausfall):
            text = asyncio.run(fahrplan.verbindung("Kröpcke", "Hannover Hbf"))
        self.assertIn("fällt aus", text)



class Verbindung(unittest.TestCase):
    def test_strecke_mit_linie_und_zeiten(self):
        # Start und Ziel werden einzeln gesucht — beide müssen zum Wunsch passen.
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[
                httpx.Response(200, json=HALT, request=httpx.Request("POST", gvh.URL)),
                httpx.Response(200, json=ZIEL, request=httpx.Request("POST", gvh.URL)),
                httpx.Response(200, json=FAHRT, request=httpx.Request("POST", gvh.URL))])):
            text = asyncio.run(fahrplan.verbindung("Kröpcke", "Hannover Hbf"))
        self.assertIn("21:23 – 21:45 (22 Min, ohne Umstieg)", text)
        self.assertIn("S5 Richtung Hameln", text)
        self.assertIn("Fahrplanzeiten", text)


class WannVerstehen(unittest.TestCase):
    """24.09.2026, 21:15: „ab 7:50“ ging ohne Datum als HEUTE 07:50 an den GVH und brachte 02:26 zurück."""
    ABEND = datetime(2026, 9, 24, 21, 15, tzinfo=ZoneInfo("Europe/Berlin"))

    def test_uhrzeit_in_mehreren_schreibweisen(self):
        for roh in ("7:50", "07:50", "0750", "075000", "7.50 Uhr", "7:50 Uhr"):
            self.assertEqual(fahrplan._wann("20260925", roh, self.ABEND), ("20260925", "075000"), roh)

    def test_vergangene_uhrzeit_ohne_datum_heisst_morgen(self):
        self.assertEqual(fahrplan._wann("", "0750", self.ABEND), ("20260925", "075000"))

    def test_kommende_uhrzeit_ohne_datum_bleibt_heute(self):
        self.assertEqual(fahrplan._wann("", "2300", self.ABEND), ("", "230000"))

    def test_ohne_alles_bleibt_es_jetzt(self):
        self.assertEqual(fahrplan._wann("", "", self.ABEND), ("", ""))

    def test_datumsworte_und_schreibweisen(self):
        for roh, soll in (("morgen", "20260925"), ("übermorgen", "20260926"), ("heute", ""),
                          ("20260927", "20260927"), ("2026-09-27", "20260927"),
                          ("27.09.2026", "20260927"), ("27.09.", "20260927"), ("1.10.", "20261001")):
            self.assertEqual(fahrplan._wann(roh, "", self.ABEND), (soll, ""), roh)

    def test_datum_ohne_jahr_das_vorbei_ist_gilt_naechstes_jahr(self):
        self.assertEqual(fahrplan._wann("3.1.", "", self.ABEND), ("20270103", ""))

    def test_unverstaendliches_wird_gemeldet_statt_geraten(self):
        self.assertIn("Uhrzeit", fahrplan._wann("", "nachmittags", self.ABEND))
        self.assertIn("Datum", fahrplan._wann("bald", "", self.ABEND))

    def test_werkzeug_reicht_bereinigte_werte_an_den_gvh(self):
        stand = {"von": "A", "nach": "B", "verbindungen": []}
        with mock.patch("kern.config.jetzt", return_value=self.ABEND), \
                mock.patch.object(gvh, "verbindungen", mock.AsyncMock(return_value=stand)) as v:
            asyncio.run(fahrplan.verbindung("Bennigsen", "KGS", "", "7:50"))
        v.assert_awaited_once_with("Bennigsen", "KGS", "20260925", "075000")


class OhneAnmeldung(unittest.TestCase):
    """Erster Konnektor ohne Zugangsdaten. Vorher hieß benutzbar() „aktiv UND
    Zugang vorhanden" — GVH wäre damit nie benutzbar gewesen."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_freigabe_allein_genuegt(self):
        self.assertFalse(konnektoren.benutzbar("gvh"))     # aus = nicht benutzbar
        konnektoren.setzen("gvh", True)
        self.assertTrue(konnektoren.benutzbar("gvh"))      # an, ohne Zugangsdaten

    def test_andere_brauchen_weiterhin_zugangsdaten(self):
        konnektoren.setzen("spotify", True)
        self.assertFalse(konnektoren.benutzbar("spotify"))

    def test_werkzeuge_erst_nach_freigabe_sichtbar(self):
        namen = lambda: {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("abfahrten", namen())
        konnektoren.setzen("gvh", True)
        self.assertTrue({"abfahrten", "verbindung"} <= namen())

    def test_seite_zeigt_kein_anmeldefeld(self):
        eintrag = next(k for k in konnektoren.uebersicht() if k["id"] == "gvh")
        self.assertTrue(eintrag["ohneZugang"])
        self.assertIsNone(eintrag["benutzer"])
        self.assertTrue(eintrag["hilfeUrl"].startswith("https://"))

    def test_nichts_zu_schreiben(self):
        eintrag = next(k for k in konnektoren.uebersicht() if k["id"] == "gvh")
        self.assertFalse(eintrag["schreibbar"])
        self.assertEqual(getattr(fahrplan, "NUR_MIT_SCHREIBRECHT", set()), set())


# LocGeoPos liefert Haltestellen OHNE Ort („Denkmal“), LocDetails die vollen Namen —
# beides an Eikes Konto gemessen (20.09.2026).
GEO = {"svcResL": [{"meth": "LocGeoPos", "err": "OK", "res": {"locL": [
    {"lid": "de:03241:5827", "name": "Holtenser Weg", "dist": 242, "type": "S"},
    {"lid": "de:03241:7711", "name": "Wolfsberg", "dist": 321, "type": "S"},
    {"lid": "de:03241:5816", "name": "Denkmal", "dist": 1093, "type": "S"},
]}}]}
DETAILS = {"svcResL": [{"meth": "LocDetails", "err": "OK", "res": {"locL": [
    {"lid": "de:03241:5827", "name": "Musterdorf, Holtenser Weg"},
    {"lid": "de:03241:7711", "name": "Musterdorf, Wolfsberg"},
    {"lid": "de:03241:5816", "name": "Musterdorf, Denkmal"},
]}}]}


class HaltestellenInDerNaehe(unittest.TestCase):
    """Eike am 20.09.2026: „die nächste Haltestelle“. Friday suchte im Web und
    `verbindung` landete mit „Musterdorf, Schlehenweg“ in Meschede."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        konnektoren.setzen("gvh", True)
        konnektoren.setzen("standort", True)
        standortdienst.vergessen()

    def tearDown(self):
        standortdienst.vergessen()
        self.env.stop()

    def _hier(self, genau=15):
        standortdienst.melden({"kennung": "handy", "breite": 52.3759, "laenge": 9.7320,
                               "genauigkeit": genau})

    def _gvh(self):
        return mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[
            httpx.Response(200, json=GEO, request=httpx.Request("POST", gvh.URL)),
            httpx.Response(200, json=DETAILS, request=httpx.Request("POST", gvh.URL))]))

    def test_nennt_haltestellen_mit_ort_und_entfernung(self):
        self._hier()
        with self._gvh():
            text = asyncio.run(fahrplan.haltestellen_hier())
        self.assertIn("Musterdorf, Holtenser Weg — 242 m", text)
        self.assertIn("Musterdorf, Wolfsberg — 321 m", text)
        self.assertIn("Luftlinie", text)

    def test_namen_tragen_immer_den_ort(self):
        """Der Kern der Sache: „Denkmal“ allein findet bei LocMatch Almhorst, nicht Musterdorf."""
        self._hier()
        with self._gvh():
            text = asyncio.run(fahrplan.haltestellen_hier())
        for zeile in text.splitlines()[1:4]:
            self.assertTrue(zeile.startswith("Musterdorf, "), zeile)

    def test_koordinaten_gehen_als_millionstel_grad_raus(self):
        self._hier()
        with self._gvh() as ruf:
            asyncio.run(fahrplan.haltestellen_hier())
        crd = ruf.call_args_list[0].kwargs["json"]["svcReqL"][0]["req"]["ring"]["cCrd"]
        self.assertEqual((crd["y"], crd["x"]), (52375900, 9732000))       # y = Breite, x = Länge

    def test_ohne_vollen_namen_lieber_nichts(self):
        # LocDetails fällt aus: ein bloßes „Denkmal“ führt in die falsche Stadt.
        leer = {"svcResL": [{"meth": "LocDetails", "err": "OK", "res": {"locL": []}}]}
        self._hier()
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[
                httpx.Response(200, json=GEO, request=httpx.Request("POST", gvh.URL)),
                httpx.Response(200, json=leer, request=httpx.Request("POST", gvh.URL))])):
            text = asyncio.run(fahrplan.haltestellen_hier())
        self.assertIn("keine Haltestelle", text)
        self.assertNotIn("Denkmal", text)

    def test_ohne_standortfreigabe_wird_nichts_geraten(self):
        konnektoren.setzen("standort", False)
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock()) as ruf:
            text = asyncio.run(fahrplan.haltestellen_hier())
        self.assertIn("Freigaben-Seite noch aus", text)
        ruf.assert_not_called()

    def test_ohne_position_ehrliche_meldung(self):
        with mock.patch.object(standortdienst, "WARTEN_S", 0.2), \
                mock.patch.object(standortdienst, "SCHRITT_S", 0.05), \
                mock.patch("httpx.AsyncClient.post", mock.AsyncMock()) as ruf:
            text = asyncio.run(fahrplan.haltestellen_hier())
        self.assertIn("keinen Standort", text)
        ruf.assert_not_called()

    def test_grober_standort_wird_dazugesagt(self):
        self._hier(genau=3000)
        with self._gvh():
            text = asyncio.run(fahrplan.haltestellen_hier())
        self.assertIn("nur grob bekannt", text)

    def test_ist_lesend_und_haengt_am_gvh(self):
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("haltestellen_hier", namen)
        konnektoren.setzen("gvh", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("haltestellen_hier", namen)


class UmkreisDienst(unittest.TestCase):
    def test_umkreis_wird_begrenzt(self):
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[
                httpx.Response(200, json=GEO, request=httpx.Request("POST", gvh.URL)),
                httpx.Response(200, json=DETAILS, request=httpx.Request("POST", gvh.URL))])) as ruf:
            asyncio.run(gvh.haltestellen_um(52.25, 9.67, umkreis=99999))
        ring = ruf.call_args_list[0].kwargs["json"]["svcReqL"][0]["req"]["ring"]
        self.assertEqual(ring["maxDist"], gvh.MAX_UMKREIS_M)

    def test_naechste_zuerst(self):
        durcheinander = {"svcResL": [{"meth": "LocGeoPos", "err": "OK", "res": {"locL": [
            {"lid": "de:03241:5816", "name": "Denkmal", "dist": 1093, "type": "S"},
            {"lid": "de:03241:5827", "name": "Holtenser Weg", "dist": 242, "type": "S"},
        ]}}]}
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[
                httpx.Response(200, json=durcheinander, request=httpx.Request("POST", gvh.URL)),
                httpx.Response(200, json=DETAILS, request=httpx.Request("POST", gvh.URL))])):
            liste = asyncio.run(gvh.haltestellen_um(52.25, 9.67))
        self.assertEqual([h["meter"] for h in liste], [242, 1093])



if __name__ == "__main__":
    unittest.main()
