"""Graph-Gruppen: nur geprüfte Gruppen, Suche nie über Gruppengrenzen, Löschen nur der genannten Episode."""
import asyncio
import os
import unittest
from unittest import mock

from kern.gedaechtnis import graph


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Pruefen(unittest.TestCase):
    def test_erlaubte_gruppen(self):
        for g in ("eike", "p1", "p123456", "friday-test"):
            self.assertEqual(graph.pruefe_gruppe(g), g)

    def test_alles_andere_wird_abgelehnt(self):
        for g in ("", "p", "P1", "p1234567", "p1,eike", "eike ", " eike", "../x", "eike\n", "p1;", None, ["eike"], 5):
            with self.assertRaises(ValueError, msg=repr(g)):
                graph.pruefe_gruppe(g)


class Aufrufe(Basis):
    def test_standard_ist_eikes_gruppe_und_aendert_nichts(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(return_value={"message": "queued"})) as a:
            lauf(graph.einspeisen("n", "Text", "b", "2026-09-29T10:00:00+00:00"))
            lauf(graph.suchen("frage"))
        self.assertEqual(a.await_args_list[0].args[1]["group_id"], "eike")
        self.assertEqual(a.await_args_list[1].args[1]["group_ids"], ["eike"])

    def test_einspeisen_in_personengruppe_mit_eigenen_anweisungen(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(return_value={"message": "queued"})) as a:
            lauf(graph.einspeisen("n", "Kontakt B: Hallo", "b", "2026-09-29T10:00:00+00:00",
                                  anweisungen=graph.anweisungen_fuer_person("Kontakt B"), gruppe="p1"))
        angaben = a.await_args.args[1]
        self.assertEqual(angaben["group_id"], "p1")
        self.assertIn("Kontakt B", angaben["custom_extraction_instructions"])
        self.assertIn("NICHT um Eike", angaben["custom_extraction_instructions"])

    def test_suche_einer_person_fragt_nie_eikes_gruppe(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(return_value={"facts": []})) as a:
            lauf(graph.suchen("Geburtstag", gruppe="p1"))
        self.assertEqual(a.await_args.args[1]["group_ids"], ["p1"])

    def test_ungueltige_gruppe_erreicht_graphiti_nie(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock()) as a:
            for aufruf in (lambda: graph.einspeisen("n", "t", "b", "z", gruppe="p1,eike"),
                           lambda: graph.suchen("f", gruppe="eike "),
                           lambda: graph.angekommen("n", None, gruppe="../x"),
                           lambda: graph.episode_loeschen("n", "x")):
                with self.assertRaises(ValueError):
                    lauf(aufruf())
        a.assert_not_called()

    def test_angekommen_sucht_in_der_genannten_gruppe(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(return_value={"episodes": [{"name": "g-1"}]})) as a:
            self.assertTrue(lauf(graph.angekommen("g-1", None, gruppe="p2")))
        self.assertEqual(a.await_args.args[1]["group_ids"], ["p2"])


class Loeschen(Basis):
    def test_loescht_nur_die_episode_mit_dem_namen_in_der_gruppe(self):
        antworten = [{"episodes": [{"name": "freigabe-p1-f1", "uuid": "u-1"}, {"name": "anderes", "uuid": "u-2"}]},
                     {"message": "deleted"}]
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(side_effect=antworten)) as a:
            self.assertIs(lauf(graph.episode_loeschen("freigabe-p1-f1", "p1")), True)
        self.assertEqual(a.await_args_list[0].args, ("get_episodes", {"group_ids": ["p1"], "max_episodes": 1000}))
        self.assertEqual(a.await_args_list[1].args, ("delete_episode", {"uuid": "u-1"}))
        self.assertEqual(a.await_count, 2)

    def test_noch_nicht_verarbeitet_gibt_none_und_loescht_nichts(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(return_value={"episodes": []})) as a:
            self.assertIsNone(lauf(graph.episode_loeschen("freigabe-p1-f1", "p1")))
        self.assertEqual(a.await_count, 1)

    def test_fehler_gibt_false(self):
        with mock.patch.object(graph, "_aufruf", mock.AsyncMock(side_effect=RuntimeError("weg"))):
            self.assertIs(lauf(graph.episode_loeschen("freigabe-p1-f1", "p1")), False)

    def test_ohne_graphiti_url_passiert_nichts(self):
        with mock.patch.dict(os.environ, {"GRAPHITI_URL": ""}), mock.patch.object(graph, "_aufruf", mock.AsyncMock()) as a:
            self.assertIs(lauf(graph.episode_loeschen("n", "p1")), False)
        a.assert_not_called()


if __name__ == "__main__":
    unittest.main()
