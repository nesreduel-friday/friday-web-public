"""Jellyfin: Sitzungen lesen, Bild holen, steuern — ohne Netz (die Anfrage wird ersetzt)."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern import konnektoren
from kern.dienste import jellyfin

FOLGE = {"Id": "a" * 32, "Name": "Der Anfang", "Type": "Episode", "SeriesName": "Beispielserie",
         "ParentIndexNumber": 1, "IndexNumber": 3, "RunTimeTicks": 27_000_000_000,
         "ImageTags": {"Primary": "x"}}
FILM = {"Id": "b" * 32, "Name": "Ein Film", "Type": "Movie", "ProductionYear": 2019,
        "RunTimeTicks": 60_000_000_000, "ImageTags": {}}
LIED = {"Id": "c" * 32, "Name": "So What", "Type": "Audio", "Artists": ["Miles Davis"], "AlbumId": "d" * 32,
        "AlbumPrimaryImageTag": "y", "RunTimeTicks": 5_000_000_000, "ImageTags": {}}


def _sitzung(item, pausiert=False, pos=600_000_000, wann="2026-09-25T12:00:00Z", fern=True, sid="s1"):
    s = {"Id": sid, "DeviceName": "Wohnzimmer-TV", "SupportsRemoteControl": fern, "LastActivityDate": wann,
         "PlayState": {"IsPaused": pausiert, "PositionTicks": pos}}
    if item:
        s["NowPlayingItem"] = item
    return s


class Antwort:
    def __init__(self, status=200, daten=None, inhalt=b"", typ="application/json"):
        self.status_code, self._daten, self.content, self.headers = status, daten, inhalt, {"content-type": typ}

    def json(self):
        return self._daten


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        konnektoren.zugang_setzen("jellyfin", "http://172.17.0.1:8096/", "geheim")

    def tearDown(self):
        self.env.stop()

    def _sitzungen(self, liste):
        return mock.patch("kern.dienste.jellyfin._anfrage", mock.AsyncMock(return_value=Antwort(daten=liste)))


class LaeuftGerade(Basis):
    def test_folge(self):
        with self._sitzungen([_sitzung(FOLGE)]):
            s = asyncio.run(jellyfin.laeuft_gerade())
        self.assertEqual((s["titel"], s["untertitel"]), ("Beispielserie", "S1 E3 · Der Anfang"))
        self.assertEqual((s["position_s"], s["dauer_s"], s["laeuft"]), (60, 2700, True))
        self.assertEqual(s["bild_id"], "a" * 32)

    def test_film_und_lied(self):
        with self._sitzungen([_sitzung(FILM)]):
            self.assertEqual(asyncio.run(jellyfin.laeuft_gerade())["untertitel"], "2019")
        with self._sitzungen([_sitzung(LIED)]):
            s = asyncio.run(jellyfin.laeuft_gerade())
        self.assertEqual((s["titel"], s["untertitel"], s["bild_id"]), ("So What", "Miles Davis", "d" * 32))

    def test_nichts_laeuft(self):
        with self._sitzungen([_sitzung(None), _sitzung(None, sid="s2")]):
            self.assertIsNone(asyncio.run(jellyfin.laeuft_gerade()))

    def test_bei_mehreren_die_zuletzt_aktive(self):
        alt = _sitzung(FILM, wann="2026-09-25T10:00:00Z", sid="alt")
        neu = _sitzung(FOLGE, pausiert=True, wann="2026-09-25T12:00:00Z", sid="neu")
        with self._sitzungen([alt, neu]):
            s = asyncio.run(jellyfin.laeuft_gerade())
        self.assertEqual((s["sitzung"], s["laeuft"]), ("neu", False))

    def test_ohne_fernsteuerung_nicht_steuerbar(self):
        with self._sitzungen([_sitzung(FOLGE, fern=False)]):
            self.assertFalse(asyncio.run(jellyfin.laeuft_gerade())["steuerbar"])

    def test_fehler_der_gegenstelle(self):
        with mock.patch("kern.dienste.jellyfin._anfrage", mock.AsyncMock(return_value=Antwort(status=401))):
            with self.assertRaises(jellyfin.JellyfinFehler):
                asyncio.run(jellyfin.laeuft_gerade())

    def test_ohne_zugang(self):
        konnektoren.zugang_loeschen("jellyfin")
        with self.assertRaises(jellyfin.JellyfinFehler):
            asyncio.run(jellyfin.laeuft_gerade())


class Befehle(Basis):
    def _befehl(self, name, sitzung):
        ruf = mock.AsyncMock(side_effect=[Antwort(daten=[sitzung]), Antwort(status=204)])
        with mock.patch("kern.dienste.jellyfin._anfrage", ruf):
            asyncio.run(jellyfin.befehl(name))
        return ruf.await_args_list

    def test_pause_und_weiter_sind_playpause_denn_der_web_player_ignoriert_pause_und_unpause(self):
        # Am 25.09.2026 gemessen: Jellyfin Web quittiert Playing/Pause mit 204 und macht nichts, PlayPause geht.
        for name, pausiert in (("pause", False), ("weiter", True)):
            with self.subTest(name):
                aufrufe = self._befehl(name, _sitzung(FOLGE, pausiert=pausiert, sid="s9"))
                self.assertEqual(aufrufe[1].args[:2], ("POST", "/Sessions/s9/Playing/PlayPause"))

    def test_pause_bei_schon_pausiert_und_weiter_bei_laufend_tun_nichts(self):
        # Sonst würde der Umschalter das Gegenteil von dem tun, was Eike auf der Karte sieht.
        for name, pausiert in (("pause", True), ("weiter", False)):
            with self.subTest(name):
                aufrufe = self._befehl_ohne_post(name, _sitzung(FOLGE, pausiert=pausiert))
                self.assertEqual(len(aufrufe), 1)          # nur das Lesen der Sitzung

    def _befehl_ohne_post(self, name, sitzung):
        ruf = mock.AsyncMock(side_effect=[Antwort(daten=[sitzung])])
        with mock.patch("kern.dienste.jellyfin._anfrage", ruf):
            asyncio.run(jellyfin.befehl(name))
        return ruf.await_args_list

    def test_weitere_namen(self):
        for name, pfad in (("naechster", "NextTrack"), ("vorheriger", "PreviousTrack")):
            with self.subTest(name):
                aufrufe = self._befehl(name, _sitzung(FOLGE, sid="s9"))
                self.assertEqual(aufrufe[1].args[:2], ("POST", f"/Sessions/s9/Playing/{pfad}"))

    def test_unbekannter_befehl(self):
        with self.assertRaises(ValueError):
            asyncio.run(jellyfin.befehl("lauter"))

    def test_ohne_sitzung_ein_fehler(self):
        with self._sitzungen([]), self.assertRaises(jellyfin.JellyfinFehler):
            asyncio.run(jellyfin.befehl("pause"))

    def test_ohne_fernsteuerung_ein_fehler(self):
        with self._sitzungen([_sitzung(FOLGE, fern=False)]), self.assertRaises(jellyfin.JellyfinFehler):
            asyncio.run(jellyfin.befehl("pause"))


class Bild(Basis):
    def test_bild(self):
        ruf = mock.AsyncMock(return_value=Antwort(inhalt=b"\x89PNG", typ="image/png"))
        with mock.patch("kern.dienste.jellyfin._anfrage", ruf):
            daten, typ = asyncio.run(jellyfin.bild("a" * 32))
        self.assertEqual((daten, typ), (b"\x89PNG", "image/png"))
        self.assertIn("/Items/" + "a" * 32 + "/Images/Primary", ruf.await_args.args[1])

    def test_kein_bild_wird_nicht_durchgereicht(self):
        with mock.patch("kern.dienste.jellyfin._anfrage",
                        mock.AsyncMock(return_value=Antwort(inhalt=b"<html>", typ="text/html"))):
            with self.assertRaises(jellyfin.JellyfinFehler):
                asyncio.run(jellyfin.bild("a" * 32))

    def test_zu_grosses_bild(self):
        with mock.patch("kern.dienste.jellyfin._anfrage",
                        mock.AsyncMock(return_value=Antwort(inhalt=b"x" * (jellyfin.BILD_MAX + 1), typ="image/png"))):
            with self.assertRaises(jellyfin.JellyfinFehler):
                asyncio.run(jellyfin.bild("a" * 32))

    def test_schlechte_kennung(self):
        for boese in ("../x", "a" * 31, "G" * 32, "a" * 32 + "/../x"):
            with self.subTest(boese), self.assertRaises(ValueError):
                asyncio.run(jellyfin.bild(boese))


if __name__ == "__main__":
    unittest.main()
