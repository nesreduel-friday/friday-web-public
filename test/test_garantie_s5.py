"""S5: eigener Filter und eigene Meldungsart (14.10.2026) — ohne Netz."""

import asyncio
import unittest
from unittest import mock

from kern.dienste import benachrichtigungen
from kern.garantie import sammler, suche
from test.test_garantie import Basis, JETZT


def _fahrt(linie, kennung):
    return {"jid": kennung, "linie": linie, "kategorie": "S-Bahn" if linie.startswith("S") else "Bus",
            "richtung": "Hannover", "halt": "Bennigsen", "verspaetung": 25, "plan": JETZT, "echt": JETZT}


class S5(Basis):
    def test_ist_s5(self):
        self.assertTrue(suche.ist_s5({"linie": "S5"}))
        self.assertTrue(suche.ist_s5({"linie": " s 5 "}))
        self.assertFalse(suche.ist_s5({"linie": "S50"}))
        self.assertFalse(suche.ist_s5({"linie": "S1"}))
        self.assertFalse(suche.ist_s5({}))

    def _melden(self, verspaetungen, s5):
        benachrichtigungen.stellen("verspaetungen", verspaetungen)
        benachrichtigungen.stellen("verspaetungen_s5", s5)
        neu = [_fahrt("S5", "jid-s5"), _fahrt("S1", "jid-s1"), _fahrt("100", "jid-bus")]
        with mock.patch.object(benachrichtigungen, "senden", return_value={}) as senden:
            self.assertEqual(asyncio.run(sammler.melden(neu)), 3)
        return senden

    def test_nur_s5_meldet_nur_die_s5(self):
        senden = self._melden(False, True)
        self.assertEqual(senden.call_count, 1)
        self.assertEqual(senden.call_args.args[0], "verspaetungen_s5")
        self.assertIn("S5", senden.call_args.args[1])

    def test_alle_meldet_alle(self):
        senden = self._melden(True, False)
        self.assertEqual({c.args[0] for c in senden.call_args_list}, {"verspaetungen"})
        self.assertEqual(senden.call_count, 3)

    def test_aus_schickt_nichts_aufs_handy(self):
        benachrichtigungen.stellen("verspaetungen", False)
        benachrichtigungen.stellen("verspaetungen_s5", False)
        with mock.patch("kern.dienste.push.senden") as push:
            asyncio.run(sammler.melden([_fahrt("S5", "jid-s5")]))
        push.assert_not_called()
