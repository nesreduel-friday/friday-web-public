"""Lokaler Embedder für den Wissensgraphen: Compose-Verdrahtung und Standardwerte."""
import os
import pathlib
import re
import unittest
from unittest import mock

WURZEL = pathlib.Path(__file__).resolve().parent.parent
# Statische Dateien (docker-compose.yml, graphiti/) sind nicht im Container-Abbild — diese Tests laufen auf dem Host
# (`python3 -m unittest test.test_graph_embedder`), im Container werden sie übersprungen (wie in test_graph_muse).
AUF_DEM_HOST = (WURZEL / "docker-compose.yml").exists()


def _dienst(compose: str, name: str) -> str:
    """Textblock eines Compose-Dienstes (bis zum nächsten Dienst auf Einrückung 2)."""
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", compose, re.S | re.M)
    return m.group(1) if m else ""


@unittest.skipUnless(AUF_DEM_HOST, "nur auf dem Host")
class Compose(unittest.TestCase):
    def setUp(self):
        self.compose = (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")

    def test_embedder_hat_kein_port_nach_aussen(self):
        d = _dienst(self.compose, "embedder")
        self.assertTrue(d, "Dienst embedder fehlt")
        self.assertNotIn("ports:", d)

    def test_embedder_tag_ist_festgeschrieben(self):
        d = _dienst(self.compose, "embedder")
        self.assertRegex(d, r"image: ghcr\.io/huggingface/text-embeddings-inference:cpu-[\d.]+")
        self.assertNotIn(":latest", d)

    def test_friday_wartet_nicht_auf_embedder(self):
        # Wie beim Graphen insgesamt: Beiwerk, darf Friday nie am Start hindern.
        self.assertNotIn("embedder", _dienst(self.compose, "friday").split("depends_on:")[-1]
                         if "depends_on:" in _dienst(self.compose, "friday") else "")


class Schalter(unittest.TestCase):
    def test_standard_ist_gemini(self):
        from kern import config
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EMBEDDER_PROVIDER_GRAPHITI", None)
            self.assertEqual(config.graph_embedder(), "gemini")

    def test_nur_exakt_openai_schaltet_um(self):
        from kern import config
        for wert, erwartet in (("openai", "lokal"), (" OPENAI ", "lokal"), ("", "gemini"), ("ja", "gemini"), ("lokal", "gemini")):
            with mock.patch.dict(os.environ, {"EMBEDDER_PROVIDER_GRAPHITI": wert}):
                self.assertEqual(config.graph_embedder(), erwartet, wert)


@unittest.skipUnless(AUF_DEM_HOST, "nur auf dem Host")
class GraphitiConfig(unittest.TestCase):
    def test_config_hat_openai_zweig_mit_lokaler_adresse(self):
        text = (WURZEL / "graphiti" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("provider: ${EMBEDDER_PROVIDER_GRAPHITI:gemini}", text)
        self.assertIn("http://embedder:8080/v1", text)

    def test_compose_reicht_den_schalter_durch_standard_gemini(self):
        d = _dienst((WURZEL / "docker-compose.yml").read_text(encoding="utf-8"), "graphiti")
        self.assertIn("EMBEDDER_PROVIDER_GRAPHITI=${EMBEDDER_PROVIDER_GRAPHITI:-gemini}", d)
        self.assertIn("EMBEDDER_MODEL=${EMBEDDER_MODEL:-gemini-embedding-001}", d)
        self.assertIn("EMBEDDER_DIMS=${EMBEDDER_DIMS:-768}", d)


@unittest.skipUnless(AUF_DEM_HOST, "nur auf dem Host")
class Drosselung(unittest.TestCase):
    def test_start_skript_drosselt_textaufrufe_und_faengt_das_minutenlimit(self):
        text = (WURZEL / "graphiti" / "friday_start.py").read_text(encoding="utf-8")
        self.assertIn("FRIDAY_GEMINI_AUFRUFE_PRO_MINUTE", text)
        self.assertIn("await _drosseln()", text)
        self.assertIn("PerMinute", text)
        # Embeddings laufen nicht durch die Drossel (lokal, kein Limit)
        self.assertIn('if art != "text":', text)


if __name__ == "__main__":
    unittest.main()
