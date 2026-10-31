"""Fokus-Personen: Ablage der markierten Personen, Zeiger und „Neu“-Einträge."""
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kern.fokus import ablage

U = "11111111-1111-1111-1111-111111111111"
JETZT = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class Markieren(Basis):
    def test_markieren_und_entfernen(self):
        self.assertTrue(ablage.markieren(U, "Summer Muster", True))
        self.assertEqual([p["name"] for p in ablage.liste()], ["Summer Muster"])
        self.assertFalse(ablage.markieren(U, "Summer Muster", False))
        self.assertEqual(ablage.liste(), [])

    def test_doppeltes_markieren_aendert_nichts(self):
        ablage.markieren(U, "Summer", True)
        ablage.markieren(U, "Summer", True)
        self.assertEqual(len(ablage.liste()), 1)

    def test_entfernen_loescht_neu_und_zeiger(self):
        ablage.markieren(U, "Summer", True)
        ablage.neu_ablegen(U, "whatsapp", ["Info"], JETZT)
        ablage.zeiger_setzen(U, "whatsapp", {"chats": {"a@c.us": 5}})
        ablage.markieren(U, "Summer", False)
        ablage.markieren(U, "Summer", True)                    # neu markiert: leer
        self.assertEqual(ablage.holen(U)["neu"], [])
        self.assertEqual(ablage.zeiger(U, "whatsapp"), {"chats": {}})

    def test_datei_hat_rechte_600(self):
        ablage.markieren(U, "Summer", True)
        modus = stat.S_IMODE(os.stat(os.path.join(self.ordner, "fokus.json")).st_mode)
        self.assertEqual(modus, 0o600)

    def test_kaputte_datei_ist_leere_liste(self):
        with open(os.path.join(self.ordner, "fokus.json"), "w") as f:
            f.write("{kaputt")
        self.assertEqual(ablage.liste(), [])
        self.assertTrue(ablage.markieren(U, "Summer", True))   # und sie lässt sich überschreiben

    def test_namen(self):
        ablage.markieren(U, "Summer Muster", True)
        self.assertEqual(ablage.namen(), ["Summer Muster"])


class Neu(Basis):
    def setUp(self):
        super().setUp()
        ablage.markieren(U, "Summer", True)

    def test_gleicher_text_innerhalb_24h_wird_nicht_doppelt_abgelegt(self):
        self.assertEqual(ablage.neu_ablegen(U, "whatsapp", ["Info am Freitag"], JETZT), 1)
        self.assertEqual(ablage.neu_ablegen(U, "whatsapp", ["Info am Freitag"], JETZT + timedelta(hours=3)), 0)
        self.assertEqual(ablage.neu_ablegen(U, "whatsapp", ["Info am Freitag"], JETZT + timedelta(hours=25)), 1)

    def test_eintraege_aelter_als_14_tage_fallen_weg(self):
        ablage.neu_ablegen(U, "mail", ["alt"], JETZT - timedelta(days=15))
        ablage.neu_ablegen(U, "mail", ["neu"], JETZT)
        self.assertEqual([n["text"] for n in ablage.holen(U)["neu"]], ["neu"])

    def test_hoechstens_20_neueste_zuerst(self):
        for i in range(25):
            ablage.neu_ablegen(U, "mail", [f"Aussage {i}"], JETZT + timedelta(minutes=i))
        neu = ablage.holen(U)["neu"]
        self.assertEqual(len(neu), 20)
        self.assertEqual(neu[0]["text"], "Aussage 24")

    def test_leere_texte_werden_ignoriert(self):
        self.assertEqual(ablage.neu_ablegen(U, "mail", ["", "   "], JETZT), 0)

    def test_unbekannte_person_ist_kein_fehler(self):
        self.assertEqual(ablage.neu_ablegen("x", "mail", ["a"], JETZT), 0)


class Episoden(Basis):
    def test_vormerken_erledigen_und_obergrenze(self):
        ablage.markieren(U, "Summer", True)
        for i in range(25):
            ablage.episode_vormerken(U, {"name": f"e{i}", "text": "t", "beschreibung": "b", "zeit": "z"})
        self.assertEqual(len(ablage.episoden_offen(U)), ablage.EPISODEN_MAX)
        ablage.episode_erledigt(U, "e24")
        self.assertNotIn("e24", [e["name"] for e in ablage.episoden_offen(U)])

    def test_gleicher_name_ersetzt_statt_zu_verdoppeln(self):
        ablage.markieren(U, "Summer", True)
        ablage.episode_vormerken(U, {"name": "e", "text": "alt", "beschreibung": "b", "zeit": "z"})
        ablage.episode_vormerken(U, {"name": "e", "text": "neu", "beschreibung": "b", "zeit": "z"})
        self.assertEqual([e["text"] for e in ablage.episoden_offen(U)], ["neu"])

    def test_entfernen_loescht_auch_vorgemerktes(self):
        ablage.markieren(U, "Summer", True)
        ablage.episode_vormerken(U, {"name": "e", "text": "t", "beschreibung": "b", "zeit": "z"})
        ablage.markieren(U, "Summer", False)
        ablage.markieren(U, "Summer", True)
        self.assertEqual(ablage.episoden_offen(U), [])


    def test_neu_aufbauen_raeumt_ab_aber_die_markierung_bleibt(self):
        ablage.markieren(U, "Summer", True)
        ablage.neu_ablegen(U, "whatsapp", ["Alt"], JETZT)
        ablage.zeiger_setzen(U, "whatsapp", {"chats": {"a@c.us": 5}})
        ablage.episode_vormerken(U, {"name": "e", "text": "t", "beschreibung": "b", "zeit": "z"})
        self.assertTrue(ablage.neu_aufbauen(U))
        p = ablage.holen(U)
        self.assertEqual((p["name"], p["neu"], p["episoden_offen"], p["quellen"]["whatsapp"]), ("Summer", [], [], {"chats": {}}))
        self.assertFalse(ablage.neu_aufbauen("unbekannt"))


class Zuordnung(Basis):
    def test_zuordnen_zuruecknehmen_und_unklar_wird_abgeraeumt(self):
        ablage.markieren(U, "Kontakt F Muster", True)
        ablage.unklar_setzen(U, [{"chat_id": "12@g.us", "chat": "JG12", "absender": "Kontakt F", "anzahl": 2}])
        self.assertTrue(ablage.zuordnung_setzen(U, "12@g.us", "Kontakt F", True))
        self.assertEqual(ablage.zeiger(U, "whatsapp")["zuordnung"], {"12@g.us": ["Kontakt F"]})
        self.assertEqual(ablage.holen(U)["unklar"], [])
        self.assertFalse(ablage.zuordnung_setzen(U, "12@g.us", "kontakt f", False))          # Groß-/Kleinschreibung egal
        self.assertEqual(ablage.zeiger(U, "whatsapp")["zuordnung"], {})

    def test_leerer_absender_und_unbekannte_person(self):
        ablage.markieren(U, "Kontakt F Muster", True)
        self.assertFalse(ablage.zuordnung_setzen(U, "12@g.us", "  ", True))
        self.assertFalse(ablage.zuordnung_setzen("x", "12@g.us", "Kontakt F", True))

    def test_neu_aufbauen_behaelt_die_zuordnung(self):
        ablage.markieren(U, "Kontakt F Muster", True)
        ablage.zuordnung_setzen(U, "12@g.us", "Kontakt F", True)
        ablage.neu_aufbauen(U)
        self.assertEqual(ablage.zeiger(U, "whatsapp")["zuordnung"], {"12@g.us": ["Kontakt F"]})


class Zaehler(Basis):
    def test_aufrufe_zaehlen_und_tageswechsel(self):
        with mock.patch.object(ablage, "_heute", return_value="2026-10-05"):
            self.assertEqual(ablage.aufruf_zaehlen(), 1)
            self.assertEqual(ablage.aufruf_zaehlen(), 2)
            self.assertEqual(ablage.aufrufe_heute(), 2)
        with mock.patch.object(ablage, "_heute", return_value="2026-10-06"):
            self.assertEqual(ablage.aufrufe_heute(), 0)


if __name__ == "__main__":
    unittest.main()
