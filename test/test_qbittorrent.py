"""qBittorrent: passende Torrents zu gelöschten Downloads entfernen — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

import httpx

from kern import konnektoren
from kern.dienste import qbittorrent


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        konnektoren.zugang_setzen("qbittorrent", "nesreduel", "geheim")
        konnektoren.setzen("qbittorrent", True)

    def tearDown(self):
        self.env.stop()

    def _client_mit(self, handler):
        return mock.patch(
            "kern.dienste.qbittorrent._client",
            lambda: httpx.AsyncClient(base_url=qbittorrent.ADRESSE, transport=httpx.MockTransport(handler)),
        )


class Entfernen(Basis):
    def test_entfernt_passende_hashes(self):
        aufrufe = []

        def handler(request):
            aufrufe.append((request.method, request.url.path))
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(204, headers={"set-cookie": "QBT_SID_8080=x; Path=/"})
            if request.url.path == "/api/v2/torrents/info":
                return httpx.Response(200, json=[{"hash": "aaa", "name": "Dark"}])
            if request.url.path == "/api/v2/torrents/delete":
                return httpx.Response(200)
            raise AssertionError(request.url.path)

        with self._client_mit(handler):
            ergebnis = asyncio.run(qbittorrent.torrents_entfernen(["aaa"], loeschen=True))
        self.assertEqual(ergebnis, ["aaa"])
        self.assertIn(("POST", "/api/v2/torrents/delete"), aufrufe)

    def test_leere_hashliste_ruft_nichts_auf(self):
        def handler(request):
            raise AssertionError("bei leerer Liste darf kein Request erfolgen")

        with self._client_mit(handler):
            ergebnis = asyncio.run(qbittorrent.torrents_entfernen([], loeschen=True))
        self.assertEqual(ergebnis, [])

    def test_falscher_hash_wird_ignoriert(self):
        def handler(request):
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(204, headers={"set-cookie": "QBT_SID_8080=x; Path=/"})
            if request.url.path == "/api/v2/torrents/info":
                return httpx.Response(200, json=[{"hash": "bbb", "name": "Anderes"}])
            raise AssertionError("delete sollte nicht aufgerufen werden")

        with self._client_mit(handler):
            ergebnis = asyncio.run(qbittorrent.torrents_entfernen(["aaa"], loeschen=True))
        self.assertEqual(ergebnis, [])

    def test_login_ohne_cookie_wirft(self):
        def handler(request):
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(200)  # kein Set-Cookie: falsches Passwort
            raise AssertionError("sollte nicht weiterkommen")

        with self._client_mit(handler):
            with self.assertRaises(qbittorrent.QbittorrentFehler):
                asyncio.run(qbittorrent.torrents_entfernen(["aaa"], loeschen=True))

    def test_ohne_zugang_wirft(self):
        konnektoren.zugang_loeschen("qbittorrent")
        with self.assertRaises(qbittorrent.QbittorrentFehler):
            asyncio.run(qbittorrent.torrents_entfernen(["aaa"], loeschen=True))

    def test_ausgeschalteter_konnektor_wirft_trotz_zugang(self):
        konnektoren.setzen("qbittorrent", False)

        def handler(request):
            raise AssertionError("bei ausgeschaltetem Konnektor darf kein Request erfolgen")

        with self._client_mit(handler):
            with self.assertRaises(qbittorrent.QbittorrentFehler):
                asyncio.run(qbittorrent.torrents_entfernen(["aaa"], loeschen=True))


if __name__ == "__main__":
    unittest.main()
