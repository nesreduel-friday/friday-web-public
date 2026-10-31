"""Datenschutz: Der Pre-Commit-Haken und seine Sperrliste dürfen nicht still verschwinden.

Der Test prüft nur Dateien und ihren Inhalt — kein Git, kein Netz. Damit wird die
Suite rot, sobald jemand den Haken, die Begriffsliste oder die .gitignore-Einträge
entfernt oder entschärft.
"""

import os
import stat
import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
HAKEN = WURZEL / ".githooks" / "pre-commit"
LISTE = WURZEL / ".githooks" / "verboten.txt"
IGNORE = WURZEL / ".gitignore"


class HakenVorhanden(unittest.TestCase):
    def test_haken_existiert_und_ist_ausfuehrbar(self):
        self.assertTrue(HAKEN.is_file(), ".githooks/pre-commit fehlt")
        self.assertTrue(HAKEN.stat().st_mode & stat.S_IXUSR, ".githooks/pre-commit ist nicht ausführbar")
        self.assertTrue(os.access(HAKEN, os.X_OK), ".githooks/pre-commit ist nicht ausführbar")

    def test_haken_prueft_nur_das_staging(self):
        """Nur gestagte Dateien, keine Historie — sonst wäre der Haken langsam."""
        text = HAKEN.read_text(encoding="utf-8")
        self.assertIn("git diff --cached --name-only", text)
        self.assertIn("--diff-filter=ACMR", text)
        self.assertNotIn("git log", text)

    def test_haken_kennt_alle_regeln(self):
        text = HAKEN.read_text(encoding="utf-8")
        for wort in ("Regel a", "Regel b", "Regel c", "Regel d", "Regel e", "Regel f",
                     "Telefon", "E-Mail", "Bild", "verboten.txt"):
            self.assertIn(wort, text, f"Regel fehlt im Haken: {wort}")

    def test_haken_hat_die_muster(self):
        text = HAKEN.read_text(encoding="utf-8")
        for muster in (r"\+49[0-9 /()-]{8,}",
                       r"0[1-9][0-9]{3,5}[ /-][0-9]{4,}",
                       r"\.tail[0-9a-z]+\.ts\.net",
                       r"100\.(6[4-9]|[7-9][0-9]",
                       "example",                       # Testadressen bleiben erlaubt
                       "204800"):                       # 200 KB
            self.assertIn(muster, text, f"Muster fehlt im Haken: {muster}")

    def test_haken_ist_fail_closed(self):
        """Fehlt die Begriffsliste, blockiert der Haken statt durchzuwinken."""
        text = HAKEN.read_text(encoding="utf-8")
        self.assertIn('if [ ! -s "$LISTE" ]', text)
        self.assertIn("Regel e kann nicht greifen", text)


class Sperrliste(unittest.TestCase):
    def begriffe(self):
        return [z.strip() for z in LISTE.read_text(encoding="utf-8").splitlines()
                if z.strip() and not z.strip().startswith("#")]

    def test_liste_ist_vorhanden_und_gefuellt(self):
        self.assertTrue(LISTE.is_file(), ".githooks/verboten.txt fehlt")
        self.assertGreaterEqual(len(self.begriffe()), 20,
                                "zu wenige Sperrbegriffe — Liste gekürzt?")

    def test_liste_hat_keine_doppelten(self):
        begriffe = [b.lower() for b in self.begriffe()]
        self.assertEqual(len(begriffe), len(set(begriffe)), "doppelte Begriffe in verboten.txt")

    def test_liste_enthaelt_keine_daten(self):
        """Die Liste ist ein Muster, keine Datenablage: keine Adressen, Nummern, IPs."""
        for begriff in self.begriffe():
            for verboten in ("@", "+49", "http", "100."):
                self.assertNotIn(verboten, begriff, f"verboten.txt enthält Daten statt Begriffen: {verboten}")


class Ignore(unittest.TestCase):
    def zeilen(self):
        return [z.strip() for z in IGNORE.read_text(encoding="utf-8").splitlines()]

    def test_laufzeitdaten_bleiben_ausgeschlossen(self):
        self.assertIn("data/", self.zeilen(), ".gitignore schließt data/ nicht mehr aus")

    def test_werkbank_logs_sind_ausgeschlossen(self):
        self.assertIn(".agent-loop/logs/", self.zeilen())

    def test_sicherheitsnetz_fuer_dateien(self):
        zeilen = self.zeilen()
        for eintrag in ("*.bundle", "*.bak", "snapmem/tests/daten/*.jpg"):
            self.assertIn(eintrag, zeilen, f".gitignore fehlt: {eintrag}")


if __name__ == "__main__":
    unittest.main()
