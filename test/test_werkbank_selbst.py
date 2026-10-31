"""Selbstentwicklung (29.10.2026): Hebel „selbst übernehmen“, Positivliste für den Diff, Grenzen — Helfer gemockt."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from kern.dienste import claude_sitzung
from kern.werkbank import automatik, selbst, speicher, starten, uebernehmen

MITTAG = datetime(2026, 10, 29, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
HELFER_DA = {"helfer": True, "sitzungen": [], "max": 3, "werkbank": {"laeuft": None}}


def diff(*dateien) -> str:
    """dateien: (pfad, zeilen, neu)"""
    t = []
    for pfad, zeilen, neu in dateien:
        t.append(f"diff --git a/{pfad} b/{pfad}")
        if neu:
            t.append("new file mode 100644")
        t += [f"--- a/{pfad}", f"+++ b/{pfad}", "@@ -1,1 +1,2 @@", *zeilen]
    return "\n".join(t) + "\n"


TOOL = ("kern/werkzeuge/minecraft.py", ["+def status():", "+    return 1"], True)
TEST = ("test/test_minecraft.py", ["+class T: pass"], True)
KATALOG = ("kern/werkzeuge/katalog.py", ["-_MODULE = [basis, chatverlauf]", "+_MODULE = [basis, chatverlauf, minecraft]"], False)


class DiffOk(unittest.TestCase):
    def test_neues_werkzeug_mit_test_und_registrierung_geht(self):
        self.assertEqual(selbst.diff_ok(diff(TOOL, TEST, KATALOG)), (True, ""))

    def test_docs_gehen(self):
        self.assertTrue(selbst.diff_ok(diff(("docs/LEHREN.md", ["+text"], False)))[0])

    def test_bestehendes_werkzeug_darf_sich_aendern(self):
        self.assertTrue(selbst.diff_ok(diff(("kern/werkzeuge/musik.py", ["-alt", "+neu"], False)))[0])

    def test_sicherheitsdateien_und_fremde_pfade_nicht(self):
        for pfad in ("kern/werkzeuge/rechte.py", "kern/werkzeuge/sendepruefung.py", "kern/main.py", "kern/config.py",
                     "web/chat.js", "host-helfer/claude_helfer.py", "docker-compose.yml", ".env", "AGENTS.md",
                     "kern/werkzeuge/../main.py", "kern/werkzeuge/unter/x.py", "data/x.py"):
            ok, grund = selbst.diff_ok(diff((pfad, ["+x"], False)))
            self.assertFalse(ok, pfad)
            self.assertTrue(grund, pfad)

    def test_katalog_nur_modulnamen(self):
        boese = ("kern/werkzeuge/katalog.py", ["-    if not _frei(m):", "+    if True:"], False)
        self.assertFalse(selbst.diff_ok(diff(boese))[0])
        self.assertFalse(selbst.diff_ok(diff(("kern/werkzeuge/katalog.py", ["+import os; os.system('x')"], False)))[0])
        self.assertFalse(selbst.diff_ok(diff(("kern/werkzeuge/katalog.py", ["+x"], True)))[0])

    def test_bestehende_tests_nur_erweitern(self):
        self.assertTrue(selbst.diff_ok(diff(("test/test_chat.py", ["+    def test_neu(self): pass"], False)))[0])
        self.assertFalse(selbst.diff_ok(diff(("test/test_chat.py", ["-    def test_alt(self): pass", "+    pass"], False)))[0])
        # Entschärfen durch Zusatzzeilen (30.10.2026): „+" allein genügte vorher, um einen Test stillzulegen.
        for zeile in ("+    @unittest.skip('kaputt')", "+        self.skipTest('egal')",
                      "+    @unittest.expectedFailure", "+    if False:"):
            with self.subTest(zeile):
                ok, grund = selbst.diff_ok(diff(("test/test_chat.py", [zeile], False)))
                self.assertFalse(ok)
                self.assertIn("entschärft", grund)
        # In einer NEUEN Testdatei ist das erlaubt — dort gibt es nichts zu entschärfen.
        self.assertTrue(selbst.diff_ok(diff(("test/test_neu.py", ["+    @unittest.skip('x')"], True)))[0])
        self.assertFalse(selbst.diff_ok(diff(("test/helfer.py", ["+x"], True)))[0])      # kein test_*.py
        self.assertFalse(selbst.diff_ok(diff(("test/daten/x.json", ["+x"], True)))[0])

    def test_loeschung_umbenennung_binaer_leer_gross(self):
        self.assertFalse(selbst.diff_ok("diff --git a/test/test_a.py b/test/test_a.py\ndeleted file mode 100644\n")[0])
        self.assertFalse(selbst.diff_ok("diff --git a/docs/a.md b/docs/b.md\nrename from docs/a.md\n")[0])
        self.assertFalse(selbst.diff_ok("diff --git a/docs/a.png b/docs/a.png\nBinary files a and b differ\n")[0])
        self.assertFalse(selbst.diff_ok("")[0])
        self.assertFalse(selbst.diff_ok(diff(("docs/a.md", ["+x"] * (selbst.ZEILEN_MAX + 1), False)))[0])
        self.assertFalse(selbst.diff_ok(diff(*[(f"docs/{n}.md", ["+x"], False) for n in range(selbst.DATEIEN_MAX + 1)]))[0])


class Basis(unittest.TestCase):
    def setUp(self):
        self.daten = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.daten, "FRIDAY_HINTERGRUND": "aus"})
        env.start()
        self.addCleanup(env.stop)
        u = mock.patch("kern.config.jetzt", side_effect=lambda: MITTAG)
        u.start()
        self.addCleanup(u.stop)
        self.fach = speicher.standard()
        for ziel, ersatz in ((claude_sitzung, ("zustand", mock.Mock(return_value=HELFER_DA))),
                             (claude_sitzung, ("uebernehmen_auftrag", mock.Mock(return_value={"ok": True}))),
                             (claude_sitzung, ("werkbank_auftrag", mock.Mock(return_value={"ok": True, "text": "x"})))):
            p = mock.patch.object(ziel, ersatz[0], ersatz[1])
            setattr(self, ersatz[0], p.start())
            self.addCleanup(p.stop)
        b = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        self.push = b.start()
        self.addCleanup(b.stop)

    def bereit(self, titel="Minecraft-Status fehlt", text=None, tests="ok", backend="claude", quelle="gespraech", fremd=False):
        vid = self.fach.anlegen(titel, "wunsch", quelle, fremd=fremd)["id"]
        self.fach.uebergang(vid, "vorgeschlagen", "laeuft", backend=backend)
        self.fach.uebergang(vid, "laeuft", "bereit", tests=tests, branch=f"werkbank/{vid}", diff_pfad=f"werkbank/{vid}/diff.patch")
        ordner = Path(self.daten) / "werkbank" / str(vid)
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "diff.patch").write_text(text if text is not None else diff(TOOL, TEST), encoding="utf-8")
        return vid


class Hebel(Basis):
    def test_standard_aus_und_nichts_wird_uebernommen(self):
        vid = self.bereit()
        self.assertFalse(selbst.eingeschaltet())
        self.assertIsNone(selbst.einmal(self.fach))
        self.assertEqual(self.fach.eintrag(vid)["status"], "bereit")

    def test_unbekannter_wert_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            selbst.einstellen("vielleicht")
        self.assertFalse(selbst.eingeschaltet())

    def test_hebel_nimmt_denkrunde_vorhaben_in_die_automatik(self):
        vid = self.fach.anlegen("Minecraft-Status fehlt", "wunsch", "denkrunde")["id"]
        automatik.einstellen(auto="an", backend="claude")
        self.assertIsNone(automatik.kandidat(self.fach, "claude")[0])
        selbst.einstellen("an")
        self.assertEqual(automatik.kandidat(self.fach, "claude")[0]["id"], vid)
        selbst.einstellen("aus")
        self.assertIsNone(automatik.kandidat(self.fach, "claude")[0])

    def test_fremde_denkrunde_vorhaben_bleiben_draussen(self):
        self.fach.anlegen("Minecraft-Status fehlt", "wunsch", "denkrunde", fremd=True)
        automatik.einstellen(auto="an", backend="claude")
        selbst.einstellen("an")
        self.assertIsNone(automatik.kandidat(self.fach, "claude")[0])

    def test_kandidat_nennt_das_backend_mit(self):
        vid = self.fach.anlegen("Minecraft-Status fehlt", "wunsch", "gespraech")["id"]
        with mock.patch.object(automatik, "_sandbox_da", return_value=True):
            v, backend = automatik.kandidat(self.fach, "opencode")
        self.assertEqual(v["id"], vid)
        self.assertEqual(backend, "deepseek")     # OpenCode kann keinen Wunsch bauen (30.10.2026)


class BackendWahl(unittest.TestCase):
    """„Kombination aus opencode und deepseek“: OpenCode für Erkundung, DeepSeek für echte Bauten."""

    def test_opencode_nimmt_nur_erkundung(self):
        with mock.patch.object(automatik, "_sandbox_da", return_value=True):
            self.assertEqual(automatik.backend_fuer({"art": "test"}, "opencode"), "opencode")
            self.assertEqual(automatik.backend_fuer({"art": "idee"}, "opencode"), "opencode")
            self.assertEqual(automatik.backend_fuer({"art": "wunsch"}, "opencode"), "deepseek")
            self.assertEqual(automatik.backend_fuer({"art": "fehler"}, "opencode"), "deepseek")

    def test_ohne_sandbox_kein_opencode(self):
        with mock.patch.object(automatik, "_sandbox_da", return_value=False):
            self.assertEqual(automatik.backend_fuer({"art": "test"}, "opencode"), "deepseek")

    def test_andere_backends_bleiben_wie_eingestellt(self):
        self.assertEqual(automatik.backend_fuer({"art": "wunsch"}, "claude"), "claude")
        self.assertEqual(automatik.backend_fuer({"art": "idee"}, "deepseek"), "deepseek")


class Uebernahme(Basis):
    def setUp(self):
        super().setUp()
        selbst.einstellen("an")

    def test_gruenes_werkzeug_wird_uebernommen_und_gemeldet(self):
        vid = self.bereit()
        d = selbst.einmal(self.fach)
        self.assertTrue(d["ok"], d)
        self.assertEqual(self.fach.eintrag(vid)["status"], "uebernommen")
        self.uebernehmen_auftrag.assert_called_once_with(vid)
        self.assertEqual(self.push.call_args.args[0], "werkbank")

    def test_automatik_takt_ruft_es_auf_auch_ohne_automatik_hebel(self):
        vid = self.bereit()
        automatik.einmal(self.fach)
        self.assertEqual(self.fach.eintrag(vid)["status"], "uebernommen")

    def test_rote_oder_fehlende_tests_nicht(self):
        for tests in ("rot", "nicht_gelaufen", None):
            vid = self.bereit(titel=f"Tool {tests} Wetter Snap", tests=tests)
            self.assertIsNone(selbst.einmal(self.fach))
            self.assertEqual(self.fach.eintrag(vid)["status"], "bereit")

    def test_opencode_und_fremd_nicht(self):
        self.bereit(titel="Eins Jellyfin Bild", backend="opencode")
        self.bereit(titel="Zwei Garantie Zahl", fremd=True)
        self.assertIsNone(selbst.einmal(self.fach))

    def test_diff_ausserhalb_der_positivliste_bleibt_beim_knopf(self):
        vid = self.bereit(text=diff(("kern/main.py", ["+x"], False)))
        self.assertIsNone(selbst.einmal(self.fach))
        self.assertEqual(self.fach.eintrag(vid)["status"], "bereit")
        self.uebernehmen_auftrag.assert_not_called()

    def test_fehlender_oder_gekuerzter_diff_nicht(self):
        vid = self.bereit()
        (Path(self.daten) / "werkbank" / str(vid) / "diff.patch").unlink()
        self.assertIsNone(selbst.einmal(self.fach))
        vid2 = self.bereit(titel="Anderes Thema Spotify Lied", text=diff(("docs/a.md", ["+x" * 10], False)))
        with mock.patch.object(selbst.lauf, "datei_lesen", return_value=(b"diff", True)):
            self.assertIsNone(selbst.einmal(self.fach))
        self.assertEqual(self.fach.eintrag(vid2)["status"], "bereit")

    def test_tageslimit(self):
        ids = [self.bereit(titel=t) for t in ("Wetter Hamburg Bericht", "Spotify Hörbuch spielt", "Busabfahrt nachts fehlt",
                                              "Snapchat Umlaute tippen")]
        for _ in range(selbst.TAGESLIMIT):
            self.assertTrue(selbst.einmal(self.fach)["ok"])
            for v in self.fach._alle("SELECT id FROM vorhaben WHERE status = 'uebernommen'"):
                self.fach.uebernahme_abschliessen(v["id"], "ok")
        self.assertIsNone(selbst.einmal(self.fach))
        self.assertEqual(sum(self.fach.eintrag(i)["status"] == "bereit" for i in ids), 1)

    def test_limit_laesst_sich_abschalten(self):
        self.assertTrue(selbst.limit_an())
        with self.assertRaises(ValueError):
            selbst.limit_einstellen("vielleicht")
        selbst.limit_einstellen("aus")
        self.assertFalse(selbst.limit_an())
        for t in ("Wetter Hamburg Bericht", "Spotify Hörbuch spielt", "Busabfahrt nachts fehlt", "Snapchat Umlaute tippen",
                  "Rezepte Liste leer"):
            self.bereit(titel=t)
        for _ in range(selbst.TAGESLIMIT + 2):                      # mehr als das Limit
            self.assertTrue(selbst.einmal(self.fach)["ok"])
            for v in self.fach._alle("SELECT id FROM vorhaben WHERE status = 'uebernommen'"):
                self.fach.uebernahme_abschliessen(v["id"], "ok")
        selbst.limit_einstellen("an")
        self.assertTrue(selbst.limit_an())

    def test_nicht_waehrend_eine_uebernahme_laeuft(self):
        self.bereit(titel="Erstes Ding Wetter Karte")
        self.assertTrue(selbst.einmal(self.fach)["ok"])
        self.bereit(titel="Zweites Ding Garantie Bus")
        self.assertIsNone(selbst.einmal(self.fach))

    def test_ein_vorhaben_wird_nie_zweimal_versucht(self):
        vid = self.bereit()
        self.assertTrue(selbst.einmal(self.fach)["ok"])
        self.fach.uebernahme_abschliessen(vid, "zurueckgerollt")      # zurück auf bereit
        self.assertEqual(self.fach.eintrag(vid)["status"], "bereit")
        self.assertIsNone(selbst.einmal(self.fach))
        self.assertEqual(self.uebernehmen_auftrag.call_count, 1)

    def test_nichts_im_katalog_kennt_die_selbstuebernahme(self):
        from kern.werkzeuge import katalog
        namen = " ".join(e["name"] for m in katalog._MODULE for e in m.ERKLAERUNGEN).lower()
        self.assertNotIn("selbst", namen.replace("selbstbau", ""))


if __name__ == "__main__":
    unittest.main()
