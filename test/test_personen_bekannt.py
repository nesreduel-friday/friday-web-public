"""Bekannte Personen aus allen Quellen zusammenführen — eine Quelle darf ausfallen, ohne die Liste zu kippen."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.personen import bekannt, register


def lauf(coro):
    return asyncio.run(coro)


class Quellen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.wissen = {"eingerichtet": True, "erreichbar": True, "du": None, "typen": [],
                       "entitaeten": [{"name": "Kontakt B", "typ": "Person"}, {"name": "Photosynthese", "typ": "Topic"},
                                      {"name": "Eike", "typ": "Person"}, {"name": "Friday", "typ": "Person"}]}
        self.patches = [
            mock.patch.object(bekannt.wissensgraph, "uebersicht", mock.AsyncMock(return_value=self.wissen)),
            mock.patch.object(bekannt.konnektoren, "benutzbar", return_value=True),
            mock.patch.object(bekannt.personendienst, "personen",
                              mock.AsyncMock(return_value=[{"name": "Kontakt P"}, {"name": "Kontakt B"}])),
            mock.patch.object(bekannt, "_garantie_namen", return_value=["Kontakt E"]),
            mock.patch.object(bekannt, "_chat_namen", return_value=["Kontakt F", "Kontakt P"]),
            mock.patch.object(bekannt, "_bild_namen", return_value=["Kontakt Q"]),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.env.stop()

    def test_fuehrt_quellen_zusammen_ohne_gross_klein_dubletten_und_ohne_eike_friday_themen(self):
        d = lauf(bekannt.liste())
        namen = [p["name"] for p in d["personen"]]
        self.assertEqual(sorted(namen), ["Kontakt B", "Kontakt E", "Kontakt F", "Kontakt P", "Kontakt Q"])
        self.assertEqual(d["ausgefallen"], [])
        maya = next(p for p in d["personen"] if p["name"] == "Kontakt B")
        self.assertEqual(maya["quellen"], ["Wissen", "Familie"])  # „maya“ aus der Familie ist dieselbe Person
        mama = next(p for p in d["personen"] if p["name"] == "Kontakt P")
        self.assertEqual(mama["quellen"], ["Familie", "WhatsApp"])

    def test_angelegte_personen_stehen_nicht_mehr_zur_auswahl(self):
        register.anlegen("Kontakt B", snapchat="maya_x")
        namen = [p["name"] for p in lauf(bekannt.liste())["personen"]]
        self.assertNotIn("Kontakt B", namen)
        self.assertIn("Kontakt P", namen)

    def test_ausfall_einer_quelle_kippt_die_liste_nicht(self):
        with mock.patch.object(bekannt.wissensgraph, "uebersicht", mock.AsyncMock(side_effect=RuntimeError("x"))), \
             mock.patch.object(bekannt.personendienst, "personen", mock.AsyncMock(side_effect=RuntimeError("y"))):
            d = lauf(bekannt.liste())
        self.assertEqual(sorted(d["ausgefallen"]), ["Familie", "Wissen"])
        self.assertIn("Kontakt F", [p["name"] for p in d["personen"]])

    def test_nicht_erreichbarer_graph_und_ausgeschaltete_konnektoren_zaehlen_ehrlich(self):
        self.wissen["erreichbar"] = False
        with mock.patch.object(bekannt.konnektoren, "benutzbar", return_value=False):
            d = lauf(bekannt.liste())
        self.assertEqual(d["ausgefallen"], ["Wissen"])  # Familie ist aus, nicht ausgefallen
        self.assertNotIn("Familie", [q for p in d["personen"] for q in p["quellen"]])

    def test_namen_werden_bereinigt_und_leere_ignoriert(self):
        with mock.patch.object(bekannt, "_bild_namen", return_value=["Lehrkraft​ 1", "", "   ", "x" * 500]):
            namen = [p["name"] for p in lauf(bekannt.liste())["personen"]]
        self.assertIn("Lehrkraft 1", namen)
        self.assertTrue(all(0 < len(n) <= register.NAME_MAX for n in namen))


if __name__ == "__main__":
    unittest.main()
