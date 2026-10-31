"""Werkzeuge eike_fragen / frage_beantworten: nur Eike, nie nach Fremdtext, Push über `ffragen` — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.dienste import benachrichtigungen
from kern.fragen import fach
from kern.werkzeuge import katalog


def ausf(name, args=None, **kw):
    return asyncio.run(katalog.ausfuehren(name, args or {}, **kw))


def namen(tools):
    return {e["name"] for t in tools for e in t["functionDeclarations"]}


FRAGE = {"titel": "Briefing um 7 oder 8 Uhr?", "text": "Beides passt zu deinem Stundenplan.", "optionen": ["07:00", "08:00"]}


class Basis(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.f = fach.standard()
        p = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        self.push = p.start()
        self.addCleanup(p.stop)


class Fragen(Basis):
    def test_push_geht_ueber_senden_mit_art_fragen(self):
        e = ausf("eike_fragen", dict(FRAGE))
        self.assertIn("Gestellt", e["ergebnis"])
        self.assertEqual(self.push.call_args.args[0], "fragen")
        self.assertIn("fragen", benachrichtigungen.KENNT)
        self.assertEqual(self.f.offene()[0]["quelle"], "chat")

    def test_denkrunde_darf_fragen_quelle_denkrunde(self):
        from kern.denken import buch
        fid = buch.standard().faden_anlegen("Mathe Abitur", "Wann beginnt die Vorbereitung", quelle="eike")["id"]
        ausf("eike_fragen", {**FRAGE, "bezug": f"faden:{fid}"}, modus="denken", runde=4)
        self.assertEqual(self.f.offene()[0]["quelle"], "denkrunde")

    def test_denkrunde_darf_nicht_beantworten(self):
        fid = self.f.stellen("chat", "Titel hier", "Text hier", ["a"])["id"]
        e = ausf("frage_beantworten", {"id": fid, "option": 1}, modus="denken", runde=1, eingabe="x")
        self.assertIn("nicht erlaubt", e["fehler"])
        self.assertEqual(self.f.eintrag(fid)["status"], "offen")

    def test_nach_fremdtext_gesperrt(self):
        e = ausf("eike_fragen", dict(FRAGE), fremde_inhalte=True)
        self.assertIn("fehler", e)
        e = ausf("eike_fragen", dict(FRAGE), modus="denken", runde=2, fremde_inhalte=True)
        self.assertIn("fehler", e)
        self.assertEqual(self.f.offene(), [])
        self.push.assert_not_called()

    def test_person_sieht_das_werkzeug_nicht(self):
        person = {"id": "p1"}
        self.assertNotIn("eike_fragen", namen(katalog.gemini_tools(person=person)))
        self.assertNotIn("frage_beantworten", namen(katalog.gemini_tools(person=person)))
        e = ausf("eike_fragen", dict(FRAGE), person=person)
        self.assertIn("fehler", e)

    def test_eike_und_denkrunde_sehen_die_passenden_werkzeuge(self):
        self.assertTrue({"eike_fragen", "frage_beantworten"} <= namen(katalog.gemini_tools()))
        denken = namen(katalog.gemini_tools(modus="denken"))
        self.assertIn("eike_fragen", denken)
        self.assertNotIn("frage_beantworten", denken)

    def test_ruhezeit_kein_push_frage_bleibt_offen(self):
        self.push.return_value = {"gesendet": 0, "unterdrueckt": "ruhezeit"}
        e = ausf("eike_fragen", dict(FRAGE))
        self.assertIn("Gestellt", e["ergebnis"])
        self.assertEqual(len(self.f.offene()), 1)

    def test_push_fehler_stoert_die_frage_nicht(self):
        self.push.side_effect = RuntimeError("push kaputt")
        self.assertIn("Gestellt", ausf("eike_fragen", dict(FRAGE))["ergebnis"])

    def test_grenzen_kommen_als_nichts_gefragt_zurueck(self):
        e = ausf("eike_fragen", {**FRAGE, "optionen": list("abcde")})
        self.assertIn("NICHTS gefragt", e["ergebnis"])
        self.assertEqual(e["status"], "nicht")
        self.push.assert_not_called()


class DenkrundeBezug(Basis):
    def test_denkrunde_fragt_nur_mit_bezug_auf_eigenes_nicht_fremdes(self):
        from kern.denken import buch
        b = buch.standard()
        eigen = b.faden_anlegen("Mathe Abitur", "Wann beginnt die Vorbereitung", quelle="eike")["id"]
        fremd = b.faden_anlegen("Konzert Dezember", "Welche Karten gibt es", fremd=True, runde=b.runde_beginnen())["id"]
        ohne = ausf("eike_fragen", dict(FRAGE), modus="denken", runde=1)
        self.assertIn("NICHTS gefragt", ohne["ergebnis"])
        falsch = ausf("eike_fragen", {**FRAGE, "bezug": f"faden:{fremd}"}, modus="denken", runde=1)
        self.assertIn("NICHTS gefragt", falsch["ergebnis"])
        nirgends = ausf("eike_fragen", {**FRAGE, "bezug": "faden:9999"}, modus="denken", runde=1)
        self.assertIn("NICHTS gefragt", nirgends["ergebnis"])
        self.assertEqual(self.f.offene(), [])
        ok = ausf("eike_fragen", {**FRAGE, "bezug": f"faden:{eigen}"}, modus="denken", runde=1)
        self.assertIn("Gestellt", ok["ergebnis"])

    def test_im_chat_ist_kein_bezug_noetig(self):
        self.assertIn("Gestellt", ausf("eike_fragen", dict(FRAGE))["ergebnis"])


class Beantworten(Basis):
    def test_antwort_muss_in_eikes_worten_stehen(self):
        a = self.f.stellen("chat", "Wann soll ich erinnern?", "Text", ["07:00", "08:00"])["id"]
        e = ausf("frage_beantworten", {"id": a, "text": "8 Uhr"}, eingabe="erinner mich morgen an Mathe")
        self.assertIn("NICHTS festgehalten", e["ergebnis"])
        e = ausf("frage_beantworten", {"id": a, "option": 2}, eingabe="erinner mich morgen an Mathe")
        self.assertIn("NICHTS festgehalten", e["ergebnis"])
        self.assertEqual(self.f.eintrag(a)["status"], "offen")
        for eingabe in ("nimm Option 2", "die zweite bitte", "08:00 passt"):
            fach.NEU_PRO_TAG, alt = 99, fach.NEU_PRO_TAG
            self.addCleanup(setattr, fach, "NEU_PRO_TAG", alt)
            b = self.f.stellen("chat", "Frage " + eingabe + " xyz", "Text", ["07:00", "08:00"])["id"]
            self.assertIn("Festgehalten", ausf("frage_beantworten", {"id": b, "option": 2}, eingabe=eingabe)["ergebnis"], eingabe)
            self.f.verfallen_markieren()
            for x in self.f.offene():
                self.f.beantworten(x["id"], option=1)

    def test_freie_antwort_nur_wenn_sie_in_der_eingabe_steht(self):
        a = self.f.stellen("chat", "Wecker laut oder leise?", "Text", [])["id"]
        e = ausf("frage_beantworten", {"id": a, "text": "Lieber leise"}, eingabe="Ich möchte lieber leise, danke")
        self.assertIn("Festgehalten", e["ergebnis"])


    def test_beantworten_per_option_und_frei(self):
        a = self.f.stellen("chat", "Erste Frage Briefing", "Text", ["07:00", "08:00"])["id"]
        b = self.f.stellen("chat", "Zweite Frage Wecker", "Text", [])["id"]
        e = ausf("frage_beantworten", {"id": a, "option": 2}, eingabe="nimm Option 2")
        self.assertIn("08:00", e["ergebnis"])
        e = ausf("frage_beantworten", {"id": b, "text": "Lieber leise"}, eingabe="lieber leise")
        self.assertIn("Lieber leise", e["ergebnis"])

    def test_ohne_eingabe_von_eike_abgelehnt(self):
        a = self.f.stellen("chat", "Erste Frage Briefing", "Text", ["x"])["id"]
        e = ausf("frage_beantworten", {"id": a, "option": 1}, eingabe="")
        self.assertIn("NICHTS festgehalten", e["ergebnis"])
        self.assertEqual(self.f.eintrag(a)["status"], "offen")

    def test_nach_fremdtext_gesperrt(self):
        a = self.f.stellen("chat", "Erste Frage Briefing", "Text", ["x"])["id"]
        e = ausf("frage_beantworten", {"id": a, "option": 1}, eingabe="nimm Option 1", fremde_inhalte=True)
        self.assertIn("fehler", e)
        self.assertEqual(self.f.eintrag(a)["status"], "offen")

    def test_zweimal_beantworten_zaehlt_nur_einmal(self):
        a = self.f.stellen("chat", "Erste Frage Briefing", "Text", ["x", "y"])["id"]
        ausf("frage_beantworten", {"id": a, "option": 1}, eingabe="Option 1")
        e = ausf("frage_beantworten", {"id": a, "option": 2}, eingabe="Option 2")
        self.assertIn("NICHTS festgehalten", e["ergebnis"])
        self.assertEqual(self.f.eintrag(a)["antwort"], "x")


if __name__ == "__main__":
    unittest.main()
