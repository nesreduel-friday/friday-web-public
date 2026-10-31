"""Der kleine socket.io-Client zum MindServer — ohne Netz.

Geprüft werden die reine Auswertung der Rahmen (Engine.IO 4 / Socket.IO 5), die Adressumrechnung und
das Verhalten der Verbindung bei Ping, Abbruch und Zeitüberschreitung. Den Handshake gegen den echten
MindServer prüft der Betrieb (`docs/LEHREN.md`, 29.10.2026).
"""

import asyncio
import unittest

from kern.dienste import mindserver


def _lauf(c):
    return asyncio.run(c)


class Adresse(unittest.TestCase):
    def test_http_wird_ws(self):
        self.assertEqual(mindserver.adresse("http://mindcraft:8080"),
                         "ws://mindcraft:8080/socket.io/?EIO=4&transport=websocket")

    def test_https_wird_wss(self):
        self.assertEqual(mindserver.adresse("https://hphome:8445/"),
                         "wss://hphome:8445/socket.io/?EIO=4&transport=websocket")

    def test_ohne_schema_wird_http_angenommen(self):
        self.assertEqual(mindserver.adresse("127.0.0.1:8081"),
                         "ws://127.0.0.1:8081/socket.io/?EIO=4&transport=websocket")

    def test_leere_adresse(self):
        self.assertTrue(mindserver.adresse("").endswith("/socket.io/?EIO=4&transport=websocket"))


class Rahmen(unittest.TestCase):
    def test_engine_io_begruessung(self):
        self.assertEqual(mindserver.rahmen('0{"sid":"abc","pingInterval":25000}'),
                         ("open", {"sid": "abc", "pingInterval": 25000}))

    def test_socket_io_bestaetigung(self):
        self.assertEqual(mindserver.rahmen('40{"sid":"xyz"}'), ("connect", {"sid": "xyz"}))
        self.assertEqual(mindserver.rahmen("40"), ("connect", None))

    def test_ereignis_mit_argumenten(self):
        art, (name, args) = mindserver.rahmen(
            '42["agents-status",[{"name":"Friday","in_game":true}]]')
        self.assertEqual(art, "event")
        self.assertEqual(name, "agents-status")
        self.assertEqual(args[0][0]["name"], "Friday")
        self.assertIs(args[0][0]["in_game"], True)

    def test_ereignis_ohne_argumente(self):
        art, (name, args) = mindserver.rahmen('42["bot-output","Friday","Hallo"]')
        self.assertEqual((art, name, args), ("event", "bot-output", ["Friday", "Hallo"]))

    def test_ping_und_pong(self):
        self.assertEqual(mindserver.rahmen("2"), ("ping", None))
        self.assertEqual(mindserver.rahmen("3"), ("pong", None))

    def test_trennung(self):
        self.assertEqual(mindserver.rahmen("41"), ("disconnect", None))

    def test_fehlerrahmen(self):
        art, text = mindserver.rahmen('44{"message":"nope"}')
        self.assertEqual(art, "fehler")
        self.assertIn("nope", text)

    def test_unbekanntes_wird_nicht_ausgewertet(self):
        for roh in ("", None, "1", "9", "4", "42", '42{"kein":"array"}', '42[1,2]', "42[["):
            with self.subTest(roh):
                self.assertEqual(mindserver.rahmen(roh)[0], "unbekannt")


class Ws:
    """Ein WebSocket, der vorgegebene Nachrichten liefert und Mitschreibt, was gesendet wurde."""

    def __init__(self, nachrichten=()):
        self.nachrichten = list(nachrichten)
        self.gesendet = []
        self.zu = False

    async def recv(self):
        if self.nachrichten:
            return self.nachrichten.pop(0)
        await asyncio.sleep(30)                 # „der Server schweigt"

    async def send(self, text):
        self.gesendet.append(text)

    async def close(self):
        self.zu = True


class Verbindung(unittest.TestCase):
    def test_empfangen_beantwortet_ping_und_liefert_das_ereignis(self):
        ws = Ws(["2", '42["agents-status",[]]'])
        name, args = _lauf(mindserver.Verbindung(ws).empfangen(1.0))
        self.assertEqual((name, args), ("agents-status", [[]]))       # ein Argument: die leere Botliste
        self.assertEqual(ws.gesendet, ["3"])          # Ping muss beantwortet werden

    def test_unbekanntes_wird_uebersprungen(self):
        ws = Ws(["1", "3", '42["state-update",{}]'])
        name, _ = _lauf(mindserver.Verbindung(ws).empfangen(1.0))
        self.assertEqual(name, "state-update")

    def test_schweigen_ist_ein_fehler(self):
        with self.assertRaises(mindserver.Fehler) as ctx:
            _lauf(mindserver.Verbindung(Ws()).empfangen(0.2))
        self.assertIn("nicht geantwortet", str(ctx.exception))

    def test_abbruch_ist_ein_fehler(self):
        class Kaputt(Ws):
            async def recv(self):
                raise ConnectionResetError("weg")

        with self.assertRaises(mindserver.Fehler) as ctx:
            _lauf(mindserver.Verbindung(Kaputt()).empfangen(1.0))
        self.assertIn("beendet", str(ctx.exception))

    def test_gegenseite_trennt(self):
        with self.assertRaises(mindserver.Fehler) as ctx:
            _lauf(mindserver.Verbindung(Ws(["41"])).empfangen(1.0))
        self.assertIn("beendet", str(ctx.exception))

    def test_senden_schreibt_socket_io_rahmen(self):
        ws = Ws()
        _lauf(mindserver.Verbindung(ws).senden("listen-to-agents"))
        self.assertEqual(ws.gesendet, ['42["listen-to-agents"]'])
        _lauf(mindserver.Verbindung(ws).senden("send-message", "Friday", {"message": "Hol Holz."}))
        self.assertEqual(ws.gesendet[-1],
                         '42["send-message","Friday",{"message":"Hol Holz."}]')

    def test_schliessen_ist_geduldig(self):
        ws = Ws()
        _lauf(mindserver.Verbindung(ws).schliessen())
        self.assertEqual(ws.gesendet, ["41"])
        self.assertTrue(ws.zu)

    def test_schliessen_wirft_nie(self):
        class Kaputt(Ws):
            async def send(self, text):
                raise ConnectionResetError("weg")

            async def close(self):
                raise ConnectionResetError("weg")

        _lauf(mindserver.Verbindung(Kaputt()).schliessen())      # kein Fehler


class Unerreichbar(unittest.TestCase):
    def test_verbinden_meldet_einen_satz(self):
        with self.assertRaises(mindserver.Fehler) as ctx:
            _lauf(mindserver.verbinden("http://127.0.0.1:1", zeitlimit=0.5))
        self.assertIn("MindServer", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
