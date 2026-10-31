"""Handy-Steuerung: scrcpy-Nachrichten, Paketkopf, Codec aus dem SPS und die Pause des Snap-Helfers."""
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host-helfer"))
import handy_steuerung as hs  # noqa: E402
import scrcpy_protokoll as p  # noqa: E402
import snap_helfer  # noqa: E402


class Nachrichten(unittest.TestCase):
    def test_beruehrung_hat_32_byte_und_die_richtigen_felder(self):
        m = p.beruehrung(p.DOWN, 100, 200, 864, 1920)
        self.assertEqual(len(m), 32)
        typ, aktion, zeiger, x, y, b, h, druck, taste, tasten = struct.unpack(">BBqiiHHHii", m)
        self.assertEqual((typ, aktion, zeiger, x, y, b, h), (2, 0, 0, 100, 200, 864, 1920))
        self.assertEqual(druck, 0xFFFF)
        self.assertEqual((taste, tasten), (1, 1))

    def test_loslassen_hat_keinen_druck(self):
        _, _, _, _, _, _, _, druck, taste, tasten = struct.unpack(">BBqiiHHHii", p.beruehrung(p.UP, 1, 1, 10, 10))
        self.assertEqual((druck, taste, tasten), (0, 0, 0))

    def test_position_ausserhalb_wird_an_den_rand_gezogen(self):
        _, _, _, x, y, *_ = struct.unpack(">BBqiiHHHii", p.beruehrung(p.MOVE, -50, 99999, 864, 1920))
        self.assertEqual((x, y), (0, 1919))

    def test_taste_hat_14_byte(self):
        m = p.taste(p.DOWN, 4)
        self.assertEqual(len(m), 14)
        self.assertEqual(struct.unpack(">BBiii", m), (0, 0, 4, 0, 0))

    def test_text_traegt_laenge_und_utf8(self):
        m = p.text("Hallo")
        self.assertEqual(m, b"\x01\x00\x00\x00\x05Hallo")

    def test_scrollen_hat_21_byte_und_vorzeichen(self):
        m = p.scrollen(5, 6, 100, 200, 0, -16)
        self.assertEqual(len(m), 21)
        _, _, _, _, _, h, v, tasten = struct.unpack(">BiiHHhhi", m)
        self.assertEqual((h, v, tasten), (0, -32767, 0))

    def test_ereignis_taste_gibt_druecken_und_loslassen(self):
        ms = p.aus_ereignis({"t": "taste", "name": "home"}, 100, 100)
        self.assertEqual([struct.unpack(">BBiii", m)[1:3] for m in ms], [(0, 3), (1, 3)])

    def test_unbekannte_taste_und_kaputtes_ereignis_tun_nichts(self):
        self.assertEqual(p.aus_ereignis({"t": "taste", "name": "format_c"}, 100, 100), [])
        self.assertEqual(p.aus_ereignis({"t": "touch", "a": "down"}, 100, 100), [])
        self.assertEqual(p.aus_ereignis({"t": "touch", "a": "zoom", "x": 1, "y": 1}, 100, 100), [])
        self.assertEqual(p.aus_ereignis({"t": "irgendwas"}, 100, 100), [])
        self.assertEqual(p.aus_ereignis({}, 100, 100), [])

    def test_ascii_text_geht_direkt_umlaute_ueber_die_zwischenablage(self):
        self.assertEqual(p.aus_ereignis({"t": "text", "s": "abc"}, 1, 1), [p.text("abc")])
        ms = p.aus_ereignis({"t": "text", "s": "Grüße"}, 1, 1)
        self.assertEqual(ms[0][0], p.SET_CLIPBOARD)
        self.assertIn("Grüße".encode(), ms[0])
        self.assertEqual(len(ms), 3)   # einfügen + Strg+V (KEYCODE_PASTE 279) drücken und loslassen

    def test_zu_langer_text_wird_gekuerzt(self):
        self.assertLessEqual(len(p.text("a" * 1000)), 5 + p.MAX_TEXT)

    def test_leerer_text_tut_nichts(self):
        self.assertEqual(p.aus_ereignis({"t": "text", "s": ""}, 1, 1), [])


class Bildpakete(unittest.TestCase):
    def test_kopf_mit_flags(self):
        pts = 123456
        self.assertEqual(p.kopf_lesen(struct.pack(">QI", pts | p.FLAG_KONFIG, 40)), (pts, True, False, 40))
        self.assertEqual(p.kopf_lesen(struct.pack(">QI", pts | p.FLAG_SCHLUESSEL, 9)), (pts, False, True, 9))
        self.assertEqual(p.kopf_lesen(struct.pack(">QI", pts, 7)), (pts, False, False, 7))

    def test_codec_aus_sps(self):
        konfig = b"\x00\x00\x00\x01\x67\x64\x00\x28\xac\xd9\x00\x00\x00\x01\x68\xee\x3c\x80"
        self.assertEqual(p.codec_aus_sps(konfig), "avc1.640028")

    def test_ohne_sps_kein_codec(self):
        self.assertIsNone(p.codec_aus_sps(b"\x00\x00\x00\x01\x68\xee\x3c\x80"))
        self.assertIsNone(p.codec_aus_sps(b""))


class Sitzungsende(unittest.TestCase):
    def test_nach_der_grenze_ohne_eingabe_ist_schluss(self):
        self.assertFalse(hs.inaktiv(100.0, 100.0 + 599, 600))
        self.assertTrue(hs.inaktiv(100.0, 100.0 + 601, 600))

    def test_leerlauf_nach_20_s_ohne_verbindung(self):
        self.assertFalse(hs.leerlauf_vorbei(None, 999.0, 20))          # verbunden: nie
        self.assertFalse(hs.leerlauf_vorbei(100.0, 100.0 + 19, 20))
        self.assertTrue(hs.leerlauf_vorbei(100.0, 100.0 + 20, 20))
        self.assertFalse(hs.leerlauf_vorbei(100.0, 100.0 + 999, 0))    # 0 = abgeschaltet

    def test_herkunft_nur_von_der_eigenen_seite(self):
        self.assertTrue(hs.herkunft_ok({"origin": "https://hphome.ts.net:8444", "host": "hphome.ts.net:8444"}))
        self.assertFalse(hs.herkunft_ok({"origin": "https://evil.example", "host": "hphome.ts.net:8444"}))
        self.assertFalse(hs.herkunft_ok({"host": "hphome.ts.net:8444"}))


class PauseDesSnapHelfers(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.datei = Path(self.ordner) / "snapchat" / "handy_steuerung.json"

    def tearDown(self):
        self.env.stop()

    def _schreiben(self, ts):
        self.datei.parent.mkdir(parents=True, exist_ok=True)
        self.datei.write_text(json.dumps({"ts": ts}))

    def test_ohne_datei_keine_pause(self):
        self.assertFalse(snap_helfer.fernsteuerung_aktiv())

    def test_frischer_herzschlag_pausiert(self):
        self._schreiben(time.time())
        self.assertTrue(snap_helfer.fernsteuerung_aktiv())

    def test_alter_herzschlag_pausiert_nicht(self):
        self._schreiben(time.time() - 60)
        self.assertFalse(snap_helfer.fernsteuerung_aktiv())

    def test_kaputte_datei_pausiert_nicht(self):
        self.datei.parent.mkdir(parents=True, exist_ok=True)
        self.datei.write_text("kein json")
        self.assertFalse(snap_helfer.fernsteuerung_aktiv())

    def test_zeit_in_der_zukunft_zaehlt_nicht(self):
        self._schreiben(time.time() + 3600)
        self.assertFalse(snap_helfer.fernsteuerung_aktiv())


if __name__ == "__main__":
    unittest.main()
