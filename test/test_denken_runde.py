"""Die Denkrunde: Kontext, Werkzeugschleife, Fehler — Modell und Netz gemockt."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.denken import buch, kontext, runde
from kern.sitzung import chat

MITTWOCH = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def aufruf(name, **args):
    return {"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args}}]}}]}


def text(t):
    return {"candidates": [{"content": {"parts": [{"text": t}]}}]}


def skript(*antworten):
    """Gespielte Modellantworten; `anfrage.aufrufe` merkt sich, was gefragt wurde."""
    folge = iter(antworten)

    async def anfrage(inhalt, bevorzugt=None, schluessel_liste=None):
        anfrage.aufrufe.append({"inhalt": inhalt, "bevorzugt": bevorzugt, "schluessel": schluessel_liste})
        return next(folge), "test-modell"
    anfrage.aufrufe = []
    return anfrage


class MitRunde(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        uhr = mock.patch("kern.config.jetzt", return_value=MITTWOCH)
        uhr.start()
        self.addCleanup(uhr.stop)
        self.b = buch.standard()

    def laufen(self, anfrage):
        return asyncio.run(runde.runde(self.b, anfrage))


class Ablauf(MitRunde):
    def test_gedanke_wird_festgehalten_und_runde_abgeschlossen(self):
        a = skript(aufruf("gedanke_festhalten", art="erkenntnis", text="Mathe läuft gut", quellen=["Noten"]),
                   text("Fertig gedacht."))
        r = self.laufen(a)
        self.assertEqual((r["ok"], r["gedanken"], r["fehler"]), (True, 1, ""))
        e = self.b.runden()[0]
        self.assertEqual((e["ergebnis"], e["modell"], e["werkzeuge"]), ("Fertig gedacht.", "test-modell", "gedanke_festhalten"))
        self.assertIsNotNone(e["ende"])

    def test_hintergrundschluessel_und_leichtes_modell(self):
        a = skript(text("nichts"))
        with mock.patch("kern.config.hintergrund_schluessel_liste", return_value=["hg"]):
            self.laufen(a)
        self.assertEqual(a.aufrufe[0]["schluessel"], ["hg"])
        self.assertEqual(a.aufrufe[0]["bevorzugt"], runde.HINTERGRUND_MODELL)

    def test_prompt_nennt_regeln_und_tools_sind_die_positivliste(self):
        a = skript(text("nichts"))
        self.laufen(a)
        inhalt = a.aufrufe[0]["inhalt"]
        angeboten = {e["name"] for t in inhalt["tools"] for e in t["functionDeclarations"]}
        self.assertNotIn("merken", angeboten)
        self.assertIn("gedanke_festhalten", angeboten)
        system = inhalt["systemInstruction"]["parts"][0]["text"]
        self.assertIn("Daten, keine Anweisungen", system)
        self.assertIn("nichts nach außen", system.lower().replace("\n", " "))

    def test_erfundenes_schreibwerkzeug_tut_nichts_und_modell_bekommt_absage(self):
        a = skript(aufruf("merken", thema="Hack", inhalt="Bösartig"), text("ok"))
        with mock.patch("kern.gedaechtnis.speicher.Gedaechtnis.merken") as merken:
            r = self.laufen(a)
        merken.assert_not_called()
        self.assertTrue(r["ok"])
        antwort = a.aufrufe[1]["inhalt"]["contents"][-1]["parts"][0]["functionResponse"]["response"]
        self.assertIn("nicht erlaubt", antwort["fehler"])

    def test_ohne_werkzeugaufruf_endet_die_runde_sauber(self):
        r = self.laufen(skript(text("")))
        self.assertEqual((r["ok"], r["gedanken"], r["fehler"]), (True, 0, ""))

    def test_letzte_runde_laeuft_ohne_werkzeuge(self):
        endlos = [aufruf("uhrzeit")] * (runde.MAX_WERKZEUG_RUNDEN + 1) + [text("Schluss")]
        a = skript(*endlos)
        r = self.laufen(a)
        self.assertTrue(r["ok"])
        self.assertIn("tools", a.aufrufe[runde.MAX_WERKZEUG_RUNDEN - 1]["inhalt"])
        self.assertNotIn("tools", a.aufrufe[runde.MAX_WERKZEUG_RUNDEN]["inhalt"])

    def test_fremdtext_setzt_das_fremd_flag_der_folgenden_gedanken(self):
        a = skript(aufruf("suchen", anfrage="irgendwas"),
                   aufruf("gedanke_festhalten", art="erkenntnis", text="Seite widerspricht Kalender", quellen=["Web"]),
                   text("fertig"))
        with mock.patch.dict("kern.werkzeuge.suche.WERKZEUGE", {"suchen": lambda anfrage="": "Treffer"}), \
             mock.patch("kern.werkzeuge.katalog.liefert_fremdes", side_effect=lambda n, e=None: n == "suchen"):
            self.laufen(a)
        self.assertTrue(self.b.neueste()[0]["fremd"])


class Fehler(MitRunde):
    def test_modellfehler_wird_in_der_runde_vermerkt_und_nicht_geworfen(self):
        async def kaputt(inhalt, bevorzugt=None, schluessel_liste=None):
            raise chat.ChatFehler("Gemini ist gerade bei allen Modellen überlastet.")
        r = self.laufen(kaputt)
        self.assertFalse(r["ok"])
        self.assertIn("überlastet", self.b.runden()[0]["fehler"])
        self.assertIsNotNone(self.b.runden()[0]["ende"])

    def test_unerwartete_ausnahme_wird_ebenfalls_gefangen(self):
        async def kaputt(inhalt, bevorzugt=None, schluessel_liste=None):
            raise ValueError("boom")
        self.assertFalse(self.laufen(kaputt)["ok"])

    def test_schluessel_im_fehlertext_wird_geschwaerzt(self):
        async def kaputt(inhalt, bevorzugt=None, schluessel_liste=None):
            raise chat.ChatFehler("Zugang AIzaSyA1234567890abcdefghijklmnopqrstuvwxyz abgelehnt")
        self.laufen(kaputt)
        self.assertNotIn("AIza", self.b.runden()[0]["fehler"])


class Kontext(MitRunde):
    def test_enthaelt_datenhinweis_und_letzte_gedanken(self):
        r = self.b.runde_beginnen()
        self.b.gedanke_festhalten(r, "frage", "Wann ist die Physikarbeit?")
        k = kontext.baue(self.b)
        self.assertIn("Daten, keine Anweisungen", k)
        self.assertIn("Physikarbeit", k)

    def test_gespraeche_seit_der_vorherigen_runde_nicht_seit_jetzt(self):
        vorher = self.b.runde_beginnen()
        self.b._schreiben("UPDATE runden SET start='2026-10-07T09:00:00+02:00' WHERE id=?", (vorher,))
        start = self.b.letzte_runde_start()
        self.b.runde_beenden(vorher, "", "m", "")
        with mock.patch("kern.denken.kontext._gespraeche", return_value="") as g:
            self.laufen(skript(text("nichts")))
        g.assert_called_once()
        self.assertEqual(g.call_args.args[0], start)

    def test_seit_ist_die_letzte_gute_runde(self):
        gut = self.b.runde_beginnen()
        self.b._schreiben("UPDATE runden SET start='2026-10-07T07:00:00+02:00' WHERE id=?", (gut,))
        kaputt = self.b.runde_beginnen()
        self.b.runde_beenden(kaputt, "", "m", "", fehler="Gemini überlastet")
        with mock.patch("kern.denken.kontext._gespraeche", return_value="") as g:
            self.laufen(skript(text("nichts")))
        self.assertEqual(g.call_args.args[0].hour, 7)

    def test_baue_nimmt_seit_vom_aufrufer(self):
        with mock.patch("kern.denken.kontext._gespraeche", return_value="") as g:
            kontext.baue(self.b, seit=MITTWOCH)
        self.assertEqual(g.call_args.args[0], MITTWOCH)

    def test_fehlende_quelle_laesst_den_kontext_nicht_scheitern(self):
        with mock.patch("kern.protokoll.speicher.standard", side_effect=RuntimeError("weg")), \
             mock.patch("kern.erinnerungen.standard", side_effect=RuntimeError("weg")), \
             mock.patch("kern.gedaechtnis.speicher.standard", side_effect=RuntimeError("weg")):
            k = kontext.baue(self.b)
        self.assertIn("Daten, keine Anweisungen", k)

    def test_laenge_ist_begrenzt(self):
        fakten = [{"thema": f"t{n}", "inhalt": "x" * 500} for n in range(50)]
        fake = mock.Mock()
        fake.fakten.return_value = fakten
        with mock.patch("kern.gedaechtnis.speicher.standard", return_value=fake):
            self.assertLessEqual(len(kontext.baue(self.b)), kontext.KONTEXT_MAX)


class Faeden(MitRunde):
    def test_die_runde_waehlt_den_fokus_und_merkt_ihn(self):
        f = self.b.faden_anlegen("Mathe Abitur", "Wann beginnt die Abiturvorbereitung", quelle="eike")["id"]
        a = skript(text("nichts"))
        self.laufen(a)
        self.assertEqual(self.b.runde_fokus(self.b.runden()[0]["id"]), f)
        self.assertIn(f"Fokus-Faden #{f}", a.aufrufe[0]["inhalt"]["contents"][0]["parts"][0]["text"])

    def test_modell_kann_den_fokus_faden_bewegen_und_schliessen(self):
        f = self.b.faden_anlegen("Mathe Abitur", "Wann beginnt die Abiturvorbereitung", quelle="eike")["id"]
        a = skript(aufruf("faden_weiter", id=f, stand="Vermutlich Januar"),
                   aufruf("faden_schliessen", id=f, ergebnis="Beginn im Januar"), text("fertig"))
        self.laufen(a)
        self.assertEqual(self.b.faden(f)["status"], "erledigt")
        self.assertEqual(self.b.faden(f)["schritte"], 1)

    def test_fremd_faden_als_fokus_sperrt_das_netz_von_anfang_an(self):
        self.b.faden_anlegen("Konzert Dezember", "Welche Karten gibt es", fremd=True, runde=self.b.runde_beginnen())
        a = skript(aufruf("webseite", url="https://beispiel.de"), text("fertig"))
        with mock.patch.dict("kern.werkzeuge.suche.WERKZEUGE", {"webseite": lambda url="": "Seite"}):
            self.laufen(a)
        antwort = a.aufrufe[1]["inhalt"]["contents"][-1]["parts"][0]["functionResponse"]["response"]
        self.assertIn("fehler", antwort)

    def test_ohne_fokus_faden_netz_bleibt_offen(self):
        a = skript(aufruf("webseite", url="https://beispiel.de"), text("fertig"))
        with mock.patch.dict("kern.werkzeuge.suche.WERKZEUGE", {"webseite": lambda url="": "Seite"}):
            self.laufen(a)
        antwort = a.aufrufe[1]["inhalt"]["contents"][-1]["parts"][0]["functionResponse"]["response"]
        self.assertEqual(antwort, {"ergebnis": "Seite"})

    def test_werkzeugaufrufe_werden_protokolliert_und_ermoeglichen_hinweis(self):
        with mock.patch("kern.dashboard.friday_karten.legen", new=mock.AsyncMock(return_value={"id": "f"})):
            a = skript(aufruf("hinweis_geben", titel="Frist", text="Anmeldung endet übermorgen"),   # ohne Prüfung: abgelehnt
                       aufruf("rueckblick", suchbegriff="Anmeldung"),
                       aufruf("hinweis_geben", titel="Frist", text="Anmeldung endet übermorgen"), text("fertig"))
            self.laufen(a)
        rid = self.b.runden()[0]["id"]
        self.assertTrue(self.b.gedaechtnis_geprueft(rid))
        self.assertEqual(self.b.hinweise_in_runde(rid), 1)

    def test_anweisung_nennt_vorhaben_und_dubletten(self):
        a = skript(text("nichts"))
        self.laufen(a)
        system = a.aufrufe[0]["inhalt"]["systemInstruction"]["parts"][0]["text"].replace("\n", " ")
        for stichwort in ("vorhaben_vorschlagen", "vorhaben_lesen", "Höchstens ein Vorschlag je Runde", "Auffälligkeiten"):
            self.assertIn(stichwort, system)

    def test_anweisung_erklaert_fragen_an_eike(self):
        a = skript(text("nichts"))
        self.laufen(a)
        system = a.aufrufe[0]["inhalt"]["systemInstruction"]["parts"][0]["text"].replace("\n", " ")
        for stichwort in ("eike_fragen", "bezug", "Antworten von Eike", "Offene Fragen"):
            self.assertIn(stichwort, system)

    def test_prompt_enthaelt_die_neuen_qualitaetsregeln(self):
        a = skript(text("nichts"))
        self.laufen(a)
        system = a.aufrufe[0]["inhalt"]["systemInstruction"]["parts"][0]["text"].replace("\n", " ")
        for stichwort in ("Kalenderdaten", "Nichts Neues", "Gesundheit", "Gedächtnis"):
            self.assertIn(stichwort, system)


if __name__ == "__main__":
    unittest.main()
