"""Übernehmen gegen ein ECHTES Wegwerf-Git-Repo (nur lokale git-Aufrufe im Temp-Ordner; Docker und Health gemockt).

Nachgewiesen im Review (Task 8): `git add -f data/x` auf dem Branch + `merge --no-ff` überschreibt ignorierte Live-Dateien still,
ein Revert löscht sie. Deshalb sind `data/` und `.env*` hart gesperrt — dieser Test prüft das mit echtem git.

Braucht `git` (im Testcontainer `friday-web:local` gibt es keins → übersprungen). Auf dem Host:
    python3 -m unittest test.test_werkbank_uebernehmen_git -v
Keine Abhängigkeit außer der Standardbibliothek.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HELFER_DATEI = Path(__file__).resolve().parent.parent / "host-helfer" / "claude_helfer.py"
IMAGE = "sha256:" + "d" * 64


@unittest.skipUnless(shutil.which("git"), "git fehlt (Testcontainer) — auf dem Host laufen lassen")
class EchtesRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, daten, self.host = self.tmp / "repo", self.tmp / "daten", self.tmp / "host"
        self.repo.mkdir()
        daten.mkdir()
        # für die ganze Testdauer: daten_ordner() liest FRIDAY_DATEN erst beim Aufruf (sonst landet stand.json im Repo-data/)
        umgebung = mock.patch.dict(os.environ, {"FRIDAY_DATEN": str(daten), "FRIDAY_WERKBANK_ZUSTAND": str(self.host)})
        umgebung.start()
        self.addCleanup(umgebung.stop)
        spec = importlib.util.spec_from_file_location(f"claude_helfer_git_{id(self)}", HELFER_DATEI)
        self.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.h)
        self.assertEqual(self.h.daten_ordner(), daten)
        self.h.REPO = self.repo
        self.git("init", "-q", "-b", "main")
        (self.repo / ".gitignore").write_text("data/\n.env\n")
        (self.repo / "kern").mkdir()
        (self.repo / "kern" / "x.py").write_text("a\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "start")
        # die echten, ignorierten Live-Dateien
        (self.repo / "data").mkdir()
        (self.repo / "data" / "geheim.json").write_text("ECHT")
        (self.repo / ".env").write_text("ECHT")
        self.docker = []
        echt = self.h._prozess

        def prozess(befehl, timeout, cwd=None, extra=None, bei_start=None):
            if befehl[0] == "docker":                  # nie echtes Docker
                self.docker.append(befehl)
                aus = IMAGE + "\n" if befehl[:2] == ["docker", "inspect"] else ""
                return subprocess.CompletedProcess(befehl, 0, aus, "")
            self.assertEqual(befehl[0], "git")
            self.assertEqual(befehl[befehl.index("-C") + 1], str(self.repo))   # nur das Wegwerf-Repo
            return echt(befehl, timeout, cwd=cwd, extra=extra, bei_start=bei_start)
        for ziel, wert in ((self.h, "_prozess"),):
            p = mock.patch.object(ziel, wert, side_effect=prozess)
            p.start()
            self.addCleanup(p.stop)
        g = mock.patch.object(self.h, "_gesund", return_value=True)
        g.start()
        self.addCleanup(g.stop)

    def git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              capture_output=True, text=True, check=True).stdout

    def branch(self, vid: str, dateien: dict, erzwungen: tuple = ()) -> str:
        self.git("checkout", "-q", "-b", f"werkbank/{vid}", "main")
        for name, inhalt in dateien.items():
            pfad = self.repo / name
            pfad.parent.mkdir(parents=True, exist_ok=True)
            pfad.write_text(inhalt)
        self.git("add", "-A")
        for name in erzwungen:                             # ignoriert in .gitignore — der Branch erzwingt sie trotzdem
            self.git("add", "-f", name)
        self.git("commit", "-qm", f"wb {vid}")
        self.git("checkout", "-q", "main")
        for name in erzwungen:                             # checkout main hat sie entfernt: Live-Stand herstellen
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text("ECHT")
        sha = self.git("rev-parse", f"werkbank/{vid}").strip()
        self.host.mkdir(parents=True, exist_ok=True)
        (self.host / f"ergebnis-{vid}.json").write_text(json.dumps({"vid": vid, "sha": sha}))
        stand = self.tmp / "daten" / "werkbank" / vid
        stand.mkdir(parents=True, exist_ok=True)
        (stand / "stand.json").write_text(json.dumps({"status": "bereit", "branch": f"werkbank/{vid}"}))
        return sha

    def uebernehmen(self, vid: str) -> dict:
        antwort = self.h._auftrag_ausfuehren({"id": "1", "aktion": "uebernehmen", "vorhaben": vid,
                                              "zeit": int(time.time())})
        if self.h._werkbank_faden is not None:
            self.h._werkbank_faden.join(30)
        return antwort

    def test_erzwungene_daten_und_env_auf_dem_branch_werden_abgelehnt(self):
        vorher = self.git("rev-parse", "HEAD").strip()
        for vid, name in (("1", "data/geheim.json"), ("2", ".env")):
            with self.subTest(name=name):
                self.branch(vid, {"kern/x.py": f"b{vid}\n", name: "BOESE"}, erzwungen=(name,))
                self.assertIn(name, self.git("ls-tree", "-r", "--name-only", f"werkbank/{vid}"))
                antwort = self.uebernehmen(vid)
                self.assertFalse(antwort["ok"])
                self.assertIn(name, antwort["text"])
                self.assertEqual(self.git("rev-parse", "HEAD").strip(), vorher)        # kein Merge
                self.assertEqual((self.repo / "data" / "geheim.json").read_text(), "ECHT")
                self.assertEqual((self.repo / ".env").read_text(), "ECHT")
                self.assertEqual(self.docker, [])

    def test_gegenprobe_normaler_branch_wird_gemergt(self):
        sha = self.branch("3", {"kern/x.py": "neu\n"})
        antwort = self.uebernehmen("3")
        self.assertTrue(antwort["ok"], antwort)
        self.assertEqual(self.git("rev-parse", "HEAD^2").strip(), sha)
        self.assertEqual((self.repo / "kern" / "x.py").read_text(), "neu\n")
        self.assertEqual((self.repo / "data" / "geheim.json").read_text(), "ECHT")
        self.assertEqual((self.repo / ".env").read_text(), "ECHT")

    def test_konflikt_in_docs_le_hren_wird_mit_beiden_eintraegen_vereinigt(self):
        lehren = self.repo / "docs" / "LEHREN.md"
        lehren.parent.mkdir()
        lehren.write_text("alt\n\n## alt\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "Lehren anlegen")
        self.branch("10", {"docs/LEHREN.md": "alt\n\n## alt\n\n## Branch-Eintrag\n"})
        lehren.write_text("alt\n\n## alt\n\n## Main-Eintrag\n", encoding="utf-8")
        self.git("commit", "-qam", "Lehren auf main")
        antwort = self.uebernehmen("10")
        self.assertTrue(antwort["ok"], antwort)
        inhalt = lehren.read_text(encoding="utf-8")
        self.assertIn("## Main-Eintrag", inhalt)
        self.assertIn("## Branch-Eintrag", inhalt)
        for marker in ("<<<<<<<", "=======", ">>>>>>>"):
            self.assertNotIn(marker, inhalt)

    def test_branch_der_sich_nach_laufende_bewegt_wird_abgelehnt(self):
        self.branch("4", {"kern/x.py": "geprueft\n"})
        self.git("checkout", "-q", "werkbank/4")
        (self.repo / "kern" / "x.py").write_text("nachgeschoben\n")
        self.git("commit", "-qam", "danach")
        self.git("checkout", "-q", "main")
        vorher = self.git("rev-parse", "HEAD").strip()
        antwort = self.uebernehmen("4")
        self.assertFalse(antwort["ok"])
        self.assertIn("bewegt", antwort["text"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), vorher)

    def test_compose_override_im_wurzelordner_wird_abgelehnt(self):
        self.branch("5", {"compose.override.yaml": "services:\n  friday:\n    privileged: true\n"})
        vorher = self.git("rev-parse", "HEAD").strip()
        self.assertFalse(self.uebernehmen("5")["ok"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), vorher)
        self.assertFalse((self.repo / "compose.override.yaml").exists())

    def _ergebnis(self, vid: str) -> None:
        sha = self.git("rev-parse", f"werkbank/{vid}").strip()
        self.host.mkdir(parents=True, exist_ok=True)
        (self.host / f"ergebnis-{vid}.json").write_text(json.dumps({"vid": vid, "sha": sha}))
        stand = self.tmp / "daten" / "werkbank" / vid
        stand.mkdir(parents=True, exist_ok=True)
        (stand / "stand.json").write_text(json.dumps({"status": "bereit", "branch": f"werkbank/{vid}"}))

    def test_umbenennung_auf_main_in_geschuetzten_pfad_wird_abgelehnt(self):
        """main verschiebt kern/tools/a.sh → scripts/a.sh bzw. kern/lib/ → host-helfer/; der Branch ändert die alten Pfade
        (in der Positivliste). Die Vorprüfung (Branch-Diff ohne Umbenennungen) sieht nichts Gesperrtes — der Merge darf
        trotzdem nicht durch."""
        for vid, (alt, neu_ort) in (("6", ("kern/tools/a.sh", "scripts/a.sh")),
                                    ("7", ("kern/lib/h.py", "host-helfer/h.py"))):
            with self.subTest(alt=alt):
                (self.repo / alt).parent.mkdir(parents=True, exist_ok=True)
                (self.repo / alt).write_text("echo alt\n" * 20)
                self.git("add", "-A")
                self.git("commit", "-qm", f"{alt} anlegen")
                self.git("checkout", "-q", "-b", f"werkbank/{vid}")
                (self.repo / alt).write_text("echo alt\n" * 20 + "echo BOESE\n")
                self.git("commit", "-qam", "Branch ändert den alten Pfad")
                self.git("checkout", "-q", "main")
                if vid == "7":                                     # ganzer Ordner kern/lib/ → host-helfer/
                    self.git("mv", "kern/lib", "host-helfer")
                else:
                    (self.repo / neu_ort).parent.mkdir(parents=True, exist_ok=True)
                    self.git("mv", alt, neu_ort)
                self.git("commit", "-qm", "auf main verschoben")
                self._ergebnis(vid)
                vorher = self.git("rev-parse", "HEAD").strip()
                self.uebernehmen(vid)
                stand = json.loads((self.tmp / "daten" / "werkbank" / vid / "stand.json").read_text())
                self.assertEqual(stand["uebernahme"], "fehler", stand)
                self.assertIn("von Hand", stand["uebernahme_text"])
                self.assertEqual(self.git("rev-parse", "HEAD").strip(), vorher)            # main unverändert
                self.assertFalse((self.repo / ".git" / "MERGE_HEAD").exists())             # kein Merge-Zustand
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.assertNotIn("BOESE", (self.repo / neu_ort).read_text())
                self.assertEqual(self.docker[-1][:2], ["docker", "tag"])                   # kein Build

    def test_umbenennung_wird_auch_ohne_no_renames_im_ergebnis_gefangen(self):
        """Gegenprobe der Nachprüfung allein: Merge MIT Umbenennungserkennung (ohne -X no-renames)."""
        (self.repo / "kern" / "tools").mkdir(parents=True)
        (self.repo / "kern" / "tools" / "a.sh").write_text("echo alt\n" * 20)
        self.git("add", "-A")
        self.git("commit", "-qm", "kern/tools/a.sh")
        self.git("checkout", "-q", "-b", "werkbank/8")
        (self.repo / "kern" / "tools" / "a.sh").write_text("echo alt\n" * 20 + "echo BOESE\n")
        self.git("commit", "-qam", "Branch")
        self.git("checkout", "-q", "main")
        (self.repo / "scripts").mkdir()
        self.git("mv", "kern/tools/a.sh", "scripts/a.sh")
        self.git("commit", "-qm", "verschoben")
        self._ergebnis("8")
        vorher = self.git("rev-parse", "HEAD").strip()
        echt = self.h._prozess

        def ohne_x(befehl, timeout, **kw):
            if "merge" in befehl and "-X" in befehl:
                i = befehl.index("-X")
                befehl = befehl[:i] + befehl[i + 2:]
            return echt(befehl, timeout, **kw)
        with mock.patch.object(self.h, "_prozess", side_effect=ohne_x):
            self.uebernehmen("8")
        stand = json.loads((self.tmp / "daten" / "werkbank" / "8" / "stand.json").read_text())
        self.assertEqual(stand["uebernahme"], "fehler", stand)
        self.assertIn("scripts/a.sh", stand["uebernahme_text"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), vorher)
        self.assertFalse((self.repo / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertNotIn("BOESE", (self.repo / "scripts" / "a.sh").read_text())

    def test_nicht_ascii_pfad_roh_und_gemergt(self):
        self.branch("9", {"kern/grüße ä.py": "x = 1\n"})
        roh = self.git("diff", "--raw", "-z", "--no-renames", "main...werkbank/9")
        self.assertEqual(self.h._merge_aenderungen(roh)[0][1], "kern/grüße ä.py")
        self.assertTrue(self.uebernehmen("9")["ok"])
        self.assertTrue((self.repo / "kern" / "grüße ä.py").exists())

    def test_positivliste_mit_echtem_git(self):
        """Nur kern/, web/, test/, eval/szenarien/, docs/ — alles andere (Agenten-Dateien, Abhängigkeiten, andere Stacks,
        Dateien ganz oben, ausführbare Hilfsskripte unter test/) muss Eike von Hand mergen."""
        abgelehnt = ("CLAUDE.md", "requirements.txt", "minecraft-mod/x", "studio/x", "snapmem/x", "pc-helfer/x",
                     "neu.py", "docs/AGENTS.md", "kern/claude.local.md", "web/package.json", "test/hilfe.sh",
                     "KERN/x.py", "Kern/x.py", "Web/x.js", "Docs/x.md")
        for i, name in enumerate(abgelehnt):
            vid = str(20 + i)
            with self.subTest(name=name):
                vorher = self.git("rev-parse", "HEAD").strip()
                self.branch(vid, {"kern/x.py": f"b{vid}\n", name: "BOESE"})
                antwort = self.uebernehmen(vid)
                self.assertFalse(antwort["ok"], antwort)
                self.assertIn(name, antwort["text"])
                self.assertIn("von Hand", antwort["text"])
                self.assertEqual(self.git("rev-parse", "HEAD").strip(), vorher)
                self.assertEqual(self.docker, [])
        # ausführbar unter test/
        vorher = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "-b", "werkbank/40", "main")
        (self.repo / "test").mkdir(exist_ok=True)
        (self.repo / "test" / "test_y.py").write_text("#!/bin/sh\n")
        os.chmod(self.repo / "test" / "test_y.py", 0o755)
        self.git("add", "-A")
        self.git("commit", "-qm", "wb 40")
        self.git("checkout", "-q", "main")
        sha = self.git("rev-parse", "werkbank/40").strip()
        (self.host / "ergebnis-40.json").write_text(json.dumps({"vid": "40", "sha": sha}))
        stand = self.tmp / "daten" / "werkbank" / "40"
        stand.mkdir(parents=True, exist_ok=True)
        (stand / "stand.json").write_text(json.dumps({"status": "bereit", "branch": "werkbank/40"}))
        self.assertFalse(self.uebernehmen("40")["ok"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), vorher)
        # Gegenprobe: nur erlaubte Bereiche
        self.branch("41", {"kern/x.py": "neu\n", "web/x.js": "1\n", "docs/x.md": "d\n", "test/test_x.py": "t\n",
                           "eval/szenarien/a.json": "[]\n"})
        antwort = self.uebernehmen("41")
        self.assertTrue(antwort["ok"], antwort)
        self.assertTrue((self.repo / "docs" / "x.md").exists())


if __name__ == "__main__":
    unittest.main()
