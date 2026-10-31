"""Der Hintergrundlauf: zusammenfassen, senden, nachsehen, sichern."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from kern.gedaechtnis import rueckblick
from kern.gedaechtnis import speicher as gspeicher
from kern.protokoll import speicher as pspeicher


class Rueckblick(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner, "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()
        self.p = pspeicher.standard()

    def tearDown(self):
        self.env.stop()

    def _gespraech(self, zusammengefasst=False):
        g = self.p.gespraech_beginnen("chat")
        self.p.beitrag(g, "du", "Ich trinke Kaffee schwarz.")
        self.p.beitrag(g, "system", "Werkzeug merken: …", "werkzeug")
        self.p.beitrag(g, "friday", "Notiert, Sir.")
        self.p.gespraech_beenden(g)
        if zusammengefasst:
            self.p.zusammenfassen(g, "Kaffeevorliebe")
        return g

    def test_abschrift_ohne_systemzeilen(self):
        g = self._gespraech()
        self.assertEqual(self.p.abschrift(g), "Eike: Ich trinke Kaffee schwarz.\nFriday: Notiert, Sir.")

    def test_fertiges_gespraech_wird_mit_leichtem_modell_zusammengefasst(self):
        g = self._gespraech()
        self.assertEqual(self.p.offene_rueckblicke(), [g])
        gefragt = {}
        async def gemini(inhalt, bevorzugt=None, schluessel_liste=None):
            gefragt["modell"] = bevorzugt
            return {"candidates": [{"content": {"parts": [{"text": "Eike sprach über Kaffee."}]}}]}, "m"
        with mock.patch("kern.sitzung.chat._gemini_anfrage", gemini):
            asyncio.run(rueckblick.zusammenfassen(g))
        self.assertEqual(self.p.kopf(g)["zusammenfassung"], "Eike sprach über Kaffee.")
        self.assertEqual(gefragt["modell"], rueckblick.HINTERGRUND_MODELL)
        self.assertEqual(self.p.offene_rueckblicke(), [])

    def test_senden_und_angekommen(self):
        g = self._gespraech(zusammengefasst=True)
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            asyncio.run(rueckblick.durchgang())
        self.assertEqual(senden.call_args.args[0], f"gespraech-{g}")
        self.assertEqual(self.p.kopf(g)["graph_status"], "gesendet")
        with mock.patch("kern.gedaechtnis.graph.angekommen", mock.AsyncMock(return_value=True)):
            asyncio.run(rueckblick.nachsehen(g))
        self.assertEqual(self.p.kopf(g)["graph_status"], "angekommen")

    def test_benannte_bilder_gehen_mit_ins_langzeitgedaechtnis(self):
        from kern.gedaechtnis import bilder
        g = self._gespraech(zusammengefasst=True)
        b = bilder.speichern(g, "image/png", "iVBORw0KGgo=")
        bilder.beschreiben(b, "Eine Frau auf einem Motorrad.")
        bilder.benennen(b, "Kontakt Q")
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            asyncio.run(rueckblick.einspeisen(g))
        self.assertIn("Eike zeigte ein Bild von Kontakt Q. Friday beschrieb es: Eine Frau auf einem Motorrad.",
                      senden.call_args.args[1])

    def test_snap_bild_geht_mit_quelle_und_sachlicher_beschreibung_ins_langzeitgedaechtnis(self):
        from kern.gedaechtnis import bilder
        g = self._gespraech(zusammengefasst=True)
        b = bilder.speichern(g, "image/png", "iVBORw0KGgo=", quelle="Snapchat · Eike")
        bilder.beschreiben(b, "Ein Monitor mit einem Film, davor eine Tastatur.")
        bilder.ergaenzen(b, "Das war bei Kontakt E")
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            asyncio.run(rueckblick.einspeisen(g))
        text = senden.call_args.args[1]
        self.assertIn("Quelle: Snapchat · Eike", text)
        self.assertIn("Ein Monitor mit einem Film, davor eine Tastatur.", text)
        self.assertIn("Ergänzung: Das war bei Kontakt E", text)
        self.assertNotIn("Friday beschrieb es", text)

    def test_still_verworfen_wird_nachgeschickt_dann_aufgegeben(self):
        # Graphitis Warteschlange verwirft gescheiterte Episoden ohne neuen Versuch.
        g = self._gespraech(zusammengefasst=True)
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden, \
             mock.patch("kern.gedaechtnis.graph.angekommen", mock.AsyncMock(return_value=False)):
            asyncio.run(rueckblick.einspeisen(g))
            for _ in range(rueckblick.MAX_VERSUCHE):
                asyncio.run(rueckblick.nachsehen(g))
        self.assertEqual(senden.call_count, rueckblick.MAX_VERSUCHE)
        self.assertEqual(self.p.kopf(g)["graph_status"], "aufgegeben")

    def test_graphiti_weg_aendert_nichts(self):
        g = self._gespraech(zusammengefasst=True)
        self.p.graph_markieren(g, "gesendet", neuer_versuch=True)
        with mock.patch("kern.gedaechtnis.graph.angekommen", mock.AsyncMock(return_value=None)):
            asyncio.run(rueckblick.nachsehen(g))
        self.assertEqual(self.p.kopf(g)["graph_status"], "gesendet")

    def test_sicherung_einmal_pro_nacht_mit_rotation(self):
        gspeicher.standard().merken("Kaffee", "schwarz")
        ordner = Path(self.ordner) / "sicherung"
        ordner.mkdir()
        for tag in range(1, 17):
            (ordner / f"friday-2026-08-{tag:02d}.db").write_bytes(b"alt")
        nachts = datetime(2026, 9, 12, 4, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with mock.patch("kern.config.jetzt", return_value=nachts):
            rueckblick.sichern()
            rueckblick.sichern()                                   # zweiter Aufruf: nichts Neues
        self.assertTrue((ordner / "gedaechtnis-2026-09-12.db").exists())
        self.assertEqual(len(list(ordner.glob("friday-*.db"))), rueckblick.SICHERUNGEN_BEHALTEN)
        self.assertTrue((ordner / "friday-2026-09-12.db").exists())
        self.assertFalse((ordner / "friday-2026-08-01.db").exists())

    def test_tagsueber_keine_sicherung_vor_drei_uhr(self):
        with mock.patch("kern.config.jetzt", return_value=datetime(2026, 9, 12, 2, 0, tzinfo=ZoneInfo("Europe/Berlin"))):
            rueckblick.sichern()
        self.assertEqual(list((Path(self.ordner) / "sicherung").glob("*.db")), [])


if __name__ == "__main__":
    unittest.main()


class Angekommen(unittest.TestCase):
    """Eine alte, gleichnamige Episode darf nicht „angekommen" vortäuschen."""

    def _pruefen(self, seit):
        from kern.gedaechtnis import graph
        antwort = {"episodes": [{"name": "gespraech-1", "created_at": "2026-09-12T00:49:43.336238+00:00"}]}
        with mock.patch.dict(os.environ, {"GRAPHITI_URL": "http://graphiti:8000/mcp"}), \
             mock.patch.object(graph, "_aufruf", mock.AsyncMock(return_value=antwort)):
            return asyncio.run(graph.angekommen("gespraech-1", seit))

    def test_alte_episode_zaehlt_nicht(self):
        self.assertFalse(self._pruefen("2026-09-12T01:40:00+00:00"))

    def test_neue_episode_zaehlt(self):
        self.assertTrue(self._pruefen("2026-09-12T00:49:40+00:00"))

    def test_ohne_zeitpunkt_reicht_der_name(self):
        self.assertTrue(self._pruefen(None))


class WiederVersuchen(unittest.TestCase):
    """Aufgegebene kommen nach 12 Stunden zurück in die Warteschlange, frische nicht."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.p = pspeicher.standard()

    def tearDown(self):
        self.env.stop()

    def _aufgegeben(self, gesendet):
        g = self.p.gespraech_beginnen("chat")
        self.p._schreiben("UPDATE gespraeche SET graph_status='aufgegeben', graph_versuche=3, graph_gesendet=? "
                          "WHERE id=?", (gesendet, g))
        return g

    def test_alt_wird_neu_angestellt_frisch_nicht(self):
        alt = self._aufgegeben("2020-01-01T00:00:00+00:00")
        frisch = self._aufgegeben("2999-01-01T00:00:00+00:00")
        self.assertEqual(self.p.graph_wieder_versuchen(), 1)
        self.assertIsNone(self.p.kopf(alt)["graph_status"])
        self.assertEqual(self.p.kopf(frisch)["graph_status"], "aufgegeben")
