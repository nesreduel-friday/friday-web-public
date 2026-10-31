"""Gesprochene Runden und im Gespräch getippter Text landen im Chat-Strang (quelle „live“)."""

import os
import tempfile
import unittest
from unittest import mock

from kern.chatstrang import speicher as strang
from kern.protokoll import speicher as protokoll
from kern.sitzung import live


class LiveStrang(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        env.start()
        self.addCleanup(env.stop)
        self.proto = protokoll.standard()
        self.g = self.proto.gespraech_beginnen("sprache")

    def test_mitschrift_schreibt_beide_rollen(self):
        mit = live._Mitschrift(self.proto, self.g)
        mit.dazu("du", "Wie spät ")
        mit.dazu("du", "ist es?")
        mit.dazu("friday", "Kurz nach acht.")
        mit.abschliessen()
        z = strang.standard().sichtbar()
        self.assertEqual([(e["rolle"], e["text"], e["quelle"]) for e in z],
                         [("du", "Wie spät ist es?", "live"), ("friday", "Kurz nach acht.", "live")])

    def test_leerer_puffer_schreibt_nichts(self):
        mit = live._Mitschrift(self.proto, self.g)
        mit.dazu("friday", "Nur ich.")
        mit.abschliessen()
        mit.abschliessen()
        self.assertEqual([e["text"] for e in strang.standard().sichtbar()], ["Nur ich."])

    def test_getippter_text_im_gespraech(self):
        import asyncio

        class Stumm:
            async def send(self, *_):
                pass

            async def send_json(self, *_):
                pass
        asyncio.run(live._text(Stumm(), Stumm(), "Hallo Friday", self.proto, self.g))
        self.assertEqual([(e["rolle"], e["text"], e["quelle"]) for e in strang.standard().sichtbar()],
                         [("du", "Hallo Friday", "live")])


if __name__ == "__main__":
    unittest.main()
