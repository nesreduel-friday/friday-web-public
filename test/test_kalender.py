"""iCloud-Kalender: Zeiträume deuten, Termine vorlesbar machen — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from kern import konnektoren
from kern.dienste import icloud
from kern.werkzeuge import kalender, katalog

SONNTAG = datetime(2026, 9, 13, 9, 0, tzinfo=ZoneInfo("Europe/Berlin"))

TERMINE = [
    {"kalender": "Privat", "titel": "Zahnarzt", "beginn": "2026-09-14T09:30+00:00", "ende": "",
     "ganztags": False, "ort": "Musterhausen", "notiz": ""},
    {"kalender": "Schule", "titel": "Wandertag", "beginn": "2026-09-14T00:00+00:00", "ende": "",
     "ganztags": True, "ort": "", "notiz": ""},
]


class Mit_Uhr(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=SONNTAG)
        self.uhr.start()
        icloud.vergessen()

    def tearDown(self):
        self.uhr.stop()
        self.env.stop()


class Zeitraeume(Mit_Uhr):
    def test_tag_woche_monat(self):
        von, bis, wort = kalender._zeitraum("morgen")
        self.assertEqual((von.date().isoformat(), bis.date().isoformat()), ("2026-09-14", "2026-09-14"))
        self.assertIn("Montag, 14.09.", wort)
        von, bis, wort = kalender._zeitraum("diese Woche")          # Sonntag → Woche ab Montag 07.09.
        self.assertEqual(von.date().isoformat(), "2026-09-07")
        self.assertEqual(bis.date().isoformat(), "2026-09-13")
        von, bis, _ = kalender._zeitraum("nächste Woche")
        self.assertEqual(von.date().isoformat(), "2026-09-14")
        von, bis, wort = kalender._zeitraum("monat")
        self.assertEqual((bis - von).days, 31)
        self.assertIn("31 Tagen", wort)


class Ausgabe(Mit_Uhr):
    def test_termine_vorlesbar(self):
        with mock.patch.object(icloud, "termine", return_value=TERMINE):
            text = asyncio.run(kalender.kalender("morgen"))
        self.assertIn("Termine am Montag, 14.09.:", text)
        self.assertIn("Montag 14.09. 11:30: Zahnarzt — Musterhausen [Privat]", text)   # UTC → deutsche Zeit
        self.assertIn("Wandertag (ganztägig)", text)

    def test_leerer_tag(self):
        with mock.patch.object(icloud, "termine", return_value=[]):
            self.assertIn("steht nichts im Kalender", asyncio.run(kalender.kalender("heute")))

    def test_anmeldefehler_wird_ein_satz(self):
        with mock.patch.object(icloud, "termine", side_effect=icloud.AnmeldeFehler("app-spezifisches Passwort")):
            self.assertIn("app-spezifisches Passwort", asyncio.run(kalender.kalender("heute")))


class SucheNachNamen(Mit_Uhr):
    """Der Eintrag heißt „Mathe Arbeit", Eike fragt nach der „Mathearbeit".

    Die starre Zeichenkettensuche fand ihn nicht — Friday sagte am 14.09.2026,
    es sei kein Termin eingetragen, und fand ihn zwei Minuten später über
    `kalender` doch. Weder Kalender noch Zeitraum waren schuld, nur das
    fehlende Leerzeichen."""

    EINTRAEGE = [
        {"titel": "Mathe Arbeit", "ort": "", "notiz": " ", "kalender": "Schule",
         "beginn": "2026-11-17T00:00+01:00", "ende": "2026-11-17T23:59+01:00", "ganztags": True},
        {"titel": "Therapie", "ort": "Beispielweg 1, 30989 Musterhausen", "notiz": "Frau Schwarting",
         "kalender": "Privat", "beginn": "2026-09-16T15:00+02:00",
         "ende": "2026-09-16T15:45+02:00", "ganztags": False},
    ]

    def _suche(self, wort):
        with mock.patch.object(icloud, "termine", return_value=self.EINTRAEGE):
            return asyncio.run(kalender.kalender_suchen(wort))

    def test_zusammengeschrieben_findet_getrennten_eintrag(self):
        self.assertIn("Mathe Arbeit", self._suche("Mathearbeit"))

    def test_einzelnes_wort_findet_weiterhin(self):
        self.assertIn("Mathe Arbeit", self._suche("Mathe"))

    def test_woerter_auch_in_anderer_reihenfolge(self):
        self.assertIn("Mathe Arbeit", self._suche("Arbeit Mathe"))

    def test_ort_wird_mitdurchsucht(self):
        self.assertIn("Therapie", self._suche("Musterhausen"))

    def test_was_wirklich_fehlt_bleibt_eine_fehlanzeige(self):
        self.assertIn("nicht im Kalender", self._suche("Segelkurs"))


class Freigabe(Mit_Uhr):
    def _namen(self):
        return {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}

    def test_erst_mit_freigabe_und_zugang_sichtbar(self):
        self.assertNotIn("kalender", self._namen())
        konnektoren.setzen("icloud_kalender", True)
        self.assertNotIn("kalender", self._namen())                  # ohne Zugang weiterhin nicht
        konnektoren.zugang_setzen("icloud_kalender", "name@example.com", "abcd-efgh-ijkl-mnop")
        self.assertIn("kalender", self._namen())
        r = asyncio.run(katalog.ausfuehren("kalender", {"tag": "heute"}))
        self.assertIn("ergebnis", r)

    def test_eigener_zugang_nicht_der_von_iserv(self):
        konnektoren.zugang_setzen("schulmanager", "eike", "geheim")
        self.assertIsNone(konnektoren.zugang("icloud_kalender"))     # teilt sich nichts mit IServ

    def test_abgewiesene_daten_nicht_nochmal_probieren(self):
        konnektoren.zugang_setzen("icloud_kalender", "name@example.com", "falsch")
        icloud._abgewiesen = icloud._fingerabdruck("name@example.com", "falsch")
        with self.assertRaises(icloud.AnmeldeFehler):
            icloud._verbinden()
        icloud.vergessen()
        self.assertIsNone(icloud._abgewiesen)


class Beschriftung(unittest.TestCase):
    """Jeder Konnektor beschriftet seine Eingabefelder selbst — beim Kalender stand
    sonst „IServ-Benutzername" da, und der Passwortmanager bot das falsche Passwort an."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_felder_je_konnektor(self):
        from fastapi.testclient import TestClient
        from kern.main import app
        felder = {k["id"]: (k["benutzerFeld"], k["passwortFeld"]) for k in TestClient(app).get("/api/konnektoren").json()}
        self.assertEqual(felder["schulmanager"], ("IServ-Benutzername", "IServ-Passwort"))
        self.assertEqual(felder["icloud_kalender"][0], "Apple-ID (E-Mail)")
        self.assertIn("App-spezifisches Passwort", felder["icloud_kalender"][1])
        self.assertNotIn("IServ", " ".join(felder["icloud_kalender"]))


class TermineAnlegen(Mit_Uhr):
    """Schreiben ist die heikelste Stelle: hier hängen die Sicherungen."""

    def setUp(self):
        super().setUp()
        kalender._vorschlag = None
        konnektoren.setzen("icloud_kalender", True)
        konnektoren.zugang_setzen("icloud_kalender", "name@example.com", "abcd-efgh-ijkl-mnop")
        konnektoren.schreiben_setzen("icloud_kalender", True)
        self.leer = mock.patch.object(icloud, "termine", return_value=[])
        self.leer.start()
        self.ziel = mock.patch.object(icloud, "aufloesen", side_effect=lambda w="": w or "Privat")
        self.ziel.start()
        self.schreiben = mock.patch.object(icloud, "anlegen", return_value={
            "kalender": "Privat", "titel": "Zahnarzt", "beginn": "2026-09-20T15:30", "ende": "2026-09-20T16:30"})
        self.anlegen = self.schreiben.start()

    def tearDown(self):
        self.schreiben.stop()
        self.ziel.stop()
        self.leer.stop()
        kalender._vorschlag = None
        super().tearDown()

    def _aus(self, name, args=None, **kw):
        return asyncio.run(katalog.ausfuehren(name, args or {}, **kw))

    def test_anlegen_traegt_nichts_ein_sondern_fragt(self):
        r = self._aus("termin_anlegen", {"titel": "Zahnarzt", "beginn": "2026-09-20T15:30"})
        self.assertIn("Noch nichts eingetragen", r["ergebnis"])
        self.assertIn("Soll ich Zahnarzt am Sonntag, 20.09.2026 um 15:30", r["ergebnis"])
        self.anlegen.assert_not_called()

    def test_wirkungen_status_art_quittung(self):
        """P0 12.10.2026: Vorschlag = Rückfrage, Eintragen = ok, Absagen = nicht."""
        from test.test_wirkung import pruefe_wirkung
        r = self._aus("termin_anlegen", {"titel": "Zahnarzt", "beginn": "2026-09-20T15:30"})
        pruefe_wirkung(self, r, "rueckfrage", "sonstiges", "Vorschlag")
        self.assertTrue(r["quittung"].startswith("Noch offen: Soll ich Zahnarzt"), r["quittung"])
        pruefe_wirkung(self, self._aus("termin_anlegen", {"titel": "", "beginn": "2026-09-20T15:30"}), "rueckfrage", "sonstiges", "ohne Titel")
        pruefe_wirkung(self, self._aus("termin_anlegen", {"titel": "X", "beginn": "2026-09-20T15:30"}, fremde_inhalte=True),
                       "nicht", "sonstiges", "Fremdtext")
        pruefe_wirkung(self, self._aus("termin_bestaetigen", {}, zustimmung=False), "nicht", "sonstiges", "ohne Ja")
        r = self._aus("termin_bestaetigen", {}, zustimmung=True)
        pruefe_wirkung(self, r, "ok", "sonstiges", "eingetragen")
        self.assertTrue(r["quittung"].startswith("Eingetragen: Zahnarzt am 20.09.2026"), r["quittung"])
        pruefe_wirkung(self, self._aus("termin_bestaetigen", {}, zustimmung=True), "nicht", "sonstiges", "nichts vorbereitet")

    def test_ohne_zustimmung_wird_nicht_geschrieben(self):
        self._aus("termin_anlegen", {"titel": "Zahnarzt", "beginn": "2026-09-20T15:30"})
        r = self._aus("termin_bestaetigen", {}, zustimmung=False)
        self.assertIn("erst ein, wenn du ausdrücklich zustimmst", r["ergebnis"])
        self.anlegen.assert_not_called()

    def test_modell_kann_sich_die_zustimmung_nicht_selbst_geben(self):
        self._aus("termin_anlegen", {"titel": "Zahnarzt", "beginn": "2026-09-20T15:30"})
        r = self._aus("termin_bestaetigen", {"zustimmung": True})      # Angabe kommt vom Modell
        self.assertIn("ausdrücklich zustimmst", r["ergebnis"])
        self.anlegen.assert_not_called()

    def test_mit_zustimmung_wird_eingetragen(self):
        self._aus("termin_anlegen", {"titel": "Zahnarzt", "beginn": "2026-09-20T15:30"})
        r = self._aus("termin_bestaetigen", {}, zustimmung=True)
        self.assertIn("Eingetragen: Zahnarzt am 20.09.2026 um 15:30", r["ergebnis"])
        self.anlegen.assert_called_once()
        self.assertIsNone(kalender._vorschlag)                          # danach verbraucht

    def test_aus_fremdem_text_niemals(self):
        r = self._aus("termin_anlegen", {"titel": "Elternabend", "beginn": "2026-09-20T19:00"},
                      fremde_inhalte=True)
        self.assertIn("fremdem Text", r["ergebnis"])
        self.assertIsNone(kalender._vorschlag)

    def test_ohne_uhrzeit_wird_nachgefragt(self):
        r = self._aus("termin_anlegen", {"titel": "Zahnarzt", "beginn": "2026-09-20"})
        self.assertIn("Datum UND Uhrzeit", r["ergebnis"])
        self.assertIsNone(kalender._vorschlag)

    def test_vergangenheit_und_ferne_zukunft(self):
        self.assertIn("Vergangenheit", self._aus("termin_anlegen", {"titel": "X", "beginn": "2020-01-01T10:00"})["ergebnis"])
        self.assertIn("zwei Jahre", self._aus("termin_anlegen", {"titel": "X", "beginn": "2099-01-01T10:00"})["ergebnis"])

    def test_keine_dubletten(self):
        with mock.patch.object(icloud, "termine", return_value=[
                {"kalender": "Privat", "titel": "Zahnarzt", "beginn": "2026-09-20T13:30+00:00",
                 "ende": "", "ganztags": False, "ort": "", "notiz": ""}]):
            r = self._aus("termin_anlegen", {"titel": "zahnarzt", "beginn": "2026-09-20T15:30"})
        self.assertIn("steht an dem Tag schon im Kalender", r["ergebnis"])
        self.assertIsNone(kalender._vorschlag)

    def test_alter_vorschlag_verfaellt(self):
        self._aus("termin_anlegen", {"titel": "Zahnarzt", "beginn": "2026-09-20T15:30"})
        kalender._vorschlag["zeit"] -= kalender.VORSCHLAG_GILT_S + 1
        r = self._aus("termin_bestaetigen", {}, zustimmung=True)
        self.assertIn("zu alt", r["ergebnis"])
        self.anlegen.assert_not_called()

    def test_ohne_schreibrecht_unsichtbar_und_abgelehnt(self):
        konnektoren.schreiben_setzen("icloud_kalender", False)
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("kalender", namen)                                # Lesen bleibt
        self.assertNotIn("termin_anlegen", namen)
        r = self._aus("termin_anlegen", {"titel": "X", "beginn": "2026-09-20T15:30"})
        self.assertIn("Schreibrecht", r["fehler"])


class Zustimmung(unittest.TestCase):
    def test_erkennen(self):
        for ja in ("ja", "Ja bitte", "okay", "mach das", "trag es ein", "passt", "Klar!"):
            self.assertTrue(katalog.zustimmung_erkannt(ja), ja)
        for nein in ("nein", "nee lass mal", "ja, aber nicht heute", "warte", "doch nicht",
                     "", "was steht morgen an?"):
            self.assertFalse(katalog.zustimmung_erkannt(nein), nein)

    def test_chat_reicht_die_zustimmung_durch(self):
        import os
        import tempfile
        from kern.sitzung import chat
        runde1 = {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "termin_bestaetigen", "args": {}}}]}}]}
        runde2 = {"candidates": [{"content": {"parts": [{"text": "Erledigt."}]}}]}
        ausfuehren = mock.AsyncMock(return_value={"ergebnis": "ok"})
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp(), "GEMINI_API_KEY": "k"}), \
             mock.patch.object(chat, "_gemini_anfrage", mock.AsyncMock(side_effect=[(runde1, "m"), (runde2, "m")])), \
             mock.patch.object(chat.werkzeuge, "ausfuehren", ausfuehren):
            asyncio.run(chat.antworten([{"rolle": "du", "text": "Ja, trag das bitte ein"}]))
        self.assertTrue(ausfuehren.call_args.kwargs["zustimmung"])


class Zielkalender(unittest.TestCase):
    """Friday wählt nach Thema — aber nie in abonnierte Kalender (Abfall, Erinnerungen)."""

    KALENDER = ["Privat", "aha Abfallkalender", "Merken", "Schule", "Arbeit", "Feuerwehr", "Erinnerungen"]

    def _auf(self, wunsch):
        kal = [mock.Mock(name=n) for n in self.KALENDER]
        for k, n in zip(kal, self.KALENDER):
            k.name = n
        with mock.patch.object(icloud, "_verbinden", return_value=mock.Mock(calendars=lambda: kal)), \
             mock.patch.dict(os.environ, {"ICLOUD_KALENDER": ""}):
            return icloud.aufloesen(wunsch)

    def test_nach_thema(self):
        self.assertEqual(self._auf("Schule"), "Schule")
        self.assertEqual(self._auf("feuerwehr"), "Feuerwehr")
        self.assertEqual(self._auf("Arbeit"), "Arbeit")

    def test_unbekanntes_oder_leeres_landet_in_privat(self):
        self.assertEqual(self._auf(""), "Privat")
        self.assertEqual(self._auf("Quatsch"), "Privat")

    def test_gesperrte_kalender_werden_nie_ziel(self):
        self.assertEqual(self._auf("aha Abfallkalender"), "Privat")
        self.assertEqual(self._auf("Erinnerungen"), "Privat")


class ZielImVorschlag(TermineAnlegen):
    def test_rueckfrage_nennt_den_kalender_und_eintrag_nutzt_ihn(self):
        r = self._aus("termin_anlegen", {"titel": "Mathe-Arbeit", "beginn": "2026-09-20T08:00",
                                         "kalender_name": "Schule"})
        self.assertIn("in den Kalender Schule eintragen?", r["ergebnis"])
        self._aus("termin_bestaetigen", {}, zustimmung=True)
        self.assertEqual(self.anlegen.call_args.args[5], "Schule")   # genau dorthin geschrieben


class Suchen(Mit_Uhr):
    """Ein Termin in anderthalb Monaten war unauffindbar — kalender reicht nur 31 Tage."""

    WEIT = [{"kalender": "Feuerwehr", "titel": "Maschinistenausbildung", "beginn": "2026-10-19T08:00+00:00",
             "ende": "", "ganztags": False, "ort": "Musterhausen", "notiz": ""},
            {"kalender": "Privat", "titel": "Therapie", "beginn": "2026-10-28T14:00+00:00",
             "ende": "", "ganztags": False, "ort": "", "notiz": "Frau Schwarting"}]

    def test_findet_termin_weit_voraus(self):
        with mock.patch.object(icloud, "termine", return_value=self.WEIT) as t:
            text = asyncio.run(kalender.kalender_suchen("maschinistenausbildung"))
        self.assertIn("Ein Treffer", text)
        self.assertIn("Maschinistenausbildung", text)
        # Der Suchzeitraum reicht weit über die 31 Tage von `kalender` hinaus.
        von, bis = t.call_args.args[0], t.call_args.args[1]
        self.assertGreater((bis - von).days, 300)

    def test_sucht_auch_in_ort_und_notiz(self):
        with mock.patch.object(icloud, "termine", return_value=self.WEIT):
            self.assertIn("Therapie", asyncio.run(kalender.kalender_suchen("Schwarting")))
            self.assertIn("Maschinistenausbildung", asyncio.run(kalender.kalender_suchen("musterhausen")))

    def test_ehrlich_wenn_nichts_da_ist(self):
        with mock.patch.object(icloud, "termine", return_value=self.WEIT):
            text = asyncio.run(kalender.kalender_suchen("Zirkus"))
        self.assertIn("finde ich", text)
        self.assertNotIn("Maschinist", text)

    def test_monate_werden_begrenzt(self):
        with mock.patch.object(icloud, "termine", return_value=[]) as t:
            asyncio.run(kalender.kalender_suchen("x", monate=99))
        self.assertLessEqual((t.call_args.args[1] - t.call_args.args[0]).days, 31 * 24 + 1)


class WeitereZeitraeume(Mit_Uhr):
    def test_naechster_monat_ist_der_kalendermonat(self):
        von, bis, wort = kalender._zeitraum("nächsten Monat")     # heute: 13.09.2026
        self.assertEqual((von.date().isoformat(), bis.date().isoformat()), ("2026-10-01", "2026-10-31"))
        self.assertIn("Oktober", wort)

    def test_drei_monate(self):
        von, bis, wort = kalender._zeitraum("drei Monate")
        self.assertEqual((bis - von).days, 92)
        self.assertIn("drei Monaten", wort)


class HilfeLinks(unittest.TestCase):
    """Jeder Dienst zeigt, WO es die Zugangsdaten gibt — auch jeder künftige.
    Dieser Test schlägt an, wenn bei einem neuen Konnektor der Link fehlt."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_jeder_konnektor_hat_einen_link(self):
        for k in konnektoren.uebersicht():
            with self.subTest(k["id"]):
                self.assertTrue(k["hilfeUrl"].startswith("https://"), k["id"])
                self.assertTrue(k["hilfeText"].strip(), k["id"])

    def test_die_richtigen_ziele(self):
        ziele = {k["id"]: k["hilfeUrl"] for k in konnektoren.uebersicht()}
        self.assertIn("developer.spotify.com", ziele["spotify"])
        self.assertIn("apple.com", ziele["icloud_kalender"])
        self.assertIn("iserv", ziele["schulmanager"].lower())
        self.assertEqual(ziele["schulmanager"], ziele["iserv_mail"])   # dieselbe Anmeldung

    def test_schuladresse_kommt_aus_der_umgebung(self):
        with mock.patch.dict(os.environ, {"ISERV_URL": "https://andere-schule.de/iserv/"}):
            ziele = {k["id"]: k["hilfeUrl"] for k in konnektoren.uebersicht()}
        self.assertEqual(ziele["schulmanager"], "https://andere-schule.de/iserv")


class Frei(Mit_Uhr):
    """Nur frei oder belegt — nie Titel, Zeiten oder Gründe (Daten-Rechte 04.10.2026)."""

    def test_leerer_tag_passt(self):
        with mock.patch.object(icloud, "termine", return_value=[]):
            self.assertEqual(asyncio.run(kalender.kalender_frei("morgen")), "Passt.")

    def test_belegter_tag_passt_nicht_ohne_details(self):
        with mock.patch.object(icloud, "termine", return_value=TERMINE):
            text = asyncio.run(kalender.kalender_frei("2026-09-14"))
        self.assertEqual(text, "Passt nicht.")
        for verboten in ("Zahnarzt", "Wandertag", "09:30", "Musterhausen", "Schule"):
            self.assertNotIn(verboten, text)

    def test_fenster_ohne_kollision_passt(self):
        with mock.patch.object(icloud, "termine", return_value=[TERMINE[0]]):
            # Zahnarzt 09:30 UTC = 11:30 Berlin — 14 bis 16 Uhr ist frei.
            self.assertEqual(asyncio.run(kalender.kalender_frei("2026-09-14", "14:00", "16:00")), "Passt.")
            self.assertEqual(asyncio.run(kalender.kalender_frei("2026-09-14", "11:00", "12:00")), "Passt nicht.")

    def test_ganztags_blockiert_jedes_fenster(self):
        ganztags = [dict(TERMINE[1], beginn="2026-09-15T00:00+00:00")]
        with mock.patch.object(icloud, "termine", return_value=ganztags):
            self.assertEqual(asyncio.run(kalender.kalender_frei("2026-09-15", "14:00", "16:00")), "Passt nicht.")

    def test_unsinnige_zeit_fragt_nach(self):
        with mock.patch.object(icloud, "termine", return_value=[]):
            text = asyncio.run(kalender.kalender_frei("morgen", "irgendwann", ""))
        self.assertIn("wann", text.lower())
