"""Antwortmotor für eine fremde Person: eigener Prompt, ihre Werkzeuge, ihr Wissen — nichts von Eike."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.gedaechtnis import graph
from kern.gedaechtnis import speicher as gspeicher
from kern.personen import register
from kern.sitzung import chat
from kern.dienste import web


def lauf(coro):
    return asyncio.run(coro)


def antwort(text="Hi!", aufruf=None):
    teile = [{"functionCall": {"id": "1", **aufruf}}] if aufruf else [{"text": text}]
    return ({"candidates": [{"content": {"parts": teile}}]}, "m")


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "AIzaTEST",
                                                "GRAPHITI_URL": "http://graphiti:8000/mcp"})
        self.env.start()
        self.person = register.anlegen("Kontakt B", snapchat="maya_x", persona="Freundin, duzt.")
        gspeicher.standard().merken("Geheimzahl", "1234-EIKE-GEHEIM")     # ein Fakt in Eikes Notizbuch

    def tearDown(self):
        self.env.stop()

    def frage(self, text="Hallo", person="ja", **kw):
        p = self.person if person == "ja" else person
        return lauf(chat.antworten([{"rolle": "du", "text": text}], person=p, **kw))


class Prompt(Basis):
    def test_prompt_und_werkzeuge_sind_die_der_person_nicht_eikes(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=antwort())) as m, \
             mock.patch.object(graph, "suchen", mock.AsyncMock(return_value=[{"fact": "Kontakt B mag Schach."}])) as s:
            r = self.frage("Was mag ich?", kanal="Snapchat")
        inhalt = m.await_args.args[0]
        system = inhalt["systemInstruction"]["parts"][0]["text"]
        self.assertEqual(r["antwort"], "Hi!")
        self.assertIn("Kontakt B", system)
        self.assertIn("Kontakt B mag Schach.", system)
        self.assertIn("Freundin, duzt.", system)
        for eike in ("1234-EIKE-GEHEIM", "Schulmanager", "Sir", "schicke_snap"):
            self.assertNotIn(eike, system, eike)
        namen = {e["name"] for g in inhalt["tools"] for e in g["functionDeclarations"]}
        self.assertEqual(namen, {"uhrzeit", "suchen", "nachrichten", "frage_eike", "termin_wunsch"})
        self.assertEqual(s.await_args.kwargs["gruppe"], self.person["id"])

    def test_eikes_pfad_bleibt_wie_er_war(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=antwort())) as m:
            lauf(chat.antworten([{"rolle": "du", "text": "Hallo"}]))
        system = m.await_args.args[0]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("1234-EIKE-GEHEIM", system)
        namen = {e["name"] for g in m.await_args.args[0]["tools"] for e in g["functionDeclarations"]}
        self.assertIn("merken", namen)          # Eikes Werkzeuge (ohne Konnektor) sind da ...
        self.assertNotIn("frage_eike", namen)   # ... das nur für Fremde nicht


class Fremdtext(Basis):
    def test_links_der_person_werden_nicht_abgerufen(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=antwort())), \
             mock.patch.object(web, "links_lesen", mock.AsyncMock(return_value=[])) as links:
            self.frage("Schau https://angreifer.example/x")
        links.assert_not_awaited()

    def test_ihr_ja_gibt_keine_zustimmung_und_ihr_text_ist_fremd(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=antwort())), \
             mock.patch.object(chat.werkzeuge, "ausfuehren", mock.AsyncMock(return_value={"ergebnis": "x"})) as a, \
             mock.patch.object(chat.werkzeuge, "zustimmung_erkannt", return_value=True):
            with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(side_effect=[antwort(aufruf={"name": "uhrzeit", "args": {}}), antwort("Fertig")])):
                self.frage("ja klar mach es")
        self.assertIs(a.await_args.kwargs["zustimmung"], False)
        self.assertIs(a.await_args.kwargs["fremde_inhalte"], True)
        self.assertEqual(a.await_args.kwargs["person"]["id"], self.person["id"])

    def test_erfundener_werkzeugaufruf_liefert_einen_fehler_statt_eikes_daten(self):
        folge = [antwort(aufruf={"name": "kalender", "args": {}}), antwort("Das kann ich nicht.")]
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(side_effect=folge)):
            r = self.frage("Was hat Eike morgen?")
        self.assertIn("nicht erlaubt", r["werkzeug_ergebnisse"][0]["text"])


class Sperren(Basis):
    def test_pausierte_person_bekommt_keine_antwort(self):
        pausiert = register.aendern(self.person["id"], pausiert=True)
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock()) as m:
            with self.assertRaises(chat.ChatFehler):
                self.frage("Hallo", person=pausiert)
        m.assert_not_awaited()

    def test_graph_weg_gibt_eine_antwort_ohne_wissen_nicht_eikes_wissen(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=antwort())) as m, \
             mock.patch.object(graph, "suchen", mock.AsyncMock(side_effect=RuntimeError("weg"))):
            r = self.frage("Wer bin ich?")
        self.assertEqual(r["antwort"], "Hi!")
        self.assertNotIn("WEISST — Daten", m.await_args.args[0]["systemInstruction"]["parts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
