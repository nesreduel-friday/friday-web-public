"""Modus „denken“: Positivliste, Runden-Werkzeuge nur dort, keine Schreibwerkzeuge — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.denken import buch
from kern.werkzeuge import katalog, suche


def namen(tools):
    return {e["name"] for t in tools for e in t["functionDeclarations"]}


class Positivliste(unittest.TestCase):
    def test_jeder_name_der_liste_existiert(self):
        vorhanden = {n for m in katalog._MODULE for n in m.WERKZEUGE}
        self.assertEqual(katalog.DENKEN_ERLAUBT - vorhanden, set())

    def test_nichts_schreibendes_in_der_liste(self):
        schreibend = set()
        for m in katalog._MODULE:
            schreibend |= set(getattr(m, "NUR_MIT_SCHREIBRECHT", ())) | set(getattr(m, "NICHT_NACH_FREMDTEXT", ()))
        # Benannte Ausnahme: `vorhaben_vorschlagen` legt nur einen Vorschlag an (startet nichts) und darf in der Denkrunde
        # entstehen; dafür steht es in NICHT_NACH_FREMDTEXT UND in DENKEN_NACH_FREMDTEXT_GESPERRT (eigener Test in
        # test_werkbank_werkzeuge). `vorhaben_starten` ist keine Ausnahme und fehlt in der Liste.
        # `eike_fragen` (28.10.2026): legt nur eine Frage an und pusht Eike selbst (wie `hinweis_geben`), nach Fremdtext gesperrt; `frage_beantworten` fehlt in der Liste.
        AUSNAHMEN = {"vorhaben_vorschlagen", "eike_fragen"}
        self.assertEqual(katalog.DENKEN_ERLAUBT & schreibend - AUSNAHMEN, set())

    def test_bekannte_schreibwerkzeuge_fehlen(self):
        for n in ("merken", "vergessen", "bild_benennen", "erinnerung", "erinnerung_absagen", "termin_anlegen",
                  "termin_bestaetigen", "schicke_nachricht", "schicke_snap", "whatsapp_senden", "whatsapp_antwort",
                  "benachrichtigung_stellen", "dashboard_karte", "person_korrigieren", "fahrt_merken", "musik_pause",
                  "faden_vorschlagen", "anliegen_erledigt"):
            self.assertNotIn(n, katalog.DENKEN_ERLAUBT, n)

    def test_tools_im_modus_denken_sind_genau_die_liste_soweit_freigegeben(self):
        angeboten = namen(katalog.gemini_tools(modus="denken"))
        self.assertTrue(angeboten <= katalog.DENKEN_ERLAUBT, angeboten - katalog.DENKEN_ERLAUBT)
        self.assertTrue({"gedanke_festhalten", "hinweis_geben", "gedanken_lesen"} <= angeboten)


class RundenWerkzeugeNurInDerRunde(unittest.TestCase):
    def test_im_normalen_chat_unsichtbar(self):
        angeboten = namen(katalog.gemini_tools())
        self.assertNotIn("gedanke_festhalten", angeboten)
        self.assertNotIn("hinweis_geben", angeboten)
        self.assertIn("gedanken_lesen", angeboten)

    def test_im_normalen_chat_nicht_ausfuehrbar(self):
        e = asyncio.run(katalog.ausfuehren("gedanke_festhalten", {"art": "frage", "text": "x"}))
        self.assertIn("fehler", e)


class Ausfuehren(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.b = buch.standard()
        self.r = self.b.runde_beginnen()

    def aus(self, name, **args):
        return asyncio.run(katalog.ausfuehren(name, args, modus="denken", runde=self.r))

    def test_erfundenes_schreibwerkzeug_wird_abgelehnt(self):
        for name, args in (("merken", {"thema": "a", "inhalt": "b"}),
                           ("termin_anlegen", {"titel": "x"}),
                           ("schicke_nachricht", {"empfaenger": "Kontakt C", "text": "hi"})):
            e = self.aus(name, **args)
            self.assertIn("fehler", e, name)
            self.assertIn("nicht erlaubt", e["fehler"])

    def test_gedanke_festhalten_nutzt_die_runde_des_kerns_nicht_die_des_modells(self):
        e = self.aus("gedanke_festhalten", art="erkenntnis", text="Etwas über Physik", quellen=["Notiz"], runde=999)
        self.assertIn("ergebnis", e)
        self.assertEqual(self.b.in_runde(self.r), 1)
        self.assertEqual(self.b.in_runde(999), 0)

    def test_fremd_flag_kommt_vom_kern(self):
        asyncio.run(katalog.ausfuehren("gedanke_festhalten", {"art": "erkenntnis", "text": "Mail widerspricht Kalender"},
                                       fremde_inhalte=True, modus="denken", runde=self.r))
        self.assertTrue(self.b.neueste()[0]["fremd"])

    def test_zehn_gedanken_nur_sechs_zaehlen_rest_bekommt_absage(self):
        antworten = [self.aus("gedanke_festhalten", art="erkenntnis", text=f"Gedanke {n} über Thema{n} und Sache{n}")
                     for n in range(10)]
        self.assertEqual(self.b.in_runde(self.r), buch.MAX_PRO_RUNDE)
        self.assertIn("schon", antworten[-1]["ergebnis"])

    def test_nach_fremdtext_gehen_keine_anfragen_mehr_ins_netz(self):
        # Eine Mail könnte die Runde anstiften, Daten in eine Adresse oder Suchanfrage zu packen („Datenabfluss“).
        for name in ("webseite", "suchen", "nachrichten"):
            with mock.patch.dict(suche.WERKZEUGE, {name: lambda *a, **k: "Treffer"}):
                danach = asyncio.run(katalog.ausfuehren(name, {}, fremde_inhalte=True, modus="denken", runde=self.r))
                davor = asyncio.run(katalog.ausfuehren(name, {}, fremde_inhalte=False, modus="denken", runde=self.r))
            self.assertIn("fehler", danach, name)
            self.assertIn("fremd", danach["fehler"].lower(), name)
            self.assertEqual((davor["ergebnis"], davor["status"]), ("Treffer", "ok"), name)   # P0: dazu Status/Art/Quittung

    def test_im_normalen_chat_bleibt_webseite_nach_fremdtext_wie_vorher(self):
        with mock.patch.dict(suche.WERKZEUGE, {"webseite": lambda *a, **k: "Seite"}):
            e = asyncio.run(katalog.ausfuehren("webseite", {}, fremde_inhalte=True))
        self.assertEqual((e["ergebnis"], e["status"]), ("Seite", "ok"))

    def test_gedanken_lesen_findet_und_meldet_leer(self):
        self.assertIn("keine", self.aus("gedanken_lesen", suche="Physik")["ergebnis"].lower())
        self.aus("gedanke_festhalten", art="erkenntnis", text="Etwas über Physik", quellen=["Notiz"])
        self.assertIn("Physik", self.aus("gedanken_lesen", suche="Physik")["ergebnis"])

    def test_hinweis_geben_geht_durch_die_grenzen(self):
        self.b.aufruf_merken(self.r, "wissen")
        with mock.patch("kern.dashboard.friday_karten.legen", new=mock.AsyncMock(return_value={"id": "f"})):
            e1 = self.aus("hinweis_geben", titel="Frist", text="Anmeldung endet übermorgen", wichtigkeit="normal")
            e2 = self.aus("hinweis_geben", titel="Noch was", text="Etwas völlig anderes zum Wetter am Samstag")
        self.assertIn("informiert", e1["ergebnis"])
        self.assertIn("Runde", e2["ergebnis"])


class FaedenWerkzeuge(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.b = buch.standard()
        self.f = self.b.faden_anlegen("Mathe Abitur", "Wann beginnt die Abiturvorbereitung", quelle="eike")["id"]
        self.r = self.b.runde_beginnen(fokus=self.f)

    def aus(self, name, runde=None, fremd=False, modus="denken", **args):
        return asyncio.run(katalog.ausfuehren(name, args, fremde_inhalte=fremd, modus=modus,
                                              runde=self.r if runde is None else runde))

    def test_sichtbarkeit_runde_chat(self):
        runde = namen(katalog.gemini_tools(modus="denken"))
        chat = namen(katalog.gemini_tools())
        self.assertTrue({"faden_anlegen", "faden_weiter", "faden_schliessen", "faeden_lesen"} <= runde)
        self.assertNotIn("faden_vorschlagen", runde)
        self.assertIn("faeden_lesen", chat)
        self.assertIn("faden_vorschlagen", chat)
        for n in ("faden_anlegen", "faden_weiter", "faden_schliessen"):
            self.assertNotIn(n, chat)

    def test_weiter_nur_fuer_den_fokus_faden(self):
        anderer = self.b.faden_anlegen("Fahrrad Reparatur", "Welche Ersatzteile braucht das Fahrrad", quelle="eike")["id"]
        e = self.aus("faden_weiter", id=anderer, stand="Heimlich weitergemacht")
        self.assertIn("Fokus", e["ergebnis"])
        self.assertEqual(self.b.faden(anderer)["schritte"], 0)
        e = self.aus("faden_weiter", id=self.f, stand="Die Vorbereitung beginnt im Januar")
        self.assertIn("Stand", e["ergebnis"])
        self.assertEqual(self.b.faden(self.f)["schritte"], 1)

    def test_schliessen_nur_fuer_den_fokus_faden_und_mit_ergebnis(self):
        anderer = self.b.faden_anlegen("Fahrrad Reparatur", "Welche Ersatzteile braucht das Fahrrad", quelle="eike")["id"]
        self.assertIn("Fokus", self.aus("faden_schliessen", id=anderer, ergebnis="X")["ergebnis"])
        self.assertIn("Ergebnis", self.aus("faden_schliessen", id=self.f, ergebnis=" ")["ergebnis"])
        self.assertEqual(self.b.faden(self.f)["status"], "offen")
        self.assertIn("abgeschlossen", self.aus("faden_schliessen", id=self.f, ergebnis="Beginn im Januar")["ergebnis"])
        self.assertEqual(self.b.faden(self.f)["status"], "erledigt")

    def test_runde_ohne_fokus_darf_nichts_bewegen(self):
        ohne = self.b.runde_beginnen()
        self.assertIn("Fokus", self.aus("faden_weiter", runde=ohne, id=self.f, stand="x")["ergebnis"])
        self.assertIn("Fokus", self.aus("faden_schliessen", runde=ohne, id=self.f, ergebnis="x")["ergebnis"])

    def test_anlegen_in_der_runde_traegt_das_fremd_flag(self):
        e = self.aus("faden_anlegen", fremd=True, titel="Konzert Dezember", aufgabe="Welche Karten gibt es für das Konzert")
        self.assertIn("angelegt", e["ergebnis"])
        self.assertTrue(self.b.faeden()[0]["fremd"])

    def test_anlegen_zweiter_faden_in_einer_runde_wird_abgelehnt(self):
        self.aus("faden_anlegen", titel="Konzert Dezember", aufgabe="Welche Karten gibt es für das Konzert")
        e = self.aus("faden_anlegen", titel="Urlaub Sommer", aufgabe="Wohin soll die Reise im nächsten Sommer gehen")
        self.assertIn("Runde", e["ergebnis"])

    def test_stand_und_ergebnis_nach_fremdtext_markieren_faden_und_gedanke(self):
        self.aus("faden_weiter", fremd=True, id=self.f, stand="Laut Mail beginnt es im Januar")
        self.assertTrue(self.b.faden(self.f)["fremd"])
        f2 = self.b.faden_anlegen("Fahrrad Reparatur", "Welche Ersatzteile braucht das Fahrrad", quelle="eike")["id"]
        r2 = self.b.runde_beginnen(fokus=f2)
        asyncio.run(katalog.ausfuehren("faden_schliessen", {"id": f2, "ergebnis": "Laut Mail: Kette tauschen"},
                                       fremde_inhalte=True, modus="denken", runde=r2))
        self.assertTrue(self.b.neueste()[0]["fremd"])

    def test_zu_langes_ergebnis_sagt_dem_modell_es_solle_sich_kuerzen(self):
        e = self.aus("faden_schliessen", id=self.f, ergebnis="wort " * 300)
        self.assertIn("Zu lang", e["ergebnis"])
        self.assertEqual(self.b.faden(self.f)["status"], "offen")

    def test_faeden_lesen_zeigt_status_stand_und_kennzeichnet_fremde(self):
        self.b.faden_weiter(self.f, "Beginn im Januar")
        self.b.faden_anlegen("Konzert Dezember", "Welche Karten gibt es", fremd=True, runde=self.r)
        text = self.aus("faeden_lesen")["ergebnis"]
        self.assertIn("Mathe Abitur", text)
        self.assertIn("Beginn im Januar", text)
        self.assertIn("unbestätigt", text)

    def test_faeden_lesen_leer(self):
        self.assertIn("keine", self.aus("faeden_lesen", status="erledigt")["ergebnis"].lower())

    def test_vorschlagen_im_chat_legt_einen_eike_faden_an(self):
        e = self.aus("faden_vorschlagen", modus="", titel="Führerschein Anmeldung", aufgabe="Bis wann muss die Anmeldung erfolgen")
        self.assertIn("denke", e["ergebnis"].lower())
        self.assertEqual(self.b.faeden()[0]["quelle"], "eike")

    def test_vorschlagen_nach_fremdtext_gesperrt_und_nicht_in_der_runde(self):
        nach = self.aus("faden_vorschlagen", modus="", fremd=True, titel="Führerschein Anmeldung", aufgabe="Bis wann")
        self.assertIn("fehler", nach)
        self.assertIn("fehler", self.aus("faden_vorschlagen", titel="X", aufgabe="Y"))   # Modus denken: nicht erlaubt
        self.assertEqual(len(self.b.faeden()), 1)


    def test_fertiger_faden_wird_ein_anliegen_laenge_gekuerzt_fremd_uebernommen(self):
        from kern.anliegen import fach
        text = ("Die Info-Veranstaltung ist am Freitag um 16:30 Uhr. " * 12).strip()
        self.aus("faden_schliessen", id=self.f, ergebnis=text, fremd=True)
        a = fach.standard().offene("eike")[0]
        self.assertEqual((a["quelle"], a["fremd"]), ("faden", True))
        self.assertTrue(a["titel"].startswith("Faden fertig: Mathe Abitur"))
        self.assertLessEqual(len(a["text"]), fach.TEXT_MAX)
        self.assertTrue(a["text"].endswith("(Mehr im Reiter Gedanken.)"))

    def test_abgelehnter_abschluss_legt_kein_anliegen_an(self):
        from kern.anliegen import fach
        self.aus("faden_schliessen", id=self.f, ergebnis="wort " * 300)
        self.assertEqual(fach.standard().offene("eike"), [])


if __name__ == "__main__":
    unittest.main()
