"""Sonarr: Serien lesen, Download-Hashes ermitteln, löschen — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern import konnektoren
from kern.dienste import sonarr


class Antwort:
    def __init__(self, status=200, daten=None):
        self.status_code, self._daten = status, daten

    def json(self):
        return self._daten


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        konnektoren.zugang_setzen("sonarr", "http://sonarr:8989", "geheim")
        konnektoren.setzen("sonarr", True)

    def tearDown(self):
        self.env.stop()

    def _async(self, koroutine):
        return asyncio.run(koroutine)


class MarvelSerien(Basis):
    def test_erkennt_marvel_serien(self):
        for titel, jahr in (("Loki", 2021), ("Marvel's Daredevil", 2015), ("Agents of S.H.I.E.L.D.", 2013), ("Echo", 2024),
                            ("Cloak & Dagger", 2018), ("What If...?", 2021), ("Marvel's Jessica Jones", 2015)):
            self.assertTrue(sonarr.ist_marvel(titel, jahr), titel)

    def test_gleichnamige_andere_serien_sind_keine(self):
        self.assertFalse(sonarr.ist_marvel("Echo", 1999))
        self.assertFalse(sonarr.ist_marvel("Legion", 2000))
        self.assertFalse(sonarr.ist_marvel("Dark", 2017))

    def test_hinzufuegen_waehlt_den_marvel_ordner(self):
        def lauf(treffer):
            gesendet = {}

            async def anfrage(methode, pfad, params=None, timeout=30, json_body=None):
                if pfad == "/api/v3/rootfolder":
                    return Antwort(daten=[{"path": "/m/serien"}, {"path": "/m/marvel-serien"}])
                if methode == "GET":
                    return Antwort(daten=[{"qualityProfileId": 6}])
                gesendet.update(json_body)
                return Antwort(daten={"id": 1})
            with mock.patch("kern.dienste.sonarr._anfrage", anfrage):
                asyncio.run(sonarr.hinzufuegen(treffer))
            return gesendet["rootFolderPath"]
        self.assertEqual(lauf({"title": "Loki", "year": 2021}), "/m/marvel-serien")
        self.assertEqual(lauf({"title": "Dexter", "year": 2006}), "/m/serien")


class Serien(Basis):
    def test_liest_serien(self):
        with mock.patch("kern.dienste.sonarr._anfrage",
                         mock.AsyncMock(return_value=Antwort(daten=[{"id": 1, "title": "Dark"}]))):
            ergebnis = self._async(sonarr.serien())
        self.assertEqual(ergebnis, [{"id": 1, "title": "Dark"}])

    def test_ohne_zugang_wirft(self):
        konnektoren.zugang_loeschen("sonarr")
        with self.assertRaises(sonarr.SonarrFehler):
            self._async(sonarr.serien())

    def test_ausgeschalteter_konnektor_wirft_trotz_zugang(self):
        """Default-Deny: 'aus' auf der Freigaben-Seite muss auch beim direkten
        Dienstaufruf greifen, nicht nur, wenn kein Zugang eingetragen ist."""
        konnektoren.setzen("sonarr", False)
        with mock.patch("kern.dienste.sonarr._anfrage",
                         mock.AsyncMock(return_value=Antwort(daten=[{"id": 1}]))):
            with self.assertRaises(sonarr.SonarrFehler):
                self._async(sonarr.serien())

    def test_401_wirft(self):
        with mock.patch("kern.dienste.sonarr._anfrage", mock.AsyncMock(return_value=Antwort(status=401))):
            with self.assertRaises(sonarr.SonarrFehler):
                self._async(sonarr.serien())

    def test_kaputtes_json_wirft_statt_valueerror(self):
        kaputt = Antwort(status=200)
        kaputt.json = lambda: (_ for _ in ()).throw(ValueError("kaputt"))
        with mock.patch("kern.dienste.sonarr._anfrage", mock.AsyncMock(return_value=kaputt)):
            with self.assertRaises(sonarr.SonarrFehler):
                self._async(sonarr.serien())


class Loeschen(Basis):
    def test_loeschen_ruft_mit_deletefiles_true(self):
        gesehen = {}

        async def anfrage(methode, pfad, params=None, timeout=30):
            gesehen["methode"], gesehen["pfad"], gesehen["params"] = methode, pfad, params
            return Antwort(status=200)

        with mock.patch("kern.dienste.sonarr._anfrage", anfrage):
            self._async(sonarr.serie_loeschen(5))
        self.assertEqual(gesehen["methode"], "DELETE")
        self.assertEqual(gesehen["pfad"], "/api/v3/series/5")
        self.assertEqual(gesehen["params"], {"deleteFiles": "true"})


class Hashes(Basis):
    def test_liest_verteilte_hashes_aus_der_historie(self):
        daten = [
            {"downloadId": "AAA111", "eventType": "downloadFolderImported"},
            {"downloadId": "AAA111", "eventType": "downloadFolderImported"},
            {"downloadId": "BBB222", "eventType": "grabbed"},
        ]
        with mock.patch("kern.dienste.sonarr._anfrage", mock.AsyncMock(return_value=Antwort(daten=daten))):
            ergebnis = self._async(sonarr.download_hashes(5))
        self.assertEqual(ergebnis, ["aaa111"])


class StaffelnUndFolgen(Basis):
    def test_serie_details_liefert_seasons(self):
        with mock.patch("kern.dienste.sonarr._anfrage",
                         mock.AsyncMock(return_value=Antwort(daten={"id": 5, "seasons": [{"seasonNumber": 1}]}))):
            ergebnis = self._async(sonarr.serie_details(5))
        self.assertEqual(ergebnis["seasons"], [{"seasonNumber": 1}])

    def test_episoden_liefert_liste(self):
        daten = [{"id": 1, "seasonNumber": 1, "episodeFileId": 10, "hasFile": True}]
        with mock.patch("kern.dienste.sonarr._anfrage", mock.AsyncMock(return_value=Antwort(daten=daten))):
            ergebnis = self._async(sonarr.episoden(5))
        self.assertEqual(ergebnis, daten)

    def test_staffel_loeschen_loescht_dateien_und_unmonitored_nur_diese_staffel(self):
        aufrufe = []

        async def anfrage(methode, pfad, params=None, json_body=None, timeout=30):
            aufrufe.append((methode, pfad, params, json_body))
            if pfad == "/api/v3/episode":
                return Antwort(daten=[
                    {"id": 1, "seasonNumber": 1, "episodeFileId": 10, "hasFile": True},
                    {"id": 2, "seasonNumber": 1, "episodeFileId": 11, "hasFile": True},
                    {"id": 3, "seasonNumber": 2, "episodeFileId": 12, "hasFile": True},
                ])
            if pfad == "/api/v3/episodefile/bulk":
                return Antwort(status=200)
            if pfad == "/api/v3/series/5":
                return Antwort(daten={
                    "id": 5,
                    "seasons": [
                        {"seasonNumber": 1, "monitored": True},
                        {"seasonNumber": 2, "monitored": True},
                    ],
                })
            raise AssertionError(pfad)

        with mock.patch("kern.dienste.sonarr._anfrage", anfrage):
            anzahl = self._async(sonarr.staffel_loeschen(5, 1))

        self.assertEqual(anzahl, 2)
        bulk = [a for a in aufrufe if a[1] == "/api/v3/episodefile/bulk"][0]
        self.assertEqual(sorted(bulk[3]["episodeFileIds"]), [10, 11])
        put = [a for a in aufrufe if a[1] == "/api/v3/series/5" and a[0] == "PUT"][0]
        seasons = put[3]["seasons"]
        self.assertFalse(next(s for s in seasons if s["seasonNumber"] == 1)["monitored"])
        self.assertTrue(next(s for s in seasons if s["seasonNumber"] == 2)["monitored"])

    def test_staffel_loeschen_parallel_verschiedene_staffeln_race_frei(self):
        """Zwei Staffeln derselben Serie gleichzeitig löschen darf kein
        verlorenes Update geben (Lesen-Ändern-Schreiben der ganzen Serie)."""
        import json as jsonlib
        serverstand = {"id": 5, "seasons": [{"seasonNumber": 1, "monitored": True},
                                             {"seasonNumber": 2, "monitored": True}]}

        async def anfrage(methode, pfad, params=None, json_body=None, timeout=30):
            if pfad == "/api/v3/episode":
                return Antwort(daten=[])
            if pfad == "/api/v3/series/5" and methode == "GET":
                await asyncio.sleep(0)
                return Antwort(daten=jsonlib.loads(jsonlib.dumps(serverstand)))
            if pfad == "/api/v3/series/5" and methode == "PUT":
                await asyncio.sleep(0)  # Chance zum Verzahnen, falls ungesperrt
                serverstand["seasons"] = json_body["seasons"]
                return Antwort(status=200)
            raise AssertionError(pfad)

        async def beide():
            return await asyncio.gather(sonarr.staffel_loeschen(5, 1), sonarr.staffel_loeschen(5, 2))

        with mock.patch("kern.dienste.sonarr._anfrage", anfrage):
            self._async(beide())

        monitored = {s["seasonNumber"]: s["monitored"] for s in serverstand["seasons"]}
        self.assertFalse(monitored[1])
        self.assertFalse(monitored[2])

    def test_staffel_loeschen_ohne_dateien_ueberspringt_bulk_delete(self):
        aufrufe = []

        async def anfrage(methode, pfad, params=None, json_body=None, timeout=30):
            aufrufe.append(pfad)
            if pfad == "/api/v3/episode":
                return Antwort(daten=[{"id": 1, "seasonNumber": 1, "episodeFileId": None, "hasFile": False}])
            if pfad == "/api/v3/series/5":
                return Antwort(daten={"id": 5, "seasons": [{"seasonNumber": 1, "monitored": True}]})
            raise AssertionError(pfad)

        with mock.patch("kern.dienste.sonarr._anfrage", anfrage):
            anzahl = self._async(sonarr.staffel_loeschen(5, 1))

        self.assertEqual(anzahl, 0)
        self.assertNotIn("/api/v3/episodefile/bulk", aufrufe)

    def test_folge_loeschen_mit_datei_loescht_und_unmonitored(self):
        aufrufe = []

        async def anfrage(methode, pfad, params=None, json_body=None, timeout=30):
            aufrufe.append((methode, pfad, json_body))
            return Antwort(status=200)

        with mock.patch("kern.dienste.sonarr._anfrage", anfrage):
            self._async(sonarr.folge_loeschen(7, 70))

        self.assertIn(("DELETE", "/api/v3/episodefile/70", None), aufrufe)
        monitor = [a for a in aufrufe if a[1] == "/api/v3/episode/monitor"][0]
        self.assertEqual(monitor[2], {"episodeIds": [7], "monitored": False})

    def test_folge_loeschen_ohne_datei_nur_unmonitored(self):
        aufrufe = []

        async def anfrage(methode, pfad, params=None, json_body=None, timeout=30):
            aufrufe.append(pfad)
            return Antwort(status=200)

        with mock.patch("kern.dienste.sonarr._anfrage", anfrage):
            self._async(sonarr.folge_loeschen(7, None))

        self.assertNotIn("/api/v3/episodefile/None", aufrufe)
        self.assertEqual(aufrufe, ["/api/v3/episode/monitor"])

    def test_download_hashes_fuer_episoden_liest_records_form(self):
        async def anfrage(methode, pfad, params=None, json_body=None, timeout=30):
            self.assertEqual(pfad, "/api/v3/history")
            self.assertEqual(params, {"episodeId": 7})
            return Antwort(daten={"records": [
                {"downloadId": "AAA", "eventType": "downloadFolderImported"},
                {"downloadId": "AAA", "eventType": "grabbed"},
            ]})

        with mock.patch("kern.dienste.sonarr._anfrage", anfrage):
            ergebnis = self._async(sonarr.download_hashes_fuer_episoden([7]))
        self.assertEqual(ergebnis, ["aaa"])

    def test_hash_episoden_zuordnung_gruppiert_nach_hash(self):
        daten = [
            {"episodeId": 1, "downloadId": "AAA", "eventType": "downloadFolderImported"},
            {"episodeId": 2, "downloadId": "AAA", "eventType": "downloadFolderImported"},
            {"episodeId": 3, "downloadId": "BBB", "eventType": "downloadFolderImported"},
            {"episodeId": 3, "downloadId": "BBB", "eventType": "grabbed"},
        ]
        with mock.patch("kern.dienste.sonarr._anfrage", mock.AsyncMock(return_value=Antwort(daten=daten))):
            ergebnis = self._async(sonarr.hash_episoden_zuordnung(5))
        self.assertEqual(ergebnis, {"aaa": {1, 2}, "bbb": {3}})

    def test_hashes_ausschliesslich_fuer_lehnt_geteilten_staffelpaket_hash_ab(self):
        """Ein Torrent-Hash, der auch eine NICHT gelöschte Folge betrifft
        (Staffelpaket), darf nicht zurückgegeben werden — sonst würde das
        Löschen einer Folge den ganzen Torrent samt fremder Folgen entfernen."""
        daten = [
            {"episodeId": 1, "downloadId": "PAKET", "eventType": "downloadFolderImported"},
            {"episodeId": 2, "downloadId": "PAKET", "eventType": "downloadFolderImported"},
            {"episodeId": 3, "downloadId": "EINZEL", "eventType": "downloadFolderImported"},
        ]
        with mock.patch("kern.dienste.sonarr._anfrage", mock.AsyncMock(return_value=Antwort(daten=daten))):
            # Nur Folge 1 wird gelöscht — "PAKET" gehört aber auch zu Folge 2.
            nur_folge_1 = self._async(sonarr.hashes_ausschliesslich_fuer(5, {1}))
            # Folgen 1 UND 2 werden gelöscht — "PAKET" betrifft dann nur noch gelöschte Folgen.
            folge_1_und_2 = self._async(sonarr.hashes_ausschliesslich_fuer(5, {1, 2}))
            # Folge 3 allein betroffen — "EINZEL" ist exklusiv.
            nur_folge_3 = self._async(sonarr.hashes_ausschliesslich_fuer(5, {3}))
        self.assertEqual(nur_folge_1, [])
        self.assertEqual(folge_1_und_2, ["paket"])
        self.assertEqual(nur_folge_3, ["einzel"])


if __name__ == "__main__":
    unittest.main()
