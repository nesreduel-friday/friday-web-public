"""Werkzeuge einer fremden Person: nur die erlaubten, Eikes Werkzeuge nie, frage_eike nur für Fremde."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.dienste import benachrichtigungen
from kern.personen import register, rechte, rueckfragen
from kern.werkzeuge import katalog


def lauf(coro):
    return asyncio.run(coro)


def namen(person=None):
    return {e["name"] for g in katalog.gemini_tools(person) for e in g["functionDeclarations"]}


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        p = mock.patch.object(benachrichtigungen, "senden", return_value={"gesendet": 1})
        p.start()
        self.addCleanup(p.stop)
        self.person = register.anlegen("Kontakt B", snapchat="maya_x", werkzeuge=["uhrzeit", "suchen"])

    def tearDown(self):
        self.env.stop()


class Anbieten(Basis):
    def test_eike_sieht_alles_ausser_frage_eike(self):
        n = namen(None)
        self.assertIn("uhrzeit", n)
        self.assertNotIn("frage_eike", n)

    def test_person_sieht_genau_ihre_werkzeuge_plus_frage_eike(self):
        self.assertEqual(namen(self.person), {"uhrzeit", "suchen", "nachrichten", "frage_eike", "termin_wunsch"})   # Grundfunktionen gelten immer (14.10.2026)

    def test_neue_person_hat_nur_die_grundfunktionen_und_frage_eike(self):
        neu = register.anlegen("Kontakt F", snapchat="kontakt_f")
        self.assertEqual(namen(neu), {"uhrzeit", "suchen", "nachrichten", "frage_eike", "termin_wunsch"})

    def test_auch_ein_manipuliertes_register_gibt_keine_werkzeuge_auf_eikes_daten(self):
        # Daten-Rechte seit 04.10.2026 wählbar — kalender/termin_wunsch im Eintrag zählen dann (Eikes Haken),
        # der Rest bleibt draußen.
        boese = dict(self.person, werkzeuge=["uhrzeit", "kalender", "termin_wunsch", "wissen", "whatsapp_lesen", "merken", "schicke_snap"])
        # kalender braucht zusätzlich den iCloud-Konnektor (hier aus) — termin_wunsch und frage_eike gelten immer.
        self.assertEqual(namen(boese), {"uhrzeit", "suchen", "nachrichten", "frage_eike", "termin_wunsch"})

    def test_alle_angebotenen_namen_liegen_in_der_positivliste(self):
        self.assertLessEqual(namen(self.person) - {"frage_eike"}, set(rechte.FREMDEN_ERLAUBT))


class Ausfuehren(Basis):
    def test_erfundener_aufruf_eines_eike_werkzeugs_laeuft_nie(self):
        for name in ("kalender", "wissen", "merken", "whatsapp_lesen", "schicke_snap", "pc_starten", "rueckblick", "erinnern"):
            r = lauf(katalog.ausfuehren(name, {}, person=self.person))
            self.assertIn("fehler", r, name)
            self.assertIn("nicht erlaubt", r["fehler"], name)

    def test_erlaubtes_werkzeug_laeuft(self):
        r = lauf(katalog.ausfuehren("uhrzeit", {}, person=self.person))
        self.assertIn("ergebnis", r)

    def test_werkzeug_ausserhalb_der_grundfunktionen_und_module_laeuft_nicht(self):
        r = lauf(katalog.ausfuehren("kalender", {}, person=register.holen(self.person["id"])))
        self.assertIn("nicht erlaubt", r["fehler"])

    def test_eike_darf_frage_eike_nicht_aufrufen(self):
        r = lauf(katalog.ausfuehren("frage_eike", {"thema": "x"}))
        self.assertIn("nicht erlaubt", r["fehler"])

    def test_eikes_aufrufe_ohne_person_bleiben_unveraendert(self):
        self.assertIn("ergebnis", lauf(katalog.ausfuehren("uhrzeit", {})))


class FrageEike(Basis):
    def test_frage_eike_legt_eine_rueckfrage_an_und_antwortet_neutral(self):
        r = lauf(katalog.ausfuehren("frage_eike", {"thema": "Geburtstag", "wunsch": "Wann hat Eike Geburtstag?"},
                                    person=self.person))
        self.assertIn("ergebnis", r)
        self.assertNotIn("Geburtstag", r["ergebnis"])       # verrät nichts über Eike
        offen = rueckfragen.offene()
        self.assertEqual([(a["person"], a["thema"]) for a in offen], [(self.person["id"], "Geburtstag")])

    def test_das_modell_kann_die_kontakt_id_nicht_setzen(self):
        anderer = register.anlegen("Kontakt F", snapchat="kontakt_f")
        lauf(katalog.ausfuehren("frage_eike", {"thema": "Thema", "kontakt_id": anderer["id"]}, person=self.person))
        self.assertEqual([a["person"] for a in rueckfragen.offene()], [self.person["id"]])

    def test_zu_viele_rueckfragen_werden_ehrlich_abgelehnt(self):
        for i in range(rueckfragen.MAX_OFFEN):
            lauf(katalog.ausfuehren("frage_eike", {"thema": f"T{i}"}, person=self.person))
        r = lauf(katalog.ausfuehren("frage_eike", {"thema": "Noch eins"}, person=self.person))
        self.assertIn("ergebnis", r)                          # neutrale Antwort, aber ...
        self.assertEqual(len(rueckfragen.offene()), rueckfragen.MAX_OFFEN)  # ... keine sechste Rückfrage


if __name__ == "__main__":
    unittest.main()
