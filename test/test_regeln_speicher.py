"""Regel-Speicher (`data/regeln.db`) — reine Logik, ohne Netz, im Temp-Ordner."""
import os
import tempfile
import unittest
from unittest import mock

from kern.dienste import whatsapp
from kern.personen import register
from kern.regeln import speicher


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.pid = register.anlegen("Kontakt F Beispiel", snapchat="paul_x")["id"]
        self.chat = "4915100000000@c.us"
        self.frei = mock.patch.object(whatsapp, "frei", return_value=[{"id": self.chat, "name": "Kontakt F", "senden": True}])
        self.frei.start()

    def tearDown(self):
        self.frei.stop()
        self.env.stop()

    def neu(self, **kw):
        a = dict(name="Kontakt F antwortet", kanal="whatsapp", ausloeser={"chat_id": self.chat, "name": "Kontakt F"},
                 auftrag="Antworte kurz und trag es in den Kalender ein.", empfaenger=[],
                 aktionen=["antwort_an_ausloeser", "kalender_eintragen"])
        a.update(kw)
        return speicher.anlegen(**a)


class Anlegen(Basis):
    def test_anlegen_ist_ein_entwurf_der_nicht_wirkt(self):
        r = self.neu()
        self.assertEqual(r["status"], "entwurf")
        self.assertEqual(speicher.holen(r["id"])["auftrag"], "Antworte kurz und trag es in den Kalender ein.")
        self.assertEqual(speicher.aktive(), [])

    def test_bestaetigen_macht_aktiv_und_nur_einmal(self):
        r = self.neu()
        self.assertEqual(speicher.bestaetigen(r["id"])["status"], "aktiv")
        self.assertEqual([x["id"] for x in speicher.aktive("whatsapp")], [r["id"]])
        self.assertEqual(speicher.aktive("snapchat"), [])
        with self.assertRaises(speicher.RegelFehler):
            speicher.bestaetigen(r["id"])

    def test_snapchat_regel_braucht_eine_bekannte_person(self):
        r = self.neu(kanal="snapchat", ausloeser={"person_id": self.pid, "name": "Kontakt F"})
        self.assertEqual(r["kanal"], "snapchat")
        with self.assertRaises(speicher.RegelFehler):
            self.neu(kanal="snapchat", ausloeser={"person_id": "p999", "name": "X"})

    def test_whatsapp_chat_muss_freigegeben_sein(self):
        with self.assertRaises(speicher.RegelFehler):
            self.neu(ausloeser={"chat_id": "4900@c.us", "name": "Fremd"})

    def test_empfaenger_person_und_chat_werden_geprueft(self):
        r = self.neu(empfaenger=[{"art": "person", "id": self.pid, "name": "Kontakt F Beispiel"},
                                 {"art": "chat", "id": self.chat, "name": "Kontakt F"}],
                     aktionen=["antwort_an_empfaenger"])
        self.assertEqual(len(r["empfaenger"]), 2)
        for schlecht in ({"art": "person", "id": "p999", "name": "X"}, {"art": "chat", "id": "x@c.us", "name": "X"},
                         {"art": "mail", "id": "a@b.example", "name": "X"}, "Kontakt P"):
            with self.assertRaises(speicher.RegelFehler, msg=schlecht):
                self.neu(empfaenger=[schlecht], aktionen=["antwort_an_empfaenger"])

    def test_ungueltiges_wird_abgelehnt(self):
        for kw in ({"kanal": "mail"}, {"aktionen": ["loeschen"]}, {"aktionen": []}, {"auftrag": "  "},
                   {"auftrag": "x" * (speicher.AUFTRAG_MAX + 1)}, {"name": ""}, {"limit_stunde": 0}, {"limit_tag": 999}):
            with self.assertRaises(speicher.RegelFehler, msg=kw):
                self.neu(**kw)

    def test_hoechstens_20_aktive(self):
        for i in range(speicher.MAX_AKTIV):
            speicher.bestaetigen(self.neu(name=f"R{i}")["id"])
        with self.assertRaises(speicher.RegelFehler):
            speicher.bestaetigen(self.neu(name="zu viel")["id"])

    def test_widerruf_loescht_nichts(self):
        r = self.neu()
        speicher.bestaetigen(r["id"])
        speicher.setzen(r["id"], "widerrufen")
        self.assertEqual(speicher.aktive(), [])
        self.assertEqual(speicher.holen(r["id"])["status"], "widerrufen")
        self.assertEqual(len(speicher.liste()), 1)


class Laeufe(Basis):
    def test_dasselbe_ereignis_loest_eine_regel_nur_einmal_aus(self):
        r = self.neu()
        self.assertIsInstance(speicher.lauf_neu(r["id"], "m1", "Hallo"), int)
        self.assertIsNone(speicher.lauf_neu(r["id"], "m1", "Hallo"))
        self.assertIsInstance(speicher.lauf_neu(r["id"], "m2", "Hallo"), int)

    def test_ausloeser_kurz_ist_hoechstens_120_zeichen(self):
        r = self.neu()
        lid = speicher.lauf_neu(r["id"], "m1", "x" * 500)
        self.assertLessEqual(len(speicher.laeufe()[0]["ausloeser_kurz"]), 120)
        self.assertIsNotNone(lid)

    def test_lauf_ende_zaehlen_und_gesehen(self):
        r = self.neu()
        lid = speicher.lauf_neu(r["id"], "m1", "Hallo")
        speicher.lauf_ende(lid, "ok", [{"werkzeug": "regel_push", "kurz": "Push", "status": "ok"}])
        l = speicher.laeufe()[0]
        self.assertEqual((l["status"], l["gesehen"], l["aktionen"][0]["werkzeug"]), ("ok", False, "regel_push"))
        self.assertEqual(speicher.laeufe_zaehlen(r["id"], "2000-01-01T00:00:00+00:00"), 1)
        self.assertEqual(speicher.laeufe_zaehlen(r["id"], "2999-01-01T00:00:00+00:00"), 0)
        self.assertTrue(speicher.gesehen_setzen(lid))
        self.assertTrue(speicher.laeufe()[0]["gesehen"])
        self.assertFalse(speicher.gesehen_setzen(99999))

    def test_aktionen_werden_sofort_angehaengt_und_ein_neustart_bricht_haengende_laeufe_ab(self):
        r = self.neu()
        lid = speicher.lauf_neu(r["id"], "m1", "Hi")
        speicher.lauf_aktion(lid, {"werkzeug": "regel_push", "kurz": "x", "status": "ok"})
        speicher.lauf_aktion(lid, {"werkzeug": "regel_antwort", "kurz": "y", "status": "ok"})
        self.assertEqual([a["werkzeug"] for a in speicher.laeufe()[0]["aktionen"]], ["regel_push", "regel_antwort"])
        lid2 = speicher.lauf_neu(r["id"], "m2", "Hi")
        speicher.lauf_ende(lid2, "ok", [])
        self.assertEqual(speicher.laufende_abbrechen(), 1)
        l = {x["id"]: x for x in speicher.laeufe()}
        self.assertEqual(l[lid]["status"], "fehler")
        self.assertIn("abgebrochen", l[lid]["aktionen"][-1]["kurz"])
        self.assertEqual(l[lid2]["status"], "ok")
        self.assertEqual(speicher.laufende_abbrechen(), 0)

    def test_letzte_status_neueste_zuerst(self):
        r = self.neu()
        for i, st in enumerate(("ok", "fehler", "fehler")):
            speicher.lauf_ende(speicher.lauf_neu(r["id"], f"m{i}", "x"), st, [])
        self.assertEqual(speicher.letzte_status(r["id"], 2), ["fehler", "fehler"])


if __name__ == "__main__":
    unittest.main()
