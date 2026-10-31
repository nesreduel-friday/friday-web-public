"""Karte „Regeln“ in der Übersicht: immer da, läuft nie ab, zeigt die Läufe, „gesehen“ je Lauf."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.dashboard import karten as dashboardkarten
from kern.dashboard import regeln as regelkarte
from kern.dienste import whatsapp
from kern.regeln import speicher

CHAT = "4915100000000@c.us"


def run(c):
    return asyncio.run(c)


class KarteTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.frei = mock.patch.object(whatsapp, "frei", return_value=[{"id": CHAT, "name": "Kontakt F", "senden": True}])
        self.frei.start()

    def tearDown(self):
        self.frei.stop()
        self.env.stop()

    def regel(self, name="Kontakt F antwortet"):
        r = speicher.anlegen(name, "whatsapp", {"chat_id": CHAT, "name": "Kontakt F"}, "Antworte kurz.", [], ["push"])
        return speicher.bestaetigen(r["id"])

    def test_karte_ist_auch_ohne_regeln_da(self):
        k = run(regelkarte.karten())
        self.assertEqual(len(k), 1)
        self.assertEqual((k[0]["id"], k[0]["art"]), ("regeln", "regeln"))
        self.assertEqual((k[0]["aktiv"], k[0]["pausiert"], k[0]["laeufe"]), (0, 0, []))
        self.assertNotIn("bis", k[0])                    # läuft nie ab

    def test_zeigt_zaehler_und_die_letzten_laeufe_neueste_zuerst_hoechstens_4(self):
        r = self.regel()
        for i in range(20):
            speicher.lauf_ende(speicher.lauf_neu(r["id"], f"m{i}", f"Nachricht {i}"), "ok",
                               [{"werkzeug": "regel_push", "kurz": "Kontakt F: Hi", "status": "ok"}])
        k = run(regelkarte.karten())[0]
        self.assertEqual(k["aktiv"], 1)
        self.assertEqual(len(k["laeufe"]), 4)
        self.assertEqual(k["laeufe"][0]["ausloeser"], "Nachricht 19")
        l = k["laeufe"][0]
        self.assertEqual((l["regel"], l["status"], l["gesehen"], l["aktionen"]), ("Kontakt F antwortet", "ok", False, ["Kontakt F: Hi"]))
        self.assertLessEqual(len(l["ausloeser"]), 120)

    def test_pausierte_regeln_werden_gezaehlt(self):
        r = self.regel()
        speicher.setzen(r["id"], "pausiert", "3 Fehler")
        k = run(regelkarte.karten())[0]
        self.assertEqual((k["aktiv"], k["pausiert"]), (0, 1))
        self.assertEqual(k["pausiert_namen"], ["Kontakt F antwortet"])

    def test_karte_listet_die_regeln_mit_wann_und_status(self):
        self.regel("Kontakt F antwortet")
        zr = speicher.anlegen("Absage", "zeit", {"typ": "taeglich", "uhrzeit": "18:00"}, "Hi", [], ["push"])
        speicher.bestaetigen(zr["id"])
        k = run(regelkarte.karten())[0]
        namen = {r["name"]: r for r in k["regeln"]}
        self.assertEqual(set(namen), {"Kontakt F antwortet", "Absage"})
        self.assertIn("18:00", namen["Absage"]["wann"])
        self.assertEqual(namen["Absage"]["status"], "aktiv")
        self.assertIn("Kontakt F", namen["Kontakt F antwortet"]["wann"])

    def test_widerrufene_regeln_stehen_nicht_in_der_liste(self):
        r = self.regel("Weg")
        speicher.setzen(r["id"], "widerrufen")
        self.assertEqual(run(regelkarte.karten())[0]["regeln"], [])

    def test_loeschen_per_api_entfernt_regel_und_laeufe(self):
        from fastapi.testclient import TestClient
        from kern.main import app
        r = self.regel("Weg")
        speicher.lauf_ende(speicher.lauf_neu(r["id"], "m1", "Hi"), "ok", [])
        c = TestClient(app)
        self.assertEqual(c.post(f"/api/regeln/{r['id']}/loeschen").status_code, 200)
        self.assertIsNone(speicher.holen(r["id"]))
        self.assertEqual(speicher.laeufe(), [])
        self.assertEqual(c.post(f"/api/regeln/{r['id']}/loeschen").status_code, 404)

    def test_gesehen_nur_fuer_regel_ids(self):
        r = self.regel()
        lid = speicher.lauf_neu(r["id"], "m1", "Hi")
        self.assertFalse(regelkarte.gesehen("m17"))
        self.assertFalse(regelkarte.gesehen("regeln"))
        self.assertFalse(regelkarte.gesehen("regeln:99999"))
        self.assertFalse(regelkarte.gesehen("regeln:abc"))
        self.assertTrue(regelkarte.gesehen(f"regeln:{lid}"))
        self.assertTrue(speicher.laeufe()[0]["gesehen"])

    def test_anbieter_und_gesehen_sind_in_der_uebersicht_eingetragen(self):
        self.assertIn("regeln", [a.name for a in dashboardkarten.ANBIETER])
        a = next(a for a in dashboardkarten.ANBIETER if a.name == "regeln")
        self.assertIsNone(a.konnektor)
        r = self.regel()
        lid = speicher.lauf_neu(r["id"], "m1", "Hi")
        self.assertTrue(dashboardkarten.gesehen(f"regeln:{lid}"))
        alle = run(dashboardkarten.sammeln())["karten"]
        self.assertIn("regeln", [k["id"] for k in alle])


if __name__ == "__main__":
    unittest.main()
