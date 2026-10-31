"""Werkbank Task 8: Übernehmen per Knopf. Route, Doppelklick-Schutz, Ergebnis aus `stand.json` und: kein Weg fürs Modell.

Der Helfer ist gemockt (`claude_sitzung.uebernehmen_auftrag`); was er auf dem Host tut, prüft `test_claude_sitzung.Uebernehmen`.
"""

import inspect
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from datetime import timedelta
from unittest import mock

from fastapi.testclient import TestClient

from kern import config
from kern.dienste import claude_sitzung
from kern.werkbank import lauf, speicher
from kern.werkzeuge import katalog

HELFER_DA = {"helfer": True, "sitzungen": [], "max": 3, "werkbank": {"laeuft": None}}
HELFER_WEG = {"helfer": False, "sitzungen": [], "max": 0, "text": "Der Helfer auf hphome läuft nicht."}


class Basis(unittest.TestCase):
    def setUp(self):
        self.daten = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.daten, "FRIDAY_HINTERGRUND": "aus"})
        env.start()
        self.addCleanup(env.stop)
        self.fach = speicher.standard()
        z = mock.patch.object(claude_sitzung, "zustand", return_value=HELFER_DA)
        self.zustand = z.start()
        self.addCleanup(z.stop)
        a = mock.patch.object(claude_sitzung, "uebernehmen_auftrag", return_value={"ok": True, "text": "läuft"})
        self.auftrag = a.start()
        self.addCleanup(a.stop)
        w = mock.patch.object(claude_sitzung, "werkbank_auftrag", return_value={"ok": True, "text": "gestartet"})
        self.werkbank_auftrag = w.start()
        self.addCleanup(w.stop)
        b = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        self.push = b.start()
        self.addCleanup(b.stop)
        from kern.main import app
        self.c = TestClient(app)

    def bereit(self, tests="ok", titel="Wecker klingelt doppelt") -> int:
        vid = self.fach.anlegen(titel, "fehler", "gespraech")["id"]
        self.fach.status_setzen(vid, "bereit", backend="claude", branch=f"werkbank/{vid}", tests=tests,
                                bericht="Repariert.")
        return vid

    def post(self, vid, body=None, **kw):
        return self.c.post(f"/api/werkbank/{vid}/uebernehmen", json=body, **kw)

    def stand_schreiben(self, vid, **felder):
        ordner = config.daten_ordner() / "werkbank" / str(vid)
        ordner.mkdir(parents=True, exist_ok=True)
        stand = {"status": "bereit", "branch": f"werkbank/{vid}", "tests": "ok", "zeit": 1}
        stand.update(felder)
        (ordner / "stand.json").write_text(json.dumps(stand), encoding="utf-8")


class Route(Basis):
    def test_uebernehmen_nur_bei_bereit(self):
        for status in ("vorgeschlagen", "freigegeben", "laeuft", "fehler", "verworfen", "ruht", "uebernommen"):
            with self.subTest(status=status):
                vid = self.bereit()
                self.fach.status_setzen(vid, status)
                r = self.post(vid)
                self.assertEqual(r.status_code, 409, r.text)
                self.assertTrue(r.json()["detail"])
                self.assertEqual(self.fach.eintrag(vid)["status"], status)
        self.assertEqual(self.post(9999).status_code, 404)
        self.auftrag.assert_not_called()

    def test_uebernehmen_geht_und_setzt_status_vor_dem_auftrag(self):
        vid = self.bereit()
        gesehen = []
        self.auftrag.side_effect = lambda v, **k: gesehen.append(self.fach.eintrag(v)["status"]) or {"ok": True}
        r = self.post(vid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(gesehen, ["uebernommen"])                  # schon gesetzt, BEVOR der Helfer gerufen wird
        self.auftrag.assert_called_once_with(vid)
        v = self.fach.eintrag(vid)
        self.assertEqual((v["status"], v["uebernahme"]), ("uebernommen", "laeuft"))
        self.assertTrue(v["uebernahme_seit"])

    def test_doppelklick_wendet_nur_einmal_an(self):
        vid = self.bereit()
        self.assertEqual(self.post(vid).status_code, 200)
        r = self.post(vid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("übernommen", r.json()["detail"])
        self.assertEqual(self.auftrag.call_count, 1)

    def test_doppelklick_gleichzeitig_wendet_nur_einmal_an(self):
        vid = self.bereit()
        tor = threading.Event()
        self.auftrag.side_effect = lambda v, **k: tor.wait(5) and {"ok": True}
        codes = []
        faeden = [threading.Thread(target=lambda: codes.append(self.post(vid).status_code)) for _ in range(3)]
        for f in faeden:
            f.start()
        for _ in range(100):
            if len(codes) >= 2:
                break
            threading.Event().wait(0.02)
        tor.set()
        for f in faeden:
            f.join(5)
        self.assertEqual(sorted(codes), [200, 409, 409])
        self.assertEqual(self.auftrag.call_count, 1)

    def test_rote_tests_brauchen_ausdrueckliches_trotz_rot(self):
        for tests in ("rot", "nicht_gelaufen", None):
            with self.subTest(tests=tests):
                self.auftrag.reset_mock()
                vid = self.bereit(tests=tests)
                for body in (None, {}, {"trotz_rot": False}, {"trotz_rot": "ja"}):
                    r = self.post(vid, body)
                    self.assertGreaterEqual(r.status_code, 400, body)
                    self.assertLess(r.status_code, 500, body)
                    self.assertEqual(self.fach.eintrag(vid)["status"], "bereit")
                self.assertIn("Tests", self.post(vid).json()["detail"])
                self.auftrag.assert_not_called()
                r = self.post(vid, {"trotz_rot": True})
                self.assertEqual(r.status_code, 200, r.text)
                self.auftrag.assert_called_once()

    def test_schalter_fuer_geschuetzte_pfade_gibt_es_nicht(self):
        """Hart gesperrt im Helfer: die Route reicht nichts dergleichen weiter (unbekanntes Feld wird ignoriert)."""
        import inspect

        from kern.werkbank import uebernehmen
        vid = self.bereit()
        self.assertEqual(self.post(vid, {"geschuetzte_pfade_ok": True}).status_code, 200)
        self.auftrag.assert_called_once_with(vid)
        self.assertNotIn("geschuetzte", inspect.getsource(uebernehmen))
        self.assertNotIn("geschuetzte", inspect.getsource(claude_sitzung))

    def test_helfer_lehnt_ab_status_zurueck_auf_bereit_mit_klartext(self):
        vid = self.bereit()
        self.auftrag.side_effect = claude_sitzung.ClaudeFehler("Im Live-Repo gibt es ungespeicherte Änderungen.")
        r = self.post(vid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("ungespeicherte", r.json()["detail"])
        v = self.fach.eintrag(vid)
        self.assertEqual(v["status"], "bereit")
        self.assertIsNone(v["uebernahme"])
        self.auftrag.side_effect = None
        self.assertEqual(self.post(vid).status_code, 200)            # danach geht es wieder

    def test_ablehnung_bleibt_am_vorhaben_nicht_nur_in_der_antwort(self):
        """30.10.2026: Nach einem abgelehnten Übernehmen stand der Grund nur in der HTTP-Antwort — die App zeigt die
        als eine Zeile am Ende der Liste, die Karte sah nach dem Neuladen wieder aus wie vorher („bereit“). Jetzt trägt
        ihn der Bericht, den die Karte bei `bereit` zeigt; der Bericht des Laufs bleibt darunter erhalten."""
        vid = self.bereit()
        self.auftrag.side_effect = claude_sitzung.ClaudeFehler("Im Live-Repo gibt es ungespeicherte Änderungen.")
        r = self.post(vid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("ungespeicherte", r.json()["detail"])
        v = self.fach.eintrag(vid)
        self.assertEqual((v["status"], v["uebernahme"]), ("bereit", None))
        self.assertIn("Übernahme nicht möglich", v["bericht"])
        self.assertIn("ungespeicherte", v["bericht"])
        self.assertIn("Repariert.", v["bericht"])                  # der Bericht des Laufs bleibt erhalten
        [x] = [x for x in self.c.get("/api/werkbank").json()["vorhaben"] if x["id"] == vid]
        self.assertIn("ungespeicherte", x["bericht"])              # so zeigt es die Karte

    def test_vorpruefungen_tragen_ihren_grund_am_vorhaben_ein(self):
        """Auch die Prüfungen im Kern (Branch, Tests, Helfer) müssen sichtbar bleiben, nicht nur in der Antwort."""
        vid = self.bereit()
        self.fach.status_setzen(vid, "bereit", branch="main")
        self.assertEqual(self.post(vid).status_code, 409)
        self.assertIn("keinen Branch", self.fach.eintrag(vid)["bericht"])
        vid2 = self.bereit(tests="rot", titel="Rote Tests")
        self.assertGreaterEqual(self.post(vid2).status_code, 400)
        self.assertIn("Tests sind nicht grün", self.fach.eintrag(vid2)["bericht"])
        self.zustand.return_value = HELFER_WEG
        vid3 = self.bereit(titel="Ohne Helfer")
        self.assertEqual(self.post(vid3).status_code, 409)
        self.assertIn("Helfer", self.fach.eintrag(vid3)["bericht"])
        self.auftrag.assert_not_called()

    def test_erfolgreiche_uebernahme_aendert_den_bericht_nicht(self):
        vid = self.bereit()
        self.assertEqual(self.post(vid).status_code, 200)
        self.assertEqual(self.fach.eintrag(vid)["bericht"], "Repariert.")

    def test_ohne_helfer_nichts_umgestellt(self):
        self.zustand.return_value = HELFER_WEG
        vid = self.bereit()
        r = self.post(vid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Helfer", r.json()["detail"])
        self.assertEqual(self.fach.eintrag(vid)["status"], "bereit")
        self.auftrag.assert_not_called()

    def test_ohne_eigenen_branch_nichts(self):
        vid = self.bereit()
        self.fach.status_setzen(vid, "bereit", branch="main")
        self.assertEqual(self.post(vid).status_code, 409)
        self.fach.status_setzen(vid, "bereit", branch=f"werkbank/{vid}", backend="opencode")
        self.assertEqual(self.post(vid).status_code, 409)
        self.auftrag.assert_not_called()

    def test_uebernehmen_lehnt_fremden_origin_ab(self):
        vid = self.bereit()
        for origin in ("http://hphome.beispiel.ts.net:8989", "https://boese.example"):
            r = self.post(vid, {"trotz_rot": True}, headers={"Origin": origin})
            self.assertEqual(r.status_code, 403, origin)
        r = self.post(vid, None, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        self.auftrag.assert_not_called()
        self.assertEqual(self.fach.eintrag(vid)["status"], "bereit")

    def test_liste_zeigt_den_stand_der_uebernahme(self):
        vid = self.bereit()
        self.post(vid)
        [v] = [x for x in self.c.get("/api/werkbank").json()["vorhaben"] if x["id"] == vid]
        self.assertEqual((v["status"], v["uebernahme"]), ("uebernommen", "laeuft"))


class Ergebnis(Basis):
    """`lauf.einmal` liest das Ergebnis aus `stand.json` und meldet es genau einmal."""

    def uebernommen(self, **kw) -> int:
        vid = self.bereit(**kw)
        self.assertEqual(self.post(vid, {"trotz_rot": True}).status_code, 200)
        return vid

    def jetzt(self) -> int:
        return int(config.jetzt().timestamp()) + 1

    def test_ok_bleibt_uebernommen_und_meldet_einmal(self):
        vid = self.uebernommen()
        self.assertEqual(lauf.einmal(), [])                          # noch kein Ergebnis
        self.stand_schreiben(vid, uebernahme="ok", uebernahme_text="Übernommen.", uebernahme_zeit=self.jetzt())
        self.assertEqual(lauf.einmal(), [vid])
        self.assertEqual(lauf.einmal(), [])
        v = self.fach.eintrag(vid)
        self.assertEqual((v["status"], v["uebernahme"]), ("uebernommen", "ok"))
        self.push.assert_called_once()
        self.assertEqual(self.push.call_args.args[0], "werkbank")

    def test_zurueckgerollt_geht_auf_bereit_mit_klartext(self):
        for ergebnis in ("zurueckgerollt", "fehler"):
            with self.subTest(ergebnis=ergebnis):
                self.push.reset_mock()
                vid = self.uebernommen(titel=f"Vorhaben {ergebnis}")
                self.stand_schreiben(vid, uebernahme=ergebnis, uebernahme_text="Friday war nicht gesund.",
                                     uebernahme_zeit=self.jetzt())
                self.assertEqual(lauf.einmal(), [vid])
                self.assertEqual(lauf.einmal(), [])
                v = self.fach.eintrag(vid)
                self.assertEqual((v["status"], v["uebernahme"]), ("bereit", ergebnis))
                self.assertIn("Friday war nicht gesund.", v["bericht"])
                self.assertIn("Repariert.", v["bericht"])
                self.push.assert_called_once()
                self.assertEqual(self.push.call_args.args[0], "werkbank")
                # danach lässt es sich erneut übernehmen
                self.assertEqual(self.post(vid, {"trotz_rot": True}).status_code, 200)
                self.stand_schreiben(vid, uebernahme="laeuft", uebernahme_zeit=self.jetzt())   # wie der Helfer

    def test_altes_ergebnis_eines_frueheren_versuchs_zaehlt_nicht(self):
        vid = self.uebernommen()
        alt = int((config.jetzt() - timedelta(hours=1)).timestamp())
        self.stand_schreiben(vid, uebernahme="zurueckgerollt", uebernahme_text="alt", uebernahme_zeit=alt)
        self.assertEqual(lauf.einmal(), [])
        self.assertEqual(self.fach.eintrag(vid)["uebernahme"], "laeuft")
        self.stand_schreiben(vid, uebernahme="laeuft", uebernahme_zeit=self.jetzt())
        self.assertEqual(lauf.einmal(), [])
        self.push.assert_not_called()

    def test_haengende_uebernahme_nach_frist(self):
        vid = self.uebernommen()
        alt = speicher._iso(speicher._jetzt() - timedelta(seconds=lauf.FRIST_UEBERNAHME_S + 60))
        with self.fach.verbindung() as db:
            db.execute("UPDATE vorhaben SET uebernahme_seit = ? WHERE id = ?", (alt, vid))
        self.assertEqual(lauf.einmal(), [vid])
        v = self.fach.eintrag(vid)
        self.assertEqual((v["status"], v["uebernahme"]), ("bereit", "fehler"))
        self.assertIn("prüfen", v["bericht"])
        self.push.assert_called_once()

    def test_abschliessen_traegt_nur_einmal_ein(self):
        vid = self.uebernommen()
        self.assertTrue(self.fach.uebernahme_abschliessen(vid, "zurueckgerollt", bericht="x"))
        self.assertFalse(self.fach.uebernahme_abschliessen(vid, "ok"))          # schon eingetragen, Status bereit
        v = self.fach.eintrag(vid)
        self.assertEqual((v["status"], v["uebernahme"]), ("bereit", "zurueckgerollt"))
        andere = self.bereit(titel="Nie übernommen")
        self.assertFalse(self.fach.uebernahme_abschliessen(andere, "ok"))
        self.assertIsNone(self.fach.eintrag(andere)["uebernahme"])
        with self.assertRaises(ValueError):
            self.fach.uebernahme_abschliessen(vid, "laeuft")

    def test_unbekanntes_ergebnis_wird_ignoriert(self):
        vid = self.uebernommen()
        self.stand_schreiben(vid, uebernahme="alles_loeschen", uebernahme_zeit=self.jetzt())
        self.assertEqual(lauf.einmal(), [])
        self.assertEqual(self.fach.eintrag(vid)["uebernahme"], "laeuft")


class KeinWegFuerDasModell(unittest.TestCase):
    """Übernehmen geht nur über den Knopf: kein Werkzeug, auch nicht über Umwege."""

    def _erreichbar(self) -> dict[str, types.ModuleType]:
        """Alle kern-Module, die ein Katalog-Modul über seine Namen (auch Unter-Imports) erreicht."""
        gesehen, offen = {}, list(katalog._MODULE)
        while offen:
            m = offen.pop()
            if m.__name__ in gesehen:
                continue
            gesehen[m.__name__] = m
            for wert in vars(m).values():
                ziel = wert if isinstance(wert, types.ModuleType) else sys.modules.get(getattr(wert, "__module__", "") or "")
                if isinstance(ziel, types.ModuleType) and ziel.__name__.startswith("kern.") and ziel.__name__ not in gesehen:
                    offen.append(ziel)
        return gesehen

    def test_kein_werkzeug_fuehrt_uebernehmen_aus(self):
        namen = [e["name"].lower() for m in katalog._MODULE for e in m.ERKLAERUNGEN]
        self.assertEqual([n for n in namen if "uebernehm" in n or "übernehm" in n or "merge" in n], [])
        erreichbar = self._erreichbar()
        self.assertNotIn("kern.werkbank.uebernehmen", erreichbar)
        self.assertNotIn("kern.main", erreichbar)
        for name, m in erreichbar.items():
            if name == "kern.dienste.claude_sitzung":                   # dort ist sie nur definiert
                continue
            try:
                quelle = inspect.getsource(m)
            except (OSError, TypeError):
                continue
            self.assertNotIn("uebernehmen_auftrag", quelle, name)
            self.assertNotIn("werkbank.uebernehmen", quelle, name)
            self.assertNotIn("import uebernehmen", quelle, name)

    def test_gegenprobe_werkzeuge_rufen_den_helfer_nie(self):
        """Alle Werkbank-Werkzeuge einmal ausführen: `uebernehmen_auftrag` bleibt unberührt."""
        import asyncio

        from kern.werkzeuge import werkbank as werkzeug
        daten = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": daten, "FRIDAY_HINTERGRUND": "aus"}), \
                mock.patch.object(claude_sitzung, "uebernehmen_auftrag") as auftrag, \
                mock.patch.object(claude_sitzung, "werkbank_auftrag", return_value={"ok": True}), \
                mock.patch.object(claude_sitzung, "zustand", return_value=HELFER_DA):
            fach = speicher.standard()
            vid = fach.anlegen("Etwas reparieren", "fehler", "gespraech")["id"]
            fach.status_setzen(vid, "bereit", backend="claude", branch=f"werkbank/{vid}", tests="ok")
            for e in werkzeug.ERKLAERUNGEN:
                funktion = getattr(werkzeug, e["name"])
                for args in ({"id": vid, "zustimmung": True}, {"titel": "Übernimm Vorhaben", "art": "wunsch"}, {}):
                    try:
                        ergebnis = funktion(**args)
                        if asyncio.iscoroutine(ergebnis):
                            asyncio.run(ergebnis)
                    except TypeError:
                        continue
            auftrag.assert_not_called()
            self.assertEqual(fach.eintrag(vid)["status"], "bereit")


if __name__ == "__main__":
    unittest.main()
