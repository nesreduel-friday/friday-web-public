"""Fäden im Gedankenbuch: Grenzen, Fokus, Schritte, Ruhen, Migration, Werkzeug-Protokoll — ohne Netz."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from kern.denken import buch

BERLIN = ZoneInfo("Europe/Berlin")
MITTWOCH = datetime(2026, 10, 7, 12, 0, tzinfo=BERLIN)

THEMEN = [("Mathe Abitur", "Wann beginnt die Abiturvorbereitung in Mathematik"),
          ("Fahrrad Reparatur", "Welche Ersatzteile braucht das Fahrrad bis zum Winter"),
          ("Konzert Dezember", "Welche Karten gibt es für das Konzert im Dezember"),
          ("Führerschein Anmeldung", "Bis wann muss die Anmeldung zur Fahrschule erfolgen"),
          ("Urlaub Sommer", "Wohin soll die Reise im nächsten Sommer gehen"),
          ("Praktikum Bewerbung", "Welche Unterlagen fehlen für die Praktikumsbewerbung")]


class MitBuch(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        env.start()
        self.addCleanup(env.stop)
        self.jetzt = MITTWOCH
        uhr = mock.patch("kern.config.jetzt", side_effect=lambda: self.jetzt)
        uhr.start()
        self.addCleanup(uhr.stop)
        self.b = buch.standard()
        self.r = self.b.runde_beginnen()


class Anlegen(MitBuch):
    def test_faden_wird_angelegt(self):
        e = self.b.faden_anlegen(*THEMEN[0], runde=self.r)
        self.assertTrue(e["ok"])
        f = self.b.faden(e["id"])
        self.assertEqual((f["status"], f["quelle"], f["schritte"], f["fremd"]), ("offen", "runde", 0, False))

    def test_leerer_titel_oder_aufgabe_wird_abgelehnt(self):
        self.assertFalse(self.b.faden_anlegen("  ", "Aufgabe")["ok"])
        self.assertFalse(self.b.faden_anlegen("Titel", "")["ok"])
        self.assertEqual(self.b.faeden(), [])

    def test_zu_lange_texte_werden_abgelehnt_nicht_abgeschnitten(self):
        for titel, aufgabe in (("T" * 300, "Kurze Aufgabe"), ("Kurzer Titel", "wort " * 300)):
            e = self.b.faden_anlegen(titel, aufgabe)
            self.assertFalse(e["ok"])
            self.assertIn("Zu lang", e["grund"])
        self.assertEqual(self.b.faeden(), [])

    def test_hoechstens_ein_neuer_faden_je_runde(self):
        self.assertTrue(self.b.faden_anlegen(*THEMEN[0], runde=self.r)["ok"])
        zweiter = self.b.faden_anlegen(*THEMEN[1], runde=self.r)
        self.assertFalse(zweiter["ok"])
        self.assertIn("Runde", zweiter["grund"])
        self.assertTrue(self.b.faden_anlegen(*THEMEN[1], runde=self.b.runde_beginnen())["ok"])

    def test_eike_darf_mehrere_je_runde_anlegen(self):
        self.assertTrue(self.b.faden_anlegen(*THEMEN[0], quelle="eike")["ok"])
        self.assertTrue(self.b.faden_anlegen(*THEMEN[1], quelle="eike")["ok"])

    def test_hoechstens_fuenf_offene(self):
        for t in THEMEN[:5]:
            self.assertTrue(self.b.faden_anlegen(*t, quelle="eike")["ok"])
        e = self.b.faden_anlegen(*THEMEN[5], quelle="eike")
        self.assertFalse(e["ok"])
        self.assertIn("offen", e["grund"])
        self.assertEqual(self.b.offene_zahl(), 5)

    def test_dublette_wird_abgelehnt_auch_gegen_ruhende(self):
        e = self.b.faden_anlegen(*THEMEN[0], quelle="eike")
        self.b.faden_status_setzen(e["id"], "ruht")
        d = self.b.faden_anlegen("Mathe Abitur Vorbereitung", "Wann beginnt die Abiturvorbereitung im Fach Mathematik", quelle="eike")
        self.assertFalse(d["ok"])
        self.assertIn("schon", d["grund"])

    def test_fremd_flag_wird_gespeichert(self):
        e = self.b.faden_anlegen(*THEMEN[0], fremd=True, runde=self.r)
        self.assertTrue(self.b.faden(e["id"])["fremd"])


class Fokus(MitBuch):
    def test_ohne_faeden_kein_fokus(self):
        self.assertIsNone(self.b.faden_fokus())

    def test_aeltester_unbewegter_zuerst(self):
        a = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        self.jetzt = MITTWOCH + timedelta(hours=1)
        self.b.faden_anlegen(*THEMEN[1], runde=self.r)
        # beide offen: Eikes Faden (und zugleich der ältere) ist dran
        self.assertEqual(self.b.faden_fokus()["id"], a)

    def test_runde_faden_hat_vorrang_nicht_vor_eike_aber_nach_alter(self):
        a = self.b.faden_anlegen(*THEMEN[0], runde=self.r)["id"]
        self.jetzt = MITTWOCH + timedelta(hours=1)
        self.b.faden_anlegen(*THEMEN[1], runde=self.b.runde_beginnen())
        self.assertEqual(self.b.faden_fokus()["id"], a)
        self.jetzt = MITTWOCH + timedelta(hours=2)
        self.b.faden_weiter(a, "Etwas herausgefunden")          # bewegt → der andere ist jetzt älter
        self.assertNotEqual(self.b.faden_fokus()["id"], a)

    def test_ruhende_und_erledigte_sind_nie_fokus(self):
        a = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        self.b.faden_status_setzen(a, "ruht")
        self.assertIsNone(self.b.faden_fokus())

    def test_runde_merkt_ihren_fokus(self):
        a = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        r2 = self.b.runde_beginnen(fokus=a)
        self.assertEqual(self.b.runde_fokus(r2), a)
        self.assertIsNone(self.b.runde_fokus(self.r))


class Weiterkommen(MitBuch):
    def setUp(self):
        super().setUp()
        self.f = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]

    def test_weiter_setzt_stand_und_zaehlt_schritte(self):
        e = self.b.faden_weiter(self.f, "Die Abiturvorbereitung beginnt im Januar")
        self.assertEqual((e["ok"], e["schritte"], e["ruht"]), (True, 1, False))
        self.assertEqual(self.b.faden(self.f)["stand"], "Die Abiturvorbereitung beginnt im Januar")

    def test_leerer_stand_wird_abgelehnt(self):
        self.assertFalse(self.b.faden_weiter(self.f, " ")["ok"])

    def test_nach_sechs_schritten_ruht_der_faden(self):
        for n in range(buch.MAX_SCHRITTE):
            e = self.b.faden_weiter(self.f, f"Stand {n}")
        self.assertTrue(e["ruht"])
        self.assertEqual(self.b.faden(self.f)["status"], "ruht")
        self.assertFalse(self.b.faden_weiter(self.f, "noch einer")["ok"])

    def test_unbekannter_oder_ruhender_faden(self):
        self.assertFalse(self.b.faden_weiter(9999, "x")["ok"])

    def test_schliessen_verlangt_ergebnis_und_erzeugt_gedanken(self):
        self.assertFalse(self.b.faden_schliessen(self.f, " ")["ok"])
        e = self.b.faden_schliessen(self.f, "Beginn der Abiturvorbereitung ist der 12. Januar", runde=self.r)
        self.assertTrue(e["ok"])
        f = self.b.faden(self.f)
        self.assertEqual((f["status"], f["ergebnis"]), ("erledigt", "Beginn der Abiturvorbereitung ist der 12. Januar"))
        g = self.b.neueste()[0]
        self.assertEqual((g["id"], g["art"], g["faden"]), (e["gedanke"], "erkenntnis", self.f))
        self.assertIn(f"Faden #{self.f}", g["quellen"][0])

    def test_ergebnis_eines_fremd_fadens_ist_ein_fremd_gedanke(self):
        f = self.b.faden_anlegen(*THEMEN[1], fremd=True, runde=self.r)["id"]
        self.b.faden_schliessen(f, "Teile braucht man bis Dezember", runde=self.r)
        self.assertTrue(self.b.neueste()[0]["fremd"])

    def test_erledigter_faden_laesst_sich_nicht_nochmal_schliessen(self):
        self.b.faden_schliessen(self.f, "Fertig", runde=self.r)
        self.assertFalse(self.b.faden_schliessen(self.f, "Noch mal", runde=self.r)["ok"])


class RuhenUndStatus(MitBuch):
    def test_nach_14_tagen_ohne_bewegung_ruht_der_faden_und_bleibt_erhalten(self):
        a = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        self.jetzt = MITTWOCH + timedelta(days=13)
        self.assertEqual(self.b.faeden_ruhen_lassen(), 0)
        self.jetzt = MITTWOCH + timedelta(days=15)
        self.assertEqual(self.b.faeden_ruhen_lassen(), 1)
        f = self.b.faden(a)
        self.assertEqual(f["status"], "ruht")
        self.assertEqual(f["titel"], THEMEN[0][0])

    def test_bewegung_haelt_den_faden_offen(self):
        a = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        self.jetzt = MITTWOCH + timedelta(days=10)
        self.b.faden_weiter(a, "Neuer Stand")
        self.jetzt = MITTWOCH + timedelta(days=20)
        self.assertEqual(self.b.faeden_ruhen_lassen(), 0)

    def test_eike_setzt_status_und_oeffnen_setzt_schritte_zurueck(self):
        a = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        for n in range(buch.MAX_SCHRITTE):
            self.b.faden_weiter(a, f"Stand {n}")
        self.assertEqual(self.b.faden(a)["status"], "ruht")
        self.assertTrue(self.b.faden_status_setzen(a, "offen")["ok"])
        self.assertEqual((self.b.faden(a)["status"], self.b.faden(a)["schritte"]), ("offen", 0))
        self.assertTrue(self.b.faden_status_setzen(a, "erledigt")["ok"])

    def test_oeffnen_scheitert_bei_fuenf_offenen(self):
        ruhend = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        self.b.faden_status_setzen(ruhend, "ruht")
        for t in THEMEN[1:6]:
            self.b.faden_anlegen(*t, quelle="eike")
        self.assertEqual(self.b.offene_zahl(), 5)
        e = self.b.faden_status_setzen(ruhend, "offen")
        self.assertFalse(e["ok"])
        self.assertEqual(self.b.faden(ruhend)["status"], "ruht")

    def test_unbekannter_status_und_faden(self):
        a = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        self.assertFalse(self.b.faden_status_setzen(a, "geloescht")["ok"])
        self.assertFalse(self.b.faden_status_setzen(9999, "ruht")["ok"])

    def test_faeden_listen_nach_status(self):
        a = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]
        self.b.faden_anlegen(*THEMEN[1], quelle="eike")
        self.b.faden_status_setzen(a, "ruht")
        self.assertEqual([f["id"] for f in self.b.faeden("ruht")], [a])
        self.assertEqual(len(self.b.faeden()), 2)


class Protokoll(MitBuch):
    def test_gedaechtnis_geprueft_nur_nach_gedaechtniswerkzeug(self):
        self.assertFalse(self.b.gedaechtnis_geprueft(self.r))
        self.b.aufruf_merken(self.r, "kalender")
        self.assertFalse(self.b.gedaechtnis_geprueft(self.r))
        self.b.aufruf_merken(self.r, "rueckblick")
        self.assertTrue(self.b.gedaechtnis_geprueft(self.r))
        self.assertFalse(self.b.gedaechtnis_geprueft(self.b.runde_beginnen()))

    def test_luecke_belegt_braucht_zwei_quellen(self):
        self.b.gedanke_festhalten(self.r, "erkenntnis", "Etwas ganz anderes über Physik", ["Notiz", "Kalender"])
        self.assertFalse(self.b.luecke_belegt(self.r))
        self.b.gedanke_festhalten(self.r, "luecke", "Kalender und Mail widersprechen sich", ["Kalender", "Mail"])
        self.assertTrue(self.b.luecke_belegt(self.r))


class Laenge(MitBuch):
    """Das Ergebnis des ersten Fadens (08.10.2026) wurde bei 400 Zeichen mitten im Wort abgeschnitten („… mit den Ku“)."""

    def setUp(self):
        super().setUp()
        self.f = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]

    def test_ein_ergebnis_von_700_zeichen_bleibt_vollstaendig(self):
        text = ("Die Info-Veranstaltung ist am Freitag um 16:30 Uhr. " * 14).strip()
        self.assertTrue(500 < len(text) <= buch.FADEN_ERGEBNIS_MAX)
        self.assertTrue(self.b.faden_schliessen(self.f, text, runde=self.r)["ok"])
        self.assertEqual(self.b.faden(self.f)["ergebnis"], text)
        self.assertEqual(self.b.neueste()[0]["text"], text)

    def test_zu_langes_ergebnis_wird_abgelehnt_und_der_faden_bleibt_offen(self):
        e = self.b.faden_schliessen(self.f, "wort " * 300, runde=self.r)
        self.assertFalse(e["ok"])
        self.assertIn("Zu lang", e["grund"])
        self.assertEqual(self.b.faden(self.f)["status"], "offen")
        self.assertEqual(self.b.neueste(), [])

    def test_zu_langer_stand_wird_abgelehnt(self):
        e = self.b.faden_weiter(self.f, "wort " * 300)
        self.assertFalse(e["ok"])
        self.assertEqual(self.b.faden(self.f)["schritte"], 0)
        self.assertTrue(self.b.faden_weiter(self.f, "Ein Stand. " * 40)["ok"])


class Verunreinigung(MitBuch):
    """Eine Runde, die Mails oder Webseiten las, schreibt Stand und Ergebnis aus Fremdtext — das gehört markiert."""

    def setUp(self):
        super().setUp()
        self.f = self.b.faden_anlegen(*THEMEN[0], quelle="eike")["id"]

    def test_stand_nach_fremdtext_macht_den_faden_zum_fremd_faden(self):
        self.b.faden_weiter(self.f, "Laut Mail beginnt es im Januar", fremd=True)
        self.assertTrue(self.b.faden(self.f)["fremd"])

    def test_stand_ohne_fremdtext_laesst_den_faden_sauber(self):
        self.b.faden_weiter(self.f, "Laut Notiz beginnt es im Januar")
        self.assertFalse(self.b.faden(self.f)["fremd"])

    def test_ergebnis_nach_fremdtext_ist_ein_fremd_gedanke_und_nicht_im_prompt(self):
        self.b.faden_schliessen(self.f, "Laut Mail: Nachweis bis 31.10. einreichen", runde=self.r, fremd=True)
        self.assertTrue(self.b.neueste()[0]["fremd"])
        self.assertTrue(self.b.faden(self.f)["fremd"])
        self.assertNotIn("Nachweis", self.b.kontext_block())

    def test_ergebnis_ohne_fremdtext_bleibt_sauber(self):
        self.b.faden_schliessen(self.f, "Laut Notiz im Januar", runde=self.r)
        self.assertFalse(self.b.neueste()[0]["fremd"])


class Migration(unittest.TestCase):
    def test_altes_gedankenbuch_ohne_neue_spalten_wird_migriert_ohne_datenverlust(self):
        ordner = Path(tempfile.mkdtemp())
        alt = sqlite3.connect(ordner / "gedanken.db")
        alt.executescript("""
            CREATE TABLE runden (id INTEGER PRIMARY KEY AUTOINCREMENT, start TEXT NOT NULL, ende TEXT, ergebnis TEXT,
                modell TEXT, werkzeuge TEXT, fehler TEXT, graph_status TEXT, graph_versuche INTEGER NOT NULL DEFAULT 0,
                graph_gesendet TEXT);
            CREATE TABLE gedanken (id INTEGER PRIMARY KEY AUTOINCREMENT, runde INTEGER NOT NULL, zeit TEXT NOT NULL,
                art TEXT NOT NULL, text TEXT NOT NULL, quellen TEXT NOT NULL DEFAULT '[]', sicherheit TEXT NOT NULL,
                fremd INTEGER NOT NULL DEFAULT 0, wichtigkeit TEXT NOT NULL DEFAULT 'normal', faden INTEGER,
                status TEXT NOT NULL DEFAULT 'offen');
            CREATE TABLE hinweise (id INTEGER PRIMARY KEY AUTOINCREMENT, runde INTEGER NOT NULL, zeit TEXT NOT NULL,
                titel TEXT NOT NULL, text TEXT NOT NULL, wichtigkeit TEXT NOT NULL);
            INSERT INTO runden (start) VALUES ('2026-10-07T22:21:06+00:00');
            INSERT INTO gedanken (runde, zeit, art, text, sicherheit) VALUES (1, '2026-10-07T22:21:19+00:00', 'erkenntnis', 'Alt', 'belegt');
        """)
        alt.commit()
        alt.close()
        b = buch.Buch(ordner)
        self.assertEqual(b.neueste()[0]["text"], "Alt")
        r = b.runde_beginnen(fokus=None)
        self.assertIsNone(b.runde_fokus(r))
        self.assertTrue(b.faden_anlegen("Neu", "Etwas herausfinden", quelle="eike")["ok"])


if __name__ == "__main__":
    unittest.main()
