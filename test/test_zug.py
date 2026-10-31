"""Zugkontext: Kennung je chat.antworten-Aufruf und das letzte Bild der Person — nie vom Modell."""
import asyncio
import unittest
from unittest import mock

from kern.skills import zug


def lauf(c):
    return asyncio.run(c)


class Zug(unittest.TestCase):
    def test_ausserhalb_eines_zuges_ist_alles_leer(self):
        self.assertEqual(zug.zug_id(), "")
        self.assertIsNone(zug.bild())

    def test_kennung_je_zug_neu_und_danach_wieder_leer(self):
        with zug.zug():
            a = zug.zug_id()
        with zug.zug():
            b = zug.zug_id()
        self.assertEqual((len(a), len(b)), (32, 32))
        self.assertNotEqual(a, b)
        self.assertEqual(zug.zug_id(), "")

    def test_letztes_bild_der_person_zaehlt(self):
        n = [{"rolle": "du", "text": "alt", "bild": {"mime": "image/jpeg", "data": "AAA"}},
             {"rolle": "friday", "text": "ok"},
             {"rolle": "du", "text": "neu", "bild": {"mime": "image/png", "data": "BBB"}}]
        with zug.zug(n):
            self.assertEqual(zug.bild(), {"mime": "image/png", "data": "BBB"})
        self.assertIsNone(zug.bild())

    def test_bild_von_friday_oder_ohne_daten_zaehlt_nicht(self):
        n = [{"rolle": "friday", "bild": {"mime": "image/jpeg", "data": "AAA"}},
             {"rolle": "du", "bild": {"mime": "image/jpeg", "data": ""}}, {"rolle": "du", "bild": "kaputt"}]
        with zug.zug(n):
            self.assertIsNone(zug.bild())

    def test_gleiche_nachrichten_gleiche_kennung_neue_nachricht_neue_kennung(self):
        # Ein Neuversuch der Wache verarbeitet DIESELBEN Nachrichten noch einmal: Das ist kein neuer Zug im Sinn der Bestätigung.
        a = [{"rolle": "du", "text": "ich will was einreichen"}]
        with zug.zug(a):
            k1 = zug.zug_id()
        with zug.zug([dict(x) for x in a]):
            k2 = zug.zug_id()
        with zug.zug(a + [{"rolle": "friday", "text": "wohin?"}, {"rolle": "du", "text": "ja"}]):
            k3 = zug.zug_id()
        with zug.zug([{"rolle": "du", "text": "ja"}]):
            k4 = zug.zug_id()
        self.assertEqual(k1, k2)
        self.assertEqual(len({k1, k3, k4}), 3)
        self.assertEqual(len(k1), 32)

    def test_gleicher_text_zweimal_hintereinander_ist_ein_neuer_zug(self):
        eins = [{"rolle": "du", "text": "ja"}]
        zwei = eins + [{"rolle": "friday", "text": "ok"}, {"rolle": "du", "text": "ja"}]
        with zug.zug(eins):
            a = zug.zug_id()
        with zug.zug(zwei):
            b = zug.zug_id()
        self.assertNotEqual(a, b)

    def test_vormerken_wird_erst_beim_festschreiben_zum_vermerk(self):
        import os, tempfile
        from kern.skills import akten
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}):
            st = akten.standard()
            a, _ = st.oeffnen("garantie_annahme", "p1")
            try:
                with zug.zug():
                    zug.vormerken(a["id"], "ab" * 8, ["12.10.", "Linie 5", "Empelde"], "Fahrt am 12.10.2026, Linie 5 nach Empelde.")
                    self.assertIsNone(st.zusammenfassung(a["id"]))                       # nur vorgemerkt
                    antwort = "Fahrt am 12.10.2026 mit Linie 5 nach Empelde. Stimmt das?"
                    self.assertEqual(zug.festschreiben(antwort), antwort)                  # nennt alles: unverändert
                    self.assertEqual(st.zusammenfassung(a["id"])[0], "ab" * 8)
            finally:
                for i in akten._instanzen.values():
                    i._db.close()
                akten._instanzen.clear()

    def test_fehlt_etwas_haengt_der_server_die_zusammenfassung_an(self):
        import os, tempfile
        from kern.skills import akten
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}):
            st = akten.standard()
            a, _ = st.oeffnen("garantie_annahme", "p1")
            try:
                with zug.zug():
                    zug.vormerken(a["id"], "cd" * 8, ["12.10.", "Linie 5", "Empelde"], "Fahrt am 12.10.2026: Linie 5 nach Empelde.")
                    r = zug.festschreiben("Alles klar, ich kümmere mich drum.")
                self.assertTrue(r.startswith("Alles klar, ich kümmere mich drum."))
                self.assertIn("Fahrt am 12.10.2026: Linie 5 nach Empelde.", r)
                self.assertIn("Stimmt das so?", r)
                self.assertEqual(st.zusammenfassung(a["id"])[0], "cd" * 8)                # jetzt wurde sie wirklich gezeigt
            finally:
                for i in akten._instanzen.values():
                    i._db.close()
                akten._instanzen.clear()

    def test_leere_antwort_wird_zur_zusammenfassung(self):
        import os, tempfile
        from kern.skills import akten
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}):
            st = akten.standard()
            a, _ = st.oeffnen("garantie_annahme", "p1")
            try:
                with zug.zug():
                    zug.vormerken(a["id"], "ef" * 8, ["Linie 5"], "Fahrt: Linie 5.")
                    r = zug.festschreiben("")
                self.assertIn("Fahrt: Linie 5.", r)
                self.assertIsNotNone(st.zusammenfassung(a["id"]))
            finally:
                for i in akten._instanzen.values():
                    i._db.close()
                akten._instanzen.clear()

    def test_ohne_vorgemerktes_bleibt_die_antwort_wie_sie_ist(self):
        with zug.zug():
            self.assertEqual(zug.festschreiben("Hallo!"), "Hallo!")
            self.assertEqual(zug.festschreiben(None), "")

    def test_festschreiben_ausserhalb_eines_zuges_tut_nichts(self):
        self.assertEqual(zug.festschreiben("irgendwas"), "irgendwas")

    def test_chat_antworten_schreibt_nur_nach_erfolg_fest(self):
        from kern.sitzung import chat
        aufrufe = []

        async def gut(nachrichten, gespraech=None, kennung="", kanal="", person=None):
            return {"antwort": "Fahrt am 12.10.2026"}

        async def kaputt(nachrichten, gespraech=None, kennung="", kanal="", person=None):
            raise chat.ChatFehler("Gemini weg")
        with mock.patch.object(zug, "festschreiben", side_effect=lambda a: aufrufe.append(a) or a + "!"):
            with mock.patch.object(chat, "_antworten", gut):
                r = lauf(chat.antworten([{"rolle": "du", "text": "x"}], person={"id": "p1"}))
            self.assertEqual(r["antwort"], "Fahrt am 12.10.2026!")                  # die Antwort der Wache ist die ergänzte
            with mock.patch.object(chat, "_antworten", kaputt):
                with self.assertRaises(chat.ChatFehler):
                    lauf(chat.antworten([{"rolle": "du", "text": "x"}], person={"id": "p1"}))
        self.assertEqual(aufrufe, ["Fahrt am 12.10.2026"])

    def test_chat_antworten_setzt_den_zug_nur_fuer_personen_mit_bild(self):
        from kern.sitzung import chat
        gesehen = {}

        async def innen(nachrichten, gespraech=None, kennung="", kanal="", person=None):
            gesehen["id"], gesehen["bild"] = zug.zug_id(), zug.bild()
            return {"antwort": "ok"}
        with mock.patch.object(chat, "_antworten", innen):
            lauf(chat.antworten([{"rolle": "du", "text": "x", "bild": {"mime": "image/jpeg", "data": "AAA"}}], person={"id": "p1"}))
            self.assertEqual(len(gesehen["id"]), 32)
            self.assertEqual(gesehen["bild"], {"mime": "image/jpeg", "data": "AAA"})
            lauf(chat.antworten([{"rolle": "du", "text": "x", "bild": {"mime": "image/jpeg", "data": "AAA"}}]))
            self.assertIsNone(gesehen["bild"])                       # Eikes Chat: kein Ticket-Bild
