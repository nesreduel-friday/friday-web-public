"""Personen-Blick: eine Person, egal in welcher Quelle (04.10.2026)."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.chats import ablage as chatablage
from kern.personen import blick, register
from kern.werkzeuge import chats as werkzeug


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        a = chatablage.standard()
        self.gabriel = a.person_anlegen("Kontakt M", "Freund")
        self.graph = mock.patch.object(werkzeug, "_graph_fakten", mock.AsyncMock(return_value=[]))
        self.graph.start()
        self.garantie = mock.patch.object(blick.garantieablage, "standard")
        self.garantie.start().return_value.profile.return_value = [
            {"id": 4, "vorname": "Kontakt O", "nachname": "", "name": "Kontakt O", "strasse": "Weg 1",
             "plz": "30000", "ort": "Hannover", "iban": "DE00GEHEIM", "email": "geheim@x.de", "kundenkarte": "123",
             "archiviert": False},
            {"id": 5, "vorname": "Lehrkraft 1", "nachname": "Muster", "name": "Lehrkraft 1 Muster", "ort": "Musterhausen", "archiviert": False},
            {"id": 6, "vorname": "Alt", "nachname": "Archiv", "name": "Alt", "archiviert": True}]

    def tearDown(self):
        self.garantie.stop()
        self.graph.stop()
        self.env.stop()


class Finden(Basis):
    def test_vorname_findet_person_nur_in_den_fahrkarten_profilen(self):
        gruppen, _ = blick.finden("Kontakt O")
        self.assertEqual([g["name"] for g in gruppen], ["Kontakt O"])

    def test_wohnort_kommt_ohne_geheimnisse(self):
        text = asyncio.run(werkzeug.person_ueber("Kontakt O"))
        self.assertIn("Weg 1", text)
        self.assertIn("Hannover", text)
        for geheim in ("DE00GEHEIM", "geheim@x.de", "123"):
            self.assertNotIn(geheim, text)

    def test_gleicher_vorname_zwei_menschen_wird_nicht_geraten(self):
        gruppen, _ = blick.finden("Kontakt")
        self.assertEqual({g["name"] for g in gruppen}, {"Kontakt M", "Kontakt O"})
        self.assertIn("passt auf 2 Menschen", asyncio.run(werkzeug.person_ueber("Kontakt")))

    def test_aehnlicher_nachname_ist_derselbe_mensch(self):
        register.anlegen("Lehrkraft 1 Muster")
        gruppen, _ = blick.finden("Muster")
        self.assertEqual(len(gruppen), 1)
        self.assertEqual(len(gruppen[0]["eintraege"]), 2)        # Register + Fahrkarten-Profil

    def test_archivierte_und_unbekannte_gibt_es_nicht(self):
        self.assertEqual(blick.finden("Archiv")[0], [])
        self.assertIn("nichts", asyncio.run(werkzeug.person_ueber("Xaver")))

    def test_verhoerter_name_wird_vorgeschlagen(self):
        text = asyncio.run(werkzeug.person_ueber("Kontak O"))
        self.assertIn("Kontakt O", text)


class Korrigieren(Basis):
    def test_nachname_wird_in_verlaeufen_und_kontakten_geaendert(self):
        register.anlegen("Kontakt M")
        antwort = werkzeug.person_korrigieren("Kontakt M", "Kontakt M Muster")
        self.assertIn("Erledigt", antwort)
        self.assertEqual([p["name"] for p in chatablage.standard().personen() if p["id"] == self.gabriel],
                         ["Kontakt M Muster"])
        self.assertEqual([p["name"] for p in register.liste()], ["Kontakt M Muster"])

    def test_mehrdeutig_aendert_nichts(self):
        antwort = werkzeug.person_korrigieren("Kontakt", "Kontakt M Muster")
        self.assertIn("nichts geändert", antwort)
        self.assertEqual(chatablage.standard().personen()[0]["name"], "Kontakt M")

    def test_nur_fahrkarten_profil_bleibt_unberuehrt(self):
        self.assertIn("ändere ich nicht", werkzeug.person_korrigieren("Kontakt O", "Kontakt Q"))


class GraphFilter(unittest.TestCase):
    def test_fremde_fakten_der_unscharfen_suche_fliegen_raus(self):
        fakten = [{"fact": "Eike besitzt ein iPhone."}, {"fact": "Kontakt O ist in der zwölften Klasse."},
                  {"fact": "Eike geht in die Klasse 12."}, {"fact": "Xaver wohnt in Musterhausen."},
                  {"fact": "Xaver mochte Kapern.", "invalid_at": "2026-01-01"}]
        with mock.patch("kern.gedaechtnis.graph.url", return_value="http://x"), \
             mock.patch("kern.gedaechtnis.graph.suchen", mock.AsyncMock(return_value=fakten)):
            self.assertEqual(asyncio.run(werkzeug._graph_fakten("Xaver")), ["- Xaver wohnt in Musterhausen."])
            self.assertEqual(asyncio.run(werkzeug._graph_fakten("Kontakt O")), ["- Kontakt O ist in der zwölften Klasse."])
