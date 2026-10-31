"""Prüf-Worker im Helfer (28.10.2026): nur lesend, eigene Aktion `pruefen`, Backend-Tabelle für weitere Modelle.

Der Helfer läuft auf dem Host; der Test lädt ihn wie `test_werkbank_uebernehmen_git` und ersetzt nur die Prozessstarts.
    python3 -m unittest test.test_werkbank_pruefen_helfer -v
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
SITZUNG = "0b1f2a3c-4d5e-4f60-8a7b-9c0d1e2f3a4b"


def _lauf(ausgabe: str, code: int = 0):
    return subprocess.CompletedProcess([], code, ausgabe, "")


class PruefHelfer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "daten").mkdir()
        umgebung = mock.patch.dict(os.environ, {"FRIDAY_DATEN": str(self.tmp / "daten"),
                                                "FRIDAY_WERKBANK_ZUSTAND": str(self.tmp / "host")})
        umgebung.start()
        self.addCleanup(umgebung.stop)
        spec = importlib.util.spec_from_file_location(f"claude_helfer_pruef_{id(self)}", HELFER_DATEI)
        self.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.h)
        self.h.WURZEL = self.tmp / "baeume"

    def frage_ablegen(self, pid="5", text="Geht das?"):
        self.h._werkbank_schreiben(f"pruef-{pid}", "frage.md", text)

    def stand(self, pid="5"):
        fd = self.h._werkbank_fd(f"pruef-{pid}")
        try:
            return json.loads(self.h._datei_lesen(fd, "stand.json", 10_000))
        finally:
            os.close(fd)

    def antwort(self, pid="5"):
        fd = self.h._werkbank_fd(f"pruef-{pid}")
        try:
            return self.h._datei_lesen(fd, "antwort.md", 100_000)
        finally:
            os.close(fd)

    def prozess_attrappe(self, claude_ausgabe, calls):
        def _prozess(befehl, timeout, cwd=None, extra=None, bei_start=None):
            calls.append(list(befehl))
            if befehl[0] == "claude":
                return _lauf(claude_ausgabe)
            if befehl[0] == "git" and "add" in befehl:
                Path(befehl[-2]).mkdir(parents=True, exist_ok=True)       # wie `git worktree add --detach <baum> main`
            if befehl[0] == "git" and "remove" in befehl:
                shutil.rmtree(befehl[-1], True)
            return _lauf("")
        return _prozess

    # --- Befehl: nur lesen ---
    def test_befehl_nur_lesen(self):
        b = self.h.pruef_befehl("frage")
        erlaubt = b[b.index("--allowedTools") + 1].split(",")
        tuer = f"Bash({HELFER_DATEI.parent.resolve()}/werkbank_docker.py:*)"
        self.assertIn("Read(./**)", erlaubt)                         # Arbeitsverzeichnis wie beim Bau-Worker
        self.assertIn(tuer, erlaubt)
        for rein in ("WebFetch", "WebSearch", "Bash(ls:*)", "Bash(ps:*)"):
            self.assertIn(rein, erlaubt)
        self.assertTrue(any("friday-mc/mindcraft/bots/**" in e for e in erlaubt))
        # Bash nur für Übersichten ohne Dateiinhalt: kein cat/grep/find/curl/bash frei
        for nie in ("Bash", "Bash(cat:*)", "Bash(grep:*)", "Bash(find:*)", "Bash(curl:*)", "Bash(python3:*)", "Bash(sh:*)"):
            self.assertNotIn(nie, erlaubt)
        self.assertNotIn("Read(**)", erlaubt)
        self.assertEqual(b[b.index("--tools") + 1], "Read,Grep,Glob,Bash,WebFetch,WebSearch")
        verbote = b[b.index("--disallowedTools") + 1].split(",")
        for gesperrt in ("Bash(docker:*)", "Bash(sudo:*)", "Edit", "Write", "Read(**/data/**)", "Read(**/.env)", "Read(**/keys.json)",
                         "Read(**/live-token.txt)", "Bash(*data*)", "Bash(*>*)", "WebFetch(domain:localhost)"):
            self.assertIn(gesperrt, verbote)
        self.assertNotIn("WebFetch", verbote)
        self.assertNotIn("Bash", verbote)
        self.assertEqual(b[b.index("--model") + 1], "sonnet")
        self.assertEqual(b[-2:], ["--", "frage"])

    def test_befehl_resume_nur_mit_sitzung(self):
        self.assertNotIn("--resume", self.h.pruef_befehl("x"))
        b = self.h.pruef_befehl("x", SITZUNG)
        self.assertEqual(b[b.index("--resume") + 1], SITZUNG)

    def test_deepseek_gleicher_kaefig_anderes_modell(self):
        c, d = self.h.pruef_befehl("x"), self.h.pruef_befehl_deepseek("x")
        i, j = c.index("--model"), d.index("--model")
        self.assertEqual(d[j + 1], self.h.BAU_MODELLE["deepseek"])
        self.assertEqual(c[:i] + c[i + 2:], d[:j] + d[j + 2:])      # sonst identisch: Rechte, Verbote, Limits

    def test_deepseek_schluessel_nur_in_der_umgebung(self):
        schluessel = Path(tempfile.mkdtemp()) / "k.key"
        self.h.DEEPSEEK_SCHLUESSEL = schluessel
        with self.assertRaises(RuntimeError):                        # Datei fehlt
            self.h.deepseek_umgebung()
        schluessel.write_text("sk-geheim\n", encoding="utf-8")
        u = self.h.deepseek_umgebung()
        self.assertEqual(u["ANTHROPIC_API_KEY"], "sk-geheim")
        self.assertIn("deepseek", u["ANTHROPIC_BASE_URL"])
        self.assertNotIn("sk-geheim", " ".join(self.h.pruef_befehl_deepseek("x")))

    def test_fehlertext_nimmt_die_ursache_aus_stdout(self):
        def lauf(out, err=""):
            return subprocess.CompletedProcess([], 1, out, err)
        self.assertEqual(self.h._fehlertext(lauf("", "kaputt")), "kaputt")
        roh = json.dumps({"subtype": "error_max_turns", "result": "", "is_error": True})
        self.assertEqual(self.h._fehlertext(lauf(roh)), "error_max_turns")
        roh = json.dumps({"subtype": "success", "is_error": True, "api_error_status": 429, "result": "Rate limit"})
        self.assertEqual(self.h._fehlertext(lauf(roh)), "429 | Rate limit")
        self.assertIn("Schlüssel ausgeblendet", self.h._fehlertext(lauf(json.dumps({"result": "AIzaSyA1234567890123456789012345678901234"}))))
        self.assertEqual(self.h._fehlertext(lauf("irgendwas Rohes")), "irgendwas Rohes")
        self.assertEqual(self.h._fehlertext(lauf("")), "keine Meldung von Claude")

    def test_backend_tabelle(self):
        self.assertIn("claude", self.h.PRUEF_BACKENDS)
        self.assertIn("deepseek", self.h.PRUEF_BACKENDS)
        ok, text = self.h.pruefen("5", time.time(), False, "unbekannt")
        self.assertFalse(ok)
        self.assertIn("Backend", text)

    # --- Bau-Worker mit DeepSeek (Claude-CLI über DeepSeeks Anthropic-Zugang) ---
    def test_bau_befehl_modell_je_backend(self):
        for backend, modell in (("claude", "sonnet"), ("deepseek", "sonnet")):
            b = self.h.claude_befehl("x", backend)
            self.assertEqual(b[b.index("--model") + 1], modell)
        self.assertEqual(self.h.claude_befehl("x")[self.h.claude_befehl("x").index("--model") + 1], "sonnet")

    def test_deepseek_umgebung_nur_aus_der_datei(self):
        self.h.DEEPSEEK_SCHLUESSEL = self.tmp / "k.key"
        with self.assertRaises(RuntimeError):
            self.h.deepseek_umgebung()
        self.h.DEEPSEEK_SCHLUESSEL.write_text("sk-geheim\n")
        u = self.h.deepseek_umgebung()
        self.assertEqual(u["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(u["ANTHROPIC_API_KEY"], "sk-geheim")
        self.assertNotIn("sk-geheim", " ".join(self.h.claude_befehl("x", "deepseek")))

    def test_werkbank_ohne_schluessel_oder_mit_fremdem_backend_wird_abgelehnt(self):
        self.h.DEEPSEEK_SCHLUESSEL = self.tmp / "gibtsnicht.key"
        ok, text = self.h.werkbank("7", time.time(), False, "deepseek")
        self.assertFalse(ok)
        self.assertIn("Schlüssel", text)
        ok, text = self.h.werkbank("7", time.time(), False, "gemini")
        self.assertFalse(ok)
        self.assertIn("Backend", text)

    # --- Aktion ---
    def test_aktion_ungueltige_id(self):
        r = self.h._auftrag_ausfuehren({"aktion": "pruefen", "vorhaben": "7x", "zeit": time.time()})
        self.assertFalse(r["ok"])
        self.assertIn("Ungültige", r["text"])
        self.assertFalse(self.h._auftrag_ausfuehren({"aktion": "gibtsnicht"})["ok"])

    def test_veraltet_wird_abgelehnt(self):
        self.frage_ablegen()
        ok, text = self.h.pruefen("5", time.time() - 120, False, "claude")
        self.assertFalse(ok)
        self.assertIn("alt", text)

    def test_ohne_frage_abgelehnt(self):
        ok, text = self.h.pruefen("9", time.time(), False, "claude")
        self.assertFalse(ok)
        self.assertIn("Keine Frage", text)

    def test_zweite_pruefung_abgelehnt(self):
        self.frage_ablegen()
        self.h._pruef_laeuft = "4"
        ok, text = self.h.pruefen("5", time.time(), False, "claude")
        self.assertEqual((ok, text), (False, "Es läuft schon eine Prüfung."))

    # --- Lauf ---
    def test_lauf_schreibt_antwort_und_merkt_sitzung(self):
        self.frage_ablegen()
        calls = []
        roh = json.dumps({"result": "Geht. Datei kern/x.py. Schlüssel AIzaSyA1234567890123456789012345678901234", "session_id": SITZUNG})
        with mock.patch.object(self.h, "_prozess", self.prozess_attrappe(roh, calls)):
            self.h._pruef_lauf("5", False, "claude")
        stand = self.stand()
        self.assertEqual(stand["status"], "fertig")
        self.assertEqual(stand["nachfragen"], 0)
        self.assertNotIn("sitzung", stand)                       # Sitzungs-Id bleibt auf dem Host (Container ist nicht vertrauenswürdig)
        text = self.antwort()
        self.assertIn("Geht.", text)
        self.assertNotIn("AIzaSy", text)
        claude = [c for c in calls if c[0] == "claude"][0]
        self.assertNotIn("--resume", claude)
        self.assertIsNone(self.h._pruef_laeuft)

    def test_deepseek_lauf_gibt_dem_prozess_die_umgebung(self):
        self.frage_ablegen()
        extras = []
        schluessel = Path(tempfile.mkdtemp()) / "k.key"
        schluessel.write_text("sk-geheim", encoding="utf-8")
        self.h.DEEPSEEK_SCHLUESSEL = schluessel
        roh = json.dumps({"result": "Ja.", "session_id": SITZUNG})
        basis = self.prozess_attrappe(roh, [])

        def _prozess(befehl, timeout, cwd=None, extra=None, bei_start=None):
            if befehl[0] == "claude":
                extras.append((befehl, extra))
            return basis(befehl, timeout, cwd, extra, bei_start)
        with mock.patch.object(self.h, "_prozess", _prozess):
            self.h._pruef_lauf("5", False, "deepseek")
        self.assertEqual(self.stand()["status"], "fertig")
        befehl, extra = extras[0]
        self.assertEqual(extra["ANTHROPIC_API_KEY"], "sk-geheim")
        self.assertNotIn("sk-geheim", " ".join(befehl))

    def test_deepseek_ohne_schluessel_meldet_klar_statt_still(self):
        self.frage_ablegen()
        self.h.DEEPSEEK_SCHLUESSEL = Path(tempfile.mkdtemp()) / "fehlt.key"
        with mock.patch.object(self.h, "_prozess", self.prozess_attrappe("{}", [])):
            self.h._pruef_lauf("5", False, "deepseek")
        st = self.stand()
        self.assertEqual(st["status"], "fehler")
        self.assertIn("DeepSeek", st["fehler"])
        self.assertNotIn("sk-", st["fehler"])

    def test_nachfrage_setzt_sitzung_fort(self):
        self.frage_ablegen()
        calls = []
        roh = json.dumps({"result": "Ja.", "session_id": SITZUNG})
        with mock.patch.object(self.h, "_prozess", self.prozess_attrappe(roh, calls)):
            self.h._pruef_lauf("5", False, "claude")
            self.frage_ablegen(text="Und mit Y?")
            self.h._pruef_lauf("5", True, "claude")
        claudes = [c for c in calls if c[0] == "claude"]
        self.assertEqual(claudes[1][claudes[1].index("--resume") + 1], SITZUNG)
        self.assertEqual(self.stand()["nachfragen"], 1)

    def test_nachfrage_ohne_bekannte_sitzung_scheitert(self):
        self.frage_ablegen()
        calls = []
        with mock.patch.object(self.h, "_prozess", self.prozess_attrappe("{}", calls)):
            self.h._pruef_lauf("5", True, "claude")
        self.assertEqual(self.stand()["status"], "fehler")
        self.assertEqual([c for c in calls if c[0] == "claude"], [])

    def test_claude_fehler_wird_fehler(self):
        self.frage_ablegen()
        with mock.patch.object(self.h, "_prozess", lambda b, t, **k: _lauf("", 1)):
            self.h._pruef_lauf("5", False, "claude")
        self.assertEqual(self.stand()["status"], "fehler")
        self.assertIsNone(self.h._pruef_laeuft)

    def test_baum_wird_aufgeraeumt(self):
        self.frage_ablegen()
        calls = []
        with mock.patch.object(self.h, "_prozess", self.prozess_attrappe(json.dumps({"result": "ok"}), calls)):
            self.h._pruef_lauf("5", False, "claude")
        self.assertTrue(any(c[:1] == ["git"] and "remove" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
