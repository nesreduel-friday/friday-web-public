"""Gemeinsame Netzhelfer: Verbindungsaufbau wiederholen, Ursache von Verbindungsfehlern loggen."""
import unittest

import httpx

from kern.dienste import netz


class Client(unittest.TestCase):
    def test_verbindungsaufbau_wird_wiederholt(self):
        c = netz.client(timeout=5)
        self.assertIsInstance(c, httpx.AsyncClient)
        self.assertEqual(c._transport._pool._retries, netz.VERSUCHE)
        self.assertGreaterEqual(netz.VERSUCHE, 2)

    def test_eigene_einstellungen_bleiben(self):
        c = netz.client(follow_redirects=True, headers={"User-Agent": "x"})
        self.assertTrue(c.follow_redirects)
        self.assertEqual(c.headers["User-Agent"], "x")


class NichtErreichbar(unittest.TestCase):
    def test_text_wie_bisher_und_ursache_im_log(self):
        ursache = OSError("Temporary failure in name resolution")
        exc = httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        exc.__cause__ = ursache
        with self.assertLogs("kern.dienste.netz", "WARNING") as log:
            text = netz.nicht_erreichbar("Spotify", exc)
        self.assertEqual(text, "Spotify antwortet gerade nicht (ConnectError).")
        self.assertIn("Temporary failure in name resolution", "\n".join(log.output))
        self.assertIn("Spotify", "\n".join(log.output))

    def test_zusatz_wird_angehaengt(self):
        with self.assertLogs("kern.dienste.netz", "WARNING"):
            text = netz.nicht_erreichbar("Der Schulmanager", RuntimeError("x"), " Später nochmal versuchen.")
        self.assertEqual(text, "Der Schulmanager antwortet gerade nicht (RuntimeError). Später nochmal versuchen.")


if __name__ == "__main__":
    unittest.main()
