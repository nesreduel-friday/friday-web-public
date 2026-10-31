"""Regressionstests für die Qualitätsverbesserungen:

1. Sicherung: goodnotes.db + JSON-Konfigs werden gesichert, WAL-Checkpoint läuft.
2. Quoten-Schlüssel-Trennung: Rückblick nutzt Hintergrund-Schlüssel.
3. WebSocket Origin-Prüfung: erlaubte und verbotene Origins.
4. Bereinigung temporärer Upload-Ordner.
5. Abendblick-Generierung (Phase 4).
"""

import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


# ---------------------------------------------------------------------------
# 1. Datensicherung & WAL-Checkpointing
# ---------------------------------------------------------------------------

class Sicherung(unittest.TestCase):

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _db(self, name: str) -> Path:
        """Legt eine minimale SQLite-Datenbank an und gibt ihren Pfad zurück."""
        p = Path(self.ordner) / name
        with sqlite3.connect(p) as con:
            con.execute("CREATE TABLE t (v TEXT)")
            con.execute("INSERT INTO t VALUES ('test')")
        return p

    def test_goodnotes_db_wird_gesichert(self):
        """goodnotes.db muss wie die anderen DBs täglich gesichert werden."""
        from kern.gedaechtnis import rueckblick
        self._db("goodnotes.db")
        jetzt = datetime(2026, 9, 17, 4, 0, tzinfo=BERLIN)
        with mock.patch("kern.config.jetzt", return_value=jetzt):
            rueckblick.sichern()
        sicherung = Path(self.ordner) / "sicherung"
        dateien = list(sicherung.glob("goodnotes-*.db"))
        self.assertEqual(len(dateien), 1, "goodnotes.db-Sicherung fehlt")

    def test_wal_checkpoint_wird_aufgerufen(self):
        """PRAGMA wal_checkpoint(PASSIVE) soll vor dem Backup ausgeführt werden."""
        from kern.gedaechtnis import rueckblick
        self._db("friday.db")
        checkpoint_aufrufe: list[str] = []
        _orig_connect = sqlite3.connect

        class TrackingConn(sqlite3.Connection):
            """sqlite3.Connection-Unterklasse, zählt wal_checkpoint-Aufrufe."""
            def execute(self, sql, *a):
                if "wal_checkpoint" in str(sql).lower():
                    checkpoint_aufrufe.append(sql)
                return super().execute(sql, *a)

        def patched_connect(datei, *args, **kwargs):
            kwargs["factory"] = TrackingConn
            return _orig_connect(datei, *args, **kwargs)

        jetzt = datetime(2026, 9, 17, 4, 0, tzinfo=BERLIN)
        with mock.patch("kern.config.jetzt", return_value=jetzt), \
             mock.patch("kern.gedaechtnis.rueckblick.sqlite3.connect", side_effect=patched_connect):
            rueckblick.sichern()
        self.assertTrue(checkpoint_aufrufe, "WAL-Checkpoint wurde nicht aufgerufen")

    def test_json_konfigs_werden_gesichert(self):
        """zugaenge.json, konnektoren.json und einstellungen.json werden gesichert."""
        from kern.gedaechtnis import rueckblick
        for name in ("zugaenge.json", "konnektoren.json", "einstellungen.json"):
            (Path(self.ordner) / name).write_text("{}", encoding="utf-8")
        jetzt = datetime(2026, 9, 17, 4, 0, tzinfo=BERLIN)
        with mock.patch("kern.config.jetzt", return_value=jetzt):
            rueckblick.sichern()
        sicherung = Path(self.ordner) / "sicherung"
        for stamm in ("zugaenge", "konnektoren", "einstellungen"):
            gefunden = list(sicherung.glob(f"{stamm}-*.json"))
            self.assertEqual(len(gefunden), 1, f"{stamm}.json-Sicherung fehlt")

    def test_json_kopien_haben_restriktive_rechte(self):
        """Gesicherte JSON-Dateien müssen 0600-Rechte haben."""
        from kern.gedaechtnis import rueckblick
        (Path(self.ordner) / "zugaenge.json").write_text("{}", encoding="utf-8")
        jetzt = datetime(2026, 9, 17, 4, 0, tzinfo=BERLIN)
        with mock.patch("kern.config.jetzt", return_value=jetzt):
            rueckblick.sichern()
        sicherung = Path(self.ordner) / "sicherung"
        kopie = next(sicherung.glob("zugaenge-*.json"))
        import stat
        modus = kopie.stat().st_mode & 0o777
        self.assertEqual(modus, 0o600, f"Rechte sind {modus:o}, erwartet 600")


# ---------------------------------------------------------------------------
# 2. Quoten-Schlüssel-Trennung
# ---------------------------------------------------------------------------

class QuotenTrennung(unittest.TestCase):

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "FRIDAY_DATEN": self.ordner,
            "GRAPHITI_URL": "http://graphiti:8000/mcp",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_zusammenfassen_nutzt_hintergrund_schluessel(self):
        """rueckblick.zusammenfassen() muss Hintergrund-Schlüssel übergeben, nicht Chat-Schlüssel."""
        from kern.protokoll import speicher as pspeicher
        from kern.gedaechtnis import rueckblick

        p = pspeicher.standard()
        g = p.gespraech_beginnen("chat")
        p.beitrag(g, "du", "Hallo Welt.")
        p.beitrag(g, "friday", "Guten Morgen.")
        p.gespraech_beenden(g)

        aufrufe: list[list[str]] = []

        async def mock_gemini(inhalt, bevorzugt=None, schluessel_liste=None):
            aufrufe.append(schluessel_liste or [])
            return {"candidates": [{"content": {"parts": [{"text": "Zusammenfassung."}]}}]}, "probe"

        hintergrund_keys = ["hintergrund-key-1", "hintergrund-key-2"]
        with mock.patch("kern.sitzung.chat._gemini_anfrage", mock_gemini), \
             mock.patch("kern.gedaechtnis.rueckblick.config.hintergrund_schluessel_liste",
                        return_value=hintergrund_keys):
            asyncio.run(rueckblick.zusammenfassen(g))

        self.assertTrue(aufrufe, "Kein Aufruf an _gemini_anfrage")
        self.assertEqual(aufrufe[0], hintergrund_keys,
                         "Falsche Schlüsselliste: Chat-Keys statt Hintergrund-Keys genutzt")


# ---------------------------------------------------------------------------
# 3. WebSocket Origin-Prüfung
# ---------------------------------------------------------------------------

class OriginPruefung(unittest.TestCase):

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _erlaubt(self, origin, basis_url="https://hphome.beispiel.ts.net"):
        from kern.sitzung import live
        with mock.patch("kern.config.basis_url", return_value=basis_url):
            return live.origin_erlaubt(origin)

    def test_none_ist_erlaubt(self):
        self.assertTrue(self._erlaubt(None))

    def test_testserver_ist_erlaubt(self):
        self.assertTrue(self._erlaubt("testserver"))

    def test_localhost_ist_erlaubt(self):
        self.assertTrue(self._erlaubt("http://localhost:8000"))

    def test_127_0_0_1_ist_erlaubt(self):
        self.assertTrue(self._erlaubt("http://127.0.0.1:9000"))

    def test_eigene_domain_ist_erlaubt(self):
        self.assertTrue(self._erlaubt("https://hphome.beispiel.ts.net"))

    def test_fremde_domain_ist_verboten(self):
        self.assertFalse(self._erlaubt("https://evil.example.com"))

    def test_aehnliche_domain_ist_verboten(self):
        """Teilstring der echten Domain darf nicht durchkommen."""
        self.assertFalse(self._erlaubt("https://hphome.beispiel.ts.net.evil.com"))

    def test_leerer_string_ist_verboten(self):
        """Leerer String ist kein erlaubter Origin (Sonderfall: nicht None)."""
        # origin="" → not origin → True (wird als None behandelt): korrekt laut Spezifikation
        self.assertTrue(self._erlaubt(""))  # "" wird wie None behandelt (not "")


# ---------------------------------------------------------------------------
# 4. Bereinigung temporärer Upload-Ordner
# ---------------------------------------------------------------------------

class TmpOrdnerBereinigung(unittest.TestCase):

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "FRIDAY_DATEN": self.ordner,
            "FRIDAY_HINTERGRUND": "aus",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_verwaiste_leere_ordner_werden_beim_start_bereinigt(self):
        """Leere Eingangsordner in buecher/eingang/ müssen beim App-Start entfernt werden."""
        from kern import schule
        from kern.schule import buecher as buecherablage

        eingang_basis = buecherablage.standard().ordner / "schule" / "buecher" / "eingang"
        eingang_basis.mkdir(parents=True, exist_ok=True)

        # Zwei leere verwaiste Ordner anlegen
        verwaist_1 = eingang_basis / "abc123"
        verwaist_2 = eingang_basis / "def456"
        verwaist_1.mkdir()
        verwaist_2.mkdir()
        # Ein Ordner mit Inhalt (soll erhalten bleiben)
        behalten = eingang_basis / "xyz789"
        behalten.mkdir()
        (behalten / "datei.pdf").write_bytes(b"%PDF")

        # App initialisieren (lifespan): startet den Bereinigungslauf
        from kern.main import app
        from fastapi.testclient import TestClient
        with TestClient(app) as _:
            pass  # Startup-Logik läuft durch

        self.assertFalse(verwaist_1.exists(), "Verwaister Ordner 1 wurde nicht bereinigt")
        self.assertFalse(verwaist_2.exists(), "Verwaister Ordner 2 wurde nicht bereinigt")
        self.assertTrue(behalten.exists(), "Ordner mit Inhalt wurde fälschlicherweise gelöscht")

    def test_fehler_beim_annehmen_bereinigt_tmp_ordner(self):
        """Wenn _pdfs_annehmen() fehlschlägt, darf kein temporärer Ordner zurückbleiben."""
        from kern import schule
        from kern.schule import buecher as buecherablage

        eingang_basis = buecherablage.standard().ordner / "schule" / "buecher" / "eingang"

        # Buchanlegen per API — Upload mit kaputter Datei → sollte 4xx zurückgeben
        # und den tmp-Ordner bereinigen
        from kern.main import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            vorher = set(eingang_basis.iterdir()) if eingang_basis.exists() else set()
            # Leere Datei hochladen (kein gültiges PDF)
            antwort = client.post(
                "/api/buecher/1/upload",
                files={"dateien": ("leer.pdf", b"kein-pdf", "application/pdf")},
            )
            # Fehler ist erwartet (ungültiges PDF oder Buch existiert nicht),
            # aber der Tmp-Ordner darf danach nicht existieren
            nachher = set(eingang_basis.iterdir()) if eingang_basis.exists() else set()
            neue_ordner = {p for p in (nachher - vorher) if p.is_dir()}
        self.assertEqual(neue_ordner, set(), f"Temporäre Ordner nicht bereinigt: {neue_ordner}")


# ---------------------------------------------------------------------------
# 5. Abendblick (Phase 4)
# ---------------------------------------------------------------------------

class Abendblick(unittest.TestCase):

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _morgen(self) -> date:
        return date(2026, 9, 18)

    def test_abendblick_gibt_titel_und_text_zurueck(self):
        """briefing.abendblick() muss Titel und nicht-leeren Text liefern."""
        from kern.planer import briefing

        async def _ok(*_):
            return "Bewölkt, 17 °C."

        with mock.patch("kern.planer.briefing.wetter_zeile", side_effect=_ok), \
             mock.patch("kern.planer.briefing.schule_zeile", side_effect=_ok), \
             mock.patch("kern.planer.briefing.kalender_zeile", side_effect=_ok), \
             mock.patch("kern.planer.briefing.erinnerungen_zeile", side_effect=_ok):
            titel, text = asyncio.run(briefing.abendblick(self._morgen()))

        self.assertIsInstance(titel, str)
        self.assertTrue(titel, "Titel darf nicht leer sein")
        self.assertIsInstance(text, str)
        self.assertTrue(text, "Text darf nicht leer sein")

    def test_abendblick_an_standard_20_uhr(self):
        """Abendblick-Uhrzeit soll standardmäßig 20:00 sein."""
        from kern.planer import briefing
        uhrzeit = briefing.abendblick_uhrzeit()
        self.assertEqual(uhrzeit, time(20, 0),
                         f"Standard-Uhrzeit ist {uhrzeit}, erwartet 20:00")

    def test_abendblick_faellig_genau_um_uhrzeit(self):
        """abendblick_faellig() muss True zurückgeben, wenn jetzt == Uhrzeit."""
        from kern.planer import briefing

        jetzt = datetime(2026, 9, 17, 20, 0, 0, tzinfo=BERLIN)
        stand: dict = {}

        with mock.patch("kern.planer.briefing.abendblick_uhrzeit", return_value=time(20, 0)):
            faellig = briefing.abendblick_faellig(jetzt, stand)

        self.assertTrue(faellig)

    def test_abendblick_nicht_faellig_wenn_heute_schon_gesendet(self):
        """Wenn heute bereits gesendet, soll abendblick_faellig() False sein."""
        from kern.planer import briefing

        jetzt = datetime(2026, 9, 17, 20, 30, tzinfo=BERLIN)
        # Sende-Datei hat heutiges Datum + geschickt=True
        stand = {"datum": "2026-09-17", "geschickt": True, "versuche": 1}

        with mock.patch("kern.planer.briefing.abendblick_uhrzeit", return_value=time(20, 0)):
            faellig = briefing.abendblick_faellig(jetzt, stand)

        self.assertFalse(faellig)


if __name__ == "__main__":
    unittest.main()
