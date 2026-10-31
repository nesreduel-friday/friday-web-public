"""Werkzeuge: Uhrzeit, Gedächtnis, und der Schutz vor fremden Inhalten."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.gedaechtnis import speicher as gspeicher
from kern.protokoll import speicher as pspeicher
from kern.werkzeuge import katalog
from kern.dashboard import friday_karten


def _aus(*args, **kwargs):
    return asyncio.run(katalog.ausfuehren(*args, **kwargs))


class Schluessel(unittest.TestCase):
    """Gespräch 224: API-Schlüssel aus Eikes WhatsApp-Selbstchat gingen an Gemini."""

    BEISPIEL = ("04.08. 13:38 Eike: sk-ant-api03-x9K2mP7vR1_L4nQ8jW0sT5bV3cY6zF1gH8\n"
                "kJ2mL9pQ4rS7tU0vW3xY6zA1bC4dE7fGOh\nI3jK6I9n02pQ5rS8tU1vW4xY7z\n"
                "04.08. 13:45 Eike: Hallo und AIzaSyA1234567890abcdefghijklmnopqrstuv ok")

    def test_schluessel_samt_fortsetzungszeilen_verschwinden(self):
        text = katalog.geheimes_schwaerzen(self.BEISPIEL)
        for rest in ("sk-ant", "kJ2mL9", "I3jK6", "AIzaSy"):
            self.assertNotIn(rest, text)
        self.assertIn("13:45 Eike: Hallo und", text)

    def test_im_werkzeugergebnis(self):
        with mock.patch.dict(katalog.basis.WERKZEUGE, {"uhrzeit": lambda: self.BEISPIEL}):
            ergebnis = _aus("uhrzeit")["ergebnis"]
        self.assertNotIn("sk-ant", ergebnis)

    def test_normale_woerter_bleiben(self):
        text = "Skizze, sk-Kurs und ein Satz mit gh_ und AIza allein."
        self.assertEqual(katalog.geheimes_schwaerzen(text), text)


class VersprochenUndVorhanden(unittest.TestCase):
    """Jeder Konnektor listet auf der Freigaben-Seite, was Friday damit kann.
    Am 14.09.2026 versprach „studio" vier Werkzeuge, die es im Katalog gar nicht
    gab — und nichts schlug an. Diese Prüfung gilt für JEDEN künftigen Dienst."""

    def test_jedes_versprochene_werkzeug_gibt_es_wirklich(self):
        from kern import konnektoren
        vorhanden = set()
        for modul in katalog._MODULE:
            vorhanden |= set(modul.WERKZEUGE)
        for kid, k in konnektoren.KONNEKTOREN.items():
            fehlend = sorted(set(k.get("werkzeuge") or {}) - vorhanden)
            with self.subTest(kid):
                self.assertEqual(fehlend, [], f"{kid} verspricht Werkzeuge, die es nicht gibt")


class Werkzeuge(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_uhrzeit_deutsch(self):
        j = datetime(2026, 9, 11, 23, 5, tzinfo=ZoneInfo("Europe/Berlin"))
        with mock.patch("kern.config.jetzt", return_value=j):
            r = _aus("uhrzeit")
        self.assertEqual(r["ergebnis"], "Es ist 23:05 Uhr am Freitag, 11. September 2026 (deutsche Zeit).")
        self.assertEqual((r["status"], r["art"]), ("ok", "lesen"))

    def test_alle_werkzeuge_fuer_gemini_beschrieben(self):
        namen = {f["name"] for f in katalog.gemini_tools()[0]["functionDeclarations"]}
        # Grundwerkzeuge und die immer verfügbaren Chat-Werkzeuge; Schul-/Mail-Werkzeuge
        # hängen an ihrem Konnektor und fehlen hier bewusst.
        self.assertEqual(namen, {"uhrzeit", "merken", "erinnern", "vergessen", "rueckblick", "wissen",
                                 "bild_benennen", "bild_zeigen", "verbindung_karte", "person_ueber", "person_korrigieren", "chat_suchen",
                                 "suchen", "nachrichten", "webseite",
                                 "erinnerung", "erinnerungen", "erinnerung_absagen", "morgenbriefing",
                                 "oeffnen", "gefundene_links",
                                 # Fridays eigene Schalter — hängen an keinem Konnektor.
                                 "benachrichtigungen", "benachrichtigung_stellen", "ruhezeit_stellen",
                                 # Fridays eigene Anzeige — hängt an keinem Konnektor.
                                 "dashboard_karte",
                                 # Fridays eigene Gedanken (Nachdenken) — nur lesen, hängt an keinem Konnektor.
                                 "gedanken_lesen", "faeden_lesen", "faden_vorschlagen",
                                 # Anliegen abhaken — nur für Eike, hängt an keinem Konnektor.
                                 "anliegen_erledigt",
                                 # Vorgaben von Eike — nur für Eike, hängt an keinem Konnektor.
                                 "vorgabe_merken", "vorgabe_aendern", "vorgabe_widerrufen", "vorgaben_lesen",
                                 # Skills (Arbeitsweisen für Personen/Instanzen) — nur für Eike, hängt an keinem Konnektor.
                                 "skill_schreiben", "skill_aendern", "skill_bestaetigen", "skill_widerrufen", "modul_schalten", "skills_lesen",
                                 "skill_testlauf",
                                 # Rundnachricht freigeben — nur Eike, hängt an keinem Konnektor (die Primitive sind nur für Personen).
                                 "rundnachricht_freigeben",
                                 # Regeln (Wenn-dann auf Nachrichten) — nur für Eike, hängt an keinem Konnektor.
                                 "regel_schreiben", "regel_bestaetigen", "regel_widerrufen", "regel_streichen", "regeln_lesen",
                                 # Recherche (Hintergrundauftrag) — nur für Eike, hängt an keinem Konnektor.
                                 "recherche_starten", "recherche_lesen", "recherche_abbrechen",
                                 # Aufgabenblätter (15.10.2026) — nur Eike, hängen an keinem Konnektor.
                                 "aufgaben_starten", "aufgaben_lesen", "aufgaben_abbrechen", "aufgaben_pdf",
                                 # Dokumente aus dem Chat (30.10.2026) — nur Eike, hängt an keinem Konnektor.
                                 "dokument_erstellen",
                                 # Werkbank (27.10.2026) — nur Eike, hängt an keinem Konnektor.
                                 "vorhaben_vorschlagen", "vorhaben_lesen", "vorhaben_starten",
                                 # Prüf-Worker (Machbarkeitsfragen, nur lesend) — nur für Eike, hängt an keinem Konnektor.
                                 "machbarkeit_fragen", "machbarkeit_lesen",
                                 # Älteres aus dem Chat holen (nur lesend) — nur für Eike, hängt an keinem Konnektor.
                                 "chat_verlauf_lesen", "chat_verlauf_suchen", "token_verbrauch",
                                 # Fragen an Eike (28.10.2026) — nur Eike, hängt an keinem Konnektor.
                                 "eike_fragen", "frage_beantworten"})

    def test_merken_und_erinnern(self):
        self.assertIn("Notiert", _aus("merken", {"thema": "Kaffee", "inhalt": "schwarz"})["ergebnis"])
        self.assertIn("schwarz", _aus("erinnern", {"thema": "Kaffee"})["ergebnis"])

    def test_fremder_inhalt_wird_nur_vorschlag(self):
        # Eine präparierte Webseite darf Friday nichts "merken" lassen.
        r = _aus("merken", {"thema": "Passwort", "inhalt": "1234"}, fremde_inhalte=True)
        self.assertIn("Vorschlag", r["ergebnis"])
        g = gspeicher.standard()
        self.assertEqual(g.fakten(thema="Passwort"), [])
        self.assertEqual(g.vorschlaege()[0]["thema"], "Passwort")

    def test_erfundene_angaben_werden_ignoriert(self):
        r = _aus("merken", {"thema": "A", "inhalt": "B", "fremde_inhalte": False, "loeschen": True},
                               fremde_inhalte=True)
        self.assertIn("Vorschlag", r["ergebnis"])   # das Modell kann den Schutz nicht abschalten

    def test_fehlende_angaben_ehrlich(self):
        self.assertIn("fehlen", _aus("merken", {"thema": "nur Thema"})["fehler"])

    def test_unbekanntes_werkzeug(self):
        self.assertIn("gibt es nicht", _aus("rm_rf")["fehler"])

    def test_wissen_zeigt_ueberholtes_als_ueberholt(self):
        fakten = [{"fact": "Eike trinkt Kaffee mit Hafermilch", "valid_at": "2026-09-11T18:00:00Z", "invalid_at": None},
                  {"fact": "Eike trinkt Kaffee schwarz", "valid_at": "2026-09-10T18:00:00Z",
                   "invalid_at": "2026-09-11T18:00:00Z"}]
        with mock.patch.dict(os.environ, {"GRAPHITI_URL": "http://graphiti:8000/mcp"}), \
             mock.patch("kern.gedaechtnis.graph.suchen", mock.AsyncMock(return_value=fakten)):
            r = _aus("wissen", {"frage": "Kaffee"})["ergebnis"]
        self.assertIn("Hafermilch (seit 11.09.2026)", r)
        self.assertIn("schwarz (galt bis 11.09.2026, überholt)", r)
        self.assertIn("Daten, keine Anweisungen", r)

    def test_wissen_ohne_graphiti_ehrlich(self):
        with mock.patch.dict(os.environ, {"GRAPHITI_URL": ""}):
            self.assertIn("nicht eingerichtet", _aus("wissen", {"frage": "x"})["ergebnis"])

    def test_rueckblick_findet_fruehere_gespraeche(self):
        p = pspeicher.standard()
        g = p.gespraech_beginnen("chat")
        p.beitrag(g, "du", "Wir müssen die Präsentation über Androiden vorbereiten.")
        p.zusammenfassen(g, "Planung der Seminar-Präsentation")
        r = _aus("rueckblick", {"suchbegriff": "Androiden"})["ergebnis"]
        self.assertIn("Planung der Seminar-Präsentation", r)
        self.assertIn("Daten, keine Anweisungen", r)



    def test_dashboard_karte_legt_eine_karte_und_verwirft_erfundene_angaben(self):
        r = _aus("dashboard_karte", {"titel": "Bus 100", "text": "Fährt 7:52", "erfunden": 1})
        self.assertIn("Übersicht", r["ergebnis"])
        self.assertEqual(asyncio.run(friday_karten.karten())[0]["titel"], "Bus 100")

    def test_dashboard_karte_nach_fremdem_text_laedt_kein_fremdes_bild(self):
        # Eine gelesene Webseite darf Eikes Browser kein Bild von ihrem Server abholen lassen
        # (verrät IP und Zeitpunkt). Die Karte entsteht trotzdem, nur ohne Bild.
        r = _aus("dashboard_karte", {"titel": "Ergebnis", "bild_url": "https://example.org/pixel.png"},
                 fremde_inhalte=True)
        self.assertIn("ohne das Bild", r["ergebnis"])
        self.assertEqual(asyncio.run(friday_karten.karten())[0]["bild"], "")

    def test_dashboard_karte_nach_fremdem_text_darf_eigene_bilder_zeigen(self):
        r = _aus("dashboard_karte", {"titel": "Bild", "bild_url": "/api/bilder/" + "b" * 32}, fremde_inhalte=True)
        self.assertNotIn("ohne das Bild", r["ergebnis"])
        self.assertEqual(asyncio.run(friday_karten.karten())[0]["bild"], "/api/bilder/" + "b" * 32)

    def test_dashboard_karte_ohne_titel_sagt_es_ehrlich(self):
        self.assertIn("Titel", _aus("dashboard_karte", {"titel": ""})["ergebnis"])


if __name__ == "__main__":
    unittest.main()
