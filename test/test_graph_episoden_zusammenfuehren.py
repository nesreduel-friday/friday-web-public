"""Zusammenführen doppelter Episoden: die reine Umhänge-Regel (das Skript selbst läuft im Graphiti-Image)."""
import importlib.util
import pathlib
import unittest

SKRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "graph_episoden_zusammenfuehren.py"


@unittest.skipUnless(SKRIPT.parent.exists(), "nur auf dem Host")
class Regel(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("zf", SKRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)

    def test_duplikate_werden_zur_behaltenen_episode(self):
        self.assertEqual(self.m.neue_quellen(["d1", "x", "d2"], {"d1", "d2"}, "k"), ["k", "x"])

    def test_behaltene_episode_wird_nicht_doppelt_eingetragen(self):
        self.assertEqual(self.m.neue_quellen(["k", "d1"], {"d1"}, "k"), ["k"])

    def test_ohne_duplikate_bleibt_alles(self):
        self.assertEqual(self.m.neue_quellen(["a", "b"], {"d1"}, "k"), ["a", "b"])

    def test_leere_oder_fehlende_liste(self):
        self.assertEqual(self.m.neue_quellen(None, {"d1"}, "k"), [])
        self.assertEqual(self.m.neue_quellen([], {"d1"}, "k"), [])

    def test_skript_ruft_nie_graph_delete_oder_delete_episode(self):
        text = SKRIPT.read_text(encoding="utf-8")
        self.assertNotIn("GRAPH.DELETE", text)
        self.assertNotIn("delete_episode", text.replace("`delete_episode`", ""))


if __name__ == "__main__":
    unittest.main()
