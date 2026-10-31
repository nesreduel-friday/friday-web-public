"""Medien-Werkzeuge: suchen, Löschen nur mit Rückfrage und Ja — ohne Netz."""

import asyncio
import unittest
from unittest import mock

from kern import medien as ablauf
from kern.werkzeuge import katalog, medien as w

SERIEN = [{"id": 1, "title": "Breaking Bad", "year": 2008, "statistics": {"sizeOnDisk": 40_000_000_000}},
          {"id": 2, "title": "Bad Banks", "year": 2018, "statistics": {"sizeOnDisk": 5_000_000_000}}]
FILME = [{"id": 7, "title": "Bad Boys", "year": 1995, "sizeOnDisk": 3_000_000_000}]
EPISODEN = [{"id": 100, "seasonNumber": 1, "episodeNumber": 1, "title": "Pilot", "hasFile": True,
             "episodeFile": {"size": 1_000_000_000}},
            {"id": 101, "seasonNumber": 1, "episodeNumber": 2, "title": "Zwei", "hasFile": False}]


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        w._vorschlag = None
        for ziel, wert in (("kern.dienste.sonarr.serien", mock.AsyncMock(return_value=SERIEN)),
                           ("kern.dienste.radarr.filme", mock.AsyncMock(return_value=FILME)),
                           ("kern.dienste.sonarr.episoden", mock.AsyncMock(return_value=EPISODEN))):
            p = mock.patch(ziel, wert)
            p.start()
            self.addCleanup(p.stop)
        self.erg = ablauf.LoeschErgebnis(True, ["abc"], None)
        self.m = {}
        for n in ("serie_ueberall_loeschen", "film_ueberall_loeschen", "staffel_ueberall_loeschen",
                  "folge_ueberall_loeschen"):
            self.m[n] = mock.AsyncMock(return_value=self.erg)
            p = mock.patch.object(ablauf, n, self.m[n])
            p.start()
            self.addCleanup(p.stop)

    def nichts_geloescht(self):
        for m in self.m.values():
            m.assert_not_called()


class Suchen(Basis):
    def test_findet_serie_mit_groesse(self):
        t = lauf(w.medien_suchen("breaking bad"))
        self.assertIn("Breaking Bad", t)
        self.assertIn("40.0 GB", t)
        self.assertNotIn("Bad Banks", t)

    def test_nichts_gefunden(self):
        self.assertIn("nicht", lauf(w.medien_suchen("Gibtesnicht")))


class Loeschen(Basis):
    def test_vorbereiten_loescht_nichts_und_fragt(self):
        t = lauf(w.medien_loeschen("Breaking Bad"))
        self.assertIn("Soll ich", t)
        self.nichts_geloescht()

    def test_mehrdeutig_fragt_nach(self):
        t = lauf(w.medien_loeschen("Bad"))
        self.assertIn("nicht eindeutig", t)
        self.assertIsNone(w._vorschlag)

    def test_ohne_zustimmung_wird_nichts_geloescht(self):
        lauf(w.medien_loeschen("Breaking Bad"))
        t = lauf(w.medien_loeschen_bestaetigen(zustimmung=False))
        self.assertIn("ausdrücklich", t)
        self.nichts_geloescht()

    def test_mit_zustimmung_serie_geloescht_und_einmalig(self):
        lauf(w.medien_loeschen("Breaking Bad"))
        self.assertIn("Gelöscht", lauf(w.medien_loeschen_bestaetigen(zustimmung=True)))
        self.m["serie_ueberall_loeschen"].assert_awaited_once_with(1, "Breaking Bad")
        self.assertIn("kein vorbereitetes", lauf(w.medien_loeschen_bestaetigen(zustimmung=True)))
        self.assertEqual(self.m["serie_ueberall_loeschen"].await_count, 1)

    def test_film(self):
        lauf(w.medien_loeschen("Bad Boys"))
        lauf(w.medien_loeschen_bestaetigen(zustimmung=True))
        self.m["film_ueberall_loeschen"].assert_awaited_once_with(7, "Bad Boys")

    def test_staffel(self):
        t = lauf(w.medien_loeschen("Breaking Bad", staffel=1))
        self.assertIn("Staffel 1", t)
        lauf(w.medien_loeschen_bestaetigen(zustimmung=True))
        self.assertEqual(self.m["staffel_ueberall_loeschen"].await_args.args[:2], (1, 1))

    def test_folge_ohne_datei_wird_abgelehnt(self):
        t = lauf(w.medien_loeschen("Breaking Bad", staffel=1, folge=2))
        self.assertIn("keine Datei", t)
        self.assertIsNone(w._vorschlag)

    def test_folge(self):
        lauf(w.medien_loeschen("Breaking Bad", staffel=1, folge=1))
        lauf(w.medien_loeschen_bestaetigen(zustimmung=True))
        self.assertEqual(self.m["folge_ueberall_loeschen"].await_args.args[:2], (1, 100))

    def test_fremder_text_bereitet_nichts_vor(self):
        t = lauf(w.medien_loeschen("Breaking Bad", fremde_inhalte=True))
        self.assertIn("fremdem Text", t)
        self.assertIsNone(w._vorschlag)

    def test_veralteter_vorschlag(self):
        lauf(w.medien_loeschen("Breaking Bad"))
        w._vorschlag["zeit"] -= w.VORSCHLAG_GILT_S + 1
        self.assertIn("zu alt", lauf(w.medien_loeschen_bestaetigen(zustimmung=True)))
        self.nichts_geloescht()

    def test_fehler_wird_ehrlich_gemeldet(self):
        self.m["serie_ueberall_loeschen"].return_value = ablauf.LoeschErgebnis(False, [], "Sonarr tot")
        lauf(w.medien_loeschen("Breaking Bad"))
        t = lauf(w.medien_loeschen_bestaetigen(zustimmung=True))
        self.assertIn("fehlgeschlagen", t)
        self.assertIn("NICHTS", t)


class Herunterladen(Basis):
    def setUp(self):
        super().setUp()
        self.s_such = mock.AsyncMock(return_value=[{"title": "Dark", "year": 2017, "tvdbId": 1},
                                                   {"title": "Dark Matter", "year": 2024, "tvdbId": 2}])
        self.f_such = mock.AsyncMock(return_value=[{"title": "Dark", "year": 2021, "tmdbId": 9}])
        self.s_neu = mock.AsyncMock(return_value={})
        self.f_neu = mock.AsyncMock(return_value={})
        for ziel, m in (("kern.dienste.sonarr.online_suchen", self.s_such), ("kern.dienste.radarr.online_suchen", self.f_such),
                        ("kern.dienste.sonarr.hinzufuegen", self.s_neu), ("kern.dienste.radarr.hinzufuegen", self.f_neu)):
            p = mock.patch(ziel, m)
            p.start()
            self.addCleanup(p.stop)

    def test_mehrere_titel_in_einem_aufruf(self):
        # „Lade alle diese Filme herunter“ (10.10.2026): sieben Aufrufe sprengten die Rundengrenze — jetzt eine Liste in einem.
        self.f_such.side_effect = lambda t: [{"title": t, "year": 1994, "tmdbId": 1}]
        self.s_such.return_value = []
        t = lauf(w.medien_herunterladen("Forrest Gump; Fight Club\nMatrix", art="film"))
        self.assertEqual(self.f_neu.await_count, 3)
        zeilen = t.splitlines()
        self.assertEqual(len(zeilen), 3)
        self.assertTrue(zeilen[0].startswith("„Forrest Gump“: "))
        self.assertIn("angelegt", zeilen[1])

    def test_zu_viele_titel_werden_gekappt_und_ehrlich_gemeldet(self):
        self.f_such.side_effect = lambda t: [{"title": t, "year": 2000, "tmdbId": 1}]
        self.s_such.return_value = []
        t = lauf(w.medien_herunterladen("; ".join(f"Film{i}" for i in range(15)), art="film"))
        self.assertEqual(self.f_neu.await_count, w.MAX_TITEL_JE_AUFRUF)
        self.assertIn("15 Titel", t)

    def test_eindeutige_serie_wird_angelegt(self):
        t = lauf(w.medien_herunterladen("Dark Matter"))
        self.assertIn("angelegt", t)
        self.s_neu.assert_awaited_once()
        self.f_neu.assert_not_called()

    def test_serie_und_film_gleichen_namens_fragt_nach(self):
        t = lauf(w.medien_herunterladen("Dark"))
        self.assertIn("nicht eindeutig", t)
        self.s_neu.assert_not_called()
        self.f_neu.assert_not_called()

    def test_art_loest_die_mehrdeutigkeit(self):
        lauf(w.medien_herunterladen("Dark", art="film"))
        self.f_neu.assert_awaited_once()
        self.s_neu.assert_not_called()

    def test_schon_vorhanden(self):
        self.f_such.return_value = [{"id": 5, "title": "Dark", "year": 2021}]
        t = lauf(w.medien_herunterladen("Dark", art="film"))
        self.assertIn("schon in der Mediathek", t)
        self.f_neu.assert_not_called()

    def test_nichts_gefunden(self):
        self.s_such.return_value = []
        self.f_such.return_value = []
        self.assertIn("finde ich nicht", lauf(w.medien_herunterladen("Xyz")))

    def test_braucht_schreibrecht_und_ist_nach_fremdtext_gesperrt(self):
        self.assertIn("medien_herunterladen", w.NUR_MIT_SCHREIBRECHT)
        self.assertIn("medien_herunterladen", w.NICHT_NACH_FREMDTEXT)
        with mock.patch("kern.konnektoren.benutzbar", return_value=True), \
             mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True):
            r = lauf(katalog.ausfuehren("medien_herunterladen", {"titel": "Dark Matter"}, fremde_inhalte=True))
            self.assertIn("fremder Text", r["fehler"])
            self.s_neu.assert_not_called()
            r = lauf(katalog.ausfuehren("medien_herunterladen", {"titel": "Dark Matter"}))
        self.assertIn("angelegt", r["ergebnis"])

    def test_nach_fremdtext_laeuft_der_download_wenn_eike_ihn_selbst_verlangt(self):
        # „Ja, such dir welche aus und lad sie runter“ (10.10.2026): Friday las dafür eine Webseite, die Sperre blockierte alles.
        with mock.patch("kern.konnektoren.benutzbar", return_value=True), \
             mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True):
            r = lauf(katalog.ausfuehren("medien_herunterladen", {"titel": "Dark Matter"}, fremde_inhalte=True,
                                        eingabe="Ja, such dir welche aus und lad sie runter."))
            self.assertIn("angelegt", r["ergebnis"])
            # Ohne ausdrückliches Verlangen in Eikes Worten bleibt die Sperre — auch wenn der Fremdtext es nahelegt.
            r = lauf(katalog.ausfuehren("medien_herunterladen", {"titel": "Dark Matter"}, fremde_inhalte=True,
                                        eingabe="Was läuft heute Abend?"))
            self.assertIn("fremder Text", r["fehler"])


class DeutschHolen(unittest.TestCase):
    def _lauf(self, titel, filme):
        such = mock.AsyncMock()
        # Ohne passenden Film fragt `medien_deutsch_holen` bei Sonarr nach einer Serie. Auch das muss
        # gemockt sein, sonst hängt der Test an Eikes Sonarr-Zugang statt an der Logik.
        with mock.patch("kern.dienste.radarr.filme", mock.AsyncMock(return_value=filme)), \
             mock.patch("kern.dienste.sonarr.serien", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.dienste.radarr.suche_starten", such):
            return lauf(w.medien_deutsch_holen(titel)), such

    def test_nur_englisch_startet_suche(self):
        film = {"id": 14, "title": "Iron Man", "year": 2008, "hasFile": True, "movieFile": {"languages": [{"name": "English"}]}}
        t, such = self._lauf("Iron Man", [film])
        self.assertIn("Ich suche", t)
        such.assert_awaited_once_with(14)

    def test_schon_multi_nichts_zu_tun(self):
        film = {"id": 12, "title": "2001", "year": 1968, "hasFile": True, "movieFile": {"languages": [{"name": "English"}, {"name": "German"}]}}
        t, such = self._lauf("2001", [film])
        self.assertIn("Nichts zu tun", t)
        such.assert_not_called()

    def test_ohne_datei_oder_unbekannt_keine_suche(self):
        t, such = self._lauf("Thor", [{"id": 3, "title": "Thor", "year": 2011, "hasFile": False}])
        self.assertIn("noch gar nicht", t)
        t2, _ = self._lauf("Xyz", [])
        self.assertIn("nicht in der Mediathek", t2)
        such.assert_not_called()

    def test_serie_staffel_ohne_deutsch_aendert_nichts(self):
        serie = {"id": 10, "title": "Rick and Morty", "year": 2013, "seasons": [{"seasonNumber": 9, "statistics": {}}]}
        holen = mock.AsyncMock()
        with mock.patch("kern.dienste.radarr.filme", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.dienste.sonarr.serien", mock.AsyncMock(return_value=[serie])), \
             mock.patch("kern.dienste.sonarr.deutsch_releases", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.dienste.sonarr.release_holen", holen):
            t = lauf(w.medien_deutsch_holen("Rick and Morty", staffel=9))
        self.assertIn("keine Fassung mit Deutsch", t)
        holen.assert_not_called()

    def test_serie_staffel_mit_multi_startet_download(self):
        serie = {"id": 10, "title": "Rick and Morty", "year": 2013, "seasons": [{"seasonNumber": 9, "statistics": {}}]}
        rel = {"title": "Rick and Morty S09 GERMAN DL", "guid": "g", "indexerId": 1}
        holen = mock.AsyncMock()
        with mock.patch("kern.dienste.radarr.filme", mock.AsyncMock(return_value=[])), \
             mock.patch("kern.dienste.sonarr.serien", mock.AsyncMock(return_value=[serie])), \
             mock.patch("kern.dienste.sonarr.deutsch_releases", mock.AsyncMock(return_value=[rel])), \
             mock.patch("kern.dienste.sonarr.release_holen", holen):
            t = lauf(w.medien_deutsch_holen("Rick and Morty", staffel=9))
        self.assertIn("Gefunden und gestartet", t)
        holen.assert_awaited_once_with(rel)

    def test_schreibrecht_und_fremdtext(self):
        self.assertIn("medien_deutsch_holen", w.NUR_MIT_SCHREIBRECHT)
        self.assertIn("medien_deutsch_holen", w.NICHT_NACH_FREMDTEXT)


class Katalog(unittest.TestCase):
    def test_zustimmung_und_sperren(self):
        self.assertTrue(katalog.braucht_zustimmung("medien_loeschen_bestaetigen"))
        self.assertFalse(katalog.braucht_zustimmung("medien_suchen"))

    def test_gemini_sieht_loeschen_nur_mit_schreibrecht(self):
        with mock.patch("kern.konnektoren.benutzbar", return_value=True), \
             mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=False):
            namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("medien_suchen", namen)
        self.assertNotIn("medien_loeschen", namen)
        self.assertNotIn("medien_loeschen_bestaetigen", namen)

    def test_denkrunde_und_fremde_duerfen_nicht(self):
        for n in ("medien_suchen", "medien_loeschen", "medien_loeschen_bestaetigen"):
            self.assertNotIn(n, katalog.DENKEN_ERLAUBT)
            self.assertNotIn(n, __import__("kern.personen.rechte", fromlist=["x"]).FREMDEN_ERLAUBT)


if __name__ == "__main__":
    unittest.main()
