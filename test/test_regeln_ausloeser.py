"""WhatsApp-Auslöser für Regeln: eigener Takt für Regel-Chats, Merker, eigene Nachrichten lösen nichts aus — ohne Netz."""
import asyncio
import os
import tempfile
import time
import unittest
from unittest import mock

from kern import konnektoren
from kern.dienste import whatsapp
from kern.regeln import lauf, speicher

CHAT = "4915100000000@c.us"
ANDERER = "4915199999999@c.us"


def run(c):
    return asyncio.run(c)


def n(i, text, stempel, von="Kontakt F", von_mir=False):
    return {"id": i, "zeit": "", "stempel": stempel, "von": von, "von_mir": von_mir, "text": text, "medien": False}


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        whatsapp._regel_gesehen.clear()
        self.jetzt = int(time.time())
        self.frei = mock.patch.object(whatsapp, "frei", return_value=[{"id": CHAT, "name": "Kontakt F", "senden": True},
                                                                      {"id": ANDERER, "name": "Kontakt P", "senden": True}])
        self.frei.start()
        self.ereignis = mock.patch.object(lauf, "ereignis", mock.AsyncMock(return_value=[]))
        self.m_ereignis = self.ereignis.start()
        r = speicher.anlegen("Kontakt F", "whatsapp", {"chat_id": CHAT, "name": "Kontakt F"}, "Antworte kurz.", [], ["push"])
        speicher.bestaetigen(r["id"])

    def tearDown(self):
        self.ereignis.stop()
        self.frei.stop()
        whatsapp._regel_gesehen.clear()
        self.env.stop()

    def stand(self, *nachrichten):
        return mock.patch.object(whatsapp, "nachrichten", mock.AsyncMock(
            return_value={"chat": "Kontakt F", "id": CHAT, "nachrichten": list(nachrichten), "alter_s": 0}))


class Durchgang(Basis):
    def t0(self):
        from datetime import datetime
        return int(datetime.fromisoformat(speicher.aktive("whatsapp")[0]["bestaetigt"]).timestamp())

    def test_erster_lauf_wertet_nachrichten_nach_der_aktivierung_aus_aeltere_nicht(self):
        t0 = self.t0()
        with self.stand(n("a", "Alt", t0 - 60), n("b", "Kannst du morgen?", t0 + 5)):
            run(whatsapp.regel_durchgang(pause=(0, 0)))
        self.m_ereignis.assert_awaited_once()
        self.assertEqual(self.m_ereignis.await_args.args, ("whatsapp", CHAT, "Kontakt F", "Kannst du morgen?", "b"))
        self.assertGreaterEqual(self.m_ereignis.await_args.kwargs["zeit"], t0)

    def test_dieselbe_nachricht_loest_nur_einmal_aus(self):
        t0 = self.t0()
        with self.stand(n("b", "Kannst du morgen?", t0 + 5)):
            run(whatsapp.regel_durchgang(pause=(0, 0)))
            run(whatsapp.regel_durchgang(pause=(0, 0)))
        self.m_ereignis.assert_awaited_once()

    def test_eigene_nachrichten_von_eike_und_friday_loesen_nichts_aus(self):
        t0 = self.t0()
        with self.stand(n("a", "ich selbst", t0 + 5, von="Eike", von_mir=True),
                        n("b", "Klar, morgen passt 🤖", t0 + 6, von_mir=True),
                        n("c", "Klar, morgen passt 🤖", t0 + 7, von_mir=False)):
            run(whatsapp.regel_durchgang(pause=(0, 0)))
        self.m_ereignis.assert_not_awaited()

    def test_sehr_alte_nachrichten_loesen_nichts_aus(self):
        whatsapp._regel_gesehen[CHAT] = 1
        with self.stand(n("a", "vor Tagen", self.jetzt - 5 * 3600)):
            run(whatsapp.regel_durchgang(pause=(0, 0)))
        self.m_ereignis.assert_not_awaited()

    def test_nur_chats_mit_aktiver_regel_werden_abgefragt(self):
        with mock.patch.object(whatsapp, "nachrichten", mock.AsyncMock(
                return_value={"chat": "x", "id": CHAT, "nachrichten": [], "alter_s": 0})) as abruf:
            run(whatsapp.regel_durchgang(pause=(0, 0)))
        self.assertEqual([c.args[0] for c in abruf.await_args_list], [CHAT])

    def test_merker_ueberlebt_einen_neustart(self):
        t0 = self.t0()
        with self.stand(n("a", "Alt", t0 + 5)):
            run(whatsapp.regel_durchgang(pause=(0, 0)))
        whatsapp._regel_gesehen.clear()
        with self.stand(n("a", "Alt", t0 + 5), n("b", "Neu", t0 + 9)):
            run(whatsapp.regel_durchgang(pause=(0, 0)))
        self.assertEqual(self.m_ereignis.await_args.args[4], "b")
        self.assertEqual(self.m_ereignis.await_count, 2)


class Schritt(Basis):
    def test_ruhezeit_ohne_regeln_und_getrennt_fragen_nicht_ab(self):
        with mock.patch.object(konnektoren, "benutzbar", return_value=True), \
             mock.patch.object(whatsapp, "regel_durchgang", mock.AsyncMock()) as d, \
             mock.patch.object(whatsapp, "status", mock.AsyncMock(return_value={"zustand": "WORKING"})):
            with mock.patch.object(whatsapp, "wach", return_value=False):
                self.assertEqual(run(whatsapp.regel_schritt()), "ruhe")
            with mock.patch.object(whatsapp, "wach", return_value=True):
                self.assertEqual(run(whatsapp.regel_schritt()), "ok")
                d.assert_awaited_once()
                with mock.patch.object(whatsapp, "status", mock.AsyncMock(return_value={"zustand": "STOPPED"})):
                    self.assertEqual(run(whatsapp.regel_schritt()), "getrennt")
                for r in speicher.aktive():
                    speicher.setzen(r["id"], "widerrufen")
                self.assertEqual(run(whatsapp.regel_schritt()), "keine")
        self.assertEqual(d.await_count, 1)

    def test_takt_ist_zehn_minuten(self):
        self.assertEqual(whatsapp.REGEL_TAKT_S, 600)


if __name__ == "__main__":
    unittest.main()
