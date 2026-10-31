"""Studio-Statistik: Zusammenbau der Zahlen, Ausfall einzelner Berichte, Zustände der Endpunkte."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import studio
from kern.main import app
from kern.studio import statistik


def _analytics(params):
    """Antworten wie die Analytics-API — je nach Frage."""
    dim, filt = params.get("dimensions", ""), params.get("filters", "")
    metriken = params["metrics"].split(",")

    def antwort(spalten, zeilen):
        return {"columnHeaders": [{"name": n} for n in spalten], "rows": zeilen}

    if dim == "day":
        return antwort(["day"] + metriken, [
            ["2026-09-14"] + [100, 50, 20, 60, 2, 0, 5, 1, 1],
            ["2026-09-15"] + [300, 90, 25, 70, 3, 1, 9, 2, 2]][:2] if len(metriken) == 9 else [])
    if dim == "video":
        return antwort(["video"] + metriken, [
            ["v1v1v1v1v1a"] + [500] + [1.0] * (len(metriken) - 1),
            ["v2v2v2v2v2b"] + [100] + [1.0] * (len(metriken) - 1)])
    if dim == "elapsedVideoTimeRatio":
        return antwort([dim] + metriken, [[0.0, 1.0, 0.5], [0.5, 0.9, 0.5], [0.6, 0.6, 0.5], [1.0, 0.55, 0.5]])
    if dim == "insightTrafficSourceType":
        return antwort([dim, "views", "estimatedMinutesWatched"],
                       [["SHORTS", 900, 10], ["YT_SEARCH", 100, 2]])
    if dim == "ageGroup,gender":
        return antwort(["ageGroup", "gender", "viewerPercentage"], [["age18-24", "male", 40.5]])
    if dim == "country":
        return antwort([dim, "views", "estimatedMinutesWatched"], [["US", 10, 1]])
    if not dim:
        return antwort(metriken, [[400, 140, 22, 65, 5, 1, 14, 3, 3]])
    raise studio.StudioFehler("YouTube antwortet mit 400: nicht unterstützt")


def _daten(url, params):
    if url.endswith("/channels"):
        return {"items": [{"id": "K", "snippet": {"title": "Beispielkanal", "publishedAt": "2025-01-05T10:00:00Z"},
                           "statistics": {"viewCount": "5000", "subscriberCount": "21", "videoCount": "2"},
                           "contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
    if url.endswith("/playlistItems"):
        return {"items": [{"contentDetails": {"videoId": "v1v1v1v1v1a"}},
                          {"contentDetails": {"videoId": "v2v2v2v2v2b"}}]}
    if url.endswith("/videos"):
        return {"items": [
            {"id": "v1v1v1v1v1a", "snippet": {"title": "Why A", "publishedAt": "2026-09-14T06:00:00Z"},
             "statistics": {"viewCount": "700", "likeCount": "9", "commentCount": "1"},
             "contentDetails": {"duration": "PT41S"}, "status": {"privacyStatus": "public"}},
            {"id": "v2v2v2v2v2b", "snippet": {"title": "Why B", "publishedAt": "2026-09-10T06:00:00Z"},
             "statistics": {"viewCount": "100"}, "contentDetails": {"duration": "PT10M"},
             "status": {"privacyStatus": "public"}}]}
    return _analytics(params)


async def _abfrage(url, params):
    return _daten(url, params)


def _lauf(coro):
    return asyncio.run(coro)


class Rechnen(unittest.TestCase):
    def setUp(self):
        statistik.leeren()
        self.p = [mock.patch.object(studio, "_abfrage", _abfrage),
                  mock.patch.object(studio, "_zugriffstoken", mock.AsyncMock(return_value="t"))]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_zeitraum_hat_gleich_langen_vorgaenger(self):
        z = statistik.zeitraum(7)
        self.assertEqual(z["vorher"]["bis"] < z["von"], True)
        from datetime import date
        lang = lambda a, b: (date.fromisoformat(b) - date.fromisoformat(a)).days
        self.assertEqual(lang(z["von"], z["bis"]), lang(z["vorher"]["von"], z["vorher"]["bis"]))

    def test_gesamt_hat_keinen_vorgaenger(self):
        self.assertIsNone(statistik.zeitraum(0, "2025-01-05")["vorher"])

    def test_unbekannter_zeitraum_wird_28(self):
        self.assertEqual(statistik.zeitraum(13)["tage"], 28)

    def test_dauer_lesen(self):
        self.assertEqual(statistik._dauer_s("PT1M5S"), 65)
        self.assertEqual(statistik._dauer_s("PT41S"), 41)
        self.assertEqual(statistik._dauer_s("PT1H"), 3600)
        self.assertEqual(statistik._dauer_s("kaputt"), 0)

    def test_summe_rechnet_abos_und_interaktion(self):
        s = statistik._summe({"views": 400, "subscribersGained": 5, "subscribersLost": 1,
                              "likes": 14, "comments": 3, "shares": 3})
        self.assertEqual(s["abos"], 4)
        self.assertEqual(s["interaktion"], 5.0)
        self.assertEqual(statistik._summe(None)["interaktion"], 0.0)   # keine Teilung durch null

    def test_uebersicht(self):
        d = _lauf(statistik.uebersicht(28))
        self.assertEqual(d["kanal"]["name"], "Beispielkanal")
        self.assertEqual(d["summe"]["aufrufe"], 400)
        self.assertEqual(len(d["reihe"]), 2)
        self.assertEqual(len(d["wochentage"]), 7)
        self.assertEqual(d["spitzen"][0]["id"], "v1v1v1v1v1a")

    def test_videos_short_erkannt_und_zeitraumzahlen_zugeordnet(self):
        d = _lauf(statistik.videos(28))
        v = {x["id"]: x for x in d["videos"]}
        self.assertTrue(v["v1v1v1v1v1a"]["kurz"])
        self.assertFalse(v["v2v2v2v2v2b"]["kurz"])
        self.assertEqual(v["v1v1v1v1v1a"]["zeitraum"]["aufrufe"], 500)
        self.assertEqual(v["v1v1v1v1v1a"]["gesamt"]["aufrufe"], 700)
        self.assertEqual(d["videos"][0]["id"], "v1v1v1v1v1a")            # neueste zuerst

    def test_video_bericht_faellt_auf_kleinen_satz_zurueck(self):
        aufrufe = []

        async def wackelig(url, params):
            if params.get("dimensions") == "video":
                aufrufe.append(params["metrics"])
                if params["metrics"] == statistik.VIDEO_VOLL:
                    raise studio.StudioFehler("YouTube antwortet mit 400: The query is not supported")
            return _daten(url, params)

        with mock.patch.object(studio, "_abfrage", wackelig):
            d = _lauf(statistik.videos(28))
        self.assertEqual(aufrufe, [statistik.VIDEO_VOLL, statistik.VIDEO_KERN])
        self.assertEqual(d["videos"][0]["zeitraum"]["aufrufe"], 500)

    def test_publikum_ein_kaputter_bericht_reisst_nichts_mit(self):
        d = _lauf(statistik.publikum(28))
        self.assertTrue(d["quellen"]["ok"])
        self.assertEqual(d["quellen"]["zeilen"][0]["name"], "Shorts-Feed")
        self.assertEqual(d["quellen"]["zeilen"][0]["anteil"], 90.0)
        self.assertTrue(d["alter"]["ok"])
        self.assertEqual(d["alter"]["zeilen"][0]["alter"], "18-24")
        self.assertFalse(d["geraete"]["ok"])                              # die Fälschung kennt keine Geräte
        self.assertIn("nicht unterstützt", d["geraete"]["fehler"])

    def test_zugangsfehler_bricht_alles_ab(self):
        async def weg(url, params):
            raise studio.ZugangFehler("zurückgezogen")

        with mock.patch.object(studio, "_abfrage", weg):
            with self.assertRaises(studio.ZugangFehler):
                _lauf(statistik.publikum(28))

    def test_video_kurve_und_einbruch(self):
        d = _lauf(statistik.video("v1v1v1v1v1a"))
        self.assertEqual(len(d["kurve"]), 4)
        self.assertEqual(d["einbruch"]["verlust"], 30.0)                  # 0,9 → 0,6 bei 60 % von 41 s
        self.assertAlmostEqual(d["einbruch"]["sekunde"], 24.6, places=1)
        self.assertEqual(d["kurve_relativ"], 0.5)

    def test_unbekanntes_video(self):
        with self.assertRaises(studio.StudioFehler):
            _lauf(statistik.video("gibtsnichtxx"))

    def test_zwischenspeicher_spart_aufrufe(self):
        n = []

        async def zaehlen(url, params):
            n.append(1)
            return _daten(url, params)

        with mock.patch.object(studio, "_abfrage", zaehlen):
            _lauf(statistik.publikum(28))
            erst = len(n)
            _lauf(statistik.publikum(28))
            zweit = len(n) - erst
            self.assertLess(zweit, erst)                # gelungene Berichte kommen aus dem Speicher
            _lauf(statistik.publikum(28, frisch=True))
            self.assertGreaterEqual(len(n) - erst - zweit, erst)


class Endpunkte(unittest.TestCase):
    def setUp(self):
        statistik.leeren()
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        self.env.start()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()

    def test_ohne_freigabe_zustand_aus(self):
        d = self.c.get("/api/studio/statistik/uebersicht").json()
        self.assertEqual(d["zustand"], "aus")

    def test_ohne_verbindung_zustand_getrennt(self):
        konnektoren.setzen("studio", True)
        d = self.c.get("/api/studio/statistik/uebersicht").json()
        self.assertEqual(d["zustand"], "getrennt")

    def test_zurueckgezogener_zugang_ist_getrennt(self):
        konnektoren.setzen("studio", True)
        with mock.patch.object(studio, "verbunden", return_value=True), \
             mock.patch.object(statistik, "uebersicht", side_effect=studio.ZugangFehler("weg")):
            d = self.c.get("/api/studio/statistik/uebersicht").json()
        self.assertEqual((d["zustand"], d["text"]), ("getrennt", "weg"))

    def test_ok_liefert_zahlen(self):
        konnektoren.setzen("studio", True)
        with mock.patch.object(studio, "verbunden", return_value=True), \
             mock.patch.object(statistik, "videos", mock.AsyncMock(return_value={"videos": []})):
            d = self.c.get("/api/studio/statistik/videos?tage=90").json()
        self.assertEqual(d["zustand"], "ok")

    def test_falscher_zeitraum_und_falsche_kennung(self):
        self.assertEqual(self.c.get("/api/studio/statistik/videos?tage=5").status_code, 400)
        self.assertIn(self.c.get("/api/studio/statistik/video/a%2F..%2Fb").status_code, (400, 404))
        self.assertEqual(self.c.get("/api/studio/statistik/video/x").status_code, 400)

    def test_themen_ohne_verlauf_leer(self):
        self.assertEqual(self.c.get("/api/studio/themen").json(), {"themen": []})


if __name__ == "__main__":
    unittest.main()
