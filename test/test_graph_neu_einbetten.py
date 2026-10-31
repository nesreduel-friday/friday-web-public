"""Skript, das die Vektoren eines Graphiti-Graphen lokal neu rechnet — reine Funktionen und Sicherheitsnetz."""
import importlib.util
import pathlib
import unittest

SKRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "graph_neu_einbetten.py"


def _laden():
    spec = importlib.util.spec_from_file_location("graph_neu_einbetten", SKRIPT)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


# scripts/ ist nicht im Container-Abbild (Dockerfile) — auf dem Host laufen lassen:
#   python3 -m unittest test.test_graph_neu_einbetten
@unittest.skipUnless(SKRIPT.parent.exists(), "nur auf dem Host")
class Funktionen(unittest.TestCase):
    def setUp(self):
        self.m = _laden()

    def test_zeilenumbrueche_werden_leerzeichen(self):
        self.assertEqual(self.m.text_fuer_vektor("Ben\nund Lea"), "Ben und Lea")

    def test_leer_wird_none(self):
        self.assertIsNone(self.m.text_fuer_vektor("   "))
        self.assertIsNone(self.m.text_fuer_vektor(None))

    def test_stuecke(self):
        self.assertEqual(list(self.m.in_stuecken([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])
        self.assertEqual(list(self.m.in_stuecken([], 3)), [])

    def test_vektor_pruefen_erkennt_falsche_dimension_und_nullen(self):
        self.m.vektor_pruefen([0.1] * 384, 384)
        for schlecht in ([0.1] * 768, [], [0.0] * 384, None):
            with self.assertRaises(ValueError):
                self.m.vektor_pruefen(schlecht, 384)

    def test_skript_loescht_nie_einen_graphen(self):
        # Sicherheitsnetz zur Regel „nichts Destruktives auf Live-Daten“.
        self.assertNotIn("GRAPH.DELETE", SKRIPT.read_text(encoding="utf-8"))
        self.assertNotIn(".delete(", SKRIPT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
