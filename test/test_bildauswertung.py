import asyncio
import unittest
from unittest import mock

from kern.gedaechtnis import bildauswertung
from kern.sitzung import chat


def lauf(coro):
    return asyncio.run(coro)


ANTWORT = ({"candidates": [{"content": {"parts": [{"text": "Ein Monitor mit einem Film."}]}}]}, "m")


class BildauswertungTest(unittest.TestCase):
    def test_liefert_die_sachliche_beschreibung_und_schickt_das_bild_mit(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=ANTWORT)) as m:
            text = lauf(bildauswertung.sachlich("QUJD", "image/png", "Snapchat · Eike"))
        self.assertEqual(text, "Ein Monitor mit einem Film.")
        inhalt = m.await_args.args[0]
        teile = inhalt["contents"][0]["parts"]
        self.assertEqual(teile[0]["inlineData"], {"mimeType": "image/png", "data": "QUJD"})
        self.assertIn("Snapchat · Eike", teile[1]["text"])
        self.assertIn("ignoriere", inhalt["systemInstruction"]["parts"][0]["text"])

    def test_gedankenteile_kommen_nicht_in_die_beschreibung(self):
        antwort = ({"candidates": [{"content": {"parts": [{"text": "Innere Überlegung", "thought": True},
                                                          {"text": "Ein Baum."}]}}]}, "m")
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=antwort)):
            self.assertEqual(lauf(bildauswertung.sachlich("QUJD", "image/png")), "Ein Baum.")

    def test_fehler_gibt_none_statt_zu_werfen(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(side_effect=chat.ChatFehler("aus"))):
            self.assertIsNone(lauf(bildauswertung.sachlich("QUJD", "image/png")))


def _json(roh):
    return ({"candidates": [{"content": {"parts": [{"text": roh}]}}]}, "m")


class AuswertenTest(unittest.TestCase):
    def test_liefert_beschreibung_personen_und_wer_und_gibt_die_nachricht_mit(self):
        roh = '{"beschreibung": "Eine Frau am Strand.", "personen": 1, "wer": "Kontakt B"}'
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=_json(roh))) as m:
            e = lauf(bildauswertung.auswerten("QUJD", "image/jpeg", "Snapchat · Eike", "Das ist Kontakt B"))
        self.assertEqual(e, {"beschreibung": "Eine Frau am Strand.", "personen": 1, "wer": "Kontakt B", "selfie": False})
        teile = m.await_args.args[0]["contents"][0]["parts"]
        self.assertIn("Das ist Kontakt B", teile[1]["text"])
        self.assertIn("NACHRICHT", m.await_args.args[0]["systemInstruction"]["parts"][0]["text"])

    def test_json_im_codeblock_und_fehlende_felder(self):
        roh = '```json\n{"beschreibung": "Ein Baum."}\n```'
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=_json(roh))):
            self.assertEqual(lauf(bildauswertung.auswerten("QUJD", "image/jpeg")),
                             {"beschreibung": "Ein Baum.", "personen": 0, "wer": None, "selfie": False})

    def test_selfie_flag_wird_gelesen_und_nur_bei_echtem_true_gesetzt(self):
        for roh, soll in (('{"beschreibung": "Ein Gesicht.", "personen": 1, "selfie": true}', True),
                          ('{"beschreibung": "Ein Gesicht.", "personen": 1, "selfie": "ja"}', False),
                          ('{"beschreibung": "Ein Gesicht.", "personen": 1}', False)):
            with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=_json(roh))) as m:
                e = lauf(bildauswertung.auswerten("QUJD", "image/jpeg"))
            self.assertIs(e["selfie"], soll)
        self.assertIn("selfie", m.await_args.args[0]["systemInstruction"]["parts"][0]["text"])

    def test_unbrauchbare_antwort_und_fehler_geben_none(self):
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(return_value=_json("kein json"))):
            self.assertIsNone(lauf(bildauswertung.auswerten("QUJD", "image/jpeg")))
        with mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(side_effect=chat.ChatFehler("aus"))):
            self.assertIsNone(lauf(bildauswertung.auswerten("QUJD", "image/jpeg")))
