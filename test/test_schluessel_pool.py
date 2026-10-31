"""Pool-Speicher und Auftrags-Protokoll für zusätzliche Gemini-Schlüssel."""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from kern.dienste import schluessel_pool as pool


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.ordner.cleanup)

    def _zustand_schreiben(self, antwort: dict, helfer_alter_s: float = 0):
        o = os.path.join(self.ordner.name, "schluessel_auftraege")
        os.makedirs(o, exist_ok=True)
        with open(os.path.join(o, "zustand.json"), "w", encoding="utf-8") as f:
            json.dump({"aktualisiert": time.time() - helfer_alter_s, "letzte_antwort": antwort}, f)

    def test_leerer_pool_hat_automatik_aus_und_ist_leer(self):
        self.assertEqual(pool.eintraege(), [])
        self.assertFalse(pool.automatik_an())

    def test_automatik_laesst_sich_schalten(self):
        pool.automatik_setzen(True)
        self.assertTrue(pool.automatik_an())
        pool.automatik_setzen(False)
        self.assertFalse(pool.automatik_an())

    def test_anlegen_ohne_automatik_lehnt_ab(self):
        self._zustand_schreiben({})
        ok, text = pool.anlegen_anstossen(erzwingen=False)
        self.assertFalse(ok)
        self.assertIn("Automatik", text)

    def test_anlegen_erzwungen_geht_auch_ohne_automatik(self):
        self._zustand_schreiben({})
        ok, _ = pool.anlegen_anstossen(erzwingen=True)
        self.assertTrue(ok)
        auftrag = os.path.join(self.ordner.name, "schluessel_auftraege", "auftrag.json")
        self.assertTrue(os.path.exists(auftrag))

    def test_anlegen_ohne_laufenden_helfer_lehnt_ab(self):
        # Keine zustand.json → Helfer gilt als nicht erreichbar.
        ok, text = pool.anlegen_anstossen(erzwingen=True)
        self.assertFalse(ok)
        self.assertIn("Helfer", text)

    def test_zweiter_auftrag_wird_abgelehnt_solange_einer_offen_ist(self):
        self._zustand_schreiben({})
        pool.anlegen_anstossen(erzwingen=True)
        ok, text = pool.anlegen_anstossen(erzwingen=True)
        self.assertFalse(ok)
        self.assertIn("läuft schon", text)

    def test_erfolgreiche_antwort_wandert_in_den_pool(self):
        self._zustand_schreiben({"ok": True, "wert": "AIzaTEST1", "projekt_id": "friday-pool-abcd1234",
                                  "zeit": time.time()})
        eintraege = pool.eintraege()
        self.assertEqual(len(eintraege), 1)
        self.assertEqual(eintraege[0]["wert"], "AIzaTEST1")
        self.assertEqual(eintraege[0]["name"], "Pool 1")
        # Ein zweiter Aufruf ohne neue Antwort hängt nichts doppelt an.
        self.assertEqual(len(pool.eintraege()), 1)

    def test_fehlerantwort_loest_abkuehlung_aus(self):
        pool.automatik_setzen(True)
        self._zustand_schreiben({"ok": False, "text": "Kontingent erreicht", "zeit": time.time()})
        ok, text = pool.anlegen_anstossen(erzwingen=False)
        self.assertFalse(ok)
        self.assertIn("Abkühl", text)

    def test_helfer_zustand_meldet_fehler_ohne_schluesselwert(self):
        self._zustand_schreiben({"ok": False, "text": "Kontingent erreicht", "zeit": time.time()})
        z = pool.helfer_zustand()
        self.assertTrue(z["helfer"])
        self.assertEqual(z["fehler"], "Kontingent erreicht")
        self.assertNotIn("wert", z)

    def test_alter_zustand_gilt_als_helfer_tot(self):
        self._zustand_schreiben({}, helfer_alter_s=600)
        self.assertFalse(pool.helfer_zustand()["helfer"])

    def test_pruefen_und_anstossen_bei_wenig_frei(self):
        self._zustand_schreiben({})
        pool.automatik_setzen(True)
        pool.pruefen_und_anstossen(anzahl_frei=1)
        auftrag = os.path.join(self.ordner.name, "schluessel_auftraege", "auftrag.json")
        self.assertTrue(os.path.exists(auftrag))

    def test_pruefen_und_anstossen_bei_genug_frei_tut_nichts(self):
        self._zustand_schreiben({})
        pool.automatik_setzen(True)
        pool.pruefen_und_anstossen(anzahl_frei=3)
        auftrag = os.path.join(self.ordner.name, "schluessel_auftraege", "auftrag.json")
        self.assertFalse(os.path.exists(auftrag))

    def test_graph_umschalten_legt_auftrag_ohne_automatik_an(self):
        self._zustand_schreiben({})
        # Automatik bleibt aus — Umschalten braucht sie nicht, es legt nichts Neues an.
        ok, text = pool.graph_umschalten_anstossen("AIzaAnderer")
        self.assertTrue(ok, text)
        with open(os.path.join(self.ordner.name, "schluessel_auftraege", "auftrag.json"), encoding="utf-8") as f:
            auftrag = json.load(f)
        self.assertEqual(auftrag["aktion"], "graphiti_umschalten")
        self.assertEqual(auftrag["schluessel"], "AIzaAnderer")

    def test_graph_umschalten_ohne_helfer_lehnt_ab(self):
        ok, text = pool.graph_umschalten_anstossen("AIzaAnderer")
        self.assertFalse(ok)
        self.assertIn("Helfer", text)

    def test_graph_umschalten_antwort_landet_nicht_im_pool(self):
        self._zustand_schreiben({"ok": True, "text": "Wissensgraph umgeschaltet.", "zeit": time.time()})
        self.assertEqual(pool.eintraege(), [])


if __name__ == "__main__":
    unittest.main()
