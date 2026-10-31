"""Die Container-Doku — wacht darüber, dass der Bestand vollständig und ehrlich bleibt.

Am 29.10.2026 gewünscht: eine ausführliche Dokumentation aller vorhandenen Container und ihrer
Steuerung, damit später darauf zugegriffen werden kann. Die lesbare Fassung ist `docs/CONTAINER.md`;
der feste Bestand steht maschinenlesbar in `kern/dienste/container_doku.py` und geht über
`GET /api/hphome/doku` hinaus.

Geprüft wird gegen das, was sich unabhängig ändern kann:
- `docker-compose.yml` (jeder Dienst mit `container_name` muss dokumentiert sein),
- `hphome.DIENSTE` (jeder dort bekannte Dienst ebenso),
- `host-helfer/werkbank_docker.py` (die Sperre für Immich und Fridays eigenen Stack),
- `hphome.NICHT_STOPPEN` (Friday darf in der App nicht gestoppt werden).

Die Markdown-Datei liegt nicht im Test-Container (`scripts/test-isoliert.sh` bindet `docs/` nicht ein);
dieser Teil läuft nur auf dem Host und wird dort übersprungen, wo `docs/` fehlt — so wie
`test_graph_embedder`.
"""

import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from kern.dienste import container_doku, hphome
from kern.main import app

WURZEL = Path(__file__).resolve().parent.parent
COMPOSE = WURZEL / "docker-compose.yml"
DOKU = WURZEL / "docs" / "CONTAINER.md"
WERKBANK_DOCKER = WURZEL / "host-helfer" / "werkbank_docker.py"

# Was ein Eintrag mindestens sagt. Absichtlich nur Textfelder: Der Test prüft Vollständigkeit,
# nicht den Geschmack der Formulierung.
PFLICHTFELDER = ("name", "titel", "stack", "zweck", "netz", "daten", "wege", "werkbank_gesperrt",
                 "stoppbar", "besonderheit")


def _compose_containernamen() -> set[str]:
    """Alle `container_name:` aus docker-compose.yml — ohne YAML-Abhängigkeit."""
    text = COMPOSE.read_text(encoding="utf-8")
    namen: set[str] = set()
    for zeile in text.splitlines():
        m = re.match(r"^    container_name:\s*([A-Za-z0-9_.-]+)\s*$", zeile)
        if m:
            namen.add(m.group(1))
    return namen


def _werkbank_docker():
    """Das Skript aus dem Helfer-Ordner laden, ohne den Importpfad dauerhaft zu verbiegen."""
    spec = importlib.util.spec_from_file_location("werkbank_docker_fuer_test", WERKBANK_DOCKER)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


class Bestand(unittest.TestCase):
    def setUp(self):
        self.eintraege = list(container_doku.CONTAINER)
        self.namen = [c["name"] for c in self.eintraege]

    def test_keine_doppelten_namen(self):
        self.assertEqual(len(self.namen), len(set(self.namen)))

    def test_jeder_compose_dienst_ist_dokumentiert(self):
        fehlend = sorted(_compose_containernamen() - set(self.namen))
        self.assertEqual(fehlend, [], "Diese Container aus docker-compose.yml fehlen in container_doku.py "
                                      "(und damit in docs/CONTAINER.md).")

    def test_jeder_bekannte_hphome_dienst_ist_dokumentiert(self):
        fehlend = sorted(set(hphome.DIENSTE) - set(self.namen))
        self.assertEqual(fehlend, [], "Diese Dienste aus hphome.DIENSTE fehlen in container_doku.py.")

    def test_personenstandort_ist_dokumentiert(self):
        # Eigener Stack, aber Friday liest ihn und der Werkbank-Worker darf ihn steuern.
        self.assertIn("friday-personenstandort", self.namen)

    def test_jeder_eintrag_sagt_das_noetige(self):
        for c in self.eintraege:
            with self.subTest(container=c.get("name")):
                self.assertEqual(set(c), set(PFLICHTFELDER))
                for feld in ("name", "titel", "stack", "zweck", "netz", "daten", "besonderheit"):
                    self.assertIsInstance(c[feld], str)
                    self.assertTrue(c[feld].strip(), f"{c['name']}: {feld} ist leer")
                self.assertIsInstance(c["wege"], tuple)
                self.assertTrue(c["wege"], f"{c['name']}: kein Steuerweg genannt")
                self.assertTrue(set(c["wege"]) <= set(container_doku.WEGE),
                                f"{c['name']}: unbekannter Weg {set(c['wege']) - set(container_doku.WEGE)}")
                self.assertIsInstance(c["werkbank_gesperrt"], bool)
                self.assertIsInstance(c["stoppbar"], bool)

    def test_jeder_weg_ist_erklaert(self):
        for weg, text in container_doku.WEGE.items():
            with self.subTest(weg=weg):
                self.assertTrue(text.strip())
        benutzt = {w for c in self.eintraege for w in c["wege"]}
        self.assertEqual(benutzt, set(container_doku.WEGE), "Ein erklärter Weg wird von keinem Container benutzt "
                                                            "oder ein benutzter Weg ist nicht erklärt.")

    def test_friday_ist_der_einzige_nicht_stoppbare(self):
        nicht_stoppbar = {c["name"] for c in self.eintraege if not c["stoppbar"]}
        self.assertEqual(nicht_stoppbar, set(hphome.NICHT_STOPPEN))

    def test_werkbank_sperre_stimmt_mit_dem_skript(self):
        skript = _werkbank_docker()
        self.assertEqual(skript.GESPERRT_PROJEKTE, {"immich", "friday-web"})
        # Die Doku sagt je Container dasselbe wie die Prüfung im Skript.
        for c in self.eintraege:
            with self.subTest(container=c["name"]):
                gesperrt = c["stack"] in skript.GESPERRT_PROJEKTE or "immich" in c["name"].lower()
                self.assertEqual(c["werkbank_gesperrt"], gesperrt)
        self.assertTrue(skript._gesperrt("friday", "friday-web"))
        self.assertTrue(skript._gesperrt("friday-werkbank-sandbox", "friday-web"))
        self.assertTrue(skript._gesperrt("immich_server", "immich"))
        self.assertFalse(skript._gesperrt("jellyfin", "medienserver"))
        # Eigener Stack: nicht Teil von friday-web, also für den Worker erlaubt.
        self.assertFalse(skript._gesperrt("friday-personenstandort", "friday-personenstandort"))

    def test_namen_und_eintrag_funktionieren(self):
        self.assertEqual(container_doku.namen(), self.namen)
        self.assertEqual(container_doku.eintrag("friday")["name"], "friday")
        self.assertIsNone(container_doku.eintrag("gibtesnicht"))


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def test_doku_ist_lesbar(self):
        c = TestClient(app)
        r = c.get("/api/hphome/doku")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["stand"], container_doku.STAND)
        self.assertEqual(d["wege"], container_doku.WEGE)
        self.assertEqual([x["name"] for x in d["container"]], container_doku.namen())

    def test_doku_aendert_nichts(self):
        # Nur lesen: Kein POST, also auch kein Auftrag an den Helfer.
        self.assertEqual(TestClient(app).post("/api/hphome/doku").status_code, 405)


@unittest.skipUnless(DOKU.is_file(), "docs/ ist im Test-Container nicht eingebunden (nur auf dem Host)")
class Markdown(unittest.TestCase):
    """Die ausführliche Fassung muss denselben Bestand nennen wie der Code."""

    @classmethod
    def setUpClass(cls):
        cls.text = DOKU.read_text(encoding="utf-8")

    def test_jeder_container_hat_einen_abschnitt(self):
        for c in container_doku.CONTAINER:
            with self.subTest(container=c["name"]):
                self.assertRegex(self.text, rf"(?m)^### `{re.escape(c['name'])}`", msg=(
                    f"Abschnitt '### `{c['name']}`' fehlt in docs/CONTAINER.md."))

    def test_die_steuerwege_stehen_drin(self):
        for stelle in ("/api/hphome", "/api/hphome/container/", "/api/hphome/doku",
                       "host-helfer/hphome_helfer.py", "host-helfer/werkbank_docker.py",
                       "docker compose", "NICHT_STOPPEN"):
            with self.subTest(stelle=stelle):
                self.assertIn(stelle, self.text)

    def test_keine_geheimnisse_in_der_doku(self):
        # Die Doku erklärt, wo Schlüssel liegen — sie nennt sie nicht. Grobe Formprüfungen reichen hier:
        # echte Schlüssel sind lang und zufällig, ein Platzhalter wie WAHA_SCHLUESSEL ist es nicht.
        self.assertNotRegex(self.text, r"\bAIza[0-9A-Za-z_-]{20,}")
        self.assertNotRegex(self.text, r"\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,})")
        self.assertNotIn("-----BEGIN", self.text)


if __name__ == "__main__":
    unittest.main()
