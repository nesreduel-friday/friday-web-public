"""Rückblick für Gespräche mit anderen Personen: eigene Gruppe, richtiger Sprecher, einmalige Kopie an Eike."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import rueckblick
from kern.personen import register
from kern.protokoll import speicher as pspeicher
from kern.werkzeuge import gedaechtnis as gwerkzeug
from kern.werkzeuge import katalog


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(),
                                                "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()
        self.p = pspeicher.standard()
        self.person = register.anlegen("Kontakt B", snapchat="maya_x")["id"]
        self.g = self.p.gespraech_beginnen("snapchat", person=self.person)
        self.p.beitrag(self.g, "du", "Ich mag Schach")
        self.p.beitrag(self.g, "friday", "Schön!")
        self.p.zusammenfassen(self.g, "Schach")

    def tearDown(self):
        self.env.stop()


class Einspeisen(Basis):
    def test_gespraech_geht_in_die_gruppe_der_person_mit_ihrem_namen_als_sprecher(self):
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            lauf(rueckblick.einspeisen(self.g))
        senden.assert_awaited_once()
        a, k = senden.await_args.args, senden.await_args.kwargs
        self.assertEqual(k["gruppe"], self.person)
        self.assertEqual(a[0], f"gespraech-{self.g}")
        self.assertIn("Kontakt B: Ich mag Schach", a[1])
        self.assertNotIn("Eike:", a[1])
        self.assertIn("Kontakt B", k["anweisungen"])
        self.assertEqual(self.p.kopf(self.g)["graph_status"], "gesendet")

    def test_eikes_eigene_gespraeche_laufen_unveraendert(self):
        eigen = self.p.gespraech_beginnen("chat")
        self.p.beitrag(eigen, "du", "Hallo")
        self.p.zusammenfassen(eigen, "z")
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            lauf(rueckblick.einspeisen(eigen))
        self.assertNotIn("gruppe", senden.await_args.kwargs)
        self.assertNotIn("anweisungen", senden.await_args.kwargs)
        self.assertIn("Eike: Hallo", senden.await_args.args[1])

    def test_unbekannte_person_im_register_wird_nicht_in_eine_gruppe_geraten(self):
        g = self.p.gespraech_beginnen("snapchat", person="p99")
        self.p.beitrag(g, "du", "x")
        self.p.zusammenfassen(g, "z")
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            lauf(rueckblick.einspeisen(g))
        senden.assert_not_awaited()  # nichts wird an Graphiti geschickt, wenn die Person nicht (mehr) existiert
        self.assertEqual(self.p.kopf(g)["graph_status"], "aufgegeben")   # blockiert die Warteschlange nicht
        self.assertNotIn(g, self.p.graph_offen())


class Nachsehen(Basis):
    def test_angekommen_wird_in_der_gruppe_der_person_gesucht(self):
        self.p.graph_markieren(self.g, "gesendet", neuer_versuch=True)
        with mock.patch("kern.gedaechtnis.graph.angekommen", mock.AsyncMock(return_value=True)) as da:
            lauf(rueckblick.nachsehen(self.g))
        self.assertEqual(da.await_args.kwargs["gruppe"], self.person)
        self.assertEqual(self.p.kopf(self.g)["graph_status"], "angekommen")


class Kopie(Basis):
    def _angekommen(self):
        self.p.graph_markieren(self.g, "angekommen")

    def test_kopie_an_eike_mit_vermerk_und_herkunft_genau_einmal(self):
        self._angekommen()
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            lauf(rueckblick.kopie_an_eike(self.g))
            lauf(rueckblick.kopie_an_eike(self.g))  # zweiter Aufruf: schon erledigt
        senden.assert_awaited_once()
        a, k = senden.await_args.args, senden.await_args.kwargs
        self.assertNotIn("gruppe", k)  # Eikes Gruppe ist der Standard
        self.assertEqual(a[0], f"gespraech-{self.g}-von-{self.person}")
        self.assertIn("Kontakt B", a[1].splitlines()[0])
        self.assertIn("nicht Eike", a[1].splitlines()[0])
        self.assertIn("Kontakt B: Ich mag Schach", a[1])
        self.assertIn("Kontakt B", k["anweisungen"])
        self.assertEqual(self.p.eike_kopie_offen(), [])

    def test_schlaegt_die_kopie_fehl_bleibt_sie_offen_und_wird_wiederholt(self):
        self._angekommen()
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=False)):
            lauf(rueckblick.kopie_an_eike(self.g))
        self.assertEqual(self.p.eike_kopie_offen(), [self.g])
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)):
            lauf(rueckblick.kopie_an_eike(self.g))
        self.assertEqual(self.p.eike_kopie_offen(), [])

    def test_kein_kopieren_fuer_eikes_eigene_gespraeche(self):
        eigen = self.p.gespraech_beginnen("chat")
        with mock.patch("kern.gedaechtnis.graph.einspeisen", mock.AsyncMock(return_value=True)) as senden:
            lauf(rueckblick.kopie_an_eike(eigen))
        senden.assert_not_awaited()


class WerkzeugPersonFilter(Basis):
    """Eike fragt „Hast du mit Kontakt B geschrieben?“ — der Name steht nie im Text,
    nur in `gespraeche.person`. Die Textsuche fände das Gespräch nicht."""

    def test_textsuche_nach_namen_findet_nichts_person_filter_schon(self):
        # Gegenprobe auf Protokoll-Ebene: Der Name steht nie im Text.
        self.assertEqual(self.p.rueckblick("Kontakt B", 7), [])
        text = gwerkzeug.rueckblick("", 7, person="Kontakt B")
        self.assertIn("[Gespräch von Kontakt B", text)
        self.assertIn("Schach", text)
        self.assertTrue(katalog.liefert_fremdes("rueckblick", {"ergebnis": text}))

    def test_details_tragen_je_einen_zeitstempel(self):
        # Eike sieht, ob gerade geschrieben wird oder die Antwort einen Tag später kam.
        import re
        text = gwerkzeug.rueckblick("", 7, person="Kontakt B")
        self.assertRegex(text, r"Kontakt B \[\d{2}\.\d{2}\. \d{2}:\d{2}\]: Ich mag Schach")
        self.assertRegex(text, r"Friday \[\d{2}\.\d{2}\. \d{2}:\d{2}\]: Schön!")

    def test_laufendes_gespraech_ohne_zusammenfassung_zeigt_nur_nachrichten(self):
        # „Noch ohne Zusammenfassung“ ist Innenleben und hilft Eike nichts — die Nachrichten sind die Antwort.
        g = self.p.gespraech_beginnen("snapchat", person=self.person)
        self.p.beitrag(g, "du", "Bist du da?")
        text = gwerkzeug.rueckblick("", 7, person="Kontakt B")
        self.assertNotIn("ohne Zusammenfassung", text)
        self.assertIn("die letzten Nachrichten:", text)
        self.assertIn("Bist du da?", text)

    def test_name_als_suchbegriff_faellt_auf_person_zurueck(self):
        # Ruft das Modell rueckblick(suchbegriff="Kontakt B") statt person="Kontakt B", bliebe die Textsuche leer —
        # statt „keine Gespräche“ kommt die Person-Antwort.
        text = gwerkzeug.rueckblick("Kontakt B", 7)
        self.assertIn("[Gespräch von Kontakt B", text)
        self.assertIn("Ich mag Schach", text)

    def test_snapchat_anzeigename_loest_dieselbe_person_auf(self):
        text = gwerkzeug.rueckblick("", 7, person="maya_x")
        self.assertIn("[Gespräch von Kontakt B", text)
        self.assertIn("Snapchat", text)

    def test_unbekannter_name_ist_ehrlich_kein_raten(self):
        text = gwerkzeug.rueckblick("", 7, person="Jemand Unbekanntes")
        self.assertIn("kenne ich nicht als Kontakt", text)

    def test_verhoerter_name_wird_verstanden(self):
        # Die Spracherkennung macht aus „Kontakt C“ ein „Kontakt T“ — genau ein ähnlicher Kontakt zählt.
        pid = register.anlegen("Lehrkraft 1", snapchat="lehrkraft_1")["id"]
        g = self.p.gespraech_beginnen("snapchat", person=pid)
        self.p.beitrag(g, "du", "Hey")
        self.p.zusammenfassen(g, "Gruß")
        text = gwerkzeug.rueckblick("", 7, person="Lehrkraftt")
        self.assertIn("[Gespräch von Lehrkraft 1", text)
        self.assertIn("als Lehrkraft 1 verstanden", text)

    def test_mehrdeutiger_name_fragt_nach_statt_zu_raten(self):
        register.anlegen("Lehrkraft 1", snapchat="lehrkraft_1")
        register.anlegen("Lehrkraft 2", snapchat="lehrkraft_2")
        text = gwerkzeug.rueckblick("", 7, person="Lehrkraft")
        self.assertIn("meintest du", text)
        self.assertIn("Lehrkraft 1", text)
        self.assertIn("Lehrkraft 2", text)
        self.assertNotIn("[Gespräch von", text)

    def test_absender_hinweis_ordnet_fridays_zeilen_friday_zu(self):
        # 02.10.2026: Das Modell gab Fridays eigene Snapchat-Antworten als Eikes Worte aus.
        text = gwerkzeug.rueckblick("", 7, person="Kontakt B")
        self.assertIn("hast du selbst", text)
        self.assertIn("nicht Eike", text)

    def test_person_ohne_gespraech_ist_ehrlich(self):
        register.anlegen("Lehrkraft 1", snapchat="lehrkraft_1")
        text = gwerkzeug.rueckblick("", 7, person="Lehrkraft 1")
        self.assertIn("keine Gespräche mit Lehrkraft 1", text)

    def test_protokoll_filtert_nach_person(self):
        treffer = self.p.rueckblick("", 7, person=self.person)
        self.assertEqual([g["id"] for g in treffer], [self.g])
        eigen = self.p.gespraech_beginnen("chat")
        self.p.beitrag(eigen, "du", "Hallo")
        ids = [g["id"] for g in self.p.rueckblick("", 7, person=self.person)]
        self.assertEqual(ids, [self.g])


if __name__ == "__main__":
    unittest.main()
