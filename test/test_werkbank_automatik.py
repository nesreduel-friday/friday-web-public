"""Werkbank-Automatik (28.10.2026): Hebel „Automatik“ + Worker, Tageslimit nur für automatische Starts.

Alles mit Temp-Datenordner; Helfer und Sandbox gemockt, nie ein echter Lauf.
"""

import inspect
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern import config
from kern.dienste import claude_sitzung
from kern.werkbank import automatik, speicher, starten
from kern.werkzeuge import katalog

HELFER_DA = {"helfer": True, "sitzungen": [], "max": 3, "werkbank": {"laeuft": None}}
HELFER_WEG = {"helfer": False, "sitzungen": [], "max": 0, "text": "Der Helfer auf hphome läuft nicht."}
MITTAG = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
# deutlich verschiedene Titel: ähnliche würde `anlegen` zu einem Vorhaben zusammenführen
TITEL = ["Wetterbericht nennt Hamburg", "Spotify spielt Hörbuch", "Busabfahrt fehlt nachts", "Snapchat tippt Umlaute",
         "Stundenplan zeigt Ferien", "Garantie zählt Bescheide", "Notizbuch vergisst Allergie", "Jellyfin Vorschaubild leer",
         "Morgenbriefing kommt doppelt", "Goodnotes Handschrift unlesbar", "Rezepte fehlen komplett"]


class Basis(unittest.TestCase):
    def setUp(self):
        self.daten = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.daten, "FRIDAY_HINTERGRUND": "aus"})
        env.start()
        self.addCleanup(env.stop)
        self.uhr = [MITTAG]
        u = mock.patch("kern.config.jetzt", side_effect=lambda: self.uhr[0])
        u.start()
        self.addCleanup(u.stop)
        self.fach = speicher.standard()
        z = mock.patch.object(claude_sitzung, "zustand", return_value=HELFER_DA)
        self.zustand = z.start()
        self.addCleanup(z.stop)
        a = mock.patch.object(claude_sitzung, "werkbank_auftrag", return_value={"ok": True, "text": "gestartet"})
        self.auftrag = a.start()
        self.addCleanup(a.stop)
        o = mock.patch.object(starten, "_opencode_starten")
        self.oc = o.start()
        self.addCleanup(o.stop)
        b = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        self.push = b.start()
        self.addCleanup(b.stop)

    def neu(self, titel, art="fehler", quelle="gespraech", fremd=False) -> int:
        return self.fach.anlegen(titel, art, quelle, fremd=fremd)["id"]

    def an(self, backend="claude"):
        automatik.einstellen(auto="an", backend=backend)

    def fertig(self, vid):
        """Lauf des Vorhabens beenden (wie der Lauf-Takt), damit die Automatik weitermachen darf."""
        self.fach.uebergang(vid, "laeuft", "bereit")


class Einstellungen(Basis):
    def test_standard_aus_und_claude(self):
        self.assertEqual(automatik.einstellungen(), {"werkbank_auto": "aus", "werkbank_auto_backend": "claude"})
        from kern.main import app
        d = TestClient(app).get("/api/werkbank/einstellungen").json()
        self.assertEqual(d, {"werkbank_auto": "aus", "werkbank_auto_backend": "claude", "auto_heute": 0,
                             "auto_limit": 5, "werkbank_auto_uebernehmen": "aus", "selbst_limit": 3,
                             "werkbank_selbst_limit": "an"})
        self.assertEqual(automatik.AUTO_TAGESLIMIT, 5)

    def test_kaputte_werte_in_der_datei_gelten_als_standard(self):
        from kern.planer import briefing
        briefing._schreiben(briefing._einstellungsdatei(), {"werkbank_auto": "ja", "werkbank_auto_backend": "gpt"})
        self.assertEqual(automatik.einstellungen(), {"werkbank_auto": "aus", "werkbank_auto_backend": "claude"})

    def test_route_nur_mit_origin_und_gueltigen_werten(self):
        from kern.main import app
        client = TestClient(app)
        r = client.post("/api/werkbank/einstellungen", json={"werkbank_auto": "an"},
                        headers={"Origin": "http://boese.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(automatik.einstellungen()["werkbank_auto"], "aus")
        for falsch in ({"werkbank_auto": "ja"}, {"werkbank_auto": True}, {"werkbank_auto_backend": "gpt"}, {}):
            self.assertEqual(client.post("/api/werkbank/einstellungen", json=falsch).status_code, 422, falsch)
        self.assertEqual(automatik.einstellungen()["werkbank_auto"], "aus")
        d = client.post("/api/werkbank/einstellungen", json={"werkbank_auto": "an"}).json()
        self.assertEqual((d["werkbank_auto"], d["werkbank_auto_backend"]), ("an", "claude"))
        d = client.post("/api/werkbank/einstellungen", json={"werkbank_auto_backend": "opencode"}).json()
        self.assertEqual((d["werkbank_auto"], d["werkbank_auto_backend"]), ("an", "opencode"))
        # die übrigen Einstellungen in derselben Datei bleiben erhalten
        from kern.denken import runde
        runde.backend_einstellen("opencode")
        client.post("/api/werkbank/einstellungen", json={"werkbank_auto": "aus"})
        self.assertEqual(runde.backend(), "opencode")

    def test_kein_werkzeug_kennt_die_hebel(self):
        for modul in [*katalog._MODULE, katalog]:
            quelle = inspect.getsource(modul)
            for verboten in ("werkbank_auto", "automatik", "auto=True"):
                self.assertNotIn(verboten, quelle, f"{modul.__name__} kennt {verboten}")
        namen = {e["name"] for m in katalog._MODULE for e in getattr(m, "ERKLAERUNGEN", [])}
        self.assertFalse({n for n in namen if "auto" in n and ("werkbank" in n or "vorhaben" in n)})

    def test_werkbank_liste_nennt_auto_zaehler_statt_tageslimit(self):
        from kern.main import app
        d = TestClient(app).get("/api/werkbank").json()
        self.assertNotIn("tageslimit", d)
        self.assertEqual((d["auto_heute"], d["auto_limit"]), (0, 5))


class Dauerlauf(Basis):
    def test_aus_startet_nichts(self):
        self.neu("Wecker klingelt doppelt")
        self.assertIsNone(automatik.einmal())
        self.auftrag.assert_not_called()

    def test_an_startet_das_aelteste_passende(self):
        alt = self.neu("Wecker klingelt doppelt")
        self.neu("Kalender zeigt falschen Tag")
        self.an()
        d = automatik.einmal()
        self.assertTrue(d["ok"], d)
        self.assertEqual(self.auftrag.call_args.args[0], alt)
        v = self.fach.eintrag(alt)
        self.assertEqual((v["status"], v["backend"]), ("laeuft", "claude"))
        self.assertEqual(self.fach.auto_heute(), 1)
        # kurze Meldung, einmal
        self.push.assert_called_once()
        self.assertIn("automatisch angefangen", self.push.call_args.args[2])
        self.assertEqual(self.push.call_args.args[0], "werkbank")

    def test_auswahlregeln(self):
        fremd = self.neu("Aus fremdem Text", fremd=True)
        denk = self.neu("Aus der Denkrunde", quelle="denkrunde")
        kaputt = self.neu("Schon gescheitert")
        self.fach.status_setzen(kaputt, "fehler")
        bereit = self.neu("Liegt bereit")
        self.fach.status_setzen(bereit, "bereit")
        ruht = self.neu("Ruht gerade")
        self.fach.status_setzen(ruht, "ruht")
        self.an()
        self.assertIsNone(automatik.einmal())
        self.auftrag.assert_not_called()
        stoerung = self.neu("Flammen-Snap scheitert", quelle="stoerung")
        frei = self.neu("Freigegebenes Vorhaben")
        self.fach.status_setzen(frei, "freigegeben")
        self.assertTrue(automatik.einmal()["ok"])
        self.assertEqual(self.auftrag.call_args.args[0], stoerung)
        for vid in (fremd, denk, kaputt, bereit, ruht):
            self.assertNotEqual(self.fach.eintrag(vid)["status"], "laeuft")

    def test_opencode_baut_echte_vorhaben_mit_deepseek(self):
        # Kombination (30.10.2026): OpenCode kann keinen Fehler bauen und wird nie live gebracht —
        # statt still zu warten, nimmt DeepSeek das Vorhaben.
        (config.daten_ordner() / "werkbank" / "sandbox").mkdir(parents=True)
        fehler = self.neu("Wecker klingelt doppelt", art="fehler")
        self.an("opencode")
        self.assertTrue(automatik.einmal()["ok"])
        self.oc.assert_not_called()
        self.auftrag.assert_called_once()
        self.assertEqual(self.auftrag.call_args.args[0], fehler)
        self.assertEqual(self.fach.eintrag(fehler)["backend"], "deepseek")

    def test_opencode_nimmt_ideen_in_der_sandbox(self):
        (config.daten_ordner() / "werkbank" / "sandbox").mkdir(parents=True)
        idee = self.neu("Was wäre wenn Friday singt", art="idee")
        self.an("opencode")
        self.assertTrue(automatik.einmal()["ok"])
        self.oc.assert_called_once()
        self.assertEqual(self.oc.call_args.args[0], idee)
        self.auftrag.assert_not_called()
        self.assertEqual(self.fach.eintrag(idee)["backend"], "opencode")

    def test_ohne_sandbox_baut_deepseek(self):
        # Ohne Austauschordner gibt es keine Sandbox — dann baut DeepSeek, statt dass nichts passiert.
        idee = self.neu("Was wäre wenn Friday singt", art="idee")
        self.an("opencode")
        self.assertTrue(automatik.einmal()["ok"])
        self.oc.assert_not_called()
        self.assertEqual(self.fach.eintrag(idee)["backend"], "deepseek")

    def test_ein_lauf_gleichzeitig(self):
        a = self.neu("Wecker klingelt doppelt")
        self.neu("Kalender zeigt falschen Tag")
        self.an()
        automatik.einmal()
        self.assertIsNone(automatik.einmal())
        self.assertEqual(self.auftrag.call_count, 1)
        # auch ein manueller Lauf eines anderen Backends hält die Automatik an
        self.fertig(a)
        (config.daten_ordner() / "werkbank" / "sandbox").mkdir(parents=True)
        idee = self.neu("Was wäre wenn Friday singt", art="idee")
        self.assertTrue(starten.starten_dict(idee, "opencode")["ok"])
        self.assertIsNone(automatik.einmal())
        self.assertEqual(self.auftrag.call_count, 1)

    def test_limit_fuenf_nur_fuer_automatik(self):
        # manuelle Starts verbrauchen das Limit nicht und haben selbst keines
        for i in range(4):
            vid = self.neu(TITEL[i])
            self.assertTrue(starten.starten_dict(vid)["ok"])
            self.fertig(vid)
        self.assertEqual(self.fach.auto_heute(), 0)
        self.assertEqual(self.fach.heute_gestartet(), 4)
        self.an()
        ids = [self.neu(t) for t in TITEL[4:10]]
        for vid in ids[:5]:
            self.assertTrue(automatik.einmal()["ok"])
            self.assertEqual(self.fach.eintrag(vid)["status"], "laeuft")
            self.fertig(vid)
        self.assertEqual(self.fach.auto_heute(), 5)
        self.assertIsNone(automatik.einmal())                          # der 6. automatische nicht
        self.assertEqual(self.fach.eintrag(ids[5])["status"], "vorgeschlagen")
        d = starten.starten_dict(ids[5], auto=True)                      # auch nicht am Dauerlauf vorbei
        self.assertFalse(d["ok"])
        self.assertEqual(d["code"], 429)
        self.assertTrue(d["text"].startswith("NICHTS gestartet."))
        self.assertTrue(starten.starten_dict(ids[5])["ok"])             # manuell geht der 6.
        self.assertEqual(self.fach.auto_heute(), 5)
        self.assertEqual(self.fach.heute_gestartet(), 10)

    def test_helfer_stumm_oder_weg_zaehlt_nicht(self):
        vid = self.neu("Wecker klingelt doppelt")
        self.an()
        self.auftrag.side_effect = claude_sitzung.HelferStumm("stumm")
        d = automatik.einmal()
        self.assertFalse(d["ok"])
        self.zustand.return_value = HELFER_WEG
        self.assertFalse(automatik.einmal()["ok"])
        self.assertEqual(self.fach.auto_heute(), 0)
        self.assertEqual(self.fach.eintrag(vid)["status"], "vorgeschlagen")
        self.push.assert_not_called()
        # nächster Tick, Helfer wieder da: jetzt startet es
        self.zustand.return_value = HELFER_DA
        self.auftrag.side_effect = None
        self.assertTrue(automatik.einmal()["ok"])
        self.assertEqual(self.fach.auto_heute(), 1)

    def test_tageswechsel(self):
        self.an()
        for t in TITEL[:5]:
            vid = self.neu(t)
            automatik.einmal()
            self.fertig(vid)
        rest = self.neu(TITEL[10])
        self.assertIsNone(automatik.einmal())
        self.uhr[0] = MITTAG.replace(hour=23, minute=59)
        self.assertIsNone(automatik.einmal())
        self.uhr[0] = MITTAG + timedelta(hours=12, minutes=1)            # 00:01 am nächsten Berliner Tag
        self.assertEqual(self.fach.auto_heute(), 0)
        self.assertTrue(automatik.einmal()["ok"])
        self.assertEqual(self.fach.eintrag(rest)["status"], "laeuft")

    def test_fehlstart_wird_nicht_wiederholt(self):
        vid = self.neu("Wecker klingelt doppelt")
        self.an()
        automatik.einmal()
        self.fach.uebergang(vid, "laeuft", "fehler")
        self.assertIsNone(automatik.einmal())
        self.assertEqual(self.auftrag.call_count, 1)

    def test_automatik_kann_nicht_uebernehmen(self):
        quelle = inspect.getsource(automatik)
        self.assertNotIn("uebernehmen", quelle)
        self.assertNotIn("uebernahme", quelle)


class Speicher(unittest.TestCase):
    def test_altzeilen_ohne_spalte_gelten_als_manuell(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = sqlite3.connect(Path(tmp) / "werkbank.db")
            db.execute("CREATE TABLE starts (vid INTEGER NOT NULL, zeit TEXT NOT NULL)")
            db.execute("INSERT INTO starts VALUES (1, ?)", (speicher._iso(speicher._jetzt()),))
            db.commit()
            db.close()
            fach = speicher.Fach(Path(tmp))
            self.assertEqual(fach.heute_gestartet(), 1)
            self.assertEqual(fach.auto_heute(), 0)
            fach.starten_notieren(1, auto=True)
            self.assertEqual((fach.heute_gestartet(), fach.auto_heute()), (2, 1))
            fach.starten_notieren(1, jetzt=speicher._jetzt() - timedelta(days=2), auto=True)
            self.assertEqual(fach.auto_heute(), 1)


if __name__ == "__main__":
    unittest.main()
