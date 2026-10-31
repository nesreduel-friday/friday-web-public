"""Unbekannte Kontakte: gesammelt, nie beantwortet, Eike bekommt genau einen Push."""
import os
import tempfile
import unittest
from unittest import mock

from kern.dienste import benachrichtigungen
from kern.personen import register, unbekannt


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        p = mock.patch.object(benachrichtigungen, "senden", return_value={"gesendet": 1})
        self.senden = p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.env.stop()


class Unbekannt(Basis):
    def test_neuer_name_wird_vermerkt_und_gemeldet_einmal(self):
        self.assertTrue(unbekannt.melden("snapchat", "Test"))
        self.assertFalse(unbekannt.melden("snapchat", " test "))  # derselbe Kontakt, andere Schreibweise
        self.assertEqual([u["anzeige"] for u in unbekannt.liste()], ["Test"])
        self.senden.assert_called_once()
        self.assertEqual(self.senden.call_args.args[0], "kontakte")

    def test_bekannte_person_wird_nicht_als_unbekannt_gemeldet(self):
        register.anlegen("Kontakt B", snapchat="Kontakt B")
        self.assertFalse(unbekannt.melden("snapchat", "Kontakt B"))
        self.assertEqual(unbekannt.liste(), [])
        self.senden.assert_not_called()

    def test_anlegen_nimmt_den_kontakt_aus_der_liste(self):
        unbekannt.melden("snapchat", "Test")
        register.anlegen("Testfreund", snapchat="test")
        self.assertEqual(unbekannt.liste(), [])

    def test_nachtraeglich_eingetragener_snapchat_name_nimmt_den_kontakt_aus_der_liste(self):
        """18.10.2026: Anna Lena Wolf war angelegt, der Snapchat-Name kam erst später dazu — der Vorschlag blieb stehen."""
        p = register.anlegen("Anna Lena Wolf")
        unbekannt.melden("snapchat", "Anna Lena Wolf")
        self.assertEqual(len(unbekannt.liste()), 1)
        register.aendern(p["id"], snapchat="Anna Lena Wolf")
        self.assertEqual(unbekannt.liste(), [])

    def test_ignoriert_kommt_nicht_wieder_und_nicht_erneut_als_push(self):
        unbekannt.melden("snapchat", "Spam")
        unbekannt.ignorieren("snapchat", "Spam")
        self.assertEqual(unbekannt.liste(), [])
        self.assertFalse(unbekannt.melden("snapchat", "Spam"))
        self.assertEqual(self.senden.call_count, 1)

    def test_eikes_eigener_name_und_leere_namen_werden_nie_gemeldet(self):
        for n in ("Eike", "ME", "Friday", "", "   "):
            self.assertFalse(unbekannt.melden("snapchat", n), n)
        self.senden.assert_not_called()

    def test_boeser_name_wird_gekuerzt_und_ohne_steuerzeichen_gespeichert(self):
        unbekannt.melden("snapchat", "<script>x</script>\n\x00" + "a" * 500)
        a = unbekannt.liste()[0]["anzeige"]
        self.assertNotIn("\x00", a)
        self.assertNotIn("\n", a)
        self.assertLessEqual(len(a), register.ANZEIGE_MAX)

    def test_ein_fehler_beim_push_verhindert_den_vermerk_nicht(self):
        self.senden.side_effect = RuntimeError("push kaputt")
        self.assertTrue(unbekannt.melden("snapchat", "Test"))
        self.assertEqual(len(unbekannt.liste()), 1)

    def test_art_kontakte_ist_bekannt_und_standardmaessig_an(self):
        art = next(a for a in benachrichtigungen.ARTEN if a["id"] == "kontakte")
        self.assertTrue(art["standard"])


if __name__ == "__main__":
    unittest.main()
