"""Verbindungskarte: Bus/Bahn, zu Fuß, Auto — echte Quellen mit Attrappen, kein Netz."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import httpx

from kern.dashboard import friday_karten, karten, routelive
from kern.dienste import gvh, homeassistant, personenstandort, routen, standort
from kern.werkzeuge import katalog
from kern.werkzeuge import route as routewerkzeug

BERLIN = ZoneInfo("Europe/Berlin")
JETZT = datetime(2026, 9, 25, 14, 0, tzinfo=BERLIN)
HIER = (52.3759, 9.7320)
DORT = (52.3830, 9.7160)


def _valhalla(sekunden, km):
    return httpx.Response(200, json={"trip": {"summary": {"time": sekunden, "length": km}}},
                          request=httpx.Request("POST", routen.VALHALLA))


# Tür zu Tür wie am 25.09.2026 gemessen: Fußweg 411 m, Fahrt, Umstieg 13 m, Fahrt, Fußweg 322 m.
HAFAS = {"common": {"locL": [{"name": "Start"}, {"name": "Hannover, Kröpcke"}, {"name": "Hannover, Hauptbahnhof"}, {"name": "Ziel"}],
                    "prodL": [{"name": "Bus 100", "cls": 32}, {"name": "S5", "cls": 16}]},
         "outConL": [{"date": "20260925", "dep": {"dTimeS": "140500", "dTimeR": "140600"}, "arr": {"aTimeS": "143000", "aTimeR": "143200"},
                      "dur": "002500", "chg": 1,
                      "secL": [
                          {"type": "WALK", "gis": {"dist": 411}, "dep": {"dTimeS": "140500", "locX": 0}, "arr": {"aTimeS": "141000", "locX": 1}},
                          {"type": "JNY", "jny": {"prodX": 0, "dirTxt": "Bennigsen"}, "dep": {"dTimeS": "141000", "dTimeR": "141100", "locX": 1},
                           "arr": {"aTimeS": "142000", "aTimeR": "142100", "locX": 2}},
                          {"type": "JNY", "jny": {"prodX": 1, "dirTxt": "Hameln"}, "dep": {"dTimeS": "142200", "locX": 2},
                           "arr": {"aTimeS": "142800", "locX": 2}},
                          {"type": "WALK", "gis": {"dist": 322}, "dep": {"dTimeS": "142800", "locX": 2}, "arr": {"aTimeS": "143000", "locX": 3}},
                      ]}]}


class Gvh(unittest.TestCase):
    def test_koordinaten_werden_zu_hafas_punkten(self):
        p = gvh._punkt(52.464, 9.5947)
        self.assertEqual(p, {"type": "C", "crd": {"x": 9594700, "y": 52464000}})
        for boese in ((91, 0), (0, 181), (-91, 0)):
            with self.assertRaises(gvh.GvhFehler):
                gvh._punkt(*boese)

    def test_tuer_zu_tuer_mit_fusswegen_und_verkehrsmitteln(self):
        with mock.patch.object(gvh, "_ruf", mock.AsyncMock(return_value=HAFAS)) as ruf:
            v = asyncio.run(gvh.verbindungen_koordinaten(HIER, DORT, anzahl=3))
        anfrage = ruf.await_args.args[1]
        self.assertEqual(anfrage["depLocL"][0]["type"], "C")
        self.assertEqual(anfrage["arrLocL"][0]["type"], "C")
        a = v[0]["abschnitte"]
        self.assertEqual([x["verkehrsmittel"] for x in a], ["fuss", "bus", "sbahn", "fuss"])
        self.assertEqual((a[0]["zu_fuss"], a[0]["meter"], a[3]["meter"]), (True, 411, 322))
        self.assertEqual((a[1]["linie"], a[1]["ab_verspaetung"]), ("Bus 100", 1))
        self.assertEqual(v[0]["datum"], "20260925")

    def test_verkehrsmittel_aus_der_klasse(self):
        for klasse, name in ((1, "zug"), (8, "zug"), (16, "sbahn"), (32, "bus"), (128, "ubahn"), (256, "tram"), (None, "bahn")):
            self.assertEqual(gvh.verkehrsmittel(klasse), name)


class Dienst(unittest.TestCase):
    def _netz(self, valhalla=None, hafas=HAFAS):
        antworten = valhalla or [_valhalla(1200, 1.6), _valhalla(260, 1.7)]
        return (mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=antworten)),
                mock.patch.object(gvh, "_ruf", mock.AsyncMock(return_value=hafas) if not isinstance(hafas, Exception)
                                  else mock.AsyncMock(side_effect=hafas)))

    def test_fuss_und_auto_kommen_von_valhalla_mit_dem_richtigen_profil(self):
        post = mock.AsyncMock(side_effect=[_valhalla(1214, 1.674), _valhalla(263, 1.635)])
        with mock.patch("httpx.AsyncClient.post", post):
            f = asyncio.run(routen.zu_fuss(HIER, DORT, JETZT))
            a = asyncio.run(routen.auto(HIER, DORT, JETZT))
        self.assertEqual([c.kwargs["json"]["costing"] for c in post.await_args_list], ["pedestrian", "auto"])
        self.assertEqual((f["modus"], f["dauer_min"], f["distanz_m"], f["ab"], f["an"]), ("fuss", 20, 1674, "14:00", "14:20"))
        self.assertEqual((a["modus"], a["dauer_min"], a["an"]), ("auto", 4, "14:04"))
        self.assertIn("Verkehr", a["hinweis"])                  # Auto: ohne aktuellen Verkehr

    def test_oepnv_hat_abschnitte_echtzeit_und_ankunft(self):
        with mock.patch.object(gvh, "_ruf", mock.AsyncMock(return_value=HAFAS)):
            o = asyncio.run(routen.oepnv(HIER, DORT, "", "", JETZT))
        v = o[0]
        self.assertEqual((v["modus"], v["ab"], v["an"], v["umstiege"], v["echtzeit"]), ("oepnv", "14:06", "14:32", 1, True))
        self.assertEqual(v["dauer_min"], 26)                    # 14:06 (echt) → 14:32 (echt)
        self.assertEqual([a["art"] for a in v["abschnitte"]], ["fuss", "bus", "sbahn", "fuss"])
        self.assertEqual(v["abschnitte"][0]["min"], 5)

    def test_alle_parallel_schnellste_und_ein_ausfall_nimmt_die_anderen_nicht_mit(self):
        with mock.patch.object(gvh, "_ruf", mock.AsyncMock(return_value=HAFAS)), \
             mock.patch.object(routen, "zu_fuss", mock.AsyncMock(return_value={"modus": "fuss", "dauer_min": 20, "an": "14:20", "_an_min": 20})), \
             mock.patch.object(routen, "auto", mock.AsyncMock(side_effect=routen.RoutenFehler("Autoroute gerade nicht verfügbar"))):
            r = asyncio.run(routen.alle(HIER, DORT, jetzt=JETZT))
        moduse = [o["modus"] for o in r["optionen"]]
        self.assertIn("oepnv", moduse)
        self.assertIn("fuss", moduse)
        self.assertNotIn("auto", moduse)
        self.assertEqual(r["ausfaelle"], [{"modus": "auto", "text": "Autoroute gerade nicht verfügbar"}])
        self.assertEqual(r["optionen"][r["schnellste"]]["modus"], "fuss")       # Fuß: an 14:20, ÖPNV erst 14:32

    def test_auswahl_der_verkehrsmittel(self):
        with mock.patch.object(routen, "zu_fuss", mock.AsyncMock(return_value={"modus": "fuss", "_an_min": 5})) as f, \
             mock.patch.object(routen, "auto", mock.AsyncMock()) as a, mock.patch.object(routen, "oepnv", mock.AsyncMock()) as o:
            asyncio.run(routen.alle(HIER, DORT, verkehrsmittel=("fuss",), jetzt=JETZT))
        f.assert_awaited_once()
        a.assert_not_awaited()
        o.assert_not_awaited()

    def test_valhalla_fehler_sind_ehrlich(self):
        for antwort in (httpx.Response(500, request=httpx.Request("POST", routen.VALHALLA)),
                        httpx.Response(200, json={"error": "kein Weg"}, request=httpx.Request("POST", routen.VALHALLA)),
                        httpx.Response(400, json={"error": "Es gibt keinen Weg"}, request=httpx.Request("POST", routen.VALHALLA))):
            with self.subTest(antwort.status_code), mock.patch("httpx.AsyncClient.post", mock.AsyncMock(return_value=antwort)):
                with self.assertRaises(routen.RoutenFehler):
                    asyncio.run(routen.zu_fuss(HIER, DORT, JETZT))
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=httpx.ConnectTimeout("x"))):
            with self.assertRaises(routen.RoutenFehler):
                asyncio.run(routen.auto(HIER, DORT, JETZT))

    def test_weite_fusswege_werden_benannt(self):
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(return_value=_valhalla(3 * 3600, 15))):
            f = asyncio.run(routen.zu_fuss(HIER, DORT, JETZT))
        self.assertIn("weit", f["hinweis"].lower())

    def test_gvh_ausfall_wird_zum_ausfall(self):
        with mock.patch.object(gvh, "_ruf", mock.AsyncMock(side_effect=gvh.GvhFehler("Die Fahrplanauskunft antwortet gerade nicht."))), \
             mock.patch("httpx.AsyncClient.post", mock.AsyncMock(side_effect=[_valhalla(600, 0.8), _valhalla(100, 0.9)])):
            r = asyncio.run(routen.alle(HIER, DORT, jetzt=JETZT))
        self.assertEqual([a["modus"] for a in r["ausfaelle"]], ["oepnv"])
        self.assertEqual({o["modus"] for o in r["optionen"]}, {"fuss", "auto"})


class Karte(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _daten(self, **extra):
        opt = {"modus": "fuss", "titel": "Zu Fuß", "dauer_min": 20, "distanz_m": 1674, "ab": "14:00", "an": "14:20", "hinweis": ""}
        return {"start": {"name": "Zuhause", "breite": 52.3759, "laenge": 9.732}, "ziel": {"name": "Kröpcke", "breite": 52.383, "laenge": 9.716},
                "optionen": [opt], "schnellste": 0, "ausfaelle": [], "hinweise": ["Auto ohne aktuellen Verkehr."], **extra}

    def test_karte_wird_gelegt_und_geliefert(self):
        k = asyncio.run(friday_karten.legen_route(self._daten()))
        self.assertEqual(k["art"], "route")
        offen = asyncio.run(friday_karten.karten())[0]
        self.assertEqual((offen["art"], offen["ziel"]["name"], offen["optionen"][0]["an"], offen["schnellste"]), ("route", "Kröpcke", "14:20", 0))
        self.assertIn("Kröpcke", offen["titel"])

    def test_nur_saubere_daten_kommen_auf_die_karte(self):
        d = self._daten()
        d["ziel"]["breite"] = 999                    # keine Koordinate der Erde
        with self.assertRaises(friday_karten.KarteFehler):
            asyncio.run(friday_karten.legen_route(d))
        d = self._daten(optionen=[])
        with self.assertRaises(friday_karten.KarteFehler):
            asyncio.run(friday_karten.legen_route(d))
        d = self._daten()
        d["ziel"]["name"] = "Ü" * 500
        d["optionen"][0]["hinweis"] = "<script>alert(1)</script>" * 40
        k = asyncio.run(friday_karten.legen_route(d))
        offen = asyncio.run(friday_karten.karten())[0]
        self.assertLessEqual(len(offen["ziel"]["name"]), 120)
        self.assertLessEqual(len(offen["optionen"][0]["hinweis"]), 160)
        self.assertEqual(k["art"], "route")

    def test_zu_viele_optionen_und_abschnitte_werden_begrenzt(self):
        d = self._daten()
        d["optionen"] = [dict(d["optionen"][0], modus="oepnv", abschnitte=[{"art": "bus", "linie": "1", "min": 1}] * 40) for _ in range(20)]
        asyncio.run(friday_karten.legen_route(d))
        offen = asyncio.run(friday_karten.karten())[0]
        self.assertLessEqual(len(offen["optionen"]), 5)
        self.assertLessEqual(len(offen["optionen"][0]["abschnitte"]), 12)


def _pos(genau=15, alter=5):
    return {"breite": HIER[0], "laenge": HIER[1], "genauigkeit": genau, "alter_s": alter}


def _ort(name, b, l):
    return {"name": name, "breite": b, "laenge": l, "voll": name}


class Werkzeug(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.opt = {"optionen": [{"modus": "fuss", "titel": "Zu Fuß", "dauer_min": 20, "distanz_m": 1674, "ab": "14:00", "an": "14:20",
                                  "hinweis": "", "_an_min": 20},
                                 {"modus": "auto", "titel": "Auto", "dauer_min": 4, "distanz_m": 1635, "ab": "14:00", "an": "14:04",
                                  "hinweis": "ohne aktuellen Verkehr", "_an_min": 4}],
                    "schnellste": 1, "ausfaelle": []}

    def tearDown(self):
        self.env.stop()

    def _lauf(self, ziel="Kröpcke", pos=None, orte=None, **kw):
        with mock.patch("kern.konnektoren.benutzbar", return_value=True), \
             mock.patch.object(standort, "aktuell", return_value=pos or _pos()), \
             mock.patch.object(standort, "frisch_holen", mock.AsyncMock(return_value=None)), \
             mock.patch.object(standort, "ortsname", mock.AsyncMock(return_value="Zuhause")), \
             mock.patch.object(personenstandort, "personen", mock.AsyncMock(return_value=[])), \
             mock.patch.object(standort, "ort_finden", mock.AsyncMock(return_value=orte if orte is not None else [_ort("Hannover, Kröpcke", *DORT)])), \
             mock.patch.object(routen, "alle", mock.AsyncMock(return_value=self.opt)) as alle, \
             mock.patch("kern.config.jetzt", return_value=JETZT):
            text = asyncio.run(routewerkzeug.verbindung_karte(ziel, **kw))
        return text, alle

    def test_karte_liegt_auf_der_uebersicht_und_der_text_nennt_die_fakten(self):
        text, alle = self._lauf()
        with mock.patch("kern.config.jetzt", return_value=JETZT):          # die Karte gilt 45 Minuten ab dem geprüften „jetzt“
            k = asyncio.run(friday_karten.karten())[0]
        self.assertEqual((k["art"], k["ziel"]["name"], k["start"]["breite"]), ("route", "Hannover, Kröpcke", HIER[0]))
        self.assertIn("Auto", text)
        self.assertIn("ohne aktuellen Verkehr", text)
        self.assertIn("14:04", text)
        self.assertNotIn("_an_min", str(k))                 # interne Rechenfelder gehören nicht auf die Karte
        self.assertEqual(alle.await_args.args[:2], (HIER, DORT))

    def test_grob_geortet_gibt_einen_hinweis_zu_ungenau_gar_keine_route(self):
        """Gespräch 306, 25.09.2026: Der PC meldete „Genauigkeit rund 100 km“ — die Karte startete trotzdem in Musterhausen-Burgstraße."""
        text, alle = self._lauf(pos=_pos(genau=200))
        self.assertIn("daneben", text.lower())                       # 200 m: brauchbar, mit Hinweis
        alle.assert_awaited_once()
        for genau in (routewerkzeug.ROUTE_GENAU_M + 1, 900, 100_000, None):
            with self.subTest(genau):
                text, alle = self._lauf(pos=_pos(genau=genau))
                self.assertIn("ungenau", text.lower())
                self.assertIn("Handy", text)                         # nächster Schritt: Friday auf dem Handy öffnen oder Start nennen
                alle.assert_not_awaited()

    def _owntracks(self, alter=30, genau=8, zustand="ok", name="Eike", **kw):
        p = {"name": name, "kennung": "eike", "zustand": zustand, "breite": DORT[0], "laenge": DORT[1], "genauigkeit_m": genau,
             "alter_s": alter, "regionen": []}
        return mock.patch.object(personenstandort, "personen", mock.AsyncMock(return_value=[p]))

    def _start(self, app_pos, ot):
        """(Start des ersten Aufrufs an routen.alle, Text) mit den beiden Quellen."""
        with mock.patch("kern.konnektoren.benutzbar", return_value=True), \
             mock.patch.object(standort, "aktuell", return_value=app_pos), \
             mock.patch.object(standort, "frisch_holen", mock.AsyncMock(return_value=None)), \
             mock.patch.object(standort, "ortsname", mock.AsyncMock(return_value="Zuhause")), \
             mock.patch.object(standort, "ort_finden", mock.AsyncMock(return_value=[_ort("Fern", 52.9, 10.0)])), \
             ot, mock.patch.object(routen, "alle", mock.AsyncMock(return_value=self.opt)) as alle, \
             mock.patch("kern.config.jetzt", return_value=JETZT):
            text = asyncio.run(routewerkzeug.verbindung_karte("Fern"))
        return (alle.await_args.args[0] if alle.await_args else None), text

    def test_owntracks_ist_die_quelle_wenn_die_app_nichts_hat(self):
        start, text = self._start(None, self._owntracks())
        self.assertEqual(start, DORT)
        self.assertIn("OwnTracks", text)

    def test_home_assistant_allein_reicht_fuer_den_start(self):
        """Nur HA freigegeben: weder App noch OwnTracks liefern — der Start kommt aus Home Assistant."""
        ha = {"quelle": "Home Assistant", "breite": DORT[0], "laenge": DORT[1],
              "genau": 15, "alter_s": 30, "geraet": ""}
        with mock.patch("kern.konnektoren.benutzbar", side_effect=lambda k: k == "homeassistant"), \
             mock.patch.object(homeassistant, "position", mock.AsyncMock(return_value=ha)), \
             mock.patch.object(standort, "ortsname", mock.AsyncMock(return_value="Zuhause")), \
             mock.patch.object(standort, "ort_finden", mock.AsyncMock(return_value=[_ort("Fern", 52.9, 10.0)])), \
             mock.patch.object(routen, "alle", mock.AsyncMock(return_value=self.opt)) as alle, \
             mock.patch("kern.config.jetzt", return_value=JETZT):
            text = asyncio.run(routewerkzeug.verbindung_karte("Fern"))
        self.assertEqual(alle.await_args.args[0], DORT)
        self.assertIn("Home Assistant", text)

    def test_owntracks_gewinnt_wenn_es_frischer_und_genau_genug_ist(self):
        start, _ = self._start(_pos(genau=40, alter=110), self._owntracks(alter=20))
        self.assertEqual(start, DORT)
        start, _ = self._start(_pos(genau=40, alter=5), self._owntracks(alter=600))
        self.assertEqual(start, HIER)                                 # die App war gerade eben, OwnTracks vor 10 Minuten

    def test_owntracks_ungenau_oder_pausiert_zaehlt_nicht(self):
        for ot in (self._owntracks(genau=900), self._owntracks(zustand="pausiert"), self._owntracks(zustand="noch_nie")):
            start, _ = self._start(_pos(genau=40, alter=100), ot)
            self.assertEqual(start, HIER)
        with mock.patch.object(personenstandort, "personen", mock.AsyncMock(side_effect=RuntimeError("Container aus"))):
            start, _ = self._start(_pos(genau=40, alter=100), mock.patch.object(personenstandort, "personen", mock.AsyncMock(side_effect=RuntimeError("x"))))
        self.assertEqual(start, HIER)                                 # ein Fehler im Container darf die Karte nicht kosten

    def test_owntracks_nur_mit_eigener_freigabe(self):
        def nur_standort(kid):
            return kid == "standort"
        with mock.patch("kern.konnektoren.benutzbar", side_effect=nur_standort), \
             mock.patch.object(standort, "aktuell", return_value=_pos(genau=40, alter=100)), \
             mock.patch.object(standort, "frisch_holen", mock.AsyncMock(return_value=None)), \
             mock.patch.object(standort, "ortsname", mock.AsyncMock(return_value="Zuhause")), \
             mock.patch.object(standort, "ort_finden", mock.AsyncMock(return_value=[_ort("Fern", 52.9, 10.0)])), \
             self._owntracks() as ot, mock.patch.object(routen, "alle", mock.AsyncMock(return_value=self.opt)) as alle, \
             mock.patch("kern.config.jetzt", return_value=JETZT):
            asyncio.run(routewerkzeug.verbindung_karte("Fern"))
        ot.assert_not_awaited()
        self.assertEqual(alle.await_args.args[0], HIER)

    def test_die_antwort_verbietet_eine_zweite_karte(self):
        text, _ = self._lauf()
        self.assertIn("keine zweite Karte", text)

    def test_alter_standort_wird_genannt(self):
        text, _ = self._lauf(pos=_pos(alter=standort.AKTUELL_S + 60))
        self.assertIn("woanders", text.lower())

    def test_mehrere_treffer_nennt_die_auswahl(self):
        orte = [_ort("Penny, Bennigsen", 52.30, 9.62), _ort("Penny, Wennigsen", 52.26, 9.58)]
        text, _ = self._lauf(ziel="Penny", orte=orte)
        self.assertIn("Penny, Bennigsen", text)
        self.assertIn("Wennigsen", text)                    # „es gibt auch …“

    def test_ziel_nicht_gefunden_keine_karte(self):
        text, alle = self._lauf(orte=[])
        self.assertIn("finde ich", text)
        alle.assert_not_awaited()
        self.assertEqual(asyncio.run(friday_karten.karten()), [])

    def test_ohne_standort_kein_raten(self):
        with mock.patch("kern.konnektoren.benutzbar", return_value=True), \
             mock.patch.object(standort, "aktuell", return_value=None), \
             mock.patch.object(standort, "frisch_holen", mock.AsyncMock(return_value=None)), \
             mock.patch.object(personenstandort, "personen", mock.AsyncMock(return_value=[])), \
             mock.patch.object(routen, "alle", mock.AsyncMock()) as alle:
            text = asyncio.run(routewerkzeug.verbindung_karte("Kröpcke"))
        self.assertIn("Standort", text)
        alle.assert_not_awaited()

    def test_fremder_text_bekommt_keinen_standort(self):
        with mock.patch.object(standort, "aktuell", return_value=_pos()) as pos, mock.patch.object(routen, "alle", mock.AsyncMock()) as alle:
            text = asyncio.run(routewerkzeug.verbindung_karte("Kröpcke", fremde_inhalte=True))
        self.assertIn("fremdem Text", text)
        pos.assert_not_called()
        alle.assert_not_awaited()

    def test_standort_ist_eine_eigene_freigabe(self):
        with mock.patch("kern.konnektoren.benutzbar", return_value=False), \
             mock.patch.object(standort, "aktuell", return_value=_pos()) as pos, mock.patch.object(routen, "alle", mock.AsyncMock()) as alle:
            text = asyncio.run(routewerkzeug.verbindung_karte("Kröpcke"))
        self.assertIn("Freigaben", text)
        pos.assert_not_called()
        alle.assert_not_awaited()

    def test_start_angegeben_braucht_keinen_standort(self):
        with mock.patch.object(standort, "aktuell", return_value=None) as pos, \
             mock.patch.object(standort, "ort_finden", mock.AsyncMock(side_effect=[[_ort("Bahnhof Musterhausen", 52.21, 9.55)], [_ort("Hannover Hbf", *DORT)]])), \
             mock.patch.object(routen, "alle", mock.AsyncMock(return_value=self.opt)) as alle, mock.patch("kern.config.jetzt", return_value=JETZT):
            text = asyncio.run(routewerkzeug.verbindung_karte("Hannover Hbf", start="Bahnhof Musterhausen"))
        pos.assert_not_called()
        self.assertEqual(alle.await_args.args[0], (52.21, 9.55))
        self.assertIn("Bahnhof Musterhausen", text)

    def test_schon_da(self):
        text, alle = self._lauf(orte=[_ort("Hier", HIER[0] + 0.0001, HIER[1])])
        self.assertIn("schon", text.lower())
        alle.assert_not_awaited()

    def test_verkehrsmittel_wunsch_wird_weitergegeben_und_geprueft(self):
        _, alle = self._lauf(verkehrsmittel="Bahn, zu Fuß")
        self.assertEqual(set(alle.await_args.kwargs["verkehrsmittel"]), {"oepnv", "fuss"})
        _, alle = self._lauf(verkehrsmittel="Hubschrauber")
        self.assertEqual(set(alle.await_args.kwargs["verkehrsmittel"]), {"oepnv", "fuss", "auto"})

    def test_die_karte_bekommt_einen_auftrag_fuer_die_live_verfolgung(self):
        self._lauf()
        with mock.patch("kern.config.jetzt", return_value=JETZT):
            k = friday_karten.aktive_routen()[0]
        a = k["daten"]["auftrag"]
        self.assertIsNone(a["start"])                                   # „mein Standort“: wandert mit
        self.assertEqual((a["ziel"]["breite"], a["modi"]), (DORT[0], list(routen.ALLE_MODI)))
        with mock.patch.object(standort, "aktuell", return_value=None), \
             mock.patch.object(standort, "ort_finden", mock.AsyncMock(side_effect=[[_ort("Bahnhof Musterhausen", 52.21, 9.55)], [_ort("Hannover Hbf", *DORT)]])), \
             mock.patch.object(routen, "alle", mock.AsyncMock(return_value=self.opt)), mock.patch("kern.config.jetzt", return_value=JETZT):
            asyncio.run(routewerkzeug.verbindung_karte("Hannover Hbf", start="Bahnhof Musterhausen"))
            fest = [x for x in friday_karten.aktive_routen() if x["daten"]["auftrag"]["start"]][0]
        self.assertEqual(fest["daten"]["auftrag"]["start"]["name"], "Bahnhof Musterhausen")

    def test_ist_im_katalog_und_kennt_fremdes(self):
        namen = [e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]]
        self.assertIn("verbindung_karte", namen)
        self.assertTrue(routewerkzeug.verbindung_karte.kennt_fremde_inhalte)


def _optionen(an="14:20", dauer=20):
    return {"optionen": [{"modus": "fuss", "titel": "Zu Fuß", "dauer_min": dauer, "distanz_m": 1674, "ab": "14:00", "an": an, "hinweis": "", "_an_min": dauer}],
            "schnellste": 0, "ausfaelle": []}


class Live(unittest.TestCase):
    """Die Karte hält sich selbst aktuell: neuer Standort, neue Abfahrten, Ankunft — ohne das Handy zu fragen."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        routelive._reset()
        self.zeit = mock.patch("kern.config.jetzt", return_value=JETZT)
        self.zeit.start()

    def tearDown(self):
        self.zeit.stop()
        self.env.stop()

    def _karte(self, start=None, wann=""):
        opt = _optionen()["optionen"][0]
        daten = {"start": {"name": "Zuhause", "breite": HIER[0], "laenge": HIER[1]}, "ziel": {"name": "Kröpcke", "breite": DORT[0], "laenge": DORT[1]},
                 "optionen": [opt], "schnellste": 0, "ausfaelle": [], "hinweise": [],
                 "auftrag": {"ziel": {"name": "Kröpcke", "breite": DORT[0], "laenge": DORT[1]}, "start": start, "modi": ["fuss", "auto"], "wann": wann}}
        asyncio.run(friday_karten.legen_route(daten))
        return friday_karten.aktive_routen()[0]

    def _auffrischen(self, k, start=None, fehler="", ergebnis=None, ort="Neue Straße", jetzt=None):
        with mock.patch.object(routewerkzeug, "_eigener_start", mock.AsyncMock(
                 return_value=(None, fehler) if fehler else (start or {"name": ort, "breite": HIER[0] + 0.01, "laenge": HIER[1]}, ""))) as es, \
             mock.patch.object(routen, "alle", mock.AsyncMock(return_value=ergebnis or _optionen(an="14:31", dauer=31))) as alle, \
             mock.patch.object(standort, "frisch_holen", mock.AsyncMock()) as frisch:
            asyncio.run(routelive.auffrischen(k))
        return es, alle, frisch

    def test_die_karte_traegt_stand_und_auftrag(self):
        k = self._karte()
        self.assertEqual(k["daten"]["auftrag"]["ziel"]["name"], "Kröpcke")
        self.assertTrue(k["daten"]["stand"])
        offen = asyncio.run(friday_karten.karten())[0]
        self.assertEqual((offen["art"], offen["stand"]), ("route", k["daten"]["stand"]))

    def test_auffrischen_ersetzt_die_daten_in_derselben_karte(self):
        k = self._karte()
        with mock.patch("kern.config.jetzt", return_value=JETZT.replace(minute=40)):           # kurz vor dem Ende der 45 Minuten
            self._auffrischen(k)
            neu = friday_karten.aktive_routen()[0]
        self.assertEqual(neu["id"], k["id"])
        self.assertEqual(neu["daten"]["optionen"][0]["an"], "14:31")
        self.assertAlmostEqual(neu["daten"]["start"]["breite"], HIER[0] + 0.01, places=4)
        self.assertNotEqual(neu["daten"]["stand"], k["daten"]["stand"])
        self.assertGreater(neu["bis"], k["bis"])                       # solange man sie ansieht, bleibt sie liegen

    def test_im_live_lauf_wird_das_handy_nie_gefragt(self):
        k = self._karte()
        es, _, frisch = self._auffrischen(k)
        frisch.assert_not_awaited()
        self.assertIs(es.await_args.kwargs.get("frisch"), False)

    def test_der_ortsname_bleibt_wenn_man_sich_kaum_bewegt(self):
        k = self._karte()
        start = {"name": "Andere Straße", "breite": HIER[0] + 0.0005, "laenge": HIER[1]}       # ~55 m
        self._auffrischen(k, start=start)
        self.assertEqual(friday_karten.aktive_routen()[0]["daten"]["start"]["name"], "Andere Straße")
        # die Namensregel sitzt in _eigener_start (behalte=…): hier wird sie übergeben
        es, _, _ = self._auffrischen(friday_karten.aktive_routen()[0])
        self.assertEqual(es.await_args.kwargs["behalte"]["name"], "Andere Straße")

    def test_ein_fester_start_bleibt_und_nur_die_zeiten_ziehen_nach(self):
        fest = {"name": "Bahnhof Musterhausen", "breite": 52.21, "laenge": 9.55}
        k = self._karte(start=fest)
        es, alle, _ = self._auffrischen(k)
        es.assert_not_awaited()
        self.assertEqual(alle.await_args.args[0], (52.21, 9.55))

    def test_angekommen_beendet_die_verfolgung(self):
        k = self._karte()
        nah = {"name": "Kröpcke", "breite": DORT[0] + 0.0002, "laenge": DORT[1]}
        _, alle, _ = self._auffrischen(k, start=nah)
        alle.assert_not_awaited()
        neu = friday_karten.aktive_routen()[0]
        self.assertTrue(neu["daten"]["angekommen"])
        self.assertLessEqual((__import__("datetime").datetime.fromisoformat(neu["bis"]) - JETZT).total_seconds(), routelive.ANGEKOMMEN_MIN * 60 + 1)

    def test_ohne_brauchbaren_standort_bleibt_die_karte_und_wird_als_veraltet_markiert(self):
        k = self._karte()
        with mock.patch("kern.config.jetzt", return_value=JETZT.replace(minute=10)):      # 10 Minuten nach dem Stand
            self._auffrischen(k, fehler="zu ungenau")
            neu = friday_karten.aktive_routen()[0]
        self.assertEqual(neu["daten"]["optionen"][0]["an"], "14:20")                     # nichts Neues erfunden
        self.assertTrue(neu["daten"]["veraltet"])

    def test_eine_quelle_die_ausfaellt_laesst_die_alte_karte_stehen(self):
        k = self._karte()
        with mock.patch.object(routewerkzeug, "_eigener_start", mock.AsyncMock(return_value=({"name": "x", "breite": HIER[0], "laenge": HIER[1]}, ""))), \
             mock.patch.object(routen, "alle", mock.AsyncMock(side_effect=RuntimeError("aus"))):
            asyncio.run(routelive.auffrischen(k))
        self.assertEqual(friday_karten.aktive_routen()[0]["daten"]["optionen"][0]["an"], "14:20")
        with mock.patch.object(routewerkzeug, "_eigener_start", mock.AsyncMock(return_value=({"name": "x", "breite": HIER[0], "laenge": HIER[1]}, ""))), \
             mock.patch.object(routen, "alle", mock.AsyncMock(return_value={"optionen": [], "schnellste": None, "ausfaelle": [{"modus": "auto", "text": "x"}]})):
            asyncio.run(routelive.auffrischen(k))
        self.assertEqual(friday_karten.aktive_routen()[0]["daten"]["optionen"][0]["an"], "14:20")

    def test_eine_geplante_fahrt_behaelt_ihre_zeit_bis_sie_vorbei_ist(self):
        spaeter = (JETZT.replace(hour=17)).isoformat()
        k = self._karte(wann=spaeter)
        _, alle, _ = self._auffrischen(k)
        self.assertEqual(alle.await_args.kwargs["wann"], JETZT.replace(hour=17))
        k2 = self._karte(wann=(JETZT.replace(hour=9)).isoformat())                       # schon vorbei: jetzt
        _, alle, _ = self._auffrischen(friday_karten.aktive_routen()[-1])
        self.assertIsNone(alle.await_args.kwargs["wann"])

    def test_der_anbieter_fragt_nur_faellige_karten_und_nie_doppelt(self):
        k = self._karte()
        async def lauf():
            with mock.patch.object(routelive, "auffrischen", mock.AsyncMock()) as a:
                await routelive.karten()                          # Stand ist frisch: nichts zu tun
                await asyncio.sleep(0)
                a.assert_not_awaited()
                with mock.patch("kern.config.jetzt", return_value=JETZT.replace(minute=5)):     # 5 Minuten später: fällig
                    await routelive.karten()
                    await routelive.karten()                      # läuft schon
                    await asyncio.sleep(0.01)
                self.assertEqual(a.await_count, 1)
        asyncio.run(lauf())

    def test_der_anbieter_ist_registriert_und_liefert_selbst_keine_karten(self):
        self.assertIn("route_live", [a.name for a in karten.ANBIETER])
        self.assertEqual(asyncio.run(routelive.karten()), [])


if __name__ == "__main__":
    unittest.main()
