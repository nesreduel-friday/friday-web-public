"""Tiefe Links: Jede Adresse, die eine Benachrichtigung oder Meldung trägt (`/#seite` oder `/#seite/reiter`),
muss in der App wirklich zu dieser Seite und diesem Reiter führen — sonst landet Eike auf der Übersicht."""

import re
import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
WEB = WURZEL / "web"
KERN = WURZEL / "kern"


def _seiten() -> list[str]:
    quelle = (WEB / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const SEITEN = \[([^\]]*)\]", quelle)
    return re.findall(r"'([^']+)'", m.group(1))


def _reiter(seite: str) -> set[str]:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    teil = html.split(f'<section id="{seite}"', 1)[1].split("</section>", 1)[0]
    return set(re.findall(r'data-reiter="([^"]+)"', teil))


def _links() -> list[tuple[str, str]]:
    gefunden = []
    for datei in KERN.rglob("*.py"):
        for m in re.finditer(r'"/#([a-z]+)(?:/([a-z]+))?"', datei.read_text(encoding="utf-8")):
            gefunden.append((str(datei.relative_to(WURZEL)), m.group(0).strip('"')))
    return gefunden


class TiefeLinks(unittest.TestCase):
    def test_jede_adresse_im_kern_fuehrt_zu_seite_und_reiter(self):
        seiten = _seiten()
        links = _links()
        self.assertTrue(links)
        for datei, url in links:
            seite, _, reiter = url[2:].partition("/")
            if seite == "sprechen":
                continue   # alter Alias auf die Übersicht
            with self.subTest(datei=datei, url=url):
                self.assertIn(seite, seiten)
                if reiter:
                    self.assertIn(reiter, _reiter(seite))

    def test_reiter_ohne_seite_ist_kein_ziel(self):
        # „/#hphome“ war ein Reiter von Extras, keine Seite: der Link landete auf der Übersicht.
        for datei, url in _links():
            with self.subTest(datei=datei, url=url):
                self.assertNotIn(url[2:], {"hphome", "kontakte", "garantie", "pc", "memories"})

    def test_kontakte_pushes_fuehren_in_den_reiter_kontakte(self):
        for name in ("personen/unbekannt.py", "personen/rueckfragen.py"):
            quelle = (KERN / name).read_text(encoding="utf-8")
            self.assertIn('"/#studio/kontakte"', quelle, name)

    def test_garantie_und_werkbank_und_nachdenken_haben_ihren_reiter(self):
        erwartet = {"werkbank/lauf.py": "/#studio/hphome", "garantie/verwaltung.py": "/#studio/garantie",
                    "werkzeuge/akte.py": "/#studio/garantie", "denken/hinweis.py": "/#gedaechtnis/gedanken"}
        for name, url in erwartet.items():
            self.assertIn(f'"{url}"', (KERN / name).read_text(encoding="utf-8"), name)

    def test_app_wertet_den_reiter_im_hash_aus(self):
        quelle = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("reiterOeffnen", quelle)
        self.assertIn("DOMContentLoaded", quelle)

    def test_pc_karte_fuehrt_zum_pc_reiter(self):
        quelle = (WEB / "uebersicht.js").read_text(encoding="utf-8")
        self.assertIn("#studio/pc", quelle)


if __name__ == "__main__":
    unittest.main()
