"""Freigaben: Kopie in die Gruppe der Person, Widerruf löscht sie — auch wenn sie noch nicht verarbeitet war."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import graph
from kern.personen import PersonenFehler, freigaben, register


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(),
                                                "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x")["id"]

    def tearDown(self):
        self.env.stop()


class Freigeben(Basis):
    def test_freigabe_geht_als_episode_in_die_gruppe_der_person(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            f = lauf(freigaben.freigeben(self.pid, "Eike hat im Oktober Geburtstag.", "Kontakt B fragte danach"))
        self.assertEqual(f["status"], "aktiv")
        a, k = ein.await_args.args, ein.await_args.kwargs
        self.assertEqual(k["gruppe"], self.pid)
        self.assertEqual(a[0], f"freigabe-{self.pid}-{f['id']}")
        self.assertEqual(a[1], "Eike hat im Oktober Geburtstag.")
        self.assertEqual([x["id"] for x in register.freigaben(self.pid)], [f["id"]])

    def test_nimmt_graphiti_nichts_an_gilt_die_freigabe_nicht(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=False)):
            with self.assertRaises(PersonenFehler):
                lauf(freigaben.freigeben(self.pid, "Geheim."))
        self.assertEqual([x["status"] for x in register.freigaben(self.pid)], ["fehlgeschlagen"])
        self.assertEqual([x for x in register.freigaben(self.pid) if x["status"] == "aktiv"], [])

    def test_leerer_text_unbekannte_person_und_boese_zeichen(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            for pid, text in ((self.pid, "  "), ("p99", "Text")):
                with self.assertRaises(PersonenFehler):
                    lauf(freigaben.freigeben(pid, text))
            lauf(freigaben.freigeben(self.pid, "Zeile zwei​" + "x" * 5000))
        gesendet = ein.await_args.args[1]
        self.assertNotIn(" ", gesendet)
        self.assertNotIn("​", gesendet)
        self.assertLessEqual(len(gesendet), register.PERSONA_MAX)

    def test_ids_zaehlen_hoch_und_werden_nie_wiederverwendet(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)):
            a = lauf(freigaben.freigeben(self.pid, "A"))
            b = lauf(freigaben.freigeben(self.pid, "B"))
        self.assertEqual([a["id"], b["id"]], ["f1", "f2"])


class Widerrufen(Basis):
    def _aktive(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)):
            return lauf(freigaben.freigeben(self.pid, "Eike mag Schach."))

    def test_widerruf_loescht_die_kopie_in_der_gruppe_der_person(self):
        f = self._aktive()
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=True)) as weg:
            r = lauf(freigaben.widerrufen(self.pid, f["id"]))
        weg.assert_awaited_once_with(f["episode"], self.pid)
        self.assertEqual((r["status"], r["geloescht"]), ("widerrufen", True))

    def test_widerruf_gilt_sofort_auch_wenn_das_loeschen_noch_nicht_geht(self):
        f = self._aktive()
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=None)):
            r = lauf(freigaben.widerrufen(self.pid, f["id"]))
        self.assertEqual((r["status"], r["geloescht"]), ("widerrufen", False))

    def test_nachlauf_loescht_spaeter_was_erst_dann_verarbeitet_wurde(self):
        f = self._aktive()
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=None)):
            lauf(freigaben.widerrufen(self.pid, f["id"]))
            self.assertEqual(lauf(freigaben.nachlauf()), 0)          # Episode noch nicht da
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=True)) as weg:
            self.assertEqual(lauf(freigaben.nachlauf()), 1)
            self.assertEqual(lauf(freigaben.nachlauf()), 0)          # nichts mehr offen
        weg.assert_awaited_once()
        self.assertTrue(register.freigaben(self.pid)[0]["geloescht"])

    def test_nachlauf_ruehrt_aktive_nicht_an_und_raeumt_fehlgeschlagene_vorsorglich_auf(self):
        aktiv = self._aktive()
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=False)):
            with self.assertRaises(PersonenFehler):
                lauf(freigaben.freigeben(self.pid, "X"))
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=None)) as weg:
            self.assertEqual(lauf(freigaben.nachlauf()), 0)
        weg.assert_awaited_once_with(f"freigabe-{self.pid}-f2", self.pid)   # nur die fehlgeschlagene, nie die aktive
        self.assertEqual(register.freigaben(self.pid)[0]["id"], aktiv["id"])

    def test_widerruf_einer_unbekannten_freigabe_oder_person(self):
        for pid, fid in ((self.pid, "f9"), ("p99", "f1")):
            with self.assertRaises(PersonenFehler):
                lauf(freigaben.widerrufen(pid, fid))

    def test_fehlgeschlagene_freigabe_ist_nicht_widerrufbar(self):
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=False)):
            with self.assertRaises(PersonenFehler):
                lauf(freigaben.freigeben(self.pid, "X"))
        with self.assertRaises(PersonenFehler):
            lauf(freigaben.widerrufen(self.pid, "f1"))

    def test_zweiter_widerruf_loescht_nicht_erneut(self):
        f = self._aktive()
        with mock.patch.object(graph, "episode_loeschen", mock.AsyncMock(return_value=True)) as weg:
            lauf(freigaben.widerrufen(self.pid, f["id"]))
            lauf(freigaben.widerrufen(self.pid, f["id"]))
        weg.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
