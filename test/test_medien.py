"""Medien-Tab: Duplikate finden/löschen, Serie/Film überall löschen — ohne Netz."""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


class DuplikateBasis(unittest.TestCase):
    def setUp(self):
        self.wurzel = Path(tempfile.mkdtemp())
        self.downloads = self.wurzel / "downloads"
        self.bibliothek = self.wurzel / "bibliothek"
        self.downloads.mkdir()
        self.bibliothek.mkdir()

    def tearDown(self):
        shutil.rmtree(self.wurzel, ignore_errors=True)


class FindDuplikate(DuplikateBasis):
    def test_findet_byte_identische_datei(self):
        from kern import medien
        (self.downloads / "film.mkv").write_bytes(b"x" * 1000)
        (self.bibliothek / "film.mkv").write_bytes(b"x" * 1000)

        ergebnis = medien.find_duplikate(self.downloads, self.bibliothek)

        self.assertEqual(len(ergebnis), 1)
        self.assertEqual(ergebnis[0].groesse, 1000)

    def test_ignoriert_gleiche_groesse_anderen_inhalt(self):
        from kern import medien
        (self.downloads / "a.mkv").write_bytes(b"a" * 1000)
        (self.bibliothek / "b.mkv").write_bytes(b"b" * 1000)

        self.assertEqual(medien.find_duplikate(self.downloads, self.bibliothek), [])

    def test_ignoriert_bereits_gehardlinkte_datei(self):
        from kern import medien
        original = self.bibliothek / "film.mkv"
        original.write_bytes(b"x" * 1000)
        (self.downloads / "film.mkv").hardlink_to(original)

        self.assertEqual(medien.find_duplikate(self.downloads, self.bibliothek), [])


class FindDuplikateRobust(DuplikateBasis):
    def test_verschwundene_datei_waehrend_scan_wirft_nicht(self):
        """Löscht qBittorrent/Sonarr eine Datei während des Scans, darf das
        nicht mit einem 500er enden (Review-Fund 27.09.2026)."""
        from kern import medien
        bleibt = self.downloads / "bleibt.mkv"
        verschwindet = self.downloads / "verschwindet.mkv"
        bleibt.write_bytes(b"x" * 1000)
        verschwindet.write_bytes(b"x" * 1000)
        (self.bibliothek / "film.mkv").write_bytes(b"x" * 1000)

        echter_stat = Path.stat

        def stat_und_loeschen(self_pfad, *a, **kw):
            ergebnis = echter_stat(self_pfad, *a, **kw)
            if self_pfad == verschwindet:
                verschwindet.unlink(missing_ok=True)
            return ergebnis

        with mock.patch.object(Path, "stat", stat_und_loeschen):
            ergebnis = medien.find_duplikate(self.downloads, self.bibliothek)
        # bleibt.mkv muss trotzdem als Duplikat erkannt werden, kein Absturz.
        self.assertEqual([d.download_pfad for d in ergebnis], [str(bleibt)])


class FindDuplikateCache(DuplikateBasis):
    def test_erkennt_inhaltsaenderung_trotz_gleicher_groesse_und_mtime(self):
        """filecmp.cmp cached Vergleichsergebnisse nach (Pfad, Größe, mtime) —
        ohne clear_cache() könnte eine echte Änderung übersehen werden, wenn
        Größe UND mtime zufällig gleich bleiben (Review-Fund 27.09.2026)."""
        from kern import medien
        download = self.downloads / "film.mkv"
        bibliothek = self.bibliothek / "film.mkv"
        download.write_bytes(b"x" * 1000)
        bibliothek.write_bytes(b"x" * 1000)
        medien.find_duplikate(self.downloads, self.bibliothek)  # füllt filecmp-Cache

        mtime_vorher = download.stat().st_mtime
        download.write_bytes(b"y" * 1000)  # anderer Inhalt, gleiche Größe
        os.utime(download, (mtime_vorher, mtime_vorher))  # erzwingt gleiche mtime

        ergebnis = medien.find_duplikate(self.downloads, self.bibliothek)
        self.assertEqual(ergebnis, [])


class DuplikatLoeschen(DuplikateBasis):
    def test_loescht_datei(self):
        from kern import medien
        datei = self.downloads / "f.mkv"
        datei.write_bytes(b"x" * 10)

        medien.duplikat_loeschen(datei)

        self.assertFalse(datei.exists())


class FakeSonarr:
    def __init__(self, scheitert=False, hashes=None, hashes_scheitert=False):
        self.scheitert, self.hashes, self.hashes_scheitert = scheitert, hashes or [], hashes_scheitert
        self.geloescht_mit = None
        self.hashes_geholt_fuer = None

    async def download_hashes(self, serie_id):
        await asyncio.sleep(0)  # echter Yield-Punkt, damit parallele Aufrufe sich wirklich verzahnen
        self.hashes_geholt_fuer = serie_id
        if self.hashes_scheitert:
            from kern.dienste.sonarr import SonarrFehler
            raise SonarrFehler("Hashes kaputt")
        return self.hashes

    async def serie_loeschen(self, serie_id):
        await asyncio.sleep(0)
        self.geloescht_mit = serie_id
        if self.scheitert:
            from kern.dienste.sonarr import SonarrFehler
            raise SonarrFehler("kaputt")


class FakeQbit:
    def __init__(self, scheitert=False, entfernt=None):
        self.scheitert, self.entfernt = scheitert, entfernt or []
        self.aufgerufen_mit = None

    async def torrents_entfernen(self, hashes, loeschen):
        await asyncio.sleep(0)
        self.aufgerufen_mit = (hashes, loeschen)
        if self.scheitert:
            from kern.dienste.qbittorrent import QbittorrentFehler
            raise QbittorrentFehler("kaputt")
        return self.entfernt


class FakeSonarrStaffel(FakeSonarr):
    def __init__(self, *a, staffel_hashes=None, folge_datei_id=70, episode_liste=None, **kw):
        super().__init__(*a, **kw)
        self.staffel_hashes = staffel_hashes if staffel_hashes is not None else self.hashes
        self.staffel_geloescht_mit = None
        self.folge_geloescht_mit = None
        self.folge_datei_id = folge_datei_id
        self.episode_liste = episode_liste if episode_liste is not None else [
            {"id": 1, "seasonNumber": 1, "hasFile": True, "episodeFileId": folge_datei_id},
        ]
        self.hashes_angefragt_fuer = None

    async def episoden(self, serie_id):
        return self.episode_liste

    async def hashes_ausschliesslich_fuer(self, serie_id, episode_ids):
        await asyncio.sleep(0)
        self.hashes_angefragt_fuer = (serie_id, frozenset(episode_ids))
        return self.staffel_hashes

    async def staffel_loeschen(self, serie_id, staffel_nr):
        await asyncio.sleep(0)
        self.staffel_geloescht_mit = (serie_id, staffel_nr)
        if self.scheitert:
            from kern.dienste.sonarr import SonarrFehler
            raise SonarrFehler("kaputt")
        return 2

    async def folge_loeschen(self, episode_id, episode_file_id):
        await asyncio.sleep(0)
        self.folge_geloescht_mit = (episode_id, episode_file_id)
        if self.scheitert:
            from kern.dienste.sonarr import SonarrFehler
            raise SonarrFehler("kaputt")


class SerieUeberallLoeschen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _lauf(self, sonarr_fake, qbit_fake):
        from kern import medien
        with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
            return asyncio.run(medien.serie_ueberall_loeschen(5, "Dark"))

    def test_erfolg_entfernt_serie_und_torrent(self):
        sonarr_fake, qbit_fake = FakeSonarr(hashes=["aaa"]), FakeQbit(entfernt=["aaa"])
        ergebnis = self._lauf(sonarr_fake, qbit_fake)
        self.assertTrue(ergebnis.hauptteil_ok)
        self.assertEqual(ergebnis.qbittorrent_entfernt, ["aaa"])
        self.assertIsNone(ergebnis.fehler)
        self.assertEqual(sonarr_fake.geloescht_mit, 5)
        self.assertEqual(qbit_fake.aufgerufen_mit, (["aaa"], True))

    def test_sonarr_fehler_stoppt_vor_qbittorrent(self):
        sonarr_fake, qbit_fake = FakeSonarr(scheitert=True, hashes=["aaa"]), FakeQbit()
        ergebnis = self._lauf(sonarr_fake, qbit_fake)
        self.assertFalse(ergebnis.hauptteil_ok)
        self.assertIsNone(qbit_fake.aufgerufen_mit)

    def test_qbittorrent_fehler_nach_erfolg_meldet_teilerfolg(self):
        sonarr_fake, qbit_fake = FakeSonarr(hashes=["aaa"]), FakeQbit(scheitert=True)
        ergebnis = self._lauf(sonarr_fake, qbit_fake)
        self.assertTrue(ergebnis.hauptteil_ok)
        self.assertEqual(ergebnis.qbittorrent_entfernt, [])
        self.assertIn("qBittorrent", ergebnis.fehler)

    def test_hash_fehler_loescht_trotzdem_meldet_teilerfolg(self):
        sonarr_fake, qbit_fake = FakeSonarr(hashes_scheitert=True), FakeQbit(entfernt=["aaa"])
        ergebnis = self._lauf(sonarr_fake, qbit_fake)
        self.assertTrue(ergebnis.hauptteil_ok)
        self.assertEqual(sonarr_fake.geloescht_mit, 5)
        self.assertIsNone(qbit_fake.aufgerufen_mit)
        self.assertIsNotNone(ergebnis.fehler)

    def test_paralleler_aufruf_wird_abgewiesen(self):
        from kern import medien
        with medien._LOCK:
            medien._IN_PROGRESS.add(("serie", 5))
        try:
            ergebnis = self._lauf(FakeSonarr(hashes=["aaa"]), FakeQbit(entfernt=["aaa"]))
        finally:
            with medien._LOCK:
                medien._IN_PROGRESS.discard(("serie", 5))
        self.assertEqual(ergebnis.fehler, medien.ALREADY_RUNNING)

    def test_echte_parallelitaet_nur_eine_loeschung_ein_logeintrag(self):
        """Zwei gleichzeitige Aufrufe (nicht nur vorbefüllte Sperre) dürfen nur
        einmal löschen und nur eine Log-Zeile schreiben (Review-Fund 27.09.2026)."""
        from kern import medien
        sonarr_fake, qbit_fake = FakeSonarr(hashes=["aaa"]), FakeQbit(entfernt=["aaa"])
        async def _beide():
            return await asyncio.gather(
                medien.serie_ueberall_loeschen(9, "Dark"),
                medien.serie_ueberall_loeschen(9, "Dark"),
            )

        with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
            ergebnisse = asyncio.run(_beide())
        erfolge = [e for e in ergebnisse if e.fehler is None]
        abgewiesen = [e for e in ergebnisse if e.fehler == medien.ALREADY_RUNNING]
        self.assertEqual(len(erfolge), 1)
        self.assertEqual(len(abgewiesen), 1)
        protokoll = medien._protokoll_pfad().read_text(encoding="utf-8")
        self.assertEqual(protokoll.count("Dark"), 1)

    def test_protokollfehler_bricht_erfolgreiches_loeschen_nicht_ab(self):
        """Volle Platte o.ä. beim Protokollieren darf einen bereits erfolgten
        Löschvorgang nicht in einen Fehler verwandeln (Review-Fund 27.09.2026).
        Echter OSError, nicht gemockt: 'medien'-Ordner existiert schon als Datei,
        `mkdir` schlägt garantiert fehl."""
        from kern import medien
        blockiert = Path(os.environ["FRIDAY_DATEN"]) / "medien"
        blockiert.write_bytes(b"")  # Datei statt Ordner — mkdir() schlägt fehl
        sonarr_fake, qbit_fake = FakeSonarr(hashes=["aaa"]), FakeQbit(entfernt=["aaa"])
        with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
            ergebnis = asyncio.run(medien.serie_ueberall_loeschen(5, "Dark"))
        self.assertTrue(ergebnis.hauptteil_ok)
        self.assertEqual(ergebnis.qbittorrent_entfernt, ["aaa"])
        self.assertIsNone(ergebnis.fehler)


class StaffelUndFolgeUeberallLoeschen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_staffel_erfolg_entfernt_dateien_und_torrent(self):
        from kern import medien
        sonarr_fake = FakeSonarrStaffel(staffel_hashes=["aaa"])
        qbit_fake = FakeQbit(entfernt=["aaa"])
        with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
            ergebnis = asyncio.run(medien.staffel_ueberall_loeschen(5, 1, "Dark Staffel 1"))
        self.assertTrue(ergebnis.hauptteil_ok)
        self.assertEqual(ergebnis.qbittorrent_entfernt, ["aaa"])
        self.assertEqual(sonarr_fake.staffel_geloescht_mit, (5, 1))

    def test_staffel_sonarr_fehler_stoppt_vor_qbittorrent(self):
        from kern import medien
        sonarr_fake = FakeSonarrStaffel(scheitert=True, staffel_hashes=["aaa"])
        qbit_fake = FakeQbit()
        with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
            ergebnis = asyncio.run(medien.staffel_ueberall_loeschen(5, 1, "Dark Staffel 1"))
        self.assertFalse(ergebnis.hauptteil_ok)
        self.assertIsNone(qbit_fake.aufgerufen_mit)

    def test_staffel_paralleler_aufruf_fuer_gleiche_staffel_wird_abgewiesen(self):
        from kern import medien
        sonarr_fake = FakeSonarrStaffel(staffel_hashes=["aaa"])
        qbit_fake = FakeQbit(entfernt=["aaa"])
        with medien._LOCK:
            medien._IN_PROGRESS.add(("staffel", "5:1"))
        try:
            with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
                ergebnis = asyncio.run(medien.staffel_ueberall_loeschen(5, 1, "Dark Staffel 1"))
        finally:
            with medien._LOCK:
                medien._IN_PROGRESS.discard(("staffel", "5:1"))
        self.assertEqual(ergebnis.fehler, medien.ALREADY_RUNNING)

    def test_folge_mit_datei_erfolg(self):
        from kern import medien
        sonarr_fake = FakeSonarrStaffel(
            staffel_hashes=["bbb"],
            episode_liste=[{"id": 1, "seasonNumber": 1, "hasFile": True, "episodeFileId": 70}],
        )
        qbit_fake = FakeQbit(entfernt=["bbb"])
        with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
            ergebnis = asyncio.run(medien.folge_ueberall_loeschen(5, 1, "Dark S01E01"))
        self.assertTrue(ergebnis.hauptteil_ok)
        # episode_file_id kommt serverseitig aus sonarr.episoden(), nicht vom Aufrufer.
        self.assertEqual(sonarr_fake.folge_geloescht_mit, (1, 70))
        self.assertEqual(sonarr_fake.hashes_angefragt_fuer, (5, frozenset({1})))
        self.assertEqual(ergebnis.qbittorrent_entfernt, ["bbb"])

    def test_folge_ohne_datei_wird_trotzdem_ueberall_versucht(self):
        from kern import medien
        sonarr_fake = FakeSonarrStaffel(
            staffel_hashes=[],
            episode_liste=[{"id": 1, "seasonNumber": 1, "hasFile": False, "episodeFileId": None}],
        )
        qbit_fake = FakeQbit(entfernt=[])
        with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
            ergebnis = asyncio.run(medien.folge_ueberall_loeschen(5, 1, "Dark S01E01"))
        self.assertTrue(ergebnis.hauptteil_ok)
        self.assertEqual(sonarr_fake.folge_geloescht_mit, (1, None))

    def test_folge_unbekannte_id_loescht_ohne_datei(self):
        """Findet sich die Episode-ID nicht in sonarr.episoden() (z.B. Liste
        veraltet), wird trotzdem nur 'nicht mehr überwachen' versucht, keine
        falsche episode_file_id verwendet."""
        from kern import medien
        sonarr_fake = FakeSonarrStaffel(staffel_hashes=[], episode_liste=[])
        qbit_fake = FakeQbit(entfernt=[])
        with mock.patch("kern.medien.sonarr", sonarr_fake), mock.patch("kern.medien.qbittorrent", qbit_fake):
            ergebnis = asyncio.run(medien.folge_ueberall_loeschen(5, 999, "Unbekannt"))
        self.assertTrue(ergebnis.hauptteil_ok)
        self.assertEqual(sonarr_fake.folge_geloescht_mit, (999, None))


class MedienRouten(unittest.TestCase):
    def setUp(self):
        self.wurzel = Path(tempfile.mkdtemp())
        (self.wurzel / "downloads").mkdir()
        (self.wurzel / "media").mkdir()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "MEDIA_WURZEL": str(self.wurzel)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.wurzel, ignore_errors=True)

    def test_serien_route_meldet_fehler_statt_leerer_liste(self):
        from kern.dienste.sonarr import SonarrFehler
        from kern.main import app

        async def fake():
            raise SonarrFehler("Sonarr lehnt den Schlüssel ab.")

        with mock.patch("kern.main.sonarrdienst.serien", fake):
            with TestClient(app) as client:
                r = client.get("/api/medien/serien")
        self.assertEqual(r.status_code, 200)
        daten = r.json()
        self.assertEqual(daten["eintraege"], [])
        self.assertIn("Sonarr", daten["fehler"])

    def test_serien_route_liefert_ohne_fehler(self):
        from kern.main import app

        async def fake():
            return [{"id": 1, "title": "Dark"}]

        with mock.patch("kern.main.sonarrdienst.serien", fake):
            with TestClient(app) as client:
                r = client.get("/api/medien/serien")
        daten = r.json()
        self.assertEqual(daten["eintraege"], [{"id": 1, "title": "Dark"}])
        self.assertIsNone(daten["fehler"])

    def test_filme_route_meldet_fehler_statt_leerer_liste(self):
        from kern.dienste.radarr import RadarrFehler
        from kern.main import app

        async def fake():
            raise RadarrFehler("Radarr antwortet nicht.")

        with mock.patch("kern.main.radarrdienst.filme", fake):
            with TestClient(app) as client:
                r = client.get("/api/medien/filme")
        daten = r.json()
        self.assertEqual(daten["eintraege"], [])
        self.assertIn("Radarr", daten["fehler"])

    def test_duplikate_route_liefert_liste(self):
        (self.wurzel / "downloads" / "f.mkv").write_bytes(b"x" * 50)
        (self.wurzel / "media" / "f.mkv").write_bytes(b"x" * 50)
        from kern.main import app
        with TestClient(app) as client:
            r = client.get("/api/medien/duplikate")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 1)

    def test_duplikate_loeschen_route_lehnt_pfad_ausserhalb_downloads_ab(self):
        original = self.wurzel / "media" / "f.mkv"
        original.write_bytes(b"x" * 50)
        from kern.main import app
        with TestClient(app) as client:
            r = client.post("/api/medien/duplikate/loeschen", json={"pfade": [str(original)]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"geloescht": []})
        self.assertTrue(original.exists())

    def test_duplikate_loeschen_route_lehnt_veraenderte_datei_ab(self):
        geaendert = self.wurzel / "downloads" / "f.mkv"
        geaendert.write_bytes(b"x" * 50)
        (self.wurzel / "media" / "f.mkv").write_bytes(b"x" * 50)
        geaendert.write_bytes(b"z" * 999)  # ändert sich zwischen Scan und Löschanfrage
        from kern.main import app
        with TestClient(app) as client:
            r = client.post("/api/medien/duplikate/loeschen", json={"pfade": [str(geaendert)]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"geloescht": []})
        self.assertTrue(geaendert.exists())

    def test_serie_loeschen_route_ruft_medien_ab(self):
        from kern import medien
        from kern.main import app

        async def fake(serie_id, titel):
            return medien.LoeschErgebnis(True, ["aaa"], None)

        with mock.patch("kern.medien.serie_ueberall_loeschen", fake):
            with TestClient(app) as client:
                r = client.post("/api/medien/serie/5/loeschen", json={"titel": "Dark"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["qbittorrent_entfernt"], ["aaa"])

    def test_staffeln_route_liefert_seasons(self):
        from kern.main import app

        async def fake(serie_id):
            return {"id": serie_id, "seasons": [{"seasonNumber": 1, "statistics": {"sizeOnDisk": 100}}]}

        with mock.patch("kern.main.sonarrdienst.serie_details", fake):
            with TestClient(app) as client:
                r = client.get("/api/medien/serie/5/staffeln")
        daten = r.json()
        self.assertEqual(daten["eintraege"], [{"seasonNumber": 1, "statistics": {"sizeOnDisk": 100}}])
        self.assertIsNone(daten["fehler"])

    def test_folgen_route_filtert_nach_staffel(self):
        from kern.main import app

        async def fake(serie_id):
            return [
                {"id": 1, "seasonNumber": 1, "title": "A"},
                {"id": 2, "seasonNumber": 2, "title": "B"},
            ]

        with mock.patch("kern.main.sonarrdienst.episoden", fake):
            with TestClient(app) as client:
                r = client.get("/api/medien/serie/5/folgen", params={"staffel": 1})
        daten = r.json()
        self.assertEqual(daten["eintraege"], [{"id": 1, "seasonNumber": 1, "title": "A"}])

    def test_staffel_loeschen_route_ruft_medien_ab(self):
        from kern import medien
        from kern.main import app

        async def fake(serie_id, staffel_nr, titel):
            return medien.LoeschErgebnis(True, ["aaa"], None)

        with mock.patch("kern.medien.staffel_ueberall_loeschen", fake):
            with TestClient(app) as client:
                r = client.post("/api/medien/serie/5/staffel/1/loeschen", json={"titel": "Dark Staffel 1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["qbittorrent_entfernt"], ["aaa"])

    def test_episode_loeschen_route_ruft_medien_ab(self):
        from kern import medien
        from kern.main import app

        gesehen = {}

        async def fake(serie_id, episode_id, titel):
            gesehen["aufruf"] = (serie_id, episode_id, titel)
            return medien.LoeschErgebnis(True, [], None)

        with mock.patch("kern.medien.folge_ueberall_loeschen", fake):
            with TestClient(app) as client:
                r = client.post("/api/medien/serie/5/episode/1/loeschen", json={"titel": "S01E01"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["hauptteil_ok"])
        self.assertEqual(gesehen["aufruf"], (5, 1, "S01E01"))


if __name__ == "__main__":
    unittest.main()
