"""Eikes Werkzeuge für Regeln: schreiben (Entwurf + Klartext), bestätigen (nur mit Ja), widerrufen, lesen."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.dienste import whatsapp
from kern.personen import register
from kern.regeln import speicher
from kern.werkzeuge import katalog

CHAT = "4915100000000@c.us"
NAMEN = {"regel_schreiben", "regel_bestaetigen", "regel_widerrufen", "regeln_lesen"}


def aus(name, args=None, **kw):
    return asyncio.run(katalog.ausfuehren(name, args or {}, **kw))


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.kontakt_f = register.anlegen("Kontakt F Beispiel", snapchat="paul_x")["id"]
        self.mama = register.anlegen("Kontakt P Beispiel", snapchat="mama_x")["id"]
        self.frei = mock.patch.object(whatsapp, "frei", return_value=[{"id": CHAT, "name": "Kontakt F", "senden": True}])
        self.frei.start()

    def tearDown(self):
        self.frei.stop()
        self.env.stop()

    def schreiben(self, **kw):
        a = {"name": "Kontakt F antwortet", "kanal": "whatsapp", "wer": "Kontakt F", "auftrag": "Antworte kurz und trag es in den Kalender ein.",
             "erlaubt": ["antwort_an_ausloeser", "kalender_eintragen"]}
        a.update(kw)
        return aus("regel_schreiben", a)


class Schreiben(Basis):
    def test_legt_einen_entwurf_an_und_zeigt_den_klartext(self):
        r = self.schreiben()
        self.assertEqual(r["status"], "rueckfrage", r)
        regel = speicher.liste()[0]
        self.assertEqual((regel["status"], regel["ausloeser"]["chat_id"]), ("entwurf", CHAT))
        for wort in ("Kontakt F", "WhatsApp", "kalender", "Antworte kurz", "5", "20"):
            self.assertIn(wort.casefold(), r["ergebnis"].casefold(), wort)
        self.assertEqual(speicher.aktive(), [])

    def test_snapchat_regel_nach_namen(self):
        r = self.schreiben(kanal="snapchat", wer="Kontakt F Beispiel", erlaubt=["push"])
        self.assertEqual(r["status"], "rueckfrage", r)
        self.assertEqual(speicher.liste()[0]["ausloeser"]["person_id"], self.kontakt_f)

    def test_unbekannt_oder_nicht_freigegeben_legt_nichts_an(self):
        for kw in ({"wer": "Oma"}, {"kanal": "snapchat", "wer": "Oma"}, {"kanal": "mail"}, {"erlaubt": ["loeschen"]}, {"auftrag": ""}):
            r = self.schreiben(**kw)
            self.assertEqual(r["status"], "nicht", (kw, r))
        self.assertEqual(speicher.liste(), [])

    def test_empfaenger_werden_nach_namen_aufgeloest(self):
        r = self.schreiben(empfaenger="Kontakt P Beispiel", erlaubt=["antwort_an_empfaenger"])
        self.assertEqual(r["status"], "rueckfrage", r)
        self.assertEqual(speicher.liste()[0]["empfaenger"][0]["id"], self.mama)
        self.assertIn("Kontakt P Beispiel", r["ergebnis"])
        r = self.schreiben(name="zwei", empfaenger="Niemand", erlaubt=["antwort_an_empfaenger"])
        self.assertEqual(r["status"], "nicht")

    def test_empfaenger_ohne_aktion_wird_abgelehnt(self):
        r = self.schreiben(empfaenger="Kontakt P Beispiel", erlaubt=["push"])
        self.assertEqual(r["status"], "nicht")
        self.assertEqual(speicher.liste(), [])


class Bestaetigen(Basis):
    def test_nur_mit_zustimmung_wird_die_regel_aktiv(self):
        self.schreiben()
        rid = speicher.liste()[0]["id"]
        r = aus("regel_bestaetigen", {"id": rid}, zustimmung=False)
        self.assertNotEqual(r["status"], "ok")
        self.assertEqual(speicher.holen(rid)["status"], "entwurf")
        r = aus("regel_bestaetigen", {"id": rid}, zustimmung=True)
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(speicher.holen(rid)["status"], "aktiv")

    def test_nach_fremdtext_laeuft_nichts(self):
        self.schreiben()
        rid = speicher.liste()[0]["id"]
        for name, args in (("regel_bestaetigen", {"id": rid}), ("regel_widerrufen", {"id": rid}), ("regel_schreiben", {"name": "x"})):
            r = aus(name, args, zustimmung=True, fremde_inhalte=True)
            self.assertIn("fehler", r, name)
        self.assertEqual(speicher.holen(rid)["status"], "entwurf")

    def test_bestaetigen_nicht_in_derselben_aeusserung_wie_das_schreiben(self):
        a = {"name": "Kontakt F antwortet", "kanal": "whatsapp", "wer": "Kontakt F", "auftrag": "Antworte kurz.", "erlaubt": ["push"]}
        aus("regel_schreiben", a, eingabe="Ja, leg eine Regel an: wenn Kontakt F schreibt …")
        rid = speicher.liste()[0]["id"]
        r = aus("regel_bestaetigen", {"id": rid}, zustimmung=True, eingabe="Ja, leg eine Regel an: wenn Kontakt F schreibt …")
        self.assertNotEqual(r["status"], "ok")
        self.assertEqual(speicher.holen(rid)["status"], "entwurf")
        r = aus("regel_bestaetigen", {"id": rid}, zustimmung=True, eingabe="Ja, genau so")
        self.assertEqual(r["status"], "ok", r)

    def test_widerrufen(self):
        self.schreiben()
        rid = speicher.liste()[0]["id"]
        aus("regel_bestaetigen", {"id": rid}, zustimmung=True)
        r = aus("regel_widerrufen", {"id": rid})
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(speicher.holen(rid)["status"], "widerrufen")
        self.assertEqual(aus("regel_widerrufen", {"id": 9999})["status"], "nicht")

    def test_regeln_lesen_zeigt_status_und_letzten_lauf(self):
        self.schreiben()
        rid = speicher.liste()[0]["id"]
        aus("regel_bestaetigen", {"id": rid}, zustimmung=True)
        speicher.lauf_ende(speicher.lauf_neu(rid, "m1", "Hi"), "ok", [])
        r = aus("regeln_lesen")
        self.assertIn("Kontakt F antwortet", r["ergebnis"])
        self.assertIn("aktiv", r["ergebnis"])
        self.assertIn("ok", r["ergebnis"])
        self.assertEqual(aus("regeln_lesen")["art"], "lesen")


class Zeit(Basis):
    def test_zeitregel_per_wann_und_klartext(self):
        r = aus("regel_schreiben", {"name": "Absage", "kanal": "zeit", "wann": "täglich 18:00", "auftrag": "Schreib Kontakt F, dass ich nicht komme.",
                                    "erlaubt": ["antwort_an_empfaenger"], "empfaenger": "Kontakt F"})
        self.assertEqual(r["status"], "rueckfrage", r)
        regel = speicher.liste()[0]
        self.assertEqual((regel["kanal"], regel["ausloeser"]), ("zeit", {"typ": "taeglich", "uhrzeit": "18:00"}))
        self.assertEqual(regel["empfaenger"][0]["id"], CHAT)
        self.assertIn("18:00", r["ergebnis"])
        self.assertIn("jeden tag", r["ergebnis"].casefold())

    def test_zeitregel_ohne_oder_mit_falscher_zeit_wird_abgelehnt(self):
        for kw in ({"wann": ""}, {"wann": "irgendwann"}, {"wann": "2020-01-01T10:00"}):
            r = aus("regel_schreiben", {"name": "x", "kanal": "zeit", "auftrag": "Hi", "erlaubt": ["push"], **kw})
            self.assertEqual(r["status"], "nicht", (kw, r))
        self.assertEqual(speicher.liste(), [])

    def test_streichen_nur_mit_ja_und_nie_nach_fremdtext(self):
        self.schreiben()
        rid = speicher.liste()[0]["id"]
        r = aus("regel_streichen", {"id": rid}, zustimmung=False)
        self.assertNotEqual(r["status"], "ok")
        self.assertIsNotNone(speicher.holen(rid))
        r = aus("regel_streichen", {"id": rid}, zustimmung=True, fremde_inhalte=True)
        self.assertIn("fehler", r)
        self.assertIsNotNone(speicher.holen(rid))
        r = aus("regel_streichen", {"id": rid}, zustimmung=True)
        self.assertEqual(r["status"], "ok", r)
        self.assertIsNone(speicher.holen(rid))
        self.assertEqual(aus("regel_streichen", {"id": rid}, zustimmung=True)["status"], "nicht")

    def test_loeschen_ist_nur_fuer_eike_sichtbar(self):
        namen = {e["name"] for t in katalog.gemini_tools() for e in t["functionDeclarations"]}
        self.assertIn("regel_streichen", namen)
        for kw in ({"person": register.holen(self.mama)}, {"modus": "denken"}, {"modus": "regel"}):
            self.assertNotIn("regel_streichen", {e["name"] for t in katalog.gemini_tools(**kw) for e in t["functionDeclarations"]})


class Sichtbarkeit(Basis):
    def namen(self, **kw):
        return {e["name"] for t in katalog.gemini_tools(**kw) for e in t["functionDeclarations"]}

    def test_nur_eike_im_gespraech(self):
        self.assertTrue(NAMEN <= self.namen())
        self.assertFalse(NAMEN & self.namen(person=register.holen(self.mama)))
        self.assertFalse(NAMEN & self.namen(modus="denken"))
        self.assertFalse(NAMEN & self.namen(modus="regel"))
        for modus in ("denken", "regel"):
            r = aus("regel_schreiben", {"name": "x"}, modus=modus)
            self.assertIn("nicht erlaubt", r["fehler"], modus)
        r = aus("regel_widerrufen", {"id": 1}, person=register.holen(self.mama))
        self.assertIn("nicht erlaubt", r["fehler"])


if __name__ == "__main__":
    unittest.main()


class PersonNachNameTest(unittest.TestCase):
    def test_vorname_eindeutig(self):
        from kern.werkzeuge import regeln
        personen = [{"id": "1", "name": "Kontakt C"}, {"id": "2", "name": "Mia Berg"}, {"id": "3", "name": "Mia Kruse"}]
        with mock.patch.object(regeln.register, "liste", lambda: personen):
            self.assertEqual(regeln._person_nach_name("Kontakt", True)["id"], "1")
            self.assertEqual(regeln._person_nach_name("kontakt c")["id"], "1")
            self.assertIsNone(regeln._person_nach_name("Kontakt"))  # ohne Vorname-Erlaubnis nur der volle Name
            self.assertIsNone(regeln._person_nach_name("Mia", True))  # mehrdeutig
            self.assertIsNone(regeln._person_nach_name("Ko", True))   # zu kurz
