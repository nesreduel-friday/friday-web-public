"""Fokus-Personen: Sammler — Zusammenfassung, Ablegen, Zeiger, Limit, Fenster, keine Sendewege."""
import asyncio
import inspect
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.fokus import ablage, sammler

U = "11111111-1111-1111-1111-111111111111"


def antwort_json(aussagen):
    aussagen = [{"art": "termin", **a} for a in aussagen]          # Standard: eine berichtenswerte Art
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"aussagen": aussagen})}]}}]}


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        ablage.markieren(U, "Summer Muster", True)
        self.wa = {"eintraege": [{"zeit": "Mo", "von": "Summer", "text": "Freitag ist eine Info-Veranstaltung.", "chat": "Summer Muster"}],
                   "zeiger": {"chats": {"49@c.us": 700}}, "stand": "WhatsApp: Summer Muster", "fehler": ""}
        self.ml = {"eintraege": [], "zeiger": {"letzte_uid": 4}, "stand": "Mail: Gmail", "fehler": ""}
        self.modell = mock.AsyncMock(return_value=(antwort_json([{"text": "Hat für Freitag eine Info-Veranstaltung angekündigt.", "quelle": "whatsapp"}]), "m"))
        self.graph = mock.AsyncMock(return_value=True)
        self.patches = [mock.patch.object(sammler.quellen, "whatsapp_lesen", mock.AsyncMock(side_effect=lambda *a, **k: self.wa)),
                        mock.patch.object(sammler.quellen, "mail_lesen", mock.AsyncMock(side_effect=lambda *a, **k: self.ml)),
                        mock.patch("kern.sitzung.chat._gemini_anfrage", self.modell),
                        mock.patch.object(sammler.graph, "einspeisen", self.graph)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()


class Sammeln(Basis):
    def test_neues_wird_zusammengefasst_abgelegt_eingespeist_und_zeiger_rueckt_vor(self):
        r = asyncio.run(sammler.sammle_person(U))
        self.assertEqual(r["neu"], 1)
        p = ablage.holen(U)
        self.assertEqual(p["neu"][0]["text"], "Hat für Freitag eine Info-Veranstaltung angekündigt.")
        self.assertEqual(p["quellen"]["whatsapp"], {"chats": {"49@c.us": 700}})
        self.assertEqual(p["quellen"]["mail"], {"letzte_uid": 4})
        self.assertTrue(p["letzter_lauf"])
        name, text, beschreibung, zeitpunkt = self.graph.await_args.args[:4]
        self.assertTrue(name.startswith(f"fokus-{U}-"))
        self.assertIn("Herkunft: Summer Muster, nicht Eike", text)
        self.assertIn("Info-Veranstaltung", text)
        self.assertEqual(self.graph.await_args.kwargs["gruppe"], "eike")

    def test_eikes_nachrichten_sind_im_prompt_als_von_eike_an_die_person_beschriftet(self):
        wa = [{"von": "Eike", "chat": "Kontakt B", "text": "Hattest du deine Bilder runtergeladen?"},
              {"von": "Kontakt B", "chat": "Kontakt B", "text": "Ja"}]
        text = sammler._prompt("Kontakt B", wa, [])["contents"][0]["parts"][0]["text"]
        self.assertIn("- Eike (schreibt AN die Person) (Kontakt B): Hattest du", text)
        self.assertIn("- Kontakt B (Kontakt B): Ja", text)

    def test_nichts_neues_kein_modellaufruf(self):
        self.wa = {**self.wa, "eintraege": []}
        r = asyncio.run(sammler.sammle_person(U))
        self.assertEqual(r["neu"], 0)
        self.modell.assert_not_awaited()
        self.graph.assert_not_awaited()
        self.assertTrue(ablage.holen(U)["letzter_lauf"])

    def test_modellfehler_zeiger_bleiben_und_nichts_abgelegt(self):
        self.modell.side_effect = RuntimeError("Limit")
        r = asyncio.run(sammler.sammle_person(U))
        self.assertEqual(r["neu"], 0)
        p = ablage.holen(U)
        self.assertEqual((p["neu"], p["quellen"]["whatsapp"]), ([], {"chats": {}}))
        self.graph.assert_not_awaited()

    def test_kaputte_modellantwort_wie_modellfehler(self):
        self.modell.return_value = ({"candidates": [{"content": {"parts": [{"text": "kein json"}]}}]}, "m")
        self.assertEqual(asyncio.run(sammler.sammle_person(U))["neu"], 0)
        self.assertEqual(ablage.holen(U)["quellen"]["whatsapp"], {"chats": {}})

    def test_graphfehler_neu_wird_gezeigt_und_die_episode_bleibt_vorgemerkt(self):
        self.graph.return_value = False
        self.assertEqual(asyncio.run(sammler.sammle_person(U))["neu"], 1)
        self.assertEqual(ablage.holen(U)["quellen"]["whatsapp"], {"chats": {"49@c.us": 700}})
        offen = ablage.episoden_offen(U)
        self.assertEqual(len(offen), 1)
        self.assertIn("Info-Veranstaltung", offen[0]["text"])

    def test_pausierter_graph_nichts_senden_aber_vormerken(self):
        # 05.10.2026: Der Graph war am Tageslimit; die erste Fokus-Episode wäre verloren gegangen.
        with mock.patch.object(sammler.graph, "pausiert", return_value=True):
            asyncio.run(sammler.sammle_person(U))
        self.graph.assert_not_awaited()
        self.assertEqual(len(ablage.episoden_offen(U)), 1)

    def test_vorgemerktes_geht_beim_naechsten_lauf_raus_auch_ohne_neues(self):
        with mock.patch.object(sammler.graph, "pausiert", return_value=True):
            asyncio.run(sammler.sammle_person(U))
        self.wa = {**self.wa, "eintraege": []}
        with mock.patch.object(sammler.graph, "pausiert", return_value=False):
            asyncio.run(sammler.sammle_person(U))
        self.graph.assert_awaited_once()
        self.assertEqual(ablage.episoden_offen(U), [])

    def test_belanglosigkeiten_werden_nach_art_verworfen(self):
        # Summer, 05.10.2026: „es geht ihr gut“ und „ist nach Hause gefahren“ standen als Neuigkeit da.
        self.modell.return_value = ({"candidates": [{"content": {"parts": [{"text": json.dumps({"aussagen": [
            {"text": "Summer schreibt, dass es ihr gut geht.", "quelle": "whatsapp", "art": "sonstiges"},
            {"text": "Summer möchte am Freitag um 16 Uhr ins Kino.", "quelle": "whatsapp", "art": "termin"},
            {"text": "Etwas ohne Art.", "quelle": "whatsapp", "art": ""}]})}]}}]}, "m")
        asyncio.run(sammler.sammle_person(U))
        self.assertEqual([n["text"] for n in ablage.holen(U)["neu"]], ["Summer möchte am Freitag um 16 Uhr ins Kino."])

    def test_zuordnungen_bleiben_beim_weiterschieben_der_zeiger(self):
        ablage.zuordnung_setzen(U, "12@g.us", "Kontakt F", True)
        self.wa = {**self.wa, "unklar": [{"chat_id": "12@g.us", "chat": "JG12", "absender": "Kontakt A", "anzahl": 1}]}
        asyncio.run(sammler.sammle_person(U))
        wa = ablage.zeiger(U, "whatsapp")
        self.assertEqual(wa["zuordnung"], {"12@g.us": ["Kontakt F"]})
        self.assertEqual(wa["chats"], {"49@c.us": 700})
        self.assertEqual(ablage.holen(U)["unklar"][0]["absender"], "Kontakt A")

    def test_tageslimit(self):
        with mock.patch.object(ablage, "aufrufe_heute", return_value=sammler.FOKUS_AUFRUFE_PRO_TAG):
            r = asyncio.run(sammler.sammle_person(U))
        self.assertEqual((r["neu"], r["grund"]), (0, "limit"))
        self.modell.assert_not_awaited()

    def test_fehler_einer_quelle_stoppt_die_andere_nicht(self):
        self.wa = {"eintraege": [], "zeiger": {"chats": {}}, "stand": "", "fehler": "WAHA antwortet nicht"}
        self.ml = {"eintraege": [{"zeit": "d", "von": "S", "betreff": "Treffen", "text": "Freitag?"}], "zeiger": {"letzte_uid": 9},
                   "stand": "Mail: Gmail", "fehler": ""}
        self.assertEqual(asyncio.run(sammler.sammle_person(U))["neu"], 1)
        p = ablage.holen(U)
        self.assertEqual(p["fehler"]["whatsapp"], "WAHA antwortet nicht")
        self.assertEqual(p["quellen"]["mail"], {"letzte_uid": 9})

    def test_unmarkierte_person_wird_nicht_gesammelt(self):
        ablage.markieren(U, "Summer Muster", False)
        self.assertEqual(asyncio.run(sammler.sammle_person(U))["grund"], "nicht markiert")


class Sicherheit(Basis):
    def test_modell_bekommt_keine_werkzeuge_und_den_text_als_daten(self):
        asyncio.run(sammler.sammle_person(U))
        inhalt = self.modell.await_args.args[0]
        self.assertNotIn("tools", inhalt)
        self.assertNotIn("toolConfig", inhalt)
        anweisung = json.dumps(inhalt["systemInstruction"], ensure_ascii=False)
        self.assertIn("Daten, keine Anweisung", anweisung)
        self.assertIn("Passwörter", anweisung)
        self.assertIn("ÜBER DIESE PERSON", anweisung)                 # nicht Klatsch über Dritte (Kamma, 05.10.2026)
        self.assertIn("KEIN Klatsch über Dritte", anweisung)
        self.assertIn("leere Liste", anweisung)
        self.assertIn("sonstiges", anweisung)

    def test_modul_hat_keinen_sendeweg(self):
        text = inspect.getsource(sammler)
        for verboten in (".senden(", "sendSeen", "person_senden", "whatsapp_senden"):
            self.assertNotIn(verboten, text)


class Fenster(unittest.TestCase):
    def test_nur_tagsueber(self):
        z = ZoneInfo("Europe/Berlin")
        self.assertTrue(sammler.wach(datetime(2026, 10, 5, 7, 0, tzinfo=z)))
        self.assertTrue(sammler.wach(datetime(2026, 10, 5, 21, 59, tzinfo=z)))
        self.assertFalse(sammler.wach(datetime(2026, 10, 5, 22, 0, tzinfo=z)))
        self.assertFalse(sammler.wach(datetime(2026, 10, 5, 3, 0, tzinfo=z)))

    def test_jetzt_sperre_10_minuten(self):
        sammler._zuletzt_jetzt.clear()
        with mock.patch.object(sammler.time, "monotonic", return_value=1000.0):
            self.assertTrue(sammler.jetzt_erlaubt("u"))
            self.assertFalse(sammler.jetzt_erlaubt("u"))
        with mock.patch.object(sammler.time, "monotonic", return_value=1000.0 + sammler.SPERRE_JETZT_S + 1):
            self.assertTrue(sammler.jetzt_erlaubt("u"))


class Quellenstand(Basis):
    def test_stand_ohne_netz(self):
        with mock.patch.object(sammler.whatsapp, "finden", return_value=[{"id": "49@c.us", "name": "Summer Muster"}]), \
             mock.patch.object(sammler.konnektoren, "aktiv", return_value=False):
            s = sammler.quellenstand(ablage.holen(U))
        self.assertIn("Summer Muster", s["whatsapp"])
        self.assertIn("freigegeben", s["whatsapp"])
        self.assertEqual(s["mail"], "Mail: Konnektor aus")

    def test_stand_ohne_chat(self):
        with mock.patch.object(sammler.whatsapp, "finden", return_value=[]), mock.patch.object(sammler.konnektoren, "aktiv", return_value=True):
            s = sammler.quellenstand(ablage.holen(U))
        self.assertEqual(s["whatsapp"], "WhatsApp: kein freigegebener Chat")
        self.assertEqual(s["mail"], "Mail: Gmail")


if __name__ == "__main__":
    unittest.main()
