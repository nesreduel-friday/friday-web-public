"""akte_zusammenfassung und die Bestätigungs-Sperre des Standard-Skills — Messdaten und Antrag sind Attrappen."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from kern.garantie import ablage
from kern.personen import register
from kern.skills import akten, fach, standard, zug
from kern.werkzeuge import akte as wakte
from kern.werkzeuge import garantie_annahme as wga

NAME = standard.NAME
VOLL = {"datum": "2026-10-12", "linie": "5", "abfahrt": "07:42", "start": "Hauptbahnhof", "ziel": "Empelde"}
FAHRT = {"reise": "r1", "von": "h1", "bis": "h2"}


def lauf(c):
    return asyncio.run(c)


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.a = ablage.standard()
        self.profil = self.a.profil_anlegen({"name": "Kontakt B", "anrede": "Frau", "vorname": "Kontakt B", "nachname": "Muster",
                                             "email": "maya@example.net", "strasse": "Weg 1", "plz": "30449", "ort": "Hannover",
                                             "vollmacht": True})
        self.p = register.anlegen("Kontakt B Muster", snapchat="maya_x")
        register.aendern(self.p["id"], quellen={"garantie": self.profil})
        self.pid = self.p["id"]
        self.vorbereiten = mock.AsyncMock(return_value={"ok": True, "antrag": {"id": 7}})
        for m in (mock.patch("kern.werkzeuge.akte.konnektoren.benutzbar", return_value=True),
                  mock.patch("kern.werkzeuge.akte._eike_melden"), mock.patch("kern.werkzeuge.akte._eike_fragen"),
                  mock.patch("kern.werkzeuge.akte.verwaltung"),
                  mock.patch("kern.werkzeuge.akte._fahrt_finden", mock.AsyncMock(return_value=(FAHRT, ""))),
                  mock.patch("kern.werkzeuge.akte.antrag.verlauf", mock.AsyncMock(return_value={"halte": [{"reference": "h2", "verspaetung": 23}]})),
                  mock.patch("kern.werkzeuge.akte.antrag.vorbereiten", self.vorbereiten)):
            m.start()
        self.addCleanup(mock.patch.stopall)
        wakte._registrieren()
        wga._registrieren()
        self.ticket = self.a.ticket_speichern("t.jpg", "image/jpeg", b"x", {
            "art": "Deutschlandticket", "bezeichnung": "Deutschlandticket", "herausgeber": "ÜSTRA", "gueltig_von": "2026-10-01",
            "gueltig_bis": "2026-10-31", "form": "digital", "kundenkarte": "123456", "ticketnummer": "AB12"}, self.profil)

    def tearDown(self):
        for i in akten._instanzen.values():
            i._db.close()
        akten._instanzen.clear()
        fach._instanzen.clear()
        self.env.stop()

    def erfassen(self, **w):
        return wakte.akte_erfassen(kontakt_id=self.pid, skill=NAME, felder={**VOLL, **w})

    def zusammenfassung(self):
        return lauf(wga.akte_zusammenfassung(kontakt_id=self.pid, skill=NAME))

    def zeigen(self):
        """Zusammenfassung bauen UND der Person zeigen (Antwort nennt die Merkstücke) — wie es `chat.antworten` am Ende des Zuges tut."""
        r = self.zusammenfassung()
        zug.festschreiben(r)
        return r

    def antrag(self):
        return lauf(wakte.garantie_antrag_aus_akte(kontakt_id=self.pid, skill=NAME))


class Zusammenfassung(Basis):
    def test_unvollstaendig_nennt_was_fehlt_und_merkt_nichts(self):
        wakte.akte_erfassen(kontakt_id=self.pid, skill=NAME, felder={"linie": "5"})
        with zug.zug():
            r = self.zusammenfassung()
        self.assertIn("fehlen", r.lower())
        self.assertIsNone(akten.standard().zusammenfassung(akten.standard().letzte(NAME, self.pid)["id"]))

    def test_ohne_ticket_fuer_den_tag_wird_das_ticket_verlangt(self):
        self.erfassen(datum="2026-08-03")                    # vor der Gültigkeit des Tickets
        with zug.zug():
            r = self.zusammenfassung()
        self.assertIn("Ticket", r)
        self.assertFalse(r.startswith("Zusammenfassung"))

    def test_zusammenfassung_nennt_fahrt_messung_und_ticket(self):
        self.erfassen()
        with zug.zug():
            r = self.zusammenfassung()
        self.assertTrue(r.startswith("Zusammenfassung"), r)
        for stueck in ("12.10.2026", "Linie 5", "07:42", "Hauptbahnhof", "Empelde", "23 Minuten", "Deutschlandticket", "31.10.2026"):
            self.assertIn(stueck, r)

    def test_ohne_gemessene_fahrt_sagt_sie_es_ehrlich(self):
        self.erfassen()
        with mock.patch("kern.werkzeuge.akte._fahrt_finden", mock.AsyncMock(return_value=(None, "keine passende gemessene Fahrt"))), zug.zug():
            r = self.zusammenfassung()
        self.assertTrue(r.startswith("Zusammenfassung"))
        self.assertIn("Eike", r)
        self.assertNotIn("Minuten", r)

    def test_nur_der_standard_skill_hat_sie(self):
        self.erfassen()
        r = lauf(wga.akte_zusammenfassung(kontakt_id=self.pid, skill="anderer"))
        self.assertIn("nichts vereinbart", r)

    def test_ohne_modul_nicht_erlaubt(self):
        register.aendern(self.pid, module={"garantie": "aus"})
        self.assertIn("nicht erlaubt", self.zusammenfassung())

    def test_der_stand_wird_fuer_die_sperre_gemerkt(self):
        self.erfassen()
        with zug.zug():
            self.zeigen()
        z = akten.standard().zusammenfassung(akten.standard().letzte(NAME, self.pid)["id"])
        self.assertIsNotNone(z)


class Sperre(Basis):
    def test_ohne_zusammenfassung_kein_antrag(self):
        self.erfassen()
        with zug.zug():
            self.assertIn("Zeig der Person erst die Zusammenfassung", self.antrag())
        self.vorbereiten.assert_not_awaited()

    def test_im_selben_zug_kein_antrag(self):
        self.erfassen()
        with zug.zug():
            self.zeigen()
            self.assertIn("gerade erst gezeigt", self.antrag())
        self.vorbereiten.assert_not_awaited()

    def test_neuer_zug_dann_geht_der_antrag(self):
        self.erfassen()
        with zug.zug():
            self.zeigen()
        with zug.zug():
            r = self.antrag()
        self.assertIn("Der Antrag ist vorbereitet", r)
        self.vorbereiten.assert_awaited_once()

    def test_geaenderte_akte_macht_die_zusammenfassung_ungueltig(self):
        self.erfassen()
        with zug.zug():
            self.zeigen()
        self.erfassen(linie="6")
        with zug.zug():
            self.assertIn("Zeig der Person erst die Zusammenfassung", self.antrag())

    def test_anderes_ticket_macht_die_zusammenfassung_ungueltig(self):
        self.erfassen()
        with zug.zug():
            self.zeigen()
        self.a.ticket_merken(self.ticket["id"], nummern_geprueft=True)
        with zug.zug():
            self.assertIn("Zeig der Person erst die Zusammenfassung", self.antrag())

    def test_ausserhalb_eines_zuges_kein_antrag(self):
        self.erfassen()
        self.zusammenfassung()                                # ohne Zugkontext: nichts vorgemerkt, nichts geschrieben
        self.assertIn("Zeig der Person erst die Zusammenfassung", self.antrag())

    def test_wird_sie_nicht_genannt_haengt_der_server_sie_an_und_dann_gilt_sie_als_gezeigt(self):
        self.erfassen()
        with zug.zug():
            self.zusammenfassung()
            antwort = zug.festschreiben("Alles klar, ich kümmere mich drum.")      # Friday nennt die Fahrt nicht
        self.assertIn("Linie 5", antwort)
        self.assertIn("Empelde", antwort)
        self.assertIn("Ticket", antwort)
        with zug.zug():
            self.assertIn("Der Antrag ist vorbereitet", self.antrag())

    def test_ohne_zustellung_gilt_sie_nicht(self):
        self.erfassen()
        with zug.zug():
            self.zusammenfassung()                            # gebaut, aber `festschreiben` kommt nie (z. B. Gemini-Fehler)
        with zug.zug():
            self.assertIn("Zeig der Person erst die Zusammenfassung", self.antrag())
        self.vorbereiten.assert_not_awaited()


class TicketHinweis(Basis):
    def test_akte_fehlt_nennt_fehlendes_ticket(self):
        self.erfassen(datum="2026-08-03")
        self.assertIn("kein gültiges Ticket", wakte.akte_fehlt(kontakt_id=self.pid, skill=NAME))

    def test_akte_fehlt_schweigt_wenn_ein_ticket_gilt(self):
        self.erfassen()
        self.assertNotIn("kein gültiges Ticket", wakte.akte_fehlt(kontakt_id=self.pid, skill=NAME))


class OhneLinie(Basis):
    """Die Person nennt nur Tag, Einstieg und Ausstieg: Die Fahrt wird gesucht und Linie/Abfahrt kommen aus der Messung."""

    ANGABEN = {"datum": "2026-10-12", "start": "Hauptbahnhof", "ziel": "Empelde"}

    def erfassen_kurz(self, **w):
        return wakte.akte_erfassen(kontakt_id=self.pid, skill=NAME, felder={**self.ANGABEN, **w})

    def treffer(self, *fahrten):
        return mock.patch("kern.werkzeuge.garantie_annahme.fahrten_suchen", mock.AsyncMock(return_value=list(fahrten)))

    def akte(self):
        return akten.standard().letzte(NAME, self.pid)

    def test_eine_passende_fahrt_wird_in_die_akte_uebernommen(self):
        self.erfassen_kurz()
        t = {"reise": "r1", "von": "h1", "bis": "h2", "linie": "5", "uhr": "07:42"}
        with self.treffer(t), zug.zug():
            r = self.zusammenfassung()
        self.assertTrue(r.startswith("Zusammenfassung"), r)
        werte = akten.standard().werte(self.akte()["id"])
        self.assertEqual((werte["linie"], werte["abfahrt"]), ("5", "07:42"))
        self.assertIn("Linie 5", r)

    def test_nur_fehlendes_wird_ergaenzt_nie_die_angabe_der_person_ueberschrieben(self):
        self.erfassen_kurz(linie="5")
        t = {"reise": "r1", "von": "h1", "bis": "h2", "linie": "5", "uhr": "07:42"}
        with self.treffer(t) as suche, zug.zug():
            self.zusammenfassung()
        self.assertEqual(suche.await_args.args[0]["linie"], "5")                     # die Suche kennt die Angabe der Person
        werte = akten.standard().werte(self.akte()["id"])
        self.assertEqual((werte["linie"], werte["abfahrt"]), ("5", "07:42"))

    def test_mehrere_passende_fahrten_werden_der_person_zur_wahl_gestellt(self):
        self.erfassen_kurz()
        a = {"reise": "r1", "von": "h1", "bis": "h2", "linie": "5", "uhr": "07:42"}
        b = {"reise": "r2", "von": "h1", "bis": "h2", "linie": "7", "uhr": "07:50"}
        with self.treffer(a, b), zug.zug():
            r = self.zusammenfassung()
        self.assertFalse(r.startswith("Zusammenfassung"))
        for stueck in ("Linie 5", "07:42", "Linie 7", "07:50"):
            self.assertIn(stueck, r)
        self.assertNotIn("linie", akten.standard().werte(self.akte()["id"]))        # nichts geraten und nichts eingetragen
        self.assertIsNone(akten.standard().zusammenfassung(self.akte()["id"]))

    def test_zu_viele_treffer_werden_gekuerzt(self):
        self.erfassen_kurz()
        viele = [{"reise": f"r{i}", "von": "h1", "bis": "h2", "linie": str(i), "uhr": f"07:{i:02d}"} for i in range(10, 20)]
        with self.treffer(*viele), zug.zug():
            r = self.zusammenfassung()
        self.assertIn("ungefähr", r)
        self.assertLessEqual(r.count("Linie"), 5)

    def test_keine_fahrt_dann_nach_linie_und_abfahrt_fragen(self):
        self.erfassen_kurz()
        with self.treffer(), zug.zug():
            r = self.zusammenfassung()
        self.assertFalse(r.startswith("Zusammenfassung"))
        self.assertIn("Linie", r)
        self.assertIn("Abfahrt", r)
        self.assertNotIn("linie", akten.standard().werte(self.akte()["id"]))

    def test_ohne_start_oder_ziel_wird_gar_nicht_gesucht(self):
        wakte.akte_erfassen(kontakt_id=self.pid, skill=NAME, felder={"datum": "2026-10-12"})
        with self.treffer() as suche, zug.zug():
            r = self.zusammenfassung()
        self.assertIn("fehlen", r.lower())
        suche.assert_not_awaited()

    def test_kaputte_uhrzeit_der_person_wird_klartext(self):
        self.erfassen_kurz(abfahrt="kurz nach sieben")
        with mock.patch("kern.werkzeuge.garantie_annahme.fahrten_suchen", mock.AsyncMock(side_effect=ValueError("Uhrzeit"))), zug.zug():
            r = self.zusammenfassung()
        self.assertIn("Uhrzeit", r)
        self.assertFalse(r.startswith("Zusammenfassung"))

    def test_die_gefundene_fahrt_steht_dann_auch_im_antrag(self):
        self.erfassen_kurz()
        t = {"reise": "r1", "von": "h1", "bis": "h2", "linie": "5", "uhr": "07:42"}
        with self.treffer(t), zug.zug():
            self.zeigen()
        with zug.zug():
            self.assertIn("Der Antrag ist vorbereitet", self.antrag())
