"""Spotify: Token holen und erneuern, laufender Titel, Freigabe — ohne Netz."""

import asyncio
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import spotify
from kern.main import app
from kern.werkzeuge import katalog, musik

LIED = {"is_playing": True, "progress_ms": 83000, "device": {"name": "Handy"},
        "shuffle_state": False, "repeat_state": "off",
        "context": {"type": "playlist", "href": "https://api.spotify.com/v1/playlists/abc",
                    "uri": "spotify:playlist:abc"},
        "item": {"type": "track", "name": "Bohemian Rhapsody", "duration_ms": 354000,
                 "artists": [{"name": "Queen"}], "album": {"name": "A Night at the Opera"}}}


class Mit_Zugang(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner,
                                                "FRIDAY_URL": "https://hphome.example.ts.net"})
        self.env.start()
        konnektoren.setzen("spotify", True)
        konnektoren.zugang_setzen("spotify", "kennung123", "geheim456")
        spotify._staat = ""

    def tearDown(self):
        self.env.stop()

    def _token(self, **werte):
        daten = {"refresh_token": "dauerhaft", "access_token": "kurz", "gilt_bis": time.time() + 600}
        daten.update(werte)
        spotify._token_schreiben(daten)


class Anmeldung(Mit_Zugang):
    def test_adresse_enthaelt_rueckweg_und_nur_leserechte(self):
        from urllib.parse import parse_qs, urlsplit
        url = spotify.anmelde_url()
        self.assertTrue(url.startswith("https://accounts.spotify.com/authorize?"))
        # Die Rückadresse steht kodiert im Query-String — also entschlüsselt prüfen.
        p = parse_qs(urlsplit(url).query)
        self.assertEqual(p["redirect_uri"][0], "https://hphome.example.ts.net/api/spotify/rueck")
        self.assertEqual(p["client_id"][0], "kennung123")
        rechte = set(p["scope"][0].split())
        self.assertIn("user-read-currently-playing", rechte)
        self.assertIn("user-read-playback-state", rechte)
        # Eike wollte am 14.09.2026 ausdrücklich ALLE Rechte, um nie neu verbinden
        # zu müssen — auch schreibende. Die Grenze hält jetzt `KeineSchreibwerkzeuge`.
        self.assertIn("playlist-read-private", rechte)
        self.assertIn("user-modify-playback-state", rechte)

    def test_fremder_ruecklaeufer_wird_abgewiesen(self):
        spotify.anmelde_url()
        with self.assertRaises(spotify.SpotifyFehler):
            asyncio.run(spotify.code_einloesen("code", "falscher-staat"))

    def test_code_wird_zu_dauerhaftem_zugang(self):
        url = spotify.anmelde_url()
        staat = url.split("state=")[1].split("&")[0]
        antwort = httpx.Response(200, json={"refresh_token": "dauerhaft", "access_token": "kurz",
                                            "expires_in": 3600}, request=httpx.Request("POST", "https://x/"))
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(return_value=antwort)):
            asyncio.run(spotify.code_einloesen("code", staat))
        self.assertTrue(spotify.verbunden())
        datei = Path(self.ordner) / "spotify_token.json"
        self.assertEqual(stat.S_IMODE(datei.stat().st_mode), 0o600)
        self.assertNotIn("geheim456", datei.read_text())      # das Secret liegt nicht im Token


class Abspielen(Mit_Zugang):
    def _mit(self, antwort):
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=antwort))

    def test_laufender_titel(self):
        self._token()
        with self._mit(httpx.Response(200, json=LIED, request=httpx.Request("GET", "https://x/"))):
            text = asyncio.run(musik.musik())
        self.assertIn("Läuft gerade: Bohemian Rhapsody von Queen", text)
        self.assertIn("Album A Night at the Opera", text)
        self.assertIn("1:23 von 5:54", text)
        self.assertIn("auf Handy", text)

    def test_pausiert(self):
        self._token()
        pausiert = {**LIED, "is_playing": False}
        with self._mit(httpx.Response(200, json=pausiert, request=httpx.Request("GET", "https://x/"))):
            self.assertIn("Pausiert:", asyncio.run(musik.musik()))

    def test_nichts_laeuft_ist_kein_fehler(self):
        self._token()
        with self._mit(httpx.Response(204, request=httpx.Request("GET", "https://x/"))):
            self.assertIn("läuft gerade nichts", asyncio.run(musik.musik()))

    def test_ohne_verbindung_sagt_sie_was_zu_tun_ist(self):
        self.assertIn("noch nicht mit Spotify verbunden", asyncio.run(musik.musik()))

    def test_zurueckgezogener_zugang(self):
        self._token()
        with self._mit(httpx.Response(401, json={}, request=httpx.Request("GET", "https://x/"))):
            self.assertIn("neu verbinden", asyncio.run(musik.musik()))

    def test_abgelaufener_token_wird_erneuert(self):
        self._token(gilt_bis=time.time() - 10)
        neu = httpx.Response(200, json={"access_token": "frisch", "expires_in": 3600},
                             request=httpx.Request("POST", "https://x/"))
        with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(return_value=neu)) as p, \
             self._mit(httpx.Response(200, json=LIED, request=httpx.Request("GET", "https://x/"))):
            asyncio.run(musik.musik())
        p.assert_awaited_once()
        self.assertEqual(json.loads((Path(self.ordner) / "spotify_token.json").read_text())["access_token"],
                         "frisch")


class Freigabe(Mit_Zugang):
    def test_erst_mit_freigabe_und_zugang_sichtbar(self):
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("musik", namen)
        konnektoren.setzen("spotify", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("musik", namen)

    def test_seite_zeigt_rueckadresse_und_verbindungsstand(self):
        k = next(x for x in TestClient(app).get("/api/konnektoren").json() if x["id"] == "spotify")
        self.assertEqual(k["benutzerFeld"], "Client ID")
        self.assertEqual(k["passwortFeld"], "Client Secret")
        self.assertTrue(k["rueckadresse"].endswith("/api/spotify/rueck"))
        self.assertFalse(k["verbunden"])
        self.assertEqual(k["stand"], "noch nicht verbunden")

    def test_start_leitet_zu_spotify(self):
        r = TestClient(app, follow_redirects=False).get("/api/spotify/start")
        self.assertEqual(r.status_code, 307)
        self.assertTrue(r.headers["location"].startswith("https://accounts.spotify.com/authorize"))

    def test_abgelehnter_zugriff_wird_vermerkt(self):
        c = TestClient(app, follow_redirects=False)
        r = c.get("/api/spotify/rueck", params={"error": "access_denied"})
        self.assertEqual(r.status_code, 303)
        k = next(x for x in TestClient(app).get("/api/konnektoren").json() if x["id"] == "spotify")
        self.assertFalse(k["pruefung"]["ok"])
        self.assertIn("nicht erlaubt", k["pruefung"]["text"])


class Zusammenhang(Mit_Zugang):
    """Woraus läuft es? Der Name kostet einen zweiten Aufruf — der wird gemerkt."""

    def setUp(self):
        super().setUp()
        spotify._namen.clear()
        self._token()

    def _antworten(self, *weitere):
        spieler = httpx.Response(200, json=LIED, request=httpx.Request("GET", "https://x/"))
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(side_effect=[spieler, *weitere]))

    def test_playlistname_wird_nachgeschlagen(self):
        name = httpx.Response(200, json={"name": "Sommer 2026"}, request=httpx.Request("GET", "https://x/"))
        with self._antworten(name):
            text = asyncio.run(musik.musik())
        self.assertIn("Läuft aus der Playlist „Sommer 2026“", text)

    def test_ohne_namen_trotzdem_die_art(self):
        fehler = httpx.Response(403, json={}, request=httpx.Request("GET", "https://x/"))
        with self._antworten(fehler):
            text = asyncio.run(musik.musik())
        self.assertIn("Läuft aus der Playlist.", text)      # ehrlich ohne Namen

    def test_zufall_und_wiederholung(self):
        gemischt = {**LIED, "shuffle_state": True, "repeat_state": "context"}
        spieler = httpx.Response(200, json=gemischt, request=httpx.Request("GET", "https://x/"))
        name = httpx.Response(200, json={"name": "P"}, request=httpx.Request("GET", "https://x/"))
        with mock.patch("httpx.AsyncClient.get", mock.AsyncMock(side_effect=[spieler, name])):
            text = asyncio.run(musik.musik())
        self.assertIn("Zufallswiedergabe an", text)
        self.assertIn("Wiederholung: alles", text)


class Schreibgrenze(Mit_Zugang):
    """Steuern gibt es seit 14.09.2026 auf Eikes Wunsch — aber JEDES schreibende
    Werkzeug muss am Schreibrecht hängen. Dieser Test schlägt an, wenn eines
    ungeschützt dazukommt."""

    # Rein lesende Werkzeuge dürfen frei sein; alles, was etwas VERÄNDERT, nicht.
    LESEND = {"musik", "musik_geraete", "warteschlange_zeigen",
              "musik_playlists", "musik_playlist", "musik_zuletzt", "musik_geschmack",
              "musik_ton_pruefen"}

    def test_lesen_frei_steuern_nur_mit_schreibrecht(self):
        from kern.werkzeuge import musik as modul
        for name in self.LESEND:
            self.assertIn(name, modul.WERKZEUGE)
            self.assertNotIn(name, modul.NUR_MIT_SCHREIBRECHT)
        steuernd = set(modul.WERKZEUGE) - self.LESEND
        self.assertEqual(steuernd, modul.NUR_MIT_SCHREIBRECHT)         # alles Verändernde geschützt

    def test_ohne_schreibrecht_unsichtbar_und_abgelehnt(self):
        konnektoren.schreiben_setzen("spotify", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("musik", namen)
        self.assertNotIn("musik_pause", namen)
        r = asyncio.run(katalog.ausfuehren("musik_pause", {}))
        self.assertIn("Schreibrecht", r["fehler"])

    def test_mit_schreibrecht_sichtbar(self):
        konnektoren.schreiben_setzen("spotify", True)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertTrue({"musik_pause", "musik_weiter", "warteschlange"} <= namen)


class Steuern(Mit_Zugang):
    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("spotify", True)
        self._token()

    def _antwort(self, code=204, json_=None):
        return mock.patch("httpx.AsyncClient.request",
                          mock.AsyncMock(return_value=httpx.Response(
                              code, json=json_ if json_ is not None else {},
                              request=httpx.Request("PUT", "https://x/"))))

    def test_pause_und_weiter(self):
        with self._antwort() as r:
            self.assertEqual(asyncio.run(musik.musik_pause()), "Pausiert.")
            self.assertEqual(asyncio.run(musik.musik_weiter()), "Läuft weiter.")
        self.assertEqual([c.args[0] for c in r.call_args_list], ["PUT", "PUT"])

    def test_ohne_aktives_geraet_ehrliche_meldung(self):
        with self._antwort(404):
            self.assertIn("kein Gerät aktiv", asyncio.run(musik.musik_naechster()))

    def test_ohne_premium(self):
        with self._antwort(403):
            self.assertIn("Premium", asyncio.run(musik.musik_vorheriger()))

    def test_aus_fremdem_text_wird_nicht_gesteuert(self):
        with self._antwort() as r:
            self.assertIn("fremdem Text", asyncio.run(musik.musik_pause(fremde_inhalte=True)))
        r.assert_not_awaited()

    def test_warteschlange_ohne_riegel(self):
        """Der Riegel blockierte Eike selbst, sobald Friday vorher etwas nachgeschlagen
        hatte (3x am 14.09.2026). Einreihen ist umkehrbar — hier gilt er nicht mehr."""
        import inspect
        self.assertNotIn("fremde_inhalte", inspect.signature(musik.warteschlange).parameters)

    def test_warteschlange_sucht_und_haengt_an(self):
        treffer = {"uri": "spotify:track:1", "titel": "Wonderwall", "wer": "Oasis"}
        with mock.patch.object(spotify, "suchen", mock.AsyncMock(return_value=treffer)) as s, \
             mock.patch.object(spotify, "zur_warteschlange", mock.AsyncMock()) as q:
            text = asyncio.run(musik.warteschlange([{"titel": "Wonderwall", "kuenstler": "Oasis"}]))
        q.assert_awaited_once_with("spotify:track:1")
        self.assertEqual(s.await_args.args, ("Wonderwall", "Oasis"))   # getrennt gesucht
        self.assertIn("In die Warteschlange: Wonderwall von Oasis", text)

    def test_jetzt_spielt_den_titel_sofort_statt_ihn_hinten_anzuhaengen(self):
        """22.09.2026: „Spiel jetzt ein Lied von Max Raabe“ landete in der Warteschlange, Friday sprang dann mit
        „Nächster Titel“ — ein Umweg, der bei vollem Warteschlangenanfang den falschen Titel startet."""
        treffer = {"uri": "spotify:track:7", "titel": "Fahrrad fahr'n", "wer": "Max Raabe"}
        with mock.patch.object(spotify, "suchen", mock.AsyncMock(return_value=treffer)) as s, \
             mock.patch.object(spotify, "abspielen", mock.AsyncMock()) as a, \
             mock.patch.object(spotify, "zur_warteschlange", mock.AsyncMock()) as q:
            text = asyncio.run(musik.musik_jetzt("Fahrrad fahr'n", "Max Raabe"))
        a.assert_awaited_once_with("spotify:track:7")
        q.assert_not_awaited()
        self.assertEqual(s.await_args.args, ("Fahrrad fahr'n", "Max Raabe"))
        self.assertIn("Fahrrad fahr'n von Max Raabe", text)
        self.assertIn("läuft jetzt", text)

    def test_jetzt_holt_ein_inaktives_geraet_dazu(self):
        treffer = {"uri": "spotify:track:7", "titel": "T", "wer": "K"}
        kein = spotify.SpotifyFehler("Auf Spotify ist gerade kein Gerät aktiv.")
        with mock.patch.object(spotify, "suchen", mock.AsyncMock(return_value=treffer)), \
             mock.patch.object(spotify, "abspielen", mock.AsyncMock(side_effect=[kein, None])) as a, \
             mock.patch.object(spotify, "uebernehmen", mock.AsyncMock(return_value="Echo Eike")) as u:
            text = asyncio.run(musik.musik_jetzt("T", "K"))
        self.assertEqual(a.await_count, 2)
        u.assert_awaited_once()
        self.assertIn("Echo Eike", text)

    def test_jetzt_nichts_gefunden_startet_nichts(self):
        with mock.patch.object(spotify, "suchen", mock.AsyncMock(return_value=None)), \
             mock.patch.object(spotify, "abspielen", mock.AsyncMock()) as a:
            text = asyncio.run(musik.musik_jetzt("Gibtsnicht XY"))
        a.assert_not_awaited()
        self.assertIn("nicht bei Spotify gefunden", text)

    def test_jetzt_braucht_schreibrecht(self):
        self.assertIn("musik_jetzt", musik.NUR_MIT_SCHREIBRECHT)
        konnektoren.schreiben_setzen("spotify", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("musik_jetzt", namen)

    def test_playlist_spielt_die_ganze_liste_nicht_nur_ihre_ersten_titel(self):
        """25.09.2026: „Kannst du die Playlist Fahren abspielen?“ — Friday hängte nur fünf Titel an die Warteschlange und
        sagte, Spotify lasse Playlists nicht abspielen. Die API kann es (`context_uri`), es fehlte das Werkzeug."""
        liste = [{"id": "37i9dQZF1DXcBWIGoYBM5M", "name": "Fahren", "eigen": True, "besitzer": ""}]
        with mock.patch.object(spotify, "playlist_finden", mock.AsyncMock(return_value=liste)), \
             mock.patch.object(spotify, "playlist_abspielen", mock.AsyncMock()) as a:
            text = asyncio.run(musik.musik_playlist_abspielen("Fahren"))
        a.assert_awaited_once_with("37i9dQZF1DXcBWIGoYBM5M")
        self.assertIn("Fahren", text)
        self.assertIn("läuft", text)

    def test_playlist_nicht_gefunden_startet_nichts(self):
        with mock.patch.object(spotify, "playlist_finden", mock.AsyncMock(return_value=[])), \
             mock.patch.object(spotify, "playlist_abspielen", mock.AsyncMock()) as a:
            text = asyncio.run(musik.musik_playlist_abspielen("Gibtsnicht"))
        a.assert_not_awaited()
        self.assertIn("finde ich bei dir nicht", text)

    def test_playlist_holt_ein_inaktives_geraet_dazu_und_nennt_aehnliche(self):
        liste = [{"id": "aaaaaaaaaaaa", "name": "Fahren", "eigen": True, "besitzer": ""},
                 {"id": "bbbbbbbbbbbb", "name": "Fahren 2", "eigen": True, "besitzer": ""}]
        kein = spotify.SpotifyFehler("Auf Spotify ist gerade kein Gerät aktiv.")
        with mock.patch.object(spotify, "playlist_finden", mock.AsyncMock(return_value=liste)), \
             mock.patch.object(spotify, "playlist_abspielen", mock.AsyncMock(side_effect=[kein, None])) as a, \
             mock.patch.object(spotify, "uebernehmen", mock.AsyncMock(return_value="Echo Eike")):
            text = asyncio.run(musik.musik_playlist_abspielen("Fahren"))
        self.assertEqual(a.await_count, 2)
        self.assertIn("Echo Eike", text)
        self.assertIn("Fahren 2", text)                            # es passen auch …

    def test_playlist_befehl_nimmt_die_liste_als_kontext_und_prueft_die_kennung(self):
        with mock.patch.object(spotify, "_befehl", mock.AsyncMock()) as b:
            asyncio.run(spotify.playlist_abspielen("37i9dQZF1DXcBWIGoYBM5M"))
        b.assert_awaited_once_with("PUT", "/me/player/play", rumpf={"context_uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"})
        for boese in ("", "../x", "a b", "x" * 80):
            with self.subTest(boese), self.assertRaises(ValueError):
                asyncio.run(spotify.playlist_abspielen(boese))

    def test_playlist_abspielen_braucht_schreibrecht(self):
        self.assertIn("musik_playlist_abspielen", musik.NUR_MIT_SCHREIBRECHT)
        konnektoren.schreiben_setzen("spotify", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("musik_playlist_abspielen", namen)

    def test_derselbe_titel_landet_nicht_doppelt(self):
        # Am 14.09.2026 hing Friday "Ikkimel die Geile" zweimal an.
        treffer = {"uri": "spotify:track:1", "titel": "T", "wer": "K"}
        with mock.patch.object(spotify, "suchen", mock.AsyncMock(return_value=treffer)), \
             mock.patch.object(spotify, "zur_warteschlange", mock.AsyncMock()) as q:
            text = asyncio.run(musik.warteschlange([{"titel": "A"}, {"titel": "B"}]))
        self.assertEqual(q.await_count, 1)
        self.assertIn("Nicht gefunden: B", text)

    def test_nicht_gefundene_titel_werden_genannt(self):
        with mock.patch.object(spotify, "suchen", mock.AsyncMock(return_value=None)), \
             mock.patch.object(spotify, "zur_warteschlange", mock.AsyncMock()) as q:
            text = asyncio.run(musik.warteschlange([{"titel": "Gibtsnicht XY"}]))
        q.assert_not_awaited()
        self.assertIn("Nichts davon habe ich bei Spotify gefunden", text)

    def test_ohne_angabe_kommt_der_laufende_titel_nochmal(self):
        laeuft = {"titel": "So What", "wer": "P!nk", "album": "", "laeuft": True, "position_ms": 0,
                  "dauer_ms": 1000, "geraet": "", "art": "track", "quelle_art": "", "quelle_name": "",
                  "zufall": False, "wiederholung": "off"}
        treffer = {"uri": "spotify:track:9", "titel": "So What", "wer": "P!nk"}
        with mock.patch.object(spotify, "laeuft_gerade", mock.AsyncMock(return_value=laeuft)), \
             mock.patch.object(spotify, "suchen", mock.AsyncMock(return_value=treffer)) as s, \
             mock.patch.object(spotify, "zur_warteschlange", mock.AsyncMock()):
            text = asyncio.run(musik.warteschlange())
        self.assertEqual(s.await_args.args, ("So What", "P!nk"))
        self.assertIn("So What von P!nk", text)

    def test_hoechstens_zehn_auf_einmal(self):
        eigene = lambda t, k="": {"uri": f"spotify:track:{t}", "titel": t, "wer": "K"}
        with mock.patch.object(spotify, "suchen", mock.AsyncMock(side_effect=eigene)), \
             mock.patch.object(spotify, "zur_warteschlange", mock.AsyncMock()) as q:
            asyncio.run(musik.warteschlange([{"titel": f"Lied {i}"} for i in range(25)]))
        self.assertEqual(q.await_count, musik.MAX_WARTESCHLANGE)


class Geraetewahl(Mit_Zugang):
    """Eike hat viele Geräte, darunter Echo Dots. Ziel darf NUR werden, was er ankreuzt."""

    GERAETE = [{"id": "echo1", "name": "Echo Dot Küche", "type": "Speaker", "is_active": False},
               {"id": "pc1", "name": "PC_VON_EIKE", "type": "Computer", "is_active": False}]

    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("spotify", True)
        self._token()

    def _liste(self):
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
            200, json={"devices": self.GERAETE}, request=httpx.Request("GET", "https://x/"))))

    def test_ohne_freigabe_wird_nichts_gestartet(self):
        with self._liste():
            with self.assertRaises(spotify.SpotifyFehler) as f:
                asyncio.run(spotify.uebernehmen())
        self.assertIn("noch keines freigegeben", str(f.exception))

    def test_nur_das_freigegebene_wird_ziel(self):
        spotify.erlaubte_setzen([{"id": "pc1", "name": "PC_VON_EIKE"}])
        with self._liste(), mock.patch.object(spotify, "_befehl", mock.AsyncMock()) as b:
            name = asyncio.run(spotify.uebernehmen())
        self.assertEqual(name, "PC_VON_EIKE")
        self.assertEqual(b.await_args.kwargs["rumpf"]["device_ids"], ["pc1"])   # nie der Echo Dot

    def test_echo_bleibt_aussen_vor_auch_wenn_es_das_einzige_ist(self):
        spotify.erlaubte_setzen([{"id": "pc1", "name": "PC_VON_EIKE"}])
        nur_echo = mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
            200, json={"devices": [self.GERAETE[0]]}, request=httpx.Request("GET", "https://x/"))))
        with nur_echo:
            with self.assertRaises(spotify.SpotifyFehler) as f:
                asyncio.run(spotify.uebernehmen())
        self.assertIn("erreichbar", str(f.exception))

    def test_echo_bekommt_einen_zweiten_versuch(self):
        """Gespräche 139/152/223: der erste Umschaltversuch auf den Echo ergibt 404, der zweite klappt."""
        spotify.erlaubte_setzen([{"id": "echo1", "name": "Echo Dot Küche"}])
        antworten = [spotify.SpotifyFehler("x"), None]

        async def befehl(*a, wenn_404="", **k):
            if antworten.pop(0) is not None:
                raise spotify.SpotifyFehler(wenn_404)
        with self._liste(), mock.patch.object(spotify, "_befehl", side_effect=befehl) as b, \
                mock.patch.object(spotify, "ECHO_ZWEITER_VERSUCH_S", 0):
            self.assertEqual(asyncio.run(spotify.auf_geraet("Echo")), "Echo Dot Küche")
        self.assertEqual(b.call_count, 2)

    def test_nach_zwei_absagen_ist_schluss(self):
        spotify.erlaubte_setzen([{"id": "echo1", "name": "Echo Dot Küche"}])

        async def befehl(*a, wenn_404="", **k):
            raise spotify.SpotifyFehler(wenn_404)
        with self._liste(), mock.patch.object(spotify, "_befehl", side_effect=befehl) as b, \
                mock.patch.object(spotify, "ECHO_ZWEITER_VERSUCH_S", 0):
            with self.assertRaises(spotify.SpotifyFehler) as f:
                asyncio.run(spotify.auf_geraet("Echo"))
        self.assertEqual(b.call_count, 2)
        self.assertIn("zweiten Versuch", str(f.exception))

    def test_fortsetzen_schaltet_um_und_sagt_es(self):
        spotify.erlaubte_setzen([{"id": "pc1", "name": "PC_VON_EIKE"}])
        with mock.patch.object(spotify, "weiter", mock.AsyncMock(return_value="PC_VON_EIKE")):
            self.assertEqual(asyncio.run(musik.musik_weiter()), "Läuft weiter auf PC_VON_EIKE.")

    def test_werkzeug_zeigt_freigabe_je_geraet(self):
        spotify.erlaubte_setzen([{"id": "pc1", "name": "PC_VON_EIKE"}])
        with self._liste():
            text = asyncio.run(musik.musik_geraete())
        self.assertIn("Echo Dot Küche (Speaker) — nicht freigegeben", text)
        self.assertIn("PC_VON_EIKE (Computer)", text)
        self.assertNotIn("PC_VON_EIKE (Computer) — nicht freigegeben", text)


class WarteschlangeAnsehen(Mit_Zugang):
    """Friday behauptete am 13.09.2026, sie könne nicht in die Warteschlange sehen
    („Spotify lässt das nicht zu"), und übersprang deshalb fünfmal blind, statt
    nachzusehen. Spotify gibt die Liste sehr wohl her — es fehlte nur das Werkzeug."""

    ANTWORT = {"currently_playing": {"name": "Highway to Hell", "uri": "spotify:track:a",
                                     "artists": [{"name": "AC/DC"}]},
               "queue": [{"name": "Kashmir - Remaster", "uri": "spotify:track:b",
                          "artists": [{"name": "Led Zeppelin"}]},
                         {"name": "Blinding Lights", "uri": "spotify:track:c",
                          "artists": [{"name": "The Weeknd"}]}]}

    def setUp(self):
        super().setUp()
        self._token()

    def _antwort(self, daten):
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
            200, json=daten, request=httpx.Request("GET", "https://x/"))))

    def test_zeigt_laufenden_titel_und_die_naechsten(self):
        with self._antwort(self.ANTWORT):
            text = asyncio.run(musik.warteschlange_zeigen())
        self.assertIn("Highway to Hell", text)
        self.assertIn("1. Kashmir - Remaster von Led Zeppelin", text)
        self.assertIn("2. Blinding Lights von The Weeknd", text)

    def test_leere_warteschlange(self):
        with self._antwort({"currently_playing": None, "queue": []}):
            self.assertIn("leer", asyncio.run(musik.warteschlange_zeigen()))

    def test_rein_lesend_also_auch_ohne_schreibrecht_da(self):
        konnektoren.schreiben_setzen("spotify", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("warteschlange_zeigen", namen)


class Treffergenauigkeit(unittest.TestCase):
    """Spotify liefert bei unbekanntem Titel irgendeinen Song des Künstlers.
    Genau so landete "Ikkimel die Geile" statt "BENGALOS" in der Warteschlange."""

    def test_falscher_treffer_wird_verworfen(self):
        self.assertFalse(spotify._passt("BENGALOS", "Ikkimel die Geile"))
        self.assertFalse(spotify._passt("ZODIAK", "Ikkimel die Geile"))

    def test_richtiger_treffer_wird_genommen(self):
        self.assertTrue(spotify._passt("COUNTRY GIRL", "COUNTRY GIRL"))
        self.assertTrue(spotify._passt("Mr. Brightside", "Mr Brightside"))
        self.assertTrue(spotify._passt("So What", "So What"))
        self.assertTrue(spotify._passt("Wonderwall", "Wonderwall - Remastered"))


class KuenstlerPruefung(Mit_Zugang):
    """Der Wunsch „ein Lied von Dorfrocker" landete als „Stille Wasser von
    Schrotthagen" in der Warteschlange (gemessen 13.09.2026): Der Titel passte,
    geprüft wurde nur er — der Künstler nie."""

    TREFFER = {"tracks": {"items": [
        {"uri": "spotify:track:x", "name": "Stille Wasser", "artists": [{"name": "Schrotthagen"}]}]}}

    def setUp(self):
        super().setUp()
        self._token()

    def _suche(self):
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
            200, json=self.TREFFER, request=httpx.Request("GET", "https://x/"))))

    def test_falscher_kuenstler_wird_verworfen(self):
        with self._suche():
            self.assertIsNone(asyncio.run(spotify.suchen("Stille Wasser", "Dorfrocker")))

    def test_passender_kuenstler_wird_genommen(self):
        with self._suche():
            self.assertIsNotNone(asyncio.run(spotify.suchen("Stille Wasser", "Schrotthagen")))

    def test_ohne_kuenstlerwunsch_bleibt_es_beim_titel(self):
        with self._suche():
            self.assertEqual(asyncio.run(spotify.suchen("Stille Wasser"))["wer"], "Schrotthagen")


class GeraetWaehlen(Mit_Zugang):
    GERAETE = [{"id": "echo1", "name": "Echo Eike", "type": "Speaker", "is_active": False},
               {"id": "pc1", "name": "PC_VON_EIKE", "type": "Computer", "is_active": True}]

    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("spotify", True)
        self._token()

    def _liste(self):
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
            200, json={"devices": self.GERAETE}, request=httpx.Request("GET", "https://x/"))))

    def test_nicht_freigegebenes_geraet_wird_abgelehnt(self):
        spotify.erlaubte_setzen([{"id": "pc1", "name": "PC_VON_EIKE"}])
        with self._liste():
            text = asyncio.run(musik.musik_geraet_waehlen("Echo Eike"))
        self.assertIn("nicht freigegeben", text)

    def test_freigegebenes_geraet_wird_genommen(self):
        spotify.erlaubte_setzen([{"id": "echo1", "name": "Echo Eike"}])
        with self._liste(), mock.patch.object(spotify, "_befehl", mock.AsyncMock()) as b:
            text = asyncio.run(musik.musik_geraet_waehlen("echo"))
        self.assertEqual(b.await_args.kwargs["rumpf"]["device_ids"], ["echo1"])
        self.assertIn("läuft jetzt auf Echo Eike", text)

    def test_unbekanntes_geraet(self):
        with self._liste():
            self.assertIn("sehe ich nicht", asyncio.run(musik.musik_geraet_waehlen("Fernseher")))

    def test_beim_umschalten_meint_404_das_zielgeraet(self):
        """Am 13.09.2026 meldete Friday viermal „kein Gerät aktiv", während auf
        PC_VON_EIKE nachweislich Musik lief. Beim Umschalten heißt 404 etwas
        anderes: das ZIEL nimmt nicht an. Die Sammelmeldung schickte Eike in
        eine Schleife, die nie klappen konnte."""
        spotify.erlaubte_setzen([{"id": "echo1", "name": "Echo Eike"}])
        nicht_da = mock.patch("httpx.AsyncClient.request", mock.AsyncMock(
            return_value=httpx.Response(404, json={"error": {"status": 404, "message": "Not found."}},
                                        request=httpx.Request("PUT", "https://x/"))))
        with self._liste(), nicht_da:
            text = asyncio.run(musik.musik_geraet_waehlen("Echo Eike"))
        self.assertIn("Echo Eike", text)
        self.assertNotIn("kein Gerät aktiv", text)   # das war die irreführende Meldung


class WasEikeHoert(Mit_Zugang):
    """Playlists, Verlauf und Geschmack — Eikes Wunsch vom 19.09.2026, damit
    Friday Empfehlungen aus seinem echten Hörverhalten ableitet statt zu raten.

    Die Antworten hier sind an seinem Konto gemessen (20.09.2026), nicht aus der
    Doku abgeschrieben: die Titelzahl steht unter `items`, die Titel einer
    Playlist kommen von `/items` und heißen dort `item`.
    """

    ICH = {"id": "eike123", "display_name": "Eike"}
    PLAYLISTS = {"total": 4, "items": [
        {"id": "p1", "name": "Chill Abends", "uri": "spotify:playlist:p1",
         "items": {"href": "https://api.spotify.com/v1/playlists/p1/items", "total": 12},
         "owner": {"id": "eike123", "display_name": "Eike"}},
        {"id": "p2", "name": "Chill", "uri": "spotify:playlist:p2",
         "tracks": {"total": 7}, "owner": {"id": "eike123", "display_name": "Eike"}},
        # Gefolgte Liste einer Freundin: Spotify gibt ihren Inhalt nicht heraus.
        {"id": "p3", "name": "Drive ", "uri": "spotify:playlist:p3",
         "items": {"total": 63}, "owner": {"id": "maya77", "display_name": "Kontakt B"}},
        # Name aus lauter Emoji — nach _knapp bleibt nichts übrig.
        {"id": "p4", "name": "🪩💃🏼🕺🏻🔥", "uri": "spotify:playlist:p4",
         "items": {"total": 145}, "owner": {"id": "kontakt_e9", "display_name": "Kontakt E"}},
        # Eine Liste heißt wirklich nur „L" — ein „l" steckt in fast jedem Wunsch.
        {"id": "p5", "name": "L", "uri": "spotify:playlist:p5",
         "items": {"total": 18}, "owner": {"id": "eike123", "display_name": "Eike"}},
    ]}
    INHALT = {"total": 12, "items": [
        {"item": {"type": "track", "name": "Cool Kids", "uri": "spotify:track:1",
                  "artists": [{"name": "Echosmith"}]}},
        {"item": {"type": "track", "name": "Riptide", "uri": "spotify:track:2",
                  "artists": [{"name": "Vance Joy"}]}},
    ]}
    VERLAUF = {"items": [
        {"played_at": "2026-09-19T20:03:00Z", "track": {"name": "Yellow", "uri": "spotify:track:9",
                                                        "artists": [{"name": "Coldplay"}]}},
        {"played_at": "2026-09-19T19:58:00Z", "track": {"name": "Yellow", "uri": "spotify:track:9",
                                                        "artists": [{"name": "Coldplay"}]}},
        {"played_at": "2026-09-19T19:50:00Z", "track": {"name": "Ho Hey", "uri": "spotify:track:8",
                                                        "artists": [{"name": "The Lumineers"}]}},
    ]}
    TOP_KUENSTLER = {"items": [{"name": "AC/DC", "uri": "spotify:artist:a"},
                               {"name": "Echosmith", "uri": "spotify:artist:b"}]}
    TOP_TITEL = {"items": [{"name": "Highway to Hell", "uri": "spotify:track:h",
                            "artists": [{"name": "AC/DC"}]}]}

    def setUp(self):
        super().setUp()
        self._token()

    def _antworten(self, plan: dict, standard=None):
        plan = {"/me": self.ICH, **plan}
        """Antwortet je nach angefragtem Pfad — die Werkzeuge rufen mehrfach ab."""
        def hole(url, **kw):
            pfad = str(url).split("api.spotify.com/v1")[-1].split("?")[0]
            for teil, daten in plan.items():
                if pfad == teil:
                    if isinstance(daten, int):
                        return httpx.Response(daten, json={"error": {"status": daten}},
                                              request=httpx.Request("GET", str(url)))
                    return httpx.Response(200, json=daten, request=httpx.Request("GET", str(url)))
            if standard is None:
                raise AssertionError(f"Unerwarteter Aufruf: {url}")
            return httpx.Response(200, json=standard, request=httpx.Request("GET", str(url)))
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(side_effect=hole))

    def test_playlists_mit_titelzahl_aus_beiden_feldformen(self):
        # Spotify liefert die Zahl inzwischen unter `items`; die alte Form `tracks`
        # muss trotzdem gelesen werden, falls sie zurückkommt.
        with self._antworten({"/me/playlists": self.PLAYLISTS}):
            text = asyncio.run(musik.musik_playlists())
        self.assertIn("Chill Abends (12 Titel)", text)
        self.assertIn("Chill (7 Titel)", text)
        self.assertIn("Drive  (63 Titel) — von Kontakt B", text)      # fremd, also gekennzeichnet

    def test_playlist_titel_kommen_von_items_nicht_von_tracks(self):
        # /playlists/{id}/tracks antwortet mit 403 (gemessen 20.09.2026). Wer dort
        # abfragt, bekommt gar nichts — dieser Test wird dann rot.
        with self._antworten({"/me/playlists": self.PLAYLISTS,
                              "/playlists/p1/items": self.INHALT,
                              "/playlists/p1/tracks": 403}):
            text = asyncio.run(musik.musik_playlist("Chill Abends"))
        self.assertIn("Chill Abends", text)
        self.assertIn("1. Cool Kids von Echosmith", text)
        self.assertIn("2. Riptide von Vance Joy", text)
        self.assertIn("10 weitere", text)          # 12 gesamt, 2 genannt

    def test_genauer_name_schlaegt_teiltreffer(self):
        # "Chill" steckt auch in "Chill Abends". Nicht blind den ersten nehmen.
        with self._antworten({"/me/playlists": self.PLAYLISTS,
                              "/playlists/p2/items": {"total": 1, "items": [
                                  {"item": {"type": "track", "name": "Ho Hey", "uri": "spotify:track:8",
                                            "artists": [{"name": "The Lumineers"}]}}]}}):
            text = asyncio.run(musik.musik_playlist("Chill"))
        self.assertIn("„Chill“ (1 Titel)", text)
        self.assertIn("Es passen auch: Chill Abends", text)   # die andere wird benannt

    def test_unbekannte_playlist_wird_ehrlich_gemeldet(self):
        with self._antworten({"/me/playlists": self.PLAYLISTS}):
            text = asyncio.run(musik.musik_playlist("Schlager"))
        self.assertIn("finde ich bei dir nicht", text)

    def test_verlauf_ohne_dubletten(self):
        with self._antworten({"/me/player/recently-played": self.VERLAUF}):
            text = asyncio.run(musik.musik_zuletzt())
        self.assertIn("1. Yellow von Coldplay", text)
        self.assertIn("2. Ho Hey von The Lumineers", text)
        self.assertEqual(text.count("Yellow"), 1)             # zweimal gehört, einmal genannt

    def test_geschmack_nennt_kuenstler_und_titel(self):
        with self._antworten({"/me/top/artists": self.TOP_KUENSTLER,
                              "/me/top/titel": self.TOP_TITEL,
                              "/me/top/tracks": self.TOP_TITEL}):
            text = asyncio.run(musik.musik_geschmack("kurz"))
        self.assertIn("der letzten Wochen", text)
        self.assertIn("AC/DC, Echosmith", text)
        self.assertIn("Highway to Hell von AC/DC", text)

    def test_unsinniger_zeitraum_faellt_auf_mittel_zurueck(self):
        with self._antworten({"/me/top/artists": self.TOP_KUENSTLER,
                              "/me/top/tracks": self.TOP_TITEL}):
            text = asyncio.run(musik.musik_geschmack("gestern"))
        self.assertIn("der letzten Monate", text)


    def test_emoji_playlist_passt_nicht_auf_jeden_wunsch(self):
        """„Schlagerparty 1998" traf am 20.09.2026 die Listen „L" und „🪩💃🏼🕺🏻🔥":
        `_knapp` macht aus Emoji einen leeren Text, und der steckt in jedem Wunsch."""
        with self._antworten({"/me/playlists": self.PLAYLISTS}):
            text = asyncio.run(musik.musik_playlist("Schlagerparty 1998"))
        self.assertIn("finde ich bei dir nicht", text)

    def test_ein_buchstabe_ist_kein_treffer_aber_der_genaue_name_schon(self):
        """Eikes Liste „L": „Schlagerparty 1998" traf sie über das „l" in
        „schlagerparty" (20.09.2026). Gefragt danach muss sie trotzdem kommen."""
        self.assertFalse(spotify._name_passt("Schlagerparty 1998", "L"))
        with self._antworten({"/me/playlists": self.PLAYLISTS,
                              "/playlists/p5/items": {"total": 1, "items": [
                                  {"item": {"type": "track", "name": "Lied", "uri": "spotify:track:l",
                                            "artists": [{"name": "Wer"}]}}]}}):
            self.assertIn("1. Lied von Wer", asyncio.run(musik.musik_playlist("L")))

    def test_leerer_fundtext_passt_auf_nichts(self):
        self.assertFalse(spotify._passt("Schlagerparty 1998", "🪩"))
        self.assertFalse(spotify._passt("irgendwas", ""))

    def test_fremde_liste_wird_benannt_statt_leer_gemeldet(self):
        # Spotify antwortet auf fremde Listen mit 403. Ohne die Besitzprüfung käme
        # „Spotify gibt diese Liste nicht mehr heraus" — richtig, aber nichtssagend.
        with self._antworten({"/me/playlists": self.PLAYLISTS, "/playlists/p3/items": 403}):
            text = asyncio.run(musik.musik_playlist("Drive"))
        self.assertIn("gehört Kontakt B", text)
        self.assertIn("folgst ihr nur", text)

    def test_bei_gleichem_rang_zuerst_die_eigene(self):
        eigen = {"id": "p9", "name": "Laufen", "items": {"total": 5},
                 "owner": {"id": "eike123", "display_name": "Eike"}}
        fremd = {"id": "p8", "name": "Laufen", "items": {"total": 9},
                 "owner": {"id": "kontakt_e9", "display_name": "Kontakt E"}}
        with self._antworten({"/me/playlists": {"items": [fremd, eigen]},
                              "/playlists/p9/items": {"total": 1, "items": [
                                  {"item": {"type": "track", "name": "Run", "uri": "spotify:track:r",
                                            "artists": [{"name": "X"}]}}]}}):
            text = asyncio.run(musik.musik_playlist("Laufen"))
        self.assertIn("1. Run von X", text)                       # die eigene, nicht die von Kontakt E

    def test_rein_lesend_also_auch_ohne_schreibrecht_da(self):
        konnektoren.schreiben_setzen("spotify", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertTrue({"musik_playlists", "musik_playlist", "musik_zuletzt",
                         "musik_geschmack"} <= namen)

    def test_zurueckgezogener_zugang_wird_weitergesagt(self):
        with self._antworten({"/me/playlists": 401}):
            self.assertIn("neu verbinden", asyncio.run(musik.musik_playlists()))


class TonPruefen(Mit_Zugang):
    """Eike, 20.09.2026: „Wenn ich sage ich höre nichts, soll Friday nicht einfach
    lauter machen — steht es schon auf 70 %, ist es ja ein anderes Problem."
    Deshalb urteilt das WERKZEUG, nicht das Modell."""

    def setUp(self):
        super().setUp()
        self._token()

    def _laeuft(self, lautstaerke, laeuft=True, regelbar=True):
        return {"is_playing": laeuft, "progress_ms": 1000, "shuffle_state": False,
                "repeat_state": "off", "context": None,
                "device": {"name": "Echo Eike", "type": "Speaker",
                           "volume_percent": lautstaerke, "supports_volume": regelbar},
                "item": {"type": "track", "name": "Cool Kids", "duration_ms": 200000,
                         "artists": [{"name": "Echosmith"}], "album": {"name": "Talking Dreams"}}}

    def _antwort(self, daten, code=200):
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
            code, json=daten, request=httpx.Request("GET", "https://x/"))))

    def test_leise_wird_als_ursache_benannt(self):
        # 9 % — genau der Stand von „Echo Eike" am 20.09.2026.
        with self._antwort(self._laeuft(9)):
            text = asyncio.run(musik.musik_ton_pruefen())
        self.assertIn("9 %", text)
        self.assertIn("Ursache", text)
        self.assertIn("lauter", text)

    def test_laut_genug_wird_nicht_aufgedreht_sondern_erklaert(self):
        with self._antwort(self._laeuft(70)):
            text = asyncio.run(musik.musik_ton_pruefen())
        self.assertIn("70 %", text)
        self.assertIn("liegt es", text)
        self.assertIn("Echo Eike", text)               # nennt das Gerät als Verdacht
        self.assertNotIn("Ursache", text)              # gerade NICHT die Lautstärke

    def test_pausiert_ist_keine_frage_der_lautstaerke(self):
        with self._antwort(self._laeuft(70, laeuft=False)):
            text = asyncio.run(musik.musik_ton_pruefen())
        self.assertIn("pausiert", text)
        self.assertIn("keine Frage der Lautstärke", text)

    def test_nicht_regelbares_geraet_wird_ehrlich_gemeldet(self):
        # Eikes „Badezimmer" meldet supports_volume: false (gemessen 20.09.2026).
        with self._antwort(self._laeuft(16, regelbar=False)):
            text = asyncio.run(musik.musik_ton_pruefen())
        self.assertIn("weder sagen noch stellen", text)

    def test_nichts_laeuft_ist_kein_lautstaerkeproblem(self):
        # /me/player antwortet mit 204, die Geräteliste kommt normal — so macht es
        # Spotify wirklich. Ein 204 auf ALLES wäre ein Testfehler, kein Befund.
        def hole(url, **kw):
            if "/me/player/devices" in str(url):
                return httpx.Response(200, json={"devices": []},
                                      request=httpx.Request("GET", str(url)))
            return httpx.Response(204, request=httpx.Request("GET", str(url)))
        with mock.patch("httpx.AsyncClient.get", mock.AsyncMock(side_effect=hole)):
            text = asyncio.run(musik.musik_ton_pruefen())
        self.assertIn("läuft gerade nichts", text)
        self.assertIn("keine Frage der Lautstärke", text)

    def test_nichts_laeuft_aber_geraet_noch_aktiv(self):
        def hole(url, **kw):
            if "/me/player/devices" in str(url):
                return httpx.Response(200, json={"devices": [
                    {"id": "g1", "name": "PC_VON_EIKE", "type": "Computer", "is_active": True,
                     "volume_percent": 60, "supports_volume": True}]},
                    request=httpx.Request("GET", str(url)))
            return httpx.Response(204, request=httpx.Request("GET", str(url)))
        with mock.patch("httpx.AsyncClient.get", mock.AsyncMock(side_effect=hole)):
            text = asyncio.run(musik.musik_ton_pruefen())
        self.assertIn("PC_VON_EIKE", text)
        self.assertIn("nichts gestartet", text)

    def test_pruefen_ist_lesend_stellen_nicht(self):
        konnektoren.schreiben_setzen("spotify", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("musik_ton_pruefen", namen)
        self.assertNotIn("musik_lautstaerke", namen)


class LautstaerkeStellen(Mit_Zugang):
    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("spotify", True)
        self._token()

    def _lage(self, lautstaerke=9, regelbar=True):
        return mock.patch("httpx.AsyncClient.get", mock.AsyncMock(return_value=httpx.Response(
            200, json={"is_playing": True, "progress_ms": 0, "context": None,
                       "shuffle_state": False, "repeat_state": "off",
                       "device": {"name": "Echo Eike", "type": "Speaker",
                                  "volume_percent": lautstaerke, "supports_volume": regelbar},
                       "item": {"type": "track", "name": "X", "duration_ms": 1,
                                "artists": [], "album": {"name": ""}}},
            request=httpx.Request("GET", "https://x/"))))

    def _stellen(self, code=204):
        return mock.patch("httpx.AsyncClient.request", mock.AsyncMock(return_value=httpx.Response(
            code, json={}, request=httpx.Request("PUT", "https://x/"))))

    def test_wert_wird_gestellt_und_vorher_nachher_genannt(self):
        with self._lage(9), self._stellen():
            text = asyncio.run(musik.musik_lautstaerke(40))
        self.assertIn("von 9 % auf 40 %", text)
        self.assertIn("Echo Eike", text)

    def test_werte_ausserhalb_werden_begrenzt(self):
        with self._lage(50), self._stellen():
            self.assertIn("auf 100 %", asyncio.run(musik.musik_lautstaerke(250)))
        with self._lage(50), self._stellen():
            self.assertIn("auf 0 %", asyncio.run(musik.musik_lautstaerke(-5)))

    def test_nicht_regelbares_geraet_wird_gar_nicht_erst_gestellt(self):
        with self._lage(16, regelbar=False), self._stellen() as ruf:
            text = asyncio.run(musik.musik_lautstaerke(60))
        self.assertIn("nicht von außen stellen", text)
        ruf.assert_not_called()

    def test_aus_fremdem_text_wird_die_lautstaerke_nicht_gestellt(self):
        self.assertIn("fremdem Text", asyncio.run(musik.musik_lautstaerke(80, fremde_inhalte=True)))

    def test_ohne_schreibrecht_abgelehnt(self):
        konnektoren.schreiben_setzen("spotify", False)
        r = asyncio.run(katalog.ausfuehren("musik_lautstaerke", {"prozent": 80}))
        self.assertIn("Schreibrecht", r["fehler"])


class Cover(unittest.TestCase):
    def test_nimmt_das_bild_mit_der_breite_naechst_300(self):
        stueck = {"album": {"images": [{"url": "https://i.scdn.co/a640", "width": 640},
                                        {"url": "https://i.scdn.co/a300", "width": 300},
                                        {"url": "https://i.scdn.co/a64", "width": 64}]}}
        self.assertEqual(spotify._cover(stueck), "https://i.scdn.co/a300")

    def test_podcast_nimmt_das_bild_der_folge_oder_der_show(self):
        folge = {"type": "episode", "images": [{"url": "https://i.scdn.co/e", "width": 300}]}
        self.assertEqual(spotify._cover(folge), "https://i.scdn.co/e")
        show = {"type": "episode", "show": {"images": [{"url": "https://i.scdn.co/s", "width": 300}]}}
        self.assertEqual(spotify._cover(show), "https://i.scdn.co/s")

    def test_ohne_bild_oder_ohne_https_leer(self):
        self.assertEqual(spotify._cover({}), "")
        self.assertEqual(spotify._cover({"album": {"images": [{"url": "http://x/a", "width": 300}]}}), "")
        self.assertEqual(spotify._cover({"album": {"images": [{"url": "javascript:1", "width": 300}]}}), "")


class Uris(unittest.TestCase):
    """Die Übersicht öffnet Titel, Album und Künstler direkt in der Spotify-App (spotify:-Adressen)."""

    def test_titel_album_kuenstler(self):
        stueck = {"uri": "spotify:track:T1", "album": {"uri": "spotify:album:A1"},
                  "artists": [{"uri": "spotify:artist:R1"}, {"uri": "spotify:artist:R2"}]}
        self.assertEqual(spotify._uris(stueck),
                         {"uri": "spotify:track:T1", "album_uri": "spotify:album:A1", "artist_uri": "spotify:artist:R1"})

    def test_podcastfolge_nimmt_die_show_als_album(self):
        folge = {"type": "episode", "uri": "spotify:episode:E1", "show": {"uri": "spotify:show:S1"}}
        self.assertEqual(spotify._uris(folge),
                         {"uri": "spotify:episode:E1", "album_uri": "spotify:show:S1", "artist_uri": ""})

    def test_fehlendes_bleibt_leer(self):
        self.assertEqual(spotify._uris({}), {"uri": "", "album_uri": "", "artist_uri": ""})
