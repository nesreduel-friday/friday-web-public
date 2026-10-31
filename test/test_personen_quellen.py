"""Quellen-Verknüpfung: ein Kontakt A überall (04.10.2026)."""
import os
import tempfile
import unittest
from unittest import mock

from kern.dienste import whatsapp
from kern.garantie import ablage as garantieablage
from kern.personen import PersonenFehler, quellen, register


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.chats = mock.patch.object(quellen.whatsapp, "frei", return_value=[
            {"id": "491701234567@c.us", "name": "Kontakt A", "senden": False},
            {"id": "120363000000000000@g.us", "name": "Kontakt A", "senden": False},
            {"id": "491709876543@c.us", "name": "Fremder", "senden": False}])
        self.chats.start()
        self.profile = mock.patch.object(quellen.garantieablage, "standard")
        self.profile.start().return_value.profile.return_value = [
            {"id": 3, "name": "Kontakt A", "archiviert": False},
            {"id": 4, "name": "Alt", "archiviert": True}]
        self.pid = register.anlegen("Kontakt A", snapchat="kontakt_a_kudla")["id"]

    def tearDown(self):
        self.env.stop()


class Vorschlaege(Basis):
    def test_gleiche_namen_werden_vorgeschlagen_gruppen_nie(self):
        v = quellen.kandidaten()
        arten = {(x["quelle"], x["ref"]) for x in v if x["pid"] == self.pid}
        self.assertIn(("whatsapp", "491701234567@c.us"), arten)
        self.assertIn(("garantie", 3), arten)
        self.assertNotIn(("whatsapp", "120363000000000000@g.us"), [a[1] for a in arten])

    def test_verknuepftes_kommt_nicht_wieder(self):
        quellen.verknuepfen(self.pid, "whatsapp", "491701234567@c.us")
        arten = {(x["quelle"]) for x in quellen.kandidaten() if x["pid"] == self.pid}
        self.assertNotIn("whatsapp", arten)
        self.assertIn("garantie", arten)

    def test_fremde_namen_kommen_nicht(self):
        v = [x for x in quellen.kandidaten() if x["anzeige"] == "Fremder"]
        self.assertEqual(v, [])


class Knuepfen(Basis):
    def test_verknuepfen_merkt_chat_und_profil(self):
        p = quellen.verknuepfen(self.pid, "whatsapp", "491701234567@c.us")
        self.assertEqual(p["quellen"], {"whatsapp": "491701234567@c.us"})
        p = quellen.verknuepfen(self.pid, "garantie", 3)
        self.assertEqual(p["quellen"], {"whatsapp": "491701234567@c.us", "garantie": 3})
        self.assertEqual(quellen.stand()[self.pid]["whatsapp"], "Kontakt A")

    def test_falscher_name_wird_abgewiesen(self):
        with self.assertRaises(PersonenFehler):
            quellen.verknuepfen(self.pid, "whatsapp", "491709876543@c.us")
        with self.assertRaises(PersonenFehler):
            quellen.verknuepfen(self.pid, "garantie", 4)
        with self.assertRaises(PersonenFehler):
            quellen.verknuepfen(self.pid, "mail", "x")
        self.assertEqual(register.holen(self.pid).get("quellen"), {})

    def test_entknuepfen_loest_nur_die_klammer(self):
        quellen.verknuepfen(self.pid, "garantie", 3)
        p = quellen.entknuepfen(self.pid, "garantie")
        self.assertEqual(p["quellen"], {})
        self.assertEqual(register.holen(self.pid)["name"], "Kontakt A")

    def test_register_prueft_quellen(self):
        with self.assertRaises(PersonenFehler):
            register.aendern(self.pid, quellen={"mail": "x"})
        with self.assertRaises(PersonenFehler):
            register.aendern(self.pid, quellen={"garantie": "keine-zahl"})
        p = register.aendern(self.pid, quellen={"garantie": 3})
        self.assertEqual(p["quellen"], {"garantie": 3})


if __name__ == "__main__":
    unittest.main()


class Punkte(Basis):
    def test_exakt_zaehlt_mehr_und_sortiert(self):
        v = quellen.kandidaten()
        for x in v:
            self.assertIn("punkte", x)
            self.assertIn("warum", x)
        punkte = [x["punkte"] for x in v]
        self.assertEqual(punkte, sorted(punkte, reverse=True))
        exakt = [x for x in v if x["ref"] == 3][0]
        self.assertIn("Name gleich", exakt["warum"])


class Adresse(Basis):
    """Kundenkarte-Adresse ins Wissen (nie IBAN/Mail), raus bei Entknüpfung."""

    def _profil(self):
        voll = mock.patch.object(quellen.garantieablage, "standard")
        status = voll.start().return_value
        status.profile.return_value = [{"id": 3, "name": "Kontakt A", "archiviert": False}]
        status.profil.side_effect = lambda nummer: {
            "id": 3, "name": "Kontakt A", "strasse": "Beispielweg 11", "plz": "12345", "ort": "Musterhausen",
            "iban": "DE00 1234", "email": "tim@example.test"}
        self.addCleanup(voll.stop)

    def test_verknuepfen_legt_adresse_in_beide_gruppen(self):
        import asyncio
        self._profil()
        quellen.verknuepfen(self.pid, "garantie", 3)
        from kern.gedaechtnis import graph
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            self.assertTrue(asyncio.run(quellen.profil_ins_gedaechtnis(self.pid)))
        self.assertEqual(ein.await_count, 2)
        gruppen = {c.kwargs["gruppe"] for c in ein.await_args_list}
        self.assertEqual(gruppen, {self.pid, "eike"})
        texte = " ".join(c.args[1] for c in ein.await_args_list)
        self.assertIn("Musterhausen", texte)
        for verboten in ("DE00", "tim@example", "IBAN", "iban"):
            self.assertNotIn(verboten, texte)

    def test_ohne_adresse_und_ohne_link_nichts(self):
        import asyncio
        from kern.gedaechtnis import graph
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock()) as ein:
            self.assertFalse(asyncio.run(quellen.profil_ins_gedaechtnis(self.pid)))
        ein.assert_not_awaited()

    def test_entknuepfen_raeumt_beide_gruppen(self):
        import asyncio
        quellen.verknuepfen(self.pid, "garantie", 3)
        from kern.gedaechtnis import graph
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=True)) as weg:
            asyncio.run(quellen.profil_aus_dem_gedaechtnis(self.pid))
        gruppen = {c.args[1] for c in weg.await_args_list}
        self.assertEqual(gruppen, {self.pid, "eike"})
