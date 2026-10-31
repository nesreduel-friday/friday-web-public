"""Rückfragen an Eike: Limit, Dedup, Verfall, Freigabe und Ablehnung."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kern.dienste import benachrichtigungen
from kern.gedaechtnis import graph
from kern.personen import PersonenFehler, register, rueckfragen


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(),
                                                "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()
        p = mock.patch.object(benachrichtigungen, "senden", return_value={"gesendet": 1})
        self.push = p.start()
        self.addCleanup(p.stop)
        self.pid = register.anlegen("Kontakt B", snapchat="maya_x")["id"]

    def tearDown(self):
        self.env.stop()


class Stellen(Basis):
    def test_rueckfrage_wird_abgelegt_und_eike_bekommt_einen_push(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag", "Wann hat Eike Geburtstag?")
        self.assertEqual((a["person"], a["thema"], a["status"]), (self.pid, "Geburtstag", "offen"))
        self.assertEqual([x["id"] for x in rueckfragen.offene()], [a["id"]])
        self.assertEqual(rueckfragen.offene()[0]["person_name"], "Kontakt B")
        self.push.assert_called_once()
        self.assertEqual(self.push.call_args.args[0], "kontakte")
        self.assertIn("Kontakt B", self.push.call_args.args[1] + self.push.call_args.args[2])

    def test_dieselbe_frage_nicht_doppelt_und_kein_zweiter_push(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag", "x")
        b = rueckfragen.stellen(self.pid, " GEBURTSTAG ", "y")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(rueckfragen.offene()), 1)
        self.assertEqual(self.push.call_count, 1)

    def test_hoechstens_fuenf_offene_je_person(self):
        for i in range(rueckfragen.MAX_OFFEN):
            rueckfragen.stellen(self.pid, f"Thema {i}")
        with self.assertRaises(PersonenFehler):
            rueckfragen.stellen(self.pid, "Noch eins")
        anderer = register.anlegen("Kontakt F", snapchat="kontakt_f")["id"]
        rueckfragen.stellen(anderer, "Thema")                       # das Limit gilt je Person
        self.assertEqual(self.push.call_count, rueckfragen.MAX_OFFEN + 1)

    def test_unbekannte_pausierte_person_und_leeres_thema(self):
        for pid, thema in (("p99", "X"), (self.pid, "   ")):
            with self.assertRaises(PersonenFehler):
                rueckfragen.stellen(pid, thema)
        register.aendern(self.pid, pausiert=True)
        with self.assertRaises(PersonenFehler):
            rueckfragen.stellen(self.pid, "X")

    def test_boese_texte_werden_bereinigt_und_gekuerzt(self):
        a = rueckfragen.stellen(self.pid, "<script>x</script>\n ​" + "a" * 500, "w\x00" + "b" * 2000)
        for wert in (a["thema"], a["wunsch"]):
            for boese in ("\n", " ", "​", "\x00"):
                self.assertNotIn(boese, wert)
        self.assertLessEqual(len(a["thema"]), 120)
        self.assertLessEqual(len(a["wunsch"]), 400)

    def test_ein_push_fehler_verhindert_die_rueckfrage_nicht(self):
        self.push.side_effect = RuntimeError("weg")
        self.assertEqual(len(rueckfragen.offene()), 0)
        rueckfragen.stellen(self.pid, "Thema")
        self.assertEqual(len(rueckfragen.offene()), 1)


class Beantworten(Basis):
    def test_freigeben_legt_die_freigabe_an_und_schliesst_die_rueckfrage(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag")
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)) as ein:
            r = lauf(rueckfragen.freigeben(a["id"], "Eike hat im Oktober Geburtstag."))
        self.assertEqual(r["status"], "freigegeben")
        self.assertEqual(ein.await_args.kwargs["gruppe"], self.pid)
        self.assertEqual(rueckfragen.offene(), [])
        self.assertEqual(register.freigaben(self.pid)[0]["anlass"], "Rückfrage: Geburtstag")

    def test_scheitert_die_freigabe_bleibt_die_rueckfrage_offen(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag")
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=False)):
            with self.assertRaises(PersonenFehler):
                lauf(rueckfragen.freigeben(a["id"], "Text"))
        self.assertEqual([x["id"] for x in rueckfragen.offene()], [a["id"]])

    def test_ablehnen_und_doppelt_beantworten(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag")
        self.assertEqual(rueckfragen.ablehnen(a["id"])["status"], "abgelehnt")
        with self.assertRaises(PersonenFehler):
            rueckfragen.ablehnen(a["id"])
        with self.assertRaises(PersonenFehler):
            lauf(rueckfragen.freigeben(a["id"], "Text"))
        with self.assertRaises(PersonenFehler):
            rueckfragen.ablehnen("a99")

    def test_verfallene_rueckfragen_sind_nicht_mehr_offen_und_nicht_freigebbar(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag")
        d = rueckfragen._lesen()
        d["anfragen"][0]["zeit"] = (datetime.now(timezone.utc) - timedelta(hours=rueckfragen.VERFALL_STUNDEN + 1)).isoformat(timespec="seconds")
        rueckfragen._speichern(d)
        self.assertEqual(rueckfragen.offene(), [])
        with self.assertRaises(PersonenFehler):
            lauf(rueckfragen.freigeben(a["id"], "Text"))
        rueckfragen.stellen(self.pid, "Geburtstag")                # verfallene zählt nicht gegen Dedup und Limit
        self.assertEqual(len(rueckfragen.offene()), 1)


class ZuMelden(Basis):
    def test_freigegeben_abgelehnt_und_verfallen_sind_zu_melden_genau_einmal(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag")
        b = rueckfragen.stellen(self.pid, "Wohnort")
        c = rueckfragen.stellen(self.pid, "Hobby")
        offen = rueckfragen.stellen(self.pid, "Noch offen")
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)):
            lauf(rueckfragen.freigeben(a["id"], "Eike hat im Oktober Geburtstag."))
        rueckfragen.ablehnen(b["id"])
        d = rueckfragen._lesen()
        for x in d["anfragen"]:
            if x["id"] == c["id"]:
                x["zeit"] = (datetime.now(timezone.utc) - timedelta(hours=rueckfragen.VERFALL_STUNDEN + 1)).isoformat(timespec="seconds")
        rueckfragen._speichern(d)
        ids = [x["id"] for x in rueckfragen.zu_melden()]
        self.assertEqual(sorted(ids), sorted([a["id"], b["id"], c["id"]]))
        self.assertNotIn(offen["id"], ids)
        rueckfragen.gemeldet_markieren(a["id"])
        self.assertNotIn(a["id"], [x["id"] for x in rueckfragen.zu_melden()])

    def test_verfallene_rueckfrage_wird_beim_melden_als_verfallen_gefuehrt(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag")
        d = rueckfragen._lesen()
        d["anfragen"][0]["zeit"] = (datetime.now(timezone.utc) - timedelta(hours=rueckfragen.VERFALL_STUNDEN + 1)).isoformat(timespec="seconds")
        rueckfragen._speichern(d)
        rueckfragen.gemeldet_markieren(a["id"], status="verfallen")
        self.assertEqual(rueckfragen._lesen()["anfragen"][0]["status"], "verfallen")
        self.assertEqual(rueckfragen.zu_melden(), [])


if __name__ == "__main__":
    unittest.main()


class Termine(Basis):
    """Terminvorschlag einer Person: Wunsch, Annahme mit Eintrag, Absage (04.10.2026)."""

    def test_wunsch_wird_abgelegt_mit_push(self):
        a = rueckfragen.termin_wunsch_stellen(self.pid, "Kaffee", "2030-10-10T15:00", 60, "Bei Kontakt B")
        self.assertEqual(a["art"], "termin")
        self.assertEqual(a["termin"]["titel"], "Kaffee")
        self.assertEqual(a["status"], "offen")
        self.assertTrue(self.push.called)

    def test_derselbe_wunsch_nur_einmal_vergangenheit_nie(self):
        a = rueckfragen.termin_wunsch_stellen(self.pid, "Kaffee", "2030-10-10T15:00")
        b = rueckfragen.termin_wunsch_stellen(self.pid, "Kaffee", "2030-10-10T15:00")
        self.assertEqual(a["id"], b["id"])
        with self.assertRaises(PersonenFehler):
            rueckfragen.termin_wunsch_stellen(self.pid, "Kaffee", "2020-01-01T15:00")
        with self.assertRaises(PersonenFehler):
            rueckfragen.termin_wunsch_stellen(self.pid, "", "2030-10-10T15:00")
        with self.assertRaises(PersonenFehler):
            rueckfragen.termin_wunsch_stellen(self.pid, "Kaffee", "irgendwann")

    def test_annehmen_traegt_mit_vermerk_ein(self):
        from kern.dienste import icloud
        a = rueckfragen.termin_wunsch_stellen(self.pid, "Kaffee", "2030-10-10T15:00", 30, "Bei Kontakt B")
        with mock.patch.object(icloud, "anlegen", return_value={"ok": True}) as ein:
            r = rueckfragen.annehmen(a["id"])
        self.assertEqual(r["status"], "eingetragen")
        self.assertIn("Kontakt B", ein.call_args.kwargs.get("notiz", ""))
        self.assertEqual(ein.call_args.args[0], "Kaffee")

    def test_scheitert_der_eintrag_bleibt_der_wunsch_offen(self):
        from kern.dienste import icloud
        a = rueckfragen.termin_wunsch_stellen(self.pid, "Kaffee", "2030-10-10T15:00")
        with mock.patch.object(icloud, "anlegen", side_effect=RuntimeError("aus")):
            with self.assertRaises(PersonenFehler):
                rueckfragen.annehmen(a["id"])
        self.assertEqual([x["id"] for x in rueckfragen.offene()], [a["id"]])

    def test_kein_termin_keine_annahme(self):
        a = rueckfragen.stellen(self.pid, "Geburtstag")
        with self.assertRaises(PersonenFehler):
            rueckfragen.annehmen(a["id"])
