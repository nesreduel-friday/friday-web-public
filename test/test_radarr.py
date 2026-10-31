"""Radarr: Filme lesen, Download-Hashes ermitteln, löschen — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern import konnektoren
from kern.dienste import radarr


class Antwort:
    def __init__(self, status=200, daten=None):
        self.status_code, self._daten = status, daten

    def json(self):
        return self._daten


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        konnektoren.zugang_setzen("radarr", "http://radarr:7878", "geheim")
        konnektoren.setzen("radarr", True)

    def tearDown(self):
        self.env.stop()


class Hinzufuegen(Basis):
    def _anlegen(self, treffer):
        gesendet = {}

        async def anfrage(methode, pfad, params=None, timeout=30, json_body=None):
            if pfad == "/api/v3/rootfolder":
                return Antwort(daten=[{"path": "/m/filme"}, {"path": "/m/marvel"}])
            if pfad == "/api/v3/movie" and methode == "GET":
                return Antwort(daten=[{"qualityProfileId": 6}])
            gesendet.update(json_body)
            return Antwort(daten={"id": 1})
        with mock.patch("kern.dienste.radarr._anfrage", anfrage):
            asyncio.run(radarr.hinzufuegen(treffer))
        return gesendet

    def test_marvel_studio_kommt_in_den_marvel_ordner(self):
        self.assertEqual(self._anlegen({"title": "Thor", "studio": "Marvel Studios"})["rootFolderPath"], "/m/marvel")

    def test_deadpool_und_mcu_sammlung_kommen_nach_marvel(self):
        self.assertEqual(self._anlegen({"title": "Deadpool", "studio": "20th Century Fox",
                                        "collection": {"title": "Deadpool Collection"}})["rootFolderPath"], "/m/marvel")
        self.assertEqual(self._anlegen({"title": "Homecoming", "studio": "Columbia",
                                        "collection": {"title": "Spider-Man (MCU) Collection"}})["rootFolderPath"], "/m/marvel")

    def test_x_men_und_andere_marvel_sammlungen_aber_nicht_blade_runner(self):
        for sammlung in ("X-Men Collection", "The Wolverine Collection", "Fantastic Four Collection", "Spider-Man Collection",
                         "Venom Collection", "Blade Collection", "Guardians of the Galaxy Collection"):
            self.assertTrue(radarr.ist_marvel({"studio": "Fox", "collection": {"title": sammlung}}), sammlung)
        for sammlung in ("Blade Runner Collection", "Batman Collection", "Star Wars Collection"):
            self.assertFalse(radarr.ist_marvel({"studio": "Warner", "collection": {"title": sammlung}}), sammlung)
        self.assertFalse(radarr.ist_marvel({"studio": "Warner"}))

    def test_andere_filme_bleiben_im_filme_ordner(self):
        self.assertEqual(self._anlegen({"title": "Heat", "studio": "Warner Bros."})["rootFolderPath"], "/m/filme")


class Filme(Basis):
    def test_liest_filme(self):
        with mock.patch("kern.dienste.radarr._anfrage",
                         mock.AsyncMock(return_value=Antwort(daten=[{"id": 9, "title": "The Martian"}]))):
            ergebnis = asyncio.run(radarr.filme())
        self.assertEqual(ergebnis, [{"id": 9, "title": "The Martian"}])

    def test_ohne_zugang_wirft(self):
        konnektoren.zugang_loeschen("radarr")
        with self.assertRaises(radarr.RadarrFehler):
            asyncio.run(radarr.filme())

    def test_ausgeschalteter_konnektor_wirft_trotz_zugang(self):
        konnektoren.setzen("radarr", False)
        with mock.patch("kern.dienste.radarr._anfrage",
                         mock.AsyncMock(return_value=Antwort(daten=[{"id": 1}]))):
            with self.assertRaises(radarr.RadarrFehler):
                asyncio.run(radarr.filme())

    def test_kaputtes_json_wirft_statt_valueerror(self):
        kaputt = Antwort(status=200)
        kaputt.json = lambda: (_ for _ in ()).throw(ValueError("kaputt"))
        with mock.patch("kern.dienste.radarr._anfrage", mock.AsyncMock(return_value=kaputt)):
            with self.assertRaises(radarr.RadarrFehler):
                asyncio.run(radarr.filme())


class Loeschen(Basis):
    def test_loeschen_ruft_mit_deletefiles_true(self):
        gesehen = {}

        async def anfrage(methode, pfad, params=None, timeout=30):
            gesehen["methode"], gesehen["pfad"], gesehen["params"] = methode, pfad, params
            return Antwort(status=200)

        with mock.patch("kern.dienste.radarr._anfrage", anfrage):
            asyncio.run(radarr.film_loeschen(9))
        self.assertEqual(gesehen["methode"], "DELETE")
        self.assertEqual(gesehen["pfad"], "/api/v3/movie/9")
        self.assertEqual(gesehen["params"], {"deleteFiles": "true"})


class Hashes(Basis):
    def test_liest_verteilte_hashes_aus_der_historie(self):
        daten = [
            {"downloadId": "CCC333", "eventType": "downloadFolderImported"},
            {"downloadId": "CCC333", "eventType": "downloadFolderImported"},
        ]
        with mock.patch("kern.dienste.radarr._anfrage", mock.AsyncMock(return_value=Antwort(daten=daten))):
            ergebnis = asyncio.run(radarr.download_hashes(9))
        self.assertEqual(ergebnis, ["ccc333"])


if __name__ == "__main__":
    unittest.main()
