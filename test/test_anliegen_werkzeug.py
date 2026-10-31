"""anliegen_erledigt: nur Eike, nur eigene Anliegen, nicht nach Fremdtext."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.anliegen import fach
from kern.werkzeuge import katalog


def namen(tools):
    return {e["name"] for t in tools for e in t["functionDeclarations"]}


class Werkzeug(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        ruhe = mock.patch("kern.anliegen.fach._ist_ruhezeit", return_value=False)
        ruhe.start()
        self.addCleanup(ruhe.stop)
        self.f = fach.standard()
        self.a = self.f.anlegen("eike", "hinweis", "Frist Anmeldung", "Die Anmeldung endet übermorgen")["id"]

    def aus(self, **args):
        fremd = args.pop("fremd", False)
        return asyncio.run(katalog.ausfuehren("anliegen_erledigt", args, fremde_inhalte=fremd))

    def test_sichtbar_fuer_eike_nicht_in_der_runde_nicht_fuer_personen(self):
        self.assertIn("anliegen_erledigt", namen(katalog.gemini_tools()))
        self.assertNotIn("anliegen_erledigt", namen(katalog.gemini_tools(modus="denken")))
        person = {"id": "p1", "name": "Kontakt B", "werkzeuge": ["uhrzeit"]}
        self.assertNotIn("anliegen_erledigt", namen(katalog.gemini_tools(person=person)))
        self.assertIn("fehler", asyncio.run(katalog.ausfuehren("anliegen_erledigt", {"id": self.a}, person=person)))

    def test_erwaehnt_spaeter_nicht_mehr(self):
        self.assertIn("erledigt", self.aus(id=self.a, wie="erwähnt")["ergebnis"].lower())
        self.assertEqual(self.f.eintrag(self.a)["status"], "erwaehnt")
        b = self.f.anlegen("eike", "hinweis", "Geburtstag Kontakt P", "Kontakt P hat nächste Woche Dienstag Geburtstag")["id"]
        self.assertIn("später", self.aus(id=b, wie="später")["ergebnis"].lower())
        self.assertEqual(self.f.eintrag(b)["status"], "zurueckgestellt")
        c = self.f.anlegen("eike", "hinweis", "Bus Ausfall", "Linie 100 fällt morgen früh komplett aus")["id"]
        self.aus(id=c, wie="nicht_mehr")
        self.assertEqual(self.f.eintrag(c)["status"], "verfallen")

    def test_standard_ist_erwaehnt(self):
        self.aus(id=self.a)
        self.assertEqual(self.f.eintrag(self.a)["status"], "erwaehnt")

    def test_fremde_unbekannte_und_unsinnige_aufrufe(self):
        p = self.f.anlegen("p1", "erinnerung", "Buch", "Bring das Buch am Montag mit zur Schule")["id"]
        self.assertIn("nicht", self.aus(id=p, wie="erwähnt")["ergebnis"].lower())     # gehört einer Person
        self.assertEqual(self.f.eintrag(p)["status"], "offen")
        self.assertIn("nicht", self.aus(id=9999)["ergebnis"].lower())
        self.assertIn("wie", self.aus(id=self.a, wie="loeschen")["ergebnis"].lower())
        self.assertIn("Nummer", self.aus(id="abc")["ergebnis"])                          # ungültige Id: Absage statt Absturz

    def test_nach_fremdtext_gesperrt(self):
        e = self.aus(id=self.a, fremd=True)
        self.assertIn("fehler", e)
        self.assertEqual(self.f.eintrag(self.a)["status"], "offen")


if __name__ == "__main__":
    unittest.main()
