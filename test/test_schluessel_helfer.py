"""Schlüssel-Helfer (host-helfer/schluessel_helfer.py): gcloud-Aufrufe, ohne echtes gcloud."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HELFER_DATEI = Path(__file__).resolve().parent.parent / "host-helfer" / "schluessel_helfer.py"


def _helfer_laden(daten: str):
    with mock.patch.dict(os.environ, {"FRIDAY_DATEN": daten}):
        spec = importlib.util.spec_from_file_location(f"schluessel_helfer_{id(daten)}", HELFER_DATEI)
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
    return modul


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.daten = Path(self.ordner.name)
        p = mock.patch.dict(os.environ, {"FRIDAY_DATEN": str(self.daten)})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.ordner.cleanup)
        self.helfer = _helfer_laden(str(self.daten))
        self.aufrufe: list[list[str]] = []


class AnlegenTest(Basis):
    def test_erfolgslauf_ruft_alle_vier_schritte_in_reihenfolge(self):
        # "services" wird für zwei verschiedene Unterbefehle gebraucht (enable, api-keys) —
        # deshalb eine feste Antwortfolge statt einer Zuordnung nach Unterbefehl.
        antworten = iter([
            subprocess.CompletedProcess([], 0, "{}", ""),                                   # projects create
            subprocess.CompletedProcess([], 0, "", ""),                                     # services enable
            subprocess.CompletedProcess([], 0, json.dumps(
                {"response": {"name": "projects/123/locations/global/keys/abcd"}}), ""),    # api-keys create
            subprocess.CompletedProcess([], 0, json.dumps({"keyString": "AIzaNEU123"}), ""),  # get-key-string
        ])
        with mock.patch("subprocess.run", side_effect=lambda argv, **kw: (self.aufrufe.append(list(argv)),
                                                                            next(antworten))[1]):
            ergebnis = self.helfer.anlegen()
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(ergebnis["wert"], "AIzaNEU123")
        self.assertTrue(ergebnis["projekt_id"].startswith("friday-pool-"))
        befehle = [a[1] for a in self.aufrufe if a[0] == "gcloud"]
        self.assertEqual(befehle, ["projects", "services", "services", "services"])

    def test_fehlgeschlagenes_projekt_anlegen_bricht_sofort_ab(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
                [], 1, "", "ERROR: Quota exceeded for projects")):
            ergebnis = self.helfer.anlegen()
        self.assertFalse(ergebnis["ok"])
        self.assertIn("Quota", ergebnis["text"])

    def test_api_aktivieren_wird_wiederholt_und_gibt_dann_auf(self):
        aufrufe = {"n": 0}

        def antwort(argv, **kw):
            self.aufrufe.append(list(argv))
            if argv[1] == "projects":
                return subprocess.CompletedProcess(argv, 0, "{}", "")
            aufrufe["n"] += 1
            return subprocess.CompletedProcess(argv, 1, "", "service not ready")

        with mock.patch("subprocess.run", side_effect=antwort), mock.patch("time.sleep"):
            ergebnis = self.helfer.anlegen()
        self.assertFalse(ergebnis["ok"])
        self.assertEqual(aufrufe["n"], self.helfer.AKTIVIEREN_VERSUCHE)

    def test_unbekannte_aktion_tut_nichts(self):
        antwort = self.helfer._auftrag_ausfuehren({"id": "1", "aktion": "loeschen"})
        self.assertFalse(antwort["ok"])
        self.assertEqual(self.aufrufe, [])


class GraphitiUmschaltenTest(Basis):
    def _env_schreiben(self, inhalt: str) -> Path:
        pfad = self.daten / ".env"
        pfad.write_text(inhalt, encoding="utf-8")
        return pfad

    def test_ersetzt_vorhandene_zeile_und_startet_neu(self):
        pfad = self._env_schreiben("A=1\nGRAPH_GEMINI_API_KEY=alt\nB=2\n")
        with mock.patch.object(self.helfer, "_env_datei", return_value=pfad), \
             mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            ergebnis = self.helfer.graphiti_umschalten("neu")
        self.assertTrue(ergebnis["ok"], ergebnis)
        inhalt = pfad.read_text(encoding="utf-8")
        self.assertIn("GRAPH_GEMINI_API_KEY=neu", inhalt)
        self.assertNotIn("alt", inhalt)
        self.assertIn("A=1", inhalt)
        self.assertEqual(run.call_args.args[0], ["docker", "compose", "up", "-d", "graphiti"])

    def test_ergaenzt_fehlende_zeile(self):
        pfad = self._env_schreiben("A=1\n")
        with mock.patch.object(self.helfer, "_env_datei", return_value=pfad), \
             mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            ergebnis = self.helfer.graphiti_umschalten("neu")
        self.assertTrue(ergebnis["ok"])
        self.assertIn("GRAPH_GEMINI_API_KEY=neu", pfad.read_text(encoding="utf-8"))

    def test_fehlgeschlagener_neustart_meldet_den_grund(self):
        pfad = self._env_schreiben("GRAPH_GEMINI_API_KEY=alt\n")
        with mock.patch.object(self.helfer, "_env_datei", return_value=pfad), \
             mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "no such service")):
            ergebnis = self.helfer.graphiti_umschalten("neu")
        self.assertFalse(ergebnis["ok"])
        self.assertIn("no such service", ergebnis["text"])

    def test_auftrag_ruft_graphiti_umschalten_mit_dem_schluessel_auf(self):
        with mock.patch.object(self.helfer, "graphiti_umschalten", return_value={"ok": True, "text": "ok"}) as fn:
            antwort = self.helfer._auftrag_ausfuehren({"id": "1", "aktion": "graphiti_umschalten", "schluessel": "x"})
        fn.assert_called_once_with("x")
        self.assertTrue(antwort["ok"])

    def test_umschalten_setzt_nur_den_gemini_schluessel(self):
        pfad = self._env_schreiben("GRAPH_GEMINI_API_KEY=alt\nANDERES=bleibt\n")
        with mock.patch.object(self.helfer, "_env_datei", return_value=pfad), \
             mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            ergebnis = self.helfer.graphiti_umschalten("neu-key")
        self.assertTrue(ergebnis["ok"], ergebnis)
        inhalt = pfad.read_text(encoding="utf-8")
        self.assertIn("GRAPH_GEMINI_API_KEY=neu-key", inhalt)
        self.assertIn("ANDERES=bleibt", inhalt)
        self.assertNotIn("GRAPH_LLM_PROVIDER", inhalt)


class LaufTest(Basis):
    def test_auftrag_wird_ausgefuehrt_und_zustand_geschrieben(self):
        with mock.patch.object(self.helfer, "anlegen",
                                return_value={"ok": True, "text": "ok", "wert": "AIzaX", "projekt_id": "friday-pool-x"}):
            o = self.daten / "schluessel_auftraege"
            o.mkdir(parents=True)
            (o / "auftrag.json").write_text(json.dumps({"id": "abc", "aktion": "anlegen"}), encoding="utf-8")
            self.helfer.lauf(takt=0, runden=1)
        zustand = json.loads((o / "zustand.json").read_text(encoding="utf-8"))
        self.assertTrue(zustand["letzte_antwort"]["ok"])
        self.assertEqual(zustand["letzte_antwort"]["wert"], "AIzaX")
        self.assertFalse((o / "auftrag.json").exists())


if __name__ == "__main__":
    unittest.main()
