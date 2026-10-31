"""Die Startseite darf keine ID doppelt haben: getElementById nimmt still das erste Element."""
import re
import shutil
import subprocess
import unittest
from collections import Counter
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
INDEX = WEB / "index.html"


class Ids(unittest.TestCase):
    def test_keine_id_doppelt(self):
        ids = re.findall(r'\bid="([^"]+)"', INDEX.read_text(encoding="utf-8"))
        doppelt = sorted(i for i, n in Counter(ids).items() if n > 1)
        self.assertEqual(doppelt, [], "doppelte IDs in web/index.html")


@unittest.skipUnless(shutil.which("node"), "node fehlt")
class Skripte(unittest.TestCase):
    def test_jedes_skript_parst(self):
        # Ein Syntaxfehler lässt das ganze Skript still nicht laden (29.09.2026: Kreis tot).
        fehler = []
        for js in sorted(WEB.glob("*.js")):
            r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
            if r.returncode:
                fehler.append(f"{js.name}: {r.stderr.strip().splitlines()[0:4]}")
        self.assertEqual(fehler, [])


if __name__ == "__main__":
    unittest.main()
