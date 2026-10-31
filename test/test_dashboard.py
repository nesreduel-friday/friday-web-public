"""Dashboard (Übersicht): Anbieter, Meldungen, Friday-Karten, Schule, Musik — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from kern.dashboard import friday_karten, karten, meldungen, schule
from kern.dashboard import jellyfin as jellyfinkarte
from kern.dashboard import musik as musikkarte
from kern.dashboard import pc as pckarte
from kern.dashboard import pcmedien as pcmedienkarte
from kern.main import app
from kern.werkzeuge import schule as schulwerkzeug

BERLIN = ZoneInfo("Europe/Berlin")


class Mit_Ordner(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()


def _anbieter(name, liste=None, konnektor=None, prio=50, fehler=None, dauer=0):
    async def f():
        if dauer:
            await asyncio.sleep(dauer)
        if fehler:
            raise fehler
        return list(liste or [])
    return karten.Anbieter(name, f, konnektor, prio)


class Registry(Mit_Ordner):
    def _sammeln(self, anbieter, benutzbar=True):
        with mock.patch.object(karten, "ANBIETER", anbieter), \
             mock.patch("kern.konnektoren.benutzbar", return_value=benutzbar):
            return asyncio.run(karten.sammeln())

    def test_sortiert_nach_prio_und_setzt_fehlende_prio(self):
        a = _anbieter("a", [{"id": "a1", "art": "x"}], prio=40)
        b = _anbieter("b", [{"id": "b1", "art": "x"}, {"id": "b2", "art": "x", "prio": 5}], prio=10)
        ids = [k["id"] for k in self._sammeln([a, b])["karten"]]
        self.assertEqual(ids, ["b2", "b1", "a1"])

    def test_konnektor_aus_heisst_keine_karten_und_kein_aufruf(self):
        aufgerufen = []

        async def f():
            aufgerufen.append(1)
            return [{"id": "z", "art": "x"}]
        a = karten.Anbieter("z", f, "spotify", 10)
        self.assertEqual(self._sammeln([a], benutzbar=False)["karten"], [])
        self.assertEqual(aufgerufen, [])

    def test_ein_kaputter_anbieter_laesst_nur_seine_karte_aus(self):
        gut = _anbieter("gut", [{"id": "g", "art": "x"}])
        kaputt = _anbieter("kaputt", fehler=RuntimeError("boom"))
        ids = [k["id"] for k in self._sammeln([kaputt, gut])["karten"]]
        self.assertEqual(ids, ["g"])

    def test_ein_haengender_anbieter_haelt_die_seite_nicht_auf(self):
        gut = _anbieter("gut", [{"id": "g", "art": "x"}])
        haengt = _anbieter("haengt", [{"id": "h", "art": "x"}], dauer=5)
        with mock.patch.object(karten, "ZEITLIMIT_S", 0.05):
            ids = [k["id"] for k in self._sammeln([haengt, gut])["karten"]]
        self.assertEqual(ids, ["g"])

    def test_karte_ohne_id_oder_art_wird_verworfen(self):
        a = _anbieter("a", [{"art": "x"}, {"id": "q"}, {"id": "ok", "art": "x"}])
        self.assertEqual([k["id"] for k in self._sammeln([a])["karten"]], ["ok"])

    def test_jetzt_ist_iso_mit_offset(self):
        d = self._sammeln([])
        self.assertIsNotNone(datetime.fromisoformat(d["jetzt"]).utcoffset())



class Meldungen(Mit_Ordner):
    def _jetzt(self, h=12, m=0):
        return datetime(2026, 9, 24, h, m, tzinfo=BERLIN)

    def _ablegen(self, *a, wann=None, **kw):
        with mock.patch("kern.config.jetzt", return_value=wann or self._jetzt()):
            meldungen.ablegen(*a, **kw)

    def _offen(self, wann=None):
        with mock.patch("kern.config.jetzt", return_value=wann or self._jetzt()):
            return asyncio.run(meldungen.karten())

    def test_abgelegte_meldung_wird_karte(self):
        self._ablegen("verspaetungen", "Verspätung", "10 € durch Verspätungen", url="/#extras")
        k = self._offen()
        self.assertEqual(len(k), 1)
        self.assertEqual((k[0]["art"], k[0]["titel"], k[0]["quelle"], k[0]["url"]),
                         ("meldung", "Verspätung", "verspaetungen", "/#extras"))
        self.assertTrue(k[0]["id"].startswith("m"))

    def test_gesehen_nimmt_sie_raus(self):
        self._ablegen("garantie", "A", "B")
        kid = self._offen()[0]["id"]
        self.assertTrue(meldungen.gesehen(kid))
        self.assertEqual(self._offen(), [])

    def test_gesehen_unbekannte_oder_fremde_kennung_ist_false(self):
        self.assertFalse(meldungen.gesehen("mdoesnotexist"))
        self.assertFalse(meldungen.gesehen("f123"))

    def test_nach_24_stunden_weg(self):
        self._ablegen("garantie", "A", "B", wann=self._jetzt(8))
        self.assertEqual(len(self._offen(self._jetzt(8) + timedelta(hours=23))), 1)
        self.assertEqual(self._offen(self._jetzt(8) + timedelta(hours=25)), [])

    def test_gleicher_tag_ersetzt_die_aeltere(self):
        self._ablegen("fahrt", "Bus 100", "5 min später", tag="bus100", wann=self._jetzt(7))
        self._ablegen("fahrt", "Bus 100", "12 min später", tag="bus100", wann=self._jetzt(7, 5))
        k = self._offen()
        self.assertEqual([x["text"] for x in k], ["12 min später"])

    def test_ohne_tag_stapeln_sich_meldungen_neueste_zuerst(self):
        self._ablegen("garantie", "Eins", "a", wann=self._jetzt(9))
        self._ablegen("garantie", "Zwei", "b", wann=self._jetzt(10))
        self.assertEqual([x["titel"] for x in self._offen()], ["Zwei", "Eins"])

    def test_hoechstens_zwanzig_gespeichert(self):
        for i in range(25):
            self._ablegen("garantie", f"T{i}", "x", wann=self._jetzt(8) + timedelta(minutes=i))
        self.assertEqual(len(self._offen(self._jetzt(9))), meldungen.MAX_ANZAHL)

    def test_langer_text_wird_gekuerzt(self):
        self._ablegen("briefing", "Morgen", "x" * 1000)
        self.assertLessEqual(len(self._offen()[0]["text"]), meldungen.TEXT_MAX)

    def test_datei_hat_rechte_600_und_kaputte_datei_ist_leere_liste(self):
        self._ablegen("garantie", "A", "B")
        pfad = meldungen._pfad()
        self.assertEqual(oct(pfad.stat().st_mode & 0o777), "0o600")
        pfad.write_text("{kaputt", encoding="utf-8")
        self.assertEqual(self._offen(), [])



class FridayKarten(Mit_Ordner):
    def _jetzt(self, h=12, m=0):
        return datetime(2026, 9, 24, h, m, tzinfo=BERLIN)

    def _legen(self, wann=None, **kw):
        with mock.patch("kern.config.jetzt", return_value=wann or self._jetzt()), \
             mock.patch("kern.dienste.web.pruefen", mock.AsyncMock()):
            return asyncio.run(friday_karten.legen(**kw))

    def _offen(self, wann=None):
        with mock.patch("kern.config.jetzt", return_value=wann or self._jetzt()):
            return asyncio.run(friday_karten.karten())

    def test_legen_und_lesen(self):
        self._legen(titel="Bus 100", text="Fährt 7:52 ab Haltestelle Kröpcke")
        k = self._offen()
        self.assertEqual((k[0]["art"], k[0]["titel"]), ("friday", "Bus 100"))
        self.assertTrue(k[0]["id"].startswith("f"))

    def test_lebensdauer_hat_einen_kurzen_standard_und_klemmt(self):
        self._legen(titel="A", minuten=1)
        self._legen(titel="B")
        self._legen(titel="C", minuten=99999)
        bis = {k["titel"]: datetime.fromisoformat(k["bis"]) - self._jetzt() for k in self._offen()}
        self.assertEqual(bis["A"], timedelta(minutes=1))
        self.assertEqual(bis["B"], timedelta(minutes=friday_karten.MINUTEN_STANDARD))
        self.assertEqual(bis["C"], timedelta(minutes=friday_karten.MINUTEN_MAX))

    def test_standard_ist_kurz_weil_eine_veraltete_karte_schlimmer_ist_als_keine(self):
        # Eike, 24.09.2026: „Aktuelle Uhrzeit“ stand nach über 10 Minuten noch da.
        self.assertLessEqual(friday_karten.MINUTEN_STANDARD, 15)

    def test_abgelaufen_ist_weg(self):
        self._legen(titel="A", minuten=10)
        self.assertEqual(self._offen(self._jetzt() + timedelta(minutes=11)), [])

    def test_hoechstens_sechs_die_aelteste_weicht(self):
        for i in range(8):
            self._legen(wann=self._jetzt(8, i), titel=f"K{i}", minuten=120)
        titel = [k["titel"] for k in self._offen(self._jetzt(9))]
        self.assertEqual(len(titel), friday_karten.MAX_AKTIV)
        self.assertNotIn("K0", titel)
        self.assertIn("K7", titel)

    def test_ohne_titel_oder_zu_lang_wird_abgelehnt_bzw_gekuerzt(self):
        with self.assertRaises(friday_karten.KarteFehler):
            self._legen(titel="  ")
        self._legen(titel="T" * 500, text="x" * 2000)
        k = self._offen()[0]
        self.assertLessEqual(len(k["titel"]), friday_karten.TITEL_MAX)
        self.assertLessEqual(len(k["text"]), friday_karten.TEXT_MAX)

    def test_bild_nur_https_oder_eigene_bilder(self):
        self._legen(titel="ok", bild_url="https://example.org/a.png")
        self._legen(titel="eigen", bild_url="/api/bilder/" + "a" * 32)      # echte Kennungen sind 32 Hex, nie Zahlen
        for schlecht in ("http://example.org/a.png", "javascript:alert(1)", "/api/bilder/../x",
                         "//evil.example/a.png", "data:image/png;base64,AAAA", "/api/bilder/" + "a" * 32 + "?x=1", "/api/bilder/12"):
            with self.assertRaises(friday_karten.KarteFehler, msg=schlecht):
                self._legen(titel="x", bild_url=schlecht)

    def test_bild_ins_heimnetz_wird_abgelehnt(self):
        from kern.dienste import web
        with mock.patch("kern.config.jetzt", return_value=self._jetzt()), \
             mock.patch("kern.dienste.web.pruefen", mock.AsyncMock(side_effect=web.NichtLesbar("Heimnetz"))):
            with self.assertRaises(friday_karten.KarteFehler):
                asyncio.run(friday_karten.legen(titel="x", bild_url="https://192.168.1.5/a.png"))

    def test_gesehen_nimmt_sie_raus_und_fremde_kennung_ist_false(self):
        self._legen(titel="A")
        kid = self._offen()[0]["id"]
        self.assertFalse(friday_karten.gesehen("m123"))
        self.assertTrue(friday_karten.gesehen(kid))
        self.assertEqual(self._offen(), [])



def _std(nummer, von, bis, fach="Mathe", raum="B 1.15", art="regularLesson", lehrer="Müller", tag="2026-09-24"):
    quelle = {"subject": {"name": fach}, "room": {"name": raum},
              "teachers": [{"lastname": lehrer}]}
    return {"date": tag, "type": art, "classHour": {"number": nummer, "from": von, "until": bis},
            "actualLesson": quelle, "originalLessons": [quelle]}


def _um(h, m=0, s=0, tag=24):
    return datetime(2026, 9, tag, h, m, s, tzinfo=BERLIN)


PLAN = [_std("1", "07:45", "08:30", "Mathe"), _std("2", "08:35", "09:20", "Deutsch", "A 2.03"),
        _std("3", "09:40", "10:25", "Englisch", "C 1.01")]


class SchuleZustand(unittest.TestCase):
    def test_lange_vor_der_schule_keine_karte(self):
        self.assertIsNone(schule.berechne(PLAN, _um(6, 30)))

    def test_vorlauf_30_minuten_vor_der_ersten_stunde(self):
        k = schule.berechne(PLAN, _um(7, 20))
        self.assertEqual((k["zustand"], k["naechste"]["fach"]), ("vor_schule", "Mathe"))
        self.assertEqual(datetime.fromisoformat(k["bis"]), _um(7, 45))

    def test_im_unterricht_bis_pause_und_danach(self):
        k = schule.berechne(PLAN, _um(7, 50))
        self.assertEqual(k["zustand"], "unterricht")
        self.assertEqual((k["aktuell"]["fach"], k["aktuell"]["raum"]), ("Mathe", "B 1.15"))
        self.assertEqual(datetime.fromisoformat(k["bis"]), _um(8, 30))
        self.assertEqual(k["naechste"]["fach"], "Deutsch")

    def test_kurze_luecke_gilt_als_pause_mit_countdown_zur_naechsten(self):
        k = schule.berechne(PLAN, _um(9, 30))
        self.assertEqual(k["zustand"], "pause")
        self.assertEqual((k["naechste"]["fach"], k["naechste"]["raum"]), ("Englisch", "C 1.01"))
        self.assertEqual(datetime.fromisoformat(k["bis"]), _um(9, 40))
        self.assertEqual(datetime.fromisoformat(k["von"]), _um(9, 20))

    def test_genau_zur_stundengrenze_beginnt_die_naechste(self):
        self.assertEqual(schule.berechne(PLAN, _um(9, 40))["zustand"], "unterricht")
        self.assertEqual(schule.berechne(PLAN, _um(9, 39, 59))["zustand"], "pause")

    def test_nach_der_letzten_stunde_keine_karte(self):
        self.assertIsNone(schule.berechne(PLAN, _um(10, 25)))
        self.assertIsNone(schule.berechne(PLAN, _um(15)))

    def test_leerer_plan_wochenende_und_falscher_tag(self):
        self.assertIsNone(schule.berechne([], _um(9)))
        self.assertIsNone(schule.berechne(PLAN, _um(9, tag=25)))   # Plan gilt für den 24.

    def test_doppelstunde_ohne_klingeln_ist_ein_block(self):
        plan = [_std("1", "07:45", "08:30", "Physik", "N 1"), _std("2", "08:35", "09:20", "Physik", "N 1")]
        k = schule.berechne(plan, _um(8, 32))
        self.assertEqual(k["zustand"], "unterricht")
        self.assertEqual(datetime.fromisoformat(k["bis"]), _um(9, 20))

    def test_gleiches_fach_in_anderem_raum_ist_getrennt(self):
        plan = [_std("1", "07:45", "08:30", "Sport", "Halle A"), _std("2", "08:35", "09:20", "Sport", "Halle B")]
        self.assertEqual(schule.berechne(plan, _um(8, 32))["zustand"], "pause")

    def test_ausfall_zaehlt_nicht_als_unterricht_und_wird_in_der_pause_genannt(self):
        plan = [_std("1", "07:45", "08:30", "Mathe"),
                _std("2", "08:35", "09:20", "Deutsch", art="cancelledLesson"),
                _std("3", "09:40", "10:25", "Englisch")]
        k = schule.berechne(plan, _um(8, 50))
        self.assertEqual(k["zustand"], "pause")
        self.assertIn("Deutsch", k["hinweis"])
        self.assertEqual(datetime.fromisoformat(k["bis"]), _um(9, 40))
        self.assertEqual(k["art_pause"], "freistunde")

    def test_vertretung_wird_markiert(self):
        eigentlich = {"subject": {"name": "Mathe"}, "room": {"name": "B 1.15"}, "teachers": [{"lastname": "Müller"}]}
        vertretung = {"subject": {"name": "Kunst"}, "room": {"name": "K 1"}, "teachers": [{"lastname": "Lehmann"}]}
        plan = [{"date": "2026-09-24", "type": "substitution",
                 "classHour": {"number": "1", "from": "07:45", "until": "08:30"},
                 "actualLesson": vertretung, "originalLessons": [eigentlich]}]
        k = schule.berechne(plan, _um(7, 50))
        self.assertTrue(k["aktuell"]["vertretung"])
        self.assertEqual(k["aktuell"]["fach"], "Kunst")
        self.assertIn("Mathe", k["hinweis"])

    def test_stunde_ohne_zeiten_wird_ignoriert(self):
        plan = [{"date": "2026-09-24", "type": "regularLesson", "classHour": {"number": "1"},
                 "actualLesson": {"subject": {"name": "Mathe"}}}]
        self.assertIsNone(schule.berechne(plan, _um(8)))


class SchuleKarte(Mit_Ordner):
    def setUp(self):
        super().setUp()
        schule._cache.clear()

    def _karten(self, wann=None, plan=PLAN, fehler=None):
        ruf = mock.AsyncMock(return_value=plan, side_effect=fehler)
        with mock.patch("kern.config.jetzt", return_value=wann or _um(8)), \
             mock.patch("kern.dienste.schulmanager.stundenplan", ruf):
            return asyncio.run(schule.karten()), ruf

    def test_karte_hat_stabile_id_und_zustand(self):
        k, _ = self._karten()
        self.assertEqual((k[0]["id"], k[0]["art"], k[0]["zustand"]), ("schule", "schule", "unterricht"))

    def test_plan_wird_zehn_minuten_zwischengespeichert(self):
        _, ruf = self._karten()
        with mock.patch("kern.config.jetzt", return_value=_um(8, 5)), \
             mock.patch("kern.dienste.schulmanager.stundenplan", ruf):
            asyncio.run(schule.karten())
        self.assertEqual(ruf.await_count, 1)

    def test_schulmanager_fehler_wird_nicht_verschluckt_und_nicht_zwischengespeichert(self):
        # Verschluckt wird er von der Registry (mit Warnung im Log), nicht hier: sonst sähe niemand,
        # WARUM die Schulkarte fehlt. Der Fehler darf aber den nächsten Abruf nicht vergiften.
        with self.assertRaises(RuntimeError):
            self._karten(fehler=RuntimeError("down"))
        k, _ = self._karten()
        self.assertEqual(len(k), 1)

    def test_ausserhalb_der_schulzeit_wird_der_plan_gar_nicht_geholt(self):
        k, ruf = self._karten(wann=_um(22))
        self.assertEqual(k, [])



    def test_am_wochenende_wird_der_plan_nicht_geholt(self):
        k, ruf = self._karten(wann=_um(9, tag=26))
        self.assertEqual(k, [])
        self.assertEqual(ruf.await_count, 0)



TRACK = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
ALBUM = "spotify:album:1DFixLWuPkv3KT3TnV35m3"
KUENSTLER = "spotify:artist:1dfeR4HaWDbWqFHLkxsg1d"


def _stand(laeuft=True, titel="So What", cover="https://i.scdn.co/a300"):
    return {"titel": titel, "wer": "P!nk", "album": "Funhouse", "cover": cover, "laeuft": laeuft,
            "position_ms": 60000, "dauer_ms": 200000, "geraet": "Echo", "art": "track", "regelbar": True,
            "uri": TRACK, "album_uri": ALBUM, "artist_uri": KUENSTLER}


class MusikKarte(Mit_Ordner):
    def setUp(self):
        super().setUp()
        musikkarte._reset()

    def _karten(self, stand, wann=None, schreiben=True):
        with mock.patch("kern.config.jetzt", return_value=wann or _um(12)), \
             mock.patch("kern.dienste.spotify.laeuft_gerade", mock.AsyncMock(return_value=stand)) as ruf, \
             mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=schreiben):
            return asyncio.run(musikkarte.karten()), ruf

    def test_laeuft_gibt_karte_mit_cover_und_steuerbar(self):
        k, _ = self._karten(_stand())
        self.assertEqual((k[0]["id"], k[0]["art"], k[0]["titel"], k[0]["cover"], k[0]["steuerbar"]),
                         ("musik", "musik", "So What", "https://i.scdn.co/a300", True))
        self.assertTrue(k[0]["laeuft"])

    def test_ohne_schreibrecht_nicht_steuerbar(self):
        k, _ = self._karten(_stand(), schreiben=False)
        self.assertFalse(k[0]["steuerbar"])

    def test_nichts_aktiv_keine_karte(self):
        self.assertEqual(self._karten(None)[0], [])

    def test_pausiert_bleibt_zehn_minuten_sichtbar_dann_weg(self):
        self._karten(_stand(True), wann=_um(12, 0))
        self.assertEqual(len(self._karten(_stand(False), wann=_um(12, 9), )[0]), 1)
        self.assertEqual(self._karten(_stand(False), wann=_um(12, 11))[0], [])

    def test_pausiert_ohne_vorher_gehoert_zu_haben_keine_karte(self):
        self.assertEqual(self._karten(_stand(False))[0], [])

    def test_zehn_sekunden_zwischenspeicher(self):
        _, ruf = self._karten(_stand(), wann=_um(12, 0, 0))
        with mock.patch("kern.config.jetzt", return_value=_um(12, 0, 5)), \
             mock.patch("kern.dienste.spotify.laeuft_gerade", ruf), \
             mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True):
            asyncio.run(musikkarte.karten())
        self.assertEqual(ruf.await_count, 1)

    def test_kaputter_cover_link_wird_leer(self):
        k, _ = self._karten(_stand(cover="http://unsicher/a"))
        self.assertEqual(k[0]["cover"], "")

    def test_karte_traegt_die_spotify_adressen(self):
        k, _ = self._karten(_stand())
        self.assertEqual((k[0]["uri"], k[0]["album_uri"], k[0]["artist_uri"]),
                         (TRACK, ALBUM, KUENSTLER))

    def test_fremde_adressen_werden_leer(self):
        # Die Adresse landet in einem Link der Seite: nur spotify:<art>:<id>, nie javascript: o. ä.
        stand = {**_stand(), "uri": "javascript:alert(1)", "album_uri": "https://evil.example/a",
                 "artist_uri": "spotify:artist:R1\"><x"}
        k, _ = self._karten(stand)
        self.assertEqual((k[0]["uri"], k[0]["album_uri"], k[0]["artist_uri"]), ("", "", ""))

    def test_befehle(self):
        for name, fn in (("pause", "pause"), ("weiter", "weiter"), ("naechster", "naechster"),
                         ("vorheriger", "vorheriger")):
            with mock.patch(f"kern.dienste.spotify.{fn}", mock.AsyncMock()) as m, \
                 mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True):
                asyncio.run(musikkarte.befehl(name))
            m.assert_awaited_once()

    def test_befehl_ohne_schreibrecht_und_unbekannter_befehl(self):
        with mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=False):
            with self.assertRaises(PermissionError):
                asyncio.run(musikkarte.befehl("pause"))
        with mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True):
            with self.assertRaises(ValueError):
                asyncio.run(musikkarte.befehl("lauter"))



class Routen(Mit_Ordner):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)

    def test_get_liefert_karten_und_jetzt_auch_wenn_alles_leer_ist(self):
        with mock.patch.object(karten, "ANBIETER", []):
            r = self.client.get("/api/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["karten"], [])
        self.assertIn("jetzt", r.json())

    def test_antwort_wird_nicht_zwischengespeichert(self):
        with mock.patch.object(karten, "ANBIETER", []):
            r = self.client.get("/api/dashboard")
        self.assertIn("no-store", r.headers.get("cache-control", ""))

    def test_gesehen(self):
        from kern.dashboard import meldungen
        meldungen.ablegen("garantie", "A", "B")
        kid = asyncio.run(meldungen.karten())[0]["id"]
        self.assertEqual(self.client.post("/api/dashboard/gesehen", json={"id": kid}).status_code, 200)
        self.assertEqual(asyncio.run(meldungen.karten()), [])
        self.assertEqual(self.client.post("/api/dashboard/gesehen", json={"id": "mnix"}).status_code, 404)

    def test_musik_befehl(self):
        with mock.patch("kern.dashboard.musik.befehl", mock.AsyncMock()) as m:
            self.assertEqual(self.client.post("/api/dashboard/musik/pause").status_code, 200)
        m.assert_awaited_once_with("pause")
        with mock.patch("kern.dashboard.musik.befehl", mock.AsyncMock(side_effect=ValueError("x"))):
            self.assertEqual(self.client.post("/api/dashboard/musik/lauter").status_code, 404)
        with mock.patch("kern.dashboard.musik.befehl", mock.AsyncMock(side_effect=PermissionError("nein"))):
            self.assertEqual(self.client.post("/api/dashboard/musik/pause").status_code, 403)
        from kern.dienste import spotify
        with mock.patch("kern.dashboard.musik.befehl", mock.AsyncMock(side_effect=spotify.SpotifyFehler("kein Gerät"))):
            r = self.client.post("/api/dashboard/musik/pause")
        self.assertEqual(r.status_code, 502)
        self.assertIn("kein Gerät", r.json()["detail"])


class StundenplanKarte(Mit_Ordner):
    """Friday legt den Stundenplan eines beliebigen Tages als Karte auf die Übersicht."""
    TAG = date(2026, 9, 24)

    def _karten(self):
        return asyncio.run(friday_karten.karten())

    def test_liste_ist_nach_stundennummer_sortiert_und_nur_dieser_tag(self):
        plan = [_std("2", "08:35", "09:20", "Deutsch"), _std("1", "07:45", "08:30", "Mathe"),
                _std("1", "07:45", "08:30", "Bio", tag="2026-09-25")]
        liste = schulwerkzeug.tages_liste(plan, self.TAG)
        self.assertEqual([x["fach"] for x in liste], ["Mathe", "Deutsch"])
        self.assertEqual((liste[0]["nr"], liste[0]["von"], liste[0]["bis"], liste[0]["raum"], liste[0]["status"]),
                         ("1", "07:45", "08:30", "B 1.15", "regulaer"))

    def test_ausfall_behaelt_das_fach_und_vertretung_nennt_das_eigentliche(self):
        eigentlich = {"subject": {"name": "Mathe"}, "room": {"name": "B 1.15"}, "teachers": [{"lastname": "Müller"}]}
        vertretung = {"subject": {"name": "Kunst"}, "room": {"name": "K 1"}, "teachers": [{"lastname": "Lehmann"}]}
        plan = [_std("1", "07:45", "08:30", "Mathe", art="cancelledLesson"),
                {"date": "2026-09-24", "type": "substitution",
                 "classHour": {"number": "2", "from": "08:35", "until": "09:20"},
                 "actualLesson": vertretung, "originalLessons": [eigentlich]}]
        a, v = schulwerkzeug.tages_liste(plan, self.TAG)
        self.assertEqual((a["fach"], a["status"]), ("Mathe", "ausfall"))
        self.assertEqual((v["fach"], v["status"]), ("Kunst", "vertretung"))
        self.assertIn("Mathe", v["hinweis"])

    def test_stunde_ohne_zeiten_bleibt_in_der_liste(self):
        plan = [{"date": "2026-09-24", "type": "regularLesson", "classHour": {"number": "3"},
                 "actualLesson": {"subject": {"name": "Sport"}}}]
        liste = schulwerkzeug.tages_liste(plan, self.TAG)
        self.assertEqual((liste[0]["fach"], liste[0]["von"], liste[0]["bis"]), ("Sport", "", ""))

    def test_fach_kommt_als_name_nicht_als_kurskuerzel(self):
        # Echte Daten (25.09.2026): subject.name ist null, es gibt nur „BI281“ / „MA281“ (Kurs + Nummer).
        echt = {"subject": {"abbreviation": "BI281", "name": None}, "subjectLabel": "BI281",
                "room": {"name": "B 0.01"}, "teachers": [{"abbreviation": "MUS", "lastname": "Muster"}]}
        plan = [{"date": "2026-09-24", "type": "regularLesson", "classHour": {"number": "1", "from": "08:10", "until": "08:55"},
                 "actualLesson": echt, "originalLessons": [echt]}]
        self.assertEqual(schulwerkzeug.tages_liste(plan, self.TAG)[0]["fach"], "Biologie")
        self.assertEqual(schulwerkzeug._fach({"subject": {"name": "Sport", "abbreviation": "SP"}}), "Sport")   # Name gewinnt
        self.assertEqual(schulwerkzeug._fach({"subject": {"name": None, "abbreviation": "XY9"}, "subjectLabel": "XY9"}), "XY9")
        self.assertEqual(schulwerkzeug._fach({}), "?")

    def test_schulkarte_zeigt_den_fachnamen(self):
        echt = {"subject": {"abbreviation": "MA281", "name": None}, "subjectLabel": "MA281", "room": {"name": "B 0.07"}}
        plan = [{"date": "2026-09-24", "type": "regularLesson", "classHour": {"number": "3", "from": "10:05", "until": "10:50"},
                 "actualLesson": echt, "originalLessons": [echt]}]
        k = schule.berechne(plan, _um(10, 10))
        self.assertEqual(k["aktuell"]["fach"], "Mathematik")

    def test_karte_ablegen_und_lesen(self):
        liste = schulwerkzeug.tages_liste([_std("1", "07:45", "08:30")], self.TAG)
        asyncio.run(friday_karten.legen_stundenplan(self.TAG, liste, 120))
        k = self._karten()[0]
        self.assertEqual((k["art"], k["datum"], len(k["stunden"])), ("stundenplan", "2026-09-24", 1))
        self.assertTrue(k["id"].startswith("f"))
        self.assertTrue(k["app_url"].startswith("https://login.schulmanager-online.de"))
        self.assertTrue(friday_karten.gesehen(k["id"]))       # wegwischbar wie jede Friday-Karte
        self.assertEqual(self._karten(), [])

    def test_ablage_begrenzt_und_saeubert_fremde_werte(self):
        roh = [{"nr": str(i), "von": "07:45", "bis": "08:30", "fach": "F" * 300, "raum": "R", "lehrer": "L",
                "status": "<script>", "hinweis": "H" * 300} for i in range(30)]
        asyncio.run(friday_karten.legen_stundenplan(self.TAG, roh, 60))
        stunden = self._karten()[0]["stunden"]
        self.assertEqual(len(stunden), friday_karten.STUNDEN_MAX)
        self.assertLessEqual(len(stunden[0]["fach"]), 60)
        self.assertLessEqual(len(stunden[0]["hinweis"]), 80)
        self.assertEqual(stunden[0]["status"], "regulaer")    # unbekannter Status wird nie durchgereicht

    def test_tool_legt_die_karte_fuer_den_gewuenschten_tag(self):
        plan = [_std("1", "07:45", "08:30", "Mathe"), _std("2", "08:35", "09:20", "Deutsch")]
        with mock.patch("kern.dienste.schulmanager.stundenplan", mock.AsyncMock(return_value=plan)) as ruf:
            text = asyncio.run(schulwerkzeug.stundenplan_karte("2026-09-24", 90))
        self.assertEqual(ruf.await_args.args, (self.TAG, self.TAG))
        self.assertIn("Karte", text)
        self.assertEqual(len(self._karten()[0]["stunden"]), 2)

    def test_tool_ohne_stunden_legt_keine_karte_und_behauptet_nichts(self):
        with mock.patch("kern.dienste.schulmanager.stundenplan", mock.AsyncMock(return_value=[])):
            text = asyncio.run(schulwerkzeug.stundenplan_karte("2026-09-24"))
        self.assertIn("keine Stunden", text)
        self.assertIn("nicht zwingend", text)          # „keine Daten“ ist nicht „kein Unterricht“
        self.assertEqual(self._karten(), [])

    def test_tool_schulmanager_fehler_sagt_es_und_legt_nichts(self):
        with mock.patch("kern.dienste.schulmanager.stundenplan", mock.AsyncMock(side_effect=httpx.ConnectError("x"))):
            text = asyncio.run(schulwerkzeug.stundenplan_karte("heute"))
        self.assertIn("Schulmanager", text)
        self.assertEqual(self._karten(), [])


PC_STATUS = {"ok": True, "rechner": "PC-VON-EIKE", "gesperrt": False, "laufzeit_s": 4200,
             "fenster": [{"programm": "chrome", "titel": "Friday"}], "ordner": []}


class PcKarte(Mit_Ordner):
    def setUp(self):
        super().setUp()
        pckarte._reset()

    def _auffrischen(self, status=None, fehler=None):
        ruf = mock.AsyncMock(side_effect=fehler) if fehler else mock.AsyncMock(return_value=status)
        with mock.patch("kern.dienste.pc.status", ruf):
            asyncio.run(pckarte._auffrischen())

    def _karten(self, offen=()):
        with mock.patch("kern.dashboard.pc._anstossen"), \
             mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True), \
             mock.patch("kern.dienste.pc.offene", return_value=list(offen)):
            return asyncio.run(pckarte.karten())

    def test_ohne_abfrage_keine_karte(self):
        self.assertEqual(self._karten(), [])

    def test_pc_an_gibt_karte(self):
        self._auffrischen(PC_STATUS)
        k = self._karten()[0]
        self.assertEqual((k["id"], k["art"], k["rechner"], k["laufzeit_s"]), ("pc", "pc", "PC-VON-EIKE", 4200))
        self.assertEqual(k["fenster"][0]["programm"], "chrome")
        self.assertTrue(k["schreiben"])

    def test_pc_aus_keine_karte(self):
        self._auffrischen(PC_STATUS)
        self._auffrischen(fehler=pckarte.pcdienst.PcFehler("nicht erreichbar"))
        self.assertEqual(self._karten(), [])

    def test_zeitueberschreitung_keine_karte(self):
        self._auffrischen(fehler=asyncio.TimeoutError())
        self.assertEqual(self._karten(), [])

    def test_alter_helfer_ohne_neue_felder(self):
        self._auffrischen({"ok": True, "rechner": "PC", "gesperrt": True, "ordner": []})
        k = self._karten()[0]
        self.assertIsNone(k["laufzeit_s"])
        self.assertEqual(k["fenster"], [])
        self.assertTrue(k["gesperrt"])

    def test_rueckfragen_stehen_in_der_karte(self):
        self._auffrischen(PC_STATUS)
        offen = [{"id": "ab12", "beschreibung": "PC in 60 Sekunden herunterfahren", "fremd": False, "rest_s": 500}]
        k = self._karten(offen)[0]
        self.assertEqual(k["offen"][0]["id"], "ab12")
        self.assertNotIn("fremd", k["offen"][0])

    def test_anfrage_blockiert_die_karten_nicht(self):
        async def lauf():
            haengt = asyncio.Event()

            async def langsam():
                await haengt.wait()
            with mock.patch("kern.dienste.pc.status", langsam):
                inhalt = await asyncio.wait_for(pckarte.karten(), 1)      # kehrt sofort zurück
            pckarte._aufgabe.cancel()
            return inhalt
        self.assertEqual(asyncio.run(lauf()), [])

    def test_neuerer_helfer_wird_gemeldet(self):
        for alt, neu, erwartet in (("1.0.0", "1.2.0", "1.2.0"), ("1.2.0", "1.2.0", ""), ("1.10.0", "1.2.0", ""), (None, "1.2.0", "")):
            with self.subTest(alt):
                pckarte._reset()
                self._auffrischen({**PC_STATUS, **({"version": alt} if alt else {})})
                with mock.patch("kern.dienste.pc.helfer_version", return_value=neu):
                    k = self._karten()[0]
                self.assertEqual(k["helfer_neu"], erwartet)

    def test_anbieter_ist_registriert(self):
        self.assertIn("pc", [a.name for a in karten.ANBIETER])


def _jf(laeuft=True, fern=True):
    return {"sitzung": "s1", "titel": "Beispielserie", "untertitel": "S1 E3 · Der Anfang", "geraet": "Wohnzimmer-TV",
            "laeuft": laeuft, "position_s": 60, "dauer_s": 2700, "bild_id": "a" * 32, "steuerbar": fern}


class JellyfinKarte(Mit_Ordner):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        jellyfinkarte._reset()

    def _karten(self, stand, wann=None, schreiben=True):
        with mock.patch("kern.config.jetzt", return_value=wann or _um(12)), \
             mock.patch("kern.dienste.jellyfin.laeuft_gerade", mock.AsyncMock(return_value=stand)) as ruf, \
             mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=schreiben):
            return asyncio.run(jellyfinkarte.karten()), ruf

    def test_laeuft_gibt_karte(self):
        k, _ = self._karten(_jf())
        self.assertEqual((k[0]["id"], k[0]["art"], k[0]["titel"]), ("jellyfin", "jellyfin", "Beispielserie"))
        self.assertEqual(k[0]["bild"], "/api/jellyfin/bild/" + "a" * 32)
        self.assertTrue(k[0]["steuerbar"])

    def test_ohne_schreibrecht_oder_fernsteuerung_nicht_steuerbar(self):
        self.assertFalse(self._karten(_jf(), schreiben=False)[0][0]["steuerbar"])
        jellyfinkarte._reset()
        self.assertFalse(self._karten(_jf(fern=False))[0][0]["steuerbar"])

    def test_ohne_bild(self):
        s = {**_jf(), "bild_id": ""}
        self.assertEqual(self._karten(s)[0][0]["bild"], "")

    def test_nichts_laeuft_keine_karte(self):
        self.assertEqual(self._karten(None)[0], [])

    def test_pause_bleibt_zehn_minuten_nur_wenn_laufen_gesehen(self):
        self._karten(_jf(True), wann=_um(12))
        self.assertEqual(len(self._karten(_jf(False), wann=_um(12, 8))[0]), 1)
        self.assertEqual(self._karten(_jf(False), wann=_um(12, 20))[0], [])
        jellyfinkarte._reset()
        self.assertEqual(self._karten(_jf(False), wann=_um(12))[0], [])     # nie laufen gesehen

    def test_zwischenspeicher(self):
        _, ruf = self._karten(_jf(), wann=_um(12))
        _, ruf2 = self._karten(_jf(), wann=_um(12, 0, 2))       # zwei Sekunden später: nicht noch einmal fragen
        self.assertEqual(ruf.await_count, 1)
        self.assertEqual(ruf2.await_count, 0)

    def test_dienstfehler_keine_karte(self):
        from kern.dienste import jellyfin
        with mock.patch("kern.dienste.jellyfin.laeuft_gerade", mock.AsyncMock(side_effect=jellyfin.JellyfinFehler("x"))), \
             mock.patch("kern.config.jetzt", return_value=_um(12)):
            self.assertEqual(asyncio.run(jellyfinkarte.karten()), [])

    def test_befehl_ohne_schreibrecht(self):
        with mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=False), \
             mock.patch("kern.dienste.jellyfin.befehl", mock.AsyncMock()) as ruf:
            with self.assertRaises(PermissionError):
                asyncio.run(jellyfinkarte.befehl("pause"))
        ruf.assert_not_awaited()

    def test_befehl_route(self):
        from kern.dienste import jellyfin
        with mock.patch("kern.dashboard.jellyfin.befehl", mock.AsyncMock()) as m:
            self.assertEqual(self.client.post("/api/dashboard/jellyfin/pause").status_code, 200)
        m.assert_awaited_once_with("pause")
        for fehler, code in ((ValueError("x"), 404), (PermissionError("nein"), 403), (jellyfin.JellyfinFehler("aus"), 502)):
            with mock.patch("kern.dashboard.jellyfin.befehl", mock.AsyncMock(side_effect=fehler)):
                self.assertEqual(self.client.post("/api/dashboard/jellyfin/pause").status_code, code)

    def test_bild_route(self):
        with mock.patch("kern.konnektoren.benutzbar", return_value=True), \
             mock.patch("kern.dienste.jellyfin.bild", mock.AsyncMock(return_value=(b"\x89PNG", "image/png"))):
            r = self.client.get("/api/jellyfin/bild/" + "a" * 32)
        self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "image/png", b"\x89PNG"))
        for boese in ("xyz", "A" * 32, "a" * 31):
            self.assertEqual(self.client.get("/api/jellyfin/bild/" + boese).status_code, 404)
        with mock.patch("kern.konnektoren.benutzbar", return_value=False):
            self.assertEqual(self.client.get("/api/jellyfin/bild/" + "a" * 32).status_code, 403)

    def test_bild_route_fehler_der_gegenstelle(self):
        from kern.dienste import jellyfin
        with mock.patch("kern.konnektoren.benutzbar", return_value=True), \
             mock.patch("kern.dienste.jellyfin.bild", mock.AsyncMock(side_effect=jellyfin.JellyfinFehler("aus"))):
            self.assertEqual(self.client.get("/api/jellyfin/bild/" + "a" * 32).status_code, 502)

    def test_anbieter_ist_registriert(self):
        self.assertIn("jellyfin", [a.name for a in karten.ANBIETER])


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def _pm(titel="Ein Video", app="Chrome", laeuft=True, bild_id="0123456789abcdef", kennung="Chrome"):
    return {"app": app, "app_kennung": kennung, "titel": titel, "kuenstler": "Ein Kanal", "laeuft": laeuft,
            "position_s": 61, "dauer_s": 300, "bild_id": bild_id, "bild": ""}


class PcMedienKarte(Mit_Ordner):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        pcmedienkarte._reset()
        jellyfinkarte._reset()

    def _auffrischen(self, medien=None, fehler=None, bild=""):
        import base64
        ruf = mock.AsyncMock(side_effect=fehler) if fehler else mock.AsyncMock(
            side_effect=lambda mit_bild=False: {**medien, "bild": bild if mit_bild else ""} if medien else None)
        with mock.patch("kern.dienste.pc.medien", ruf), mock.patch("kern.config.jetzt", return_value=_um(12)):
            asyncio.run(pcmedienkarte._auffrischen())
        return ruf

    def _karten(self, wann=None, schreiben=True):
        with mock.patch("kern.dashboard.pcmedien._anstossen"), mock.patch("kern.config.jetzt", return_value=wann or _um(12)), \
             mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=schreiben):
            return asyncio.run(pcmedienkarte.karten())

    def test_laeuft_gibt_karte_mit_bild(self):
        import base64
        self._auffrischen(_pm(), bild=base64.b64encode(PNG).decode())
        k = self._karten()[0]
        self.assertEqual((k["id"], k["art"], k["titel"], k["app"], k["laeuft"]), ("pcmedien", "pcmedien", "Ein Video", "Chrome", True))
        self.assertEqual(k["bild"], "/api/pcmedien/bild/0123456789abcdef")
        self.assertTrue(k["steuerbar"])

    def test_bild_wird_nur_einmal_geholt(self):
        import base64
        ruf = self._auffrischen(_pm(), bild=base64.b64encode(PNG).decode())
        self._auffrischen(_pm(), bild=base64.b64encode(PNG).decode())
        self.assertEqual([c.kwargs.get("mit_bild", False) for c in ruf.await_args_list], [False, True])

    def test_ohne_bild_kein_link(self):
        self._auffrischen(_pm(), bild="")
        self.assertEqual(self._karten()[0]["bild"], "")

    def test_zu_grosses_oder_kein_bild_wird_verworfen(self):
        import base64
        self._auffrischen(_pm(), bild=base64.b64encode(b"x" * 700_000).decode())
        self.assertEqual(self._karten()[0]["bild"], "")

    def test_nichts_laeuft_keine_karte(self):
        self._auffrischen(None)
        self.assertEqual(self._karten(), [])

    def test_pc_aus_keine_karte(self):
        self._auffrischen(_pm())
        self._auffrischen(fehler=pcmedienkarte.pcdienst.PcFehler("aus"))
        self.assertEqual(self._karten(), [])

    def test_spotify_am_pc_gibt_keine_zweite_karte(self):
        self._auffrischen(_pm(app="Spotify", kennung="Spotify.exe"))
        self.assertEqual(self._karten(), [])

    def test_jellyfin_im_pc_browser_gibt_keine_zweite_karte(self):
        jellyfinkarte._stand.update(zeit=_um(12), daten={"titel": "Dark", "untertitel": "S3 E2 · Die Überlebenden",
                                                          "laeuft": True})
        self._auffrischen(_pm(titel="Die Überlebenden"))
        self.assertEqual(self._karten(), [])

    def test_anderes_video_neben_jellyfin_bleibt(self):
        jellyfinkarte._stand.update(zeit=_um(12), daten={"titel": "Dark", "untertitel": "S3 E2 · Die Überlebenden",
                                                          "laeuft": True})
        self._auffrischen(_pm(titel="Katzenvideo"))
        self.assertEqual(len(self._karten()), 1)

    def test_pause_bleibt_zehn_minuten_nur_wenn_laufen_gesehen(self):
        self._auffrischen(_pm(laeuft=True))
        self._karten(wann=_um(12))
        self._auffrischen(_pm(laeuft=False))
        self.assertEqual(len(self._karten(wann=_um(12, 8))), 1)
        self.assertEqual(self._karten(wann=_um(12, 20)), [])
        pcmedienkarte._reset()
        self._auffrischen(_pm(laeuft=False))
        self.assertEqual(self._karten(), [])              # nie laufen gesehen

    def test_ohne_titel_steht_die_app(self):
        self._auffrischen(_pm(titel=""))
        self.assertEqual(self._karten()[0]["titel"], "Chrome")

    def test_befehl(self):
        ok = mock.AsyncMock(return_value={"ok": True, "text": "Erledigt."})
        with mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True), mock.patch("kern.dienste.pc.aktion", ok):
            asyncio.run(pcmedienkarte.befehl("pause"))
        ok.assert_awaited_once_with("medien", {"befehl": "pause"})
        with self.assertRaises(ValueError):
            asyncio.run(pcmedienkarte.befehl("lauter"))
        with mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=False), mock.patch("kern.dienste.pc.aktion", ok):
            with self.assertRaises(PermissionError):
                asyncio.run(pcmedienkarte.befehl("pause"))
        nein = mock.AsyncMock(return_value={"ok": False, "text": "Nichts läuft."})
        with mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True), mock.patch("kern.dienste.pc.aktion", nein):
            with self.assertRaises(pcmedienkarte.pcdienst.PcFehler):
                asyncio.run(pcmedienkarte.befehl("pause"))

    def test_routen(self):
        import base64
        for fehler, code in ((ValueError("x"), 404), (PermissionError("nein"), 403),
                             (pcmedienkarte.pcdienst.PcFehler("aus"), 502), (None, 200)):
            with mock.patch("kern.dashboard.pcmedien.befehl", mock.AsyncMock(side_effect=fehler)):
                self.assertEqual(self.client.post("/api/dashboard/pcmedien/pause").status_code, code)
        self._auffrischen(_pm(), bild=base64.b64encode(PNG).decode())
        r = self.client.get("/api/pcmedien/bild/0123456789abcdef")
        self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "image/png", PNG))
        for boese in ("nope", "0123456789ABCDEF", "0" * 17, "ffffffffffffffff"):
            self.assertEqual(self.client.get("/api/pcmedien/bild/" + boese).status_code, 404)

    def test_anbieter_ist_registriert(self):
        self.assertIn("pcmedien", [a.name for a in karten.ANBIETER])


if __name__ == "__main__":
    unittest.main()
