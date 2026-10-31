"""Die Übersicht nach Typen — gegen nachgebaute Datenbank-Antworten."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import bilder, wissensgraph

U = "0b6c1f7e-1111-4222-8333-944455556666"
V = "1b6c1f7e-1111-4222-8333-944455556666"


def fake_abfrage(zeilen_fuer):
    async def abfrage(cypher, werte=None):
        for schluessel, zeilen in zeilen_fuer.items():
            if schluessel in cypher:
                return zeilen
        return []
    return abfrage


class Wissensgraph(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FALKORDB_HOST": "falkordb", "FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_uebersicht_nach_typen_eike_obenan_friday_weg(self):
        zeilen = [[U, "Kontakt Q", ["Entity", "Person"], "Freundin von Jale", 3, "eike"],
                  ["x", "Musterhausen", ["Entity", "Location"], "", 1, "eike"],
                  ["y", "Motorrad", ["Entity"], "", 1, "eike"],
                  ["z", "Eike", ["Entity"], "Der Nutzer", 5, "eike"],
                  ["w", "Friday", ["Entity"], "", 4, "eike"]]
        with mock.patch.object(wissensgraph, "_abfrage", fake_abfrage({"OPTIONAL MATCH": zeilen})):
            d = asyncio.run(wissensgraph.uebersicht())
        self.assertEqual([(t["name"], t["anzahl"]) for t in d["typen"]],
                         [("Personen", 1), ("Orte", 1), ("Sonstiges", 1)])
        self.assertEqual(d["du"]["name"], "Eike")
        self.assertNotIn("Friday", [e["name"] for e in d["entitaeten"]])

    def test_testgruppe_erscheint_nicht_und_eike_kommt_aus_eigener_gruppe(self):
        zeilen = [[U, "Eike", ["Entity", "Person"], "Test", 1, "friday-test"],
                  ["z", "Eike", ["Entity", "Person"], "Der Nutzer", 144, "eike"],
                  ["x", "Testkram", ["Entity"], "", 1, "friday-test"]]
        with mock.patch.object(wissensgraph, "_abfrage", fake_abfrage({"OPTIONAL MATCH": zeilen})):
            d = asyncio.run(wissensgraph.uebersicht())
        self.assertEqual(d["du"]["fakten"], 144)
        self.assertNotIn("Testkram", [e["name"] for e in d["entitaeten"]])

    def test_entitaet_mit_ueberholtem_fakt_gespraechen_und_bildern(self):
        b = bilder.speichern(None, "image/png", "iVBORw0KGgo=")
        bilder.benennen(b, "Kontakt Q")
        antworten = {
            "RETURN n.name": [["Kontakt Q", ["Entity", "Person"], "Freundin von Jale"]],
            "RELATES_TO": [["Kontakt Q fährt ein altes Motorrad", "2026-09-10T00:00:00Z", "2026-09-12T00:00:00Z", "Motorrad", V],
                           ["Kontakt Q fährt ein neues Motorrad", "2026-09-12T00:00:00Z", None, "Motorrad", V]],
            "MENTIONS": [["gespraech-6"], ["gespraech-3"], ["bild-abc-amy"]],
        }
        with mock.patch.object(wissensgraph, "_abfrage", fake_abfrage(antworten)):
            e = asyncio.run(wissensgraph.entitaet(U))
        self.assertEqual(e["typ"], "Person")
        self.assertEqual(e["fakten"][0]["bis"], "12.09.2026")
        self.assertEqual(e["fakten"][1]["bis"], "")
        self.assertEqual(e["gespraeche"], [3, 6])
        self.assertEqual(e["bilder"][0]["id"], b)

    def test_ungueltige_uuid_wird_nicht_abgefragt(self):
        abfrage = mock.AsyncMock()
        with mock.patch.object(wissensgraph, "_abfrage", abfrage):
            self.assertIsNone(asyncio.run(wissensgraph.entitaet("x' OR 1=1")))
        abfrage.assert_not_called()

    def test_datenbank_weg_meldet_das_ehrlich(self):
        async def kaputt(*a, **k):
            raise ConnectionError()
        with mock.patch.object(wissensgraph, "_abfrage", kaputt):
            self.assertFalse(asyncio.run(wissensgraph.uebersicht())["erreichbar"])


if __name__ == "__main__":
    unittest.main()
