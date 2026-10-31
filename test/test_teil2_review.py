"""Fund-für-Fund-Tests zum Review von Kontakte Teil 2 (30.09.2026): jeder Test würde ohne den Fix rot."""
import asyncio
import os
import tempfile
import time
import unittest
from unittest import mock

from kern.dienste import benachrichtigungen, snapchat
from kern.gedaechtnis import graph
from kern.gehirn import persona
from kern.personen import PersonenFehler, register, rueckfragen, wissen
from kern.sitzung import chat
from kern.werkzeuge import kontakte


def lauf(coro):
    return asyncio.run(coro)


def antwort(text="Hi!"):
    return ({"candidates": [{"content": {"parts": [{"text": text}]}}]}, "m")


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST",
                                                "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()
        p = mock.patch.object(benachrichtigungen, "senden", return_value={"gesendet": 1})
        p.start()
        self.addCleanup(p.stop)
        self.person = register.anlegen("Kontakt B", snapchat="maya_x", persona="Freundin.")

    def tearDown(self):
        self.env.stop()

    def frage(self, person=None, **kw):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=antwort())) as m, \
             mock.patch.object(graph, "suchen", mock.AsyncMock(return_value=[])) as s:
            r = lauf(chat.antworten([{"rolle": "du", "text": "Hallo"}], person=person or self.person, **kw))
        return r, m, s


class KanalTextEikes(Basis):
    def test_eikes_snap_kanal_und_kontext_kommen_nie_in_den_prompt_einer_person(self):
        snapchat.kontext_vermerken("Erinnerung: Arzttermin am Freitag um 10 Uhr")
        boese = persona.SNAPCHAT_KANAL + snapchat.gesendet_kontext()
        self.assertIn("Arzttermin", boese)                      # der Kontext ist wirklich da ...
        _, m, _ = self.frage(kanal=boese)
        system = m.await_args.args[0]["systemInstruction"]["parts"][0]["text"]
        self.assertNotIn("Arzttermin", system)                  # ... und fehlt im Prompt der Person
        self.assertNotIn("gerade mit Eike", system)

    def test_ein_kanalname_aus_der_festen_liste_wird_zur_kanalbeschreibung(self):
        _, m, _ = self.frage(kanal="Snapchat")
        self.assertIn(persona.person_kanal("Snapchat"), m.await_args.args[0]["systemInstruction"]["parts"][0]["text"])


class PersonWirdFrischGeholt(Basis):
    def test_ungueltige_ids_und_personen_ohne_register_eintrag_bekommen_keine_antwort(self):
        for boese in ({"id": "eike", "name": "X", "werkzeuge": ["uhrzeit"]}, {"name": "X"}, {}, {"id": "p99"},
                      {"id": "p1;eike"}, {"id": None}, {"id": ["p1"]}):
            with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock()) as m, \
                 mock.patch.object(graph, "suchen", mock.AsyncMock()) as s:
                with self.assertRaises(chat.ChatFehler, msg=repr(boese)):
                    lauf(chat.antworten([{"rolle": "du", "text": "Hallo"}], person=boese))
            m.assert_not_awaited()
            s.assert_not_awaited()

    def test_werkzeuge_und_pause_kommen_aus_dem_register_nicht_aus_dem_dict_des_aufrufers(self):
        aufgeblasen = dict(self.person, werkzeuge=["uhrzeit", "kalender", "stundenplan"], module={"plan": "voll", "flammen": True}, pausiert=False)
        _, m, _ = self.frage(person=aufgeblasen)
        namen = {e["name"] for g in m.await_args.args[0]["tools"] for e in g["functionDeclarations"]}
        self.assertEqual(namen, {"uhrzeit", "suchen", "nachrichten", "frage_eike", "termin_wunsch"})  # Register: nur Grundfunktionen
        register.aendern(self.person["id"], pausiert=True)
        entpausiert = dict(self.person, pausiert=False)
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock()) as m2:
            with self.assertRaises(chat.ChatFehler):
                lauf(chat.antworten([{"rolle": "du", "text": "Hallo"}], person=entpausiert))
        m2.assert_not_awaited()

    def test_wissen_sucht_nie_in_einer_gruppe_die_keine_personengruppe_ist(self):
        for boese_id in ("eike", "friday-test", "p0", "", "p1,eike"):
            with mock.patch.object(graph, "suchen", mock.AsyncMock(return_value=[{"fact": "GEHEIM"}])) as s:
                self.assertEqual(lauf(wissen.block({"id": boese_id, "name": "X"}, "Frage")), "", boese_id)
            s.assert_not_awaited()


class RueckfrageRennen(Basis):
    def test_ablehnen_waehrend_die_freigabe_laeuft_wird_abgelehnt_und_bleibt_es(self):
        a = rueckfragen.stellen(self.person["id"], "Geburtstag")
        ereignisse = []

        async def langsam(*args, **kwargs):
            ereignisse.append(("offen_waehrend", [x["id"] for x in rueckfragen.offene()]))
            try:
                rueckfragen.ablehnen(a["id"])
                ereignisse.append("abgelehnt_gelang")           # darf nicht passieren
            except PersonenFehler:
                ereignisse.append("ablehnen_gesperrt")
            return True

        with mock.patch.object(graph, "einspeisen", langsam):
            lauf(rueckfragen.freigeben(a["id"], "Eike hat im Oktober Geburtstag."))
        self.assertIn("ablehnen_gesperrt", ereignisse)
        self.assertNotIn("abgelehnt_gelang", ereignisse)
        self.assertEqual(len(register.freigaben(self.person["id"])), 1)

    def test_doppeltes_freigeben_legt_nur_eine_freigabe_an(self):
        a = rueckfragen.stellen(self.person["id"], "Geburtstag")

        async def doppelt():
            with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=True)):
                ergebnisse = await asyncio.gather(rueckfragen.freigeben(a["id"], "Text"),
                                                  rueckfragen.freigeben(a["id"], "Text"), return_exceptions=True)
            return ergebnisse

        ergebnisse = lauf(doppelt())
        self.assertEqual(sum(isinstance(e, PersonenFehler) for e in ergebnisse), 1)
        self.assertEqual(len(register.freigaben(self.person["id"])), 1)

    def test_scheitert_die_freigabe_ist_die_rueckfrage_wieder_offen(self):
        a = rueckfragen.stellen(self.person["id"], "Geburtstag")
        with mock.patch.object(graph, "einspeisen", mock.AsyncMock(return_value=False)):
            with self.assertRaises(PersonenFehler):
                lauf(rueckfragen.freigeben(a["id"], "Text"))
        self.assertEqual([x["id"] for x in rueckfragen.offene()], [a["id"]])
        rueckfragen.ablehnen(a["id"])                            # und läßt sich wieder ablehnen


class FrageEikeBlockiertNicht(Basis):
    def test_frage_eike_ist_asynchron_und_haelt_die_event_loop_nicht_an(self):
        self.assertTrue(asyncio.iscoroutinefunction(kontakte.frage_eike))

        def langsam(pid, thema, wunsch=""):
            time.sleep(0.4)                                      # wie ein Web-Push mit Zeitlimit
            return {}

        async def messen():
            ticks = 0

            async def ticker():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.05)
                    ticks += 1

            t = asyncio.create_task(ticker())
            with mock.patch.object(rueckfragen, "stellen", langsam):
                await kontakte.frage_eike("Thema", "Wunsch", kontakt_id=self.person["id"])
            t.cancel()
            return ticks

        self.assertGreaterEqual(lauf(messen()), 4)               # die Schleife lief weiter, während „gepusht“ wurde


if __name__ == "__main__":
    unittest.main()
