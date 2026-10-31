"""Erinnerungen: anlegen, fällig werden, verschicken, absagen — ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from kern import erinnerungen
from kern.dienste import push, standort
from kern.werkzeuge import erinnerung, katalog

MONTAG = datetime(2026, 9, 14, 17, 0, tzinfo=ZoneInfo("Europe/Berlin"))


class Mit_Uhr(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=MONTAG)
        self.uhr.start()
        # Ein angemeldetes Gerät, sonst warnt das Werkzeug zu Recht.
        self.geraete = mock.patch.object(push, "geraete", return_value=[{"geraet": "iPhone"}])
        self.geraete.start()

    def tearDown(self):
        self.geraete.stop()
        self.uhr.stop()
        self.env.stop()


class Anlegen(Mit_Uhr):
    def test_wird_angelegt_und_bestaetigt(self):
        antwort = erinnerung.erinnerung("Wasserpest für Bio",
                                        "Denk dran, die Wasserpest einzupacken.", "2026-09-14T19:00")
        self.assertIn("Montag, 14.09. um 19:00", antwort)
        self.assertIn("Wasserpest für Bio", antwort)
        offen = erinnerungen.standard().offene()
        self.assertEqual(len(offen), 1)
        self.assertEqual(offen[0]["titel"], "Wasserpest für Bio")
        self.assertTrue(offen[0]["faellig"].endswith("+00:00"))      # intern UTC

    def test_aus_fremdem_text_niemals(self):
        antwort = katalog and erinnerung.erinnerung("X", "Y", "2026-09-14T19:00", fremde_inhalte=True)
        self.assertIn("fremdem Text", antwort)
        self.assertEqual(erinnerungen.standard().offene(), [])

    def test_vergangenheit_und_ferne_zukunft(self):
        self.assertIn("vorbei", erinnerung.erinnerung("X", "Y", "2026-09-14T08:00"))
        self.assertIn("zwei Jahre", erinnerung.erinnerung("X", "Y", "2099-01-01T10:00"))
        self.assertEqual(erinnerungen.standard().offene(), [])

    def test_ohne_uhrzeit_wird_nachgefragt(self):
        self.assertIn("Datum UND Uhrzeit", erinnerung.erinnerung("X", "Y", "2026-09-15"))

    def test_warnt_wenn_kein_geraet_angemeldet(self):
        with mock.patch.object(push, "geraete", return_value=[]):
            antwort = erinnerung.erinnerung("X", "Y", "2026-09-14T19:00")
        self.assertIn("kein Gerät", antwort)
        self.assertEqual(len(erinnerungen.standard().offene()), 1)   # trotzdem angelegt


class Zeigen(Mit_Uhr):
    def test_liste_und_absagen(self):
        self.assertIn("keine Erinnerungen", erinnerung.erinnerungen_zeigen())
        erinnerung.erinnerung("Wasserpest", "einpacken", "2026-09-14T19:00")
        eid = erinnerungen.standard().offene()[0]["id"]
        self.assertIn(f"{eid}: Montag, 14.09. um 19:00 — Wasserpest", erinnerung.erinnerungen_zeigen())
        self.assertIn("abgesagt", erinnerung.erinnerung_absagen(eid))
        self.assertEqual(erinnerungen.standard().offene(), [])
        self.assertIn("finde ich nicht", erinnerung.erinnerung_absagen(eid))   # nicht zweimal


class Verschicken(Mit_Uhr):
    def _anlegen(self, minuten):
        return erinnerungen.standard().anlegen(
            "Wasserpest für Bio", "Denk dran.", datetime.now(timezone.utc) + timedelta(minutes=minuten))

    def test_faellige_gehen_raus_spaetere_nicht(self):
        faellig = self._anlegen(-1)
        spaeter = self._anlegen(120)
        with mock.patch.object(push, "senden", return_value={"gesendet": 1}) as s:
            self.assertEqual(asyncio.run(erinnerungen.faellige_schicken()), 1)
        self.assertEqual(s.call_args.args[0], "Wasserpest für Bio")
        self.assertEqual(s.call_args.args[1], "Denk dran.")
        a = erinnerungen.standard()
        self.assertEqual(a.eintrag(faellig)["status"], "geschickt")
        self.assertEqual(a.eintrag(spaeter)["status"], "offen")

    def test_nicht_zweimal_schicken(self):
        self._anlegen(-1)
        with mock.patch.object(push, "senden", return_value={"gesendet": 1}):
            asyncio.run(erinnerungen.faellige_schicken())
            self.assertEqual(asyncio.run(erinnerungen.faellige_schicken()), 0)

    def test_nach_drei_fehlversuchen_aufgeben(self):
        eid = self._anlegen(-1)
        with mock.patch.object(push, "senden", return_value={"gesendet": 0, "grund": "kein Gerät"}):
            for _ in range(3):
                asyncio.run(erinnerungen.faellige_schicken())
        self.assertEqual(erinnerungen.standard().eintrag(eid)["status"], "gescheitert")


class ImKatalog(Mit_Uhr):
    def test_immer_verfuegbar_ohne_konnektor(self):
        namen = {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertTrue({"erinnerung", "erinnerungen", "erinnerung_absagen"} <= namen)

    def test_ueber_den_katalog_mit_fremdem_text(self):
        r = asyncio.run(katalog.ausfuehren("erinnerung",
                                           {"titel": "X", "text": "Y", "zeitpunkt": "2026-09-14T19:00"},
                                           fremde_inhalte=True))
        self.assertIn("fremdem Text", r["ergebnis"])
        r = asyncio.run(katalog.ausfuehren("erinnerung",
                                           {"titel": "Wasserpest", "text": "einpacken",
                                            "zeitpunkt": "2026-09-14T19:00"}))
        self.assertIn("steht", r["ergebnis"])


class GescheiterteErinnerung(Mit_Uhr):
    def test_endgueltig_gescheiterte_erinnerung_wird_ein_hohes_anliegen(self):
        from datetime import timezone
        from kern.anliegen import fach
        a = erinnerungen.standard()
        a.anlegen("Wasserpest", "Einpacken", datetime.now(timezone.utc) - timedelta(minutes=5))
        with mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 0}):
            for _ in range(erinnerungen.MAX_VERSUCHE):
                asyncio.run(erinnerungen.faellige_schicken())
        offen = fach.standard().offene("eike")
        self.assertEqual((offen[0]["quelle"], offen[0]["dringlichkeit"]), ("erinnerung", "hoch"))
        self.assertIn("Wasserpest", offen[0]["text"])


class Migration(unittest.TestCase):
    def test_alte_datei_ohne_neue_spalten_wird_migriert_offene_bleiben_faellig(self):
        import sqlite3, tempfile
        from pathlib import Path
        ordner = Path(tempfile.mkdtemp())
        alt = sqlite3.connect(ordner / "erinnerungen.db")
        alt.executescript("""
            CREATE TABLE erinnerungen (id INTEGER PRIMARY KEY, titel TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
                faellig TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'offen', versuche INTEGER NOT NULL DEFAULT 0,
                erstellt TEXT NOT NULL, geschickt TEXT);
            INSERT INTO erinnerungen (titel, text, faellig, erstellt) VALUES ('Alt', 'x', '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00');
        """)
        alt.commit()
        alt.close()
        a = erinnerungen.Ablage(ordner)
        e = a.faellige()[0]
        self.assertEqual((e["titel"], e["kanal"], e["empfaenger"]), ("Alt", "push", None))
        self.assertEqual((e["ausloeser"], e["ankunft_bereit"]), ("zeit", 0))   # alte Zeilen bleiben Zeit-Erinnerungen
        self.assertGreater(a.anlegen("Neu", "", datetime.now(timezone.utc) + timedelta(hours=1), kanal="snapchat", empfaenger="p1"), 0)


class KanalUndEmpfaenger(Mit_Uhr):
    def neu(self, kanal="push", empfaenger=None, titel="T"):
        return erinnerungen.standard().anlegen(titel, "Text", datetime.now(timezone.utc) + timedelta(hours=1),
                                                kanal=kanal, empfaenger=empfaenger)

    def test_standard_ist_push_an_eike(self):
        e = erinnerungen.standard().eintrag(self.neu())
        self.assertEqual((e["kanal"], e["empfaenger"], e["auftrag_id"], e["bestaetigt"]), ("push", None, None, None))

    def test_unbekannter_kanal_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            self.neu(kanal="brieftaube")

    def test_zaehler_je_person_und_gesamt(self):
        a = erinnerungen.standard()
        for n in range(3):
            self.neu("snapchat", "p1", f"T{n}")
        self.neu("snapchat", "p2")
        self.neu("push")
        self.assertEqual((a.offene_an("p1"), a.offene_an("p2"), a.offene_an_personen()), (3, 1, 4))

    def test_gesendet_heute_zaehlt_nur_geschickte_dieser_person_von_heute(self):
        a = erinnerungen.standard()
        e1, e2 = self.neu("snapchat", "p1"), self.neu("snapchat", "p1")
        a.markieren(e1, "geschickt")
        self.assertEqual((a.gesendet_heute("p1"), a.gesendet_heute("p2")), (1, 0))

    def test_zu_pruefen_nur_geschickte_snapchat_mit_auftrag_ohne_bestaetigung(self):
        a = erinnerungen.standard()
        e = self.neu("snapchat", "p1")
        self.assertEqual(a.zu_pruefen(), [])
        a.markieren(e, "geschickt")
        a.auftrag_setzen(e, "abc123")
        self.assertEqual([x["id"] for x in a.zu_pruefen()], [e])
        a.bestaetigung_setzen(e, True)
        self.assertEqual(a.zu_pruefen(), [])
        self.assertEqual(a.eintrag(e)["bestaetigt"], "ja")
        p = self.neu("push")
        a.markieren(p, "geschickt")
        self.assertNotIn(p, [x["id"] for x in a.zu_pruefen()])


class WerkzeugKanal(Mit_Uhr):
    def setUp(self):
        super().setUp()
        from kern.personen import register
        self.maya = register.anlegen("Kontakt B", snapchat="maya_x", persona="Freundin", proaktiv=True)
        self.kontakt_a = register.anlegen("Kontakt A", snapchat="kontakt_a", persona="Kumpel", proaktiv=False)
        recht = mock.patch("kern.konnektoren.schreiben_erlaubt", return_value=True)
        self.recht = recht.start()
        self.addCleanup(recht.stop)
        nutz = mock.patch("kern.konnektoren.benutzbar", return_value=True)
        nutz.start()
        self.addCleanup(nutz.stop)

    def e(self, **kw):
        a = dict(titel="Buch", text="Denk an das Buch", zeitpunkt="2026-09-15T18:00")
        a.update(kw)
        return erinnerung.erinnerung(**a)

    def liste(self):
        return erinnerungen.standard().offene()

    def test_push_an_eike_bleibt_wie_bisher(self):
        self.assertIn("steht", self.e())
        self.assertEqual((self.liste()[0]["kanal"], self.liste()[0]["empfaenger"]), ("push", None))

    def test_snapchat_an_eike_braucht_das_schreibrecht(self):
        self.recht.return_value = False
        self.assertIn("Schreibrecht", self.e(kanal="snapchat"))
        self.assertEqual(self.liste(), [])
        self.recht.return_value = True
        self.assertIn("steht", self.e(kanal="snapchat"))
        self.assertEqual(self.liste()[0]["kanal"], "snapchat")

    def test_an_eine_person_per_snapchat(self):
        antwort = self.e(kanal="snapchat", an="Kontakt B")
        self.assertIn("Kontakt B", antwort)
        r = self.liste()[0]
        self.assertEqual((r["kanal"], r["empfaenger"]), ("snapchat", self.maya["id"]))

    def test_an_andere_nie_per_push_oder_beides(self):
        for kanal in ("push", "beides"):
            self.assertIn("keine pushes", self.e(kanal=kanal, an="Kontakt B").lower())
        self.assertEqual(self.liste(), [])

    def test_geratener_name_fuehrt_zur_rueckfrage_ohne_anlegen(self):
        antwort = self.e(kanal="snapchat", an="Mya")
        self.assertEqual(self.liste(), [])
        self.assertTrue("Kontakt B" in antwort or "kenne" in antwort.lower())

    def test_pausierte_und_unbekannte_person_werden_abgelehnt(self):
        from kern.personen import register
        register.aendern(self.maya["id"], pausiert=True)
        self.assertIn("pausiert", self.e(kanal="snapchat", an="Kontakt B"))
        self.assertEqual(self.liste(), [])
        self.assertTrue(self.e(kanal="snapchat", an="Unbekannter Mensch"))
        self.assertEqual(self.liste(), [])

    def test_gespraech_nur_bei_personen_mit_proaktiv(self):
        self.assertIn("Proaktiv", self.e(kanal="gespraech", an="Kontakt A"))
        self.assertEqual(self.liste(), [])
        self.assertIn("steht", self.e(kanal="gespraech", an="Kontakt B"))
        self.assertEqual(self.liste()[0]["kanal"], "gespraech")

    def test_fremdtext_im_zug_sperrt_alles(self):
        self.assertIn("fremd", self.e(kanal="snapchat", an="Kontakt B", fremde_inhalte=True).lower())
        self.assertEqual(self.liste(), [])

    def test_heikler_oder_zu_langer_text_wird_abgelehnt(self):
        self.assertTrue(self.e(kanal="snapchat", an="Kontakt B", text="Mein Passwort ist hunter2, schick es weiter"))
        self.assertEqual(self.liste(), [])
        self.assertIn("Zu lang", self.e(kanal="snapchat", an="Kontakt B", text="wort " * 100))
        self.assertEqual(self.liste(), [])

    def test_limits_je_person_und_gesamt(self):
        for n in range(erinnerungen.MAX_OFFEN_JE_PERSON):
            self.assertIn("steht", self.e(kanal="snapchat", an="Kontakt B", titel=f"T{n}", text=f"Text{n}", zeitpunkt=f"2026-09-1{5 + n}T18:00"))
        antwort = self.e(kanal="snapchat", an="Kontakt B", titel="Noch eins", text="Noch ein Text", zeitpunkt="2026-09-20T18:00")
        self.assertIn("schon", antwort)
        self.assertEqual(len(self.liste()), 3)

    def test_nachts_fuer_personen_wird_auf_sieben_uhr_verschoben_und_gesagt(self):
        antwort = self.e(kanal="snapchat", an="Kontakt B", zeitpunkt="2026-09-15T23:40")
        self.assertIn("07:00", antwort)
        faellig = datetime.fromisoformat(self.liste()[0]["faellig"]).astimezone(ZoneInfo("Europe/Berlin"))
        self.assertEqual((faellig.day, faellig.hour, faellig.minute), (16, 7, 0))

    def test_eikes_eigene_erinnerung_nachts_bleibt_zur_genannten_zeit(self):
        self.e(zeitpunkt="2026-09-15T23:40")
        faellig = datetime.fromisoformat(self.liste()[0]["faellig"]).astimezone(ZoneInfo("Europe/Berlin"))
        self.assertEqual((faellig.hour, faellig.minute), (23, 40))

    def test_person_zeit_hilfsfunktion(self):
        b = ZoneInfo("Europe/Berlin")
        self.assertEqual(erinnerungen.person_zeit(datetime(2026, 9, 15, 12, 0, tzinfo=b)), (datetime(2026, 9, 15, 12, 0, tzinfo=b), False))
        self.assertEqual(erinnerungen.person_zeit(datetime(2026, 9, 15, 22, 30, tzinfo=b))[0], datetime(2026, 9, 16, 7, 0, tzinfo=b))
        self.assertEqual(erinnerungen.person_zeit(datetime(2026, 9, 16, 5, 0, tzinfo=b))[0], datetime(2026, 9, 16, 7, 0, tzinfo=b))

    def test_anzeige_nennt_kanal_und_empfaenger(self):
        self.e(kanal="snapchat", an="Kontakt B")
        self.assertIn("Snapchat", erinnerung.erinnerungen_zeigen())
        self.assertIn("Kontakt B", erinnerung.erinnerungen_zeigen())


class Zustellung(Mit_Uhr):
    def setUp(self):
        super().setUp()
        from kern.personen import register
        self.maya = register.anlegen("Kontakt B", snapchat="maya_x", persona="Freundin", proaktiv=True)
        for ziel, wert in (("kern.konnektoren.schreiben_erlaubt", True), ("kern.konnektoren.benutzbar", True)):
            p = mock.patch(ziel, return_value=wert)
            p.start()
            self.addCleanup(p.stop)
        self.push = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        self.push_mock = self.push.start()
        self.addCleanup(self.push.stop)
        self.auftrag = mock.patch("kern.dienste.snapchat.auftrag_legen", return_value="abc123")
        self.auftrag_mock = self.auftrag.start()
        self.addCleanup(self.auftrag.stop)

    def faellig(self, kanal, empfaenger=None, text="Denk an das Buch"):
        return erinnerungen.standard().anlegen("Buch", text, datetime.now(timezone.utc) - timedelta(minutes=5),
                                               kanal=kanal, empfaenger=empfaenger)

    def lauf(self):
        return asyncio.run(erinnerungen.faellige_schicken())

    def test_push_an_eike_wie_bisher(self):
        e = self.faellig("push")
        self.assertEqual(self.lauf(), 1)
        self.push_mock.assert_called_once()
        self.auftrag_mock.assert_not_called()
        self.assertEqual(erinnerungen.standard().eintrag(e)["status"], "geschickt")

    def test_snapchat_an_eike_legt_einen_auftrag_ohne_person(self):
        e = self.faellig("snapchat")
        self.lauf()
        self.auftrag_mock.assert_called_once_with("text", text="Denk an das Buch", person=None, einmalig=True)
        self.push_mock.assert_not_called()
        r = erinnerungen.standard().eintrag(e)
        self.assertEqual((r["status"], r["auftrag_id"]), ("geschickt", "abc123"))

    def test_beides_schickt_push_und_snapchat(self):
        self.faellig("beides")
        self.lauf()
        self.push_mock.assert_called_once()
        self.auftrag_mock.assert_called_once()

    def test_snapchat_an_person_mit_person_id(self):
        self.faellig("snapchat", self.maya["id"])
        self.lauf()
        self.auftrag_mock.assert_called_once_with("text", text="Denk an das Buch", person=self.maya["id"], einmalig=True)
        self.push_mock.assert_not_called()

    def test_gespraech_wird_ein_anliegen_fuer_den_empfaenger(self):
        from kern.anliegen import fach
        e = self.faellig("gespraech", self.maya["id"])
        self.lauf()
        a = fach.standard().offene(self.maya["id"])[0]
        self.assertEqual((a["quelle"], a["titel"]), ("erinnerung", "Buch"))
        self.assertEqual(erinnerungen.standard().eintrag(e)["status"], "geschickt")
        self.auftrag_mock.assert_not_called()

    def test_pausierte_person_zur_faelligkeit_nichts_gesendet_rueckfall_an_eike(self):
        from kern.anliegen import fach
        from kern.personen import register
        register.aendern(self.maya["id"], pausiert=True)
        e = self.faellig("snapchat", self.maya["id"])
        for _ in range(erinnerungen.MAX_VERSUCHE):
            self.lauf()
        self.auftrag_mock.assert_not_called()
        self.assertEqual(erinnerungen.standard().eintrag(e)["status"], "gescheitert")
        self.push_mock.assert_called_once()
        self.assertIn("Kontakt B", self.push_mock.call_args.args[2])
        self.assertEqual(fach.standard().offene("eike")[0]["dringlichkeit"], "hoch")

    def test_gesperrter_text_und_tageslimit_werden_nicht_gesendet(self):
        e = self.faellig("snapchat", self.maya["id"], text="Mein Passwort ist hunter2")
        for _ in range(erinnerungen.MAX_VERSUCHE):
            self.lauf()
        self.auftrag_mock.assert_not_called()
        self.assertEqual(erinnerungen.standard().eintrag(e)["status"], "gescheitert")
        a = erinnerungen.standard()
        for n in range(erinnerungen.MAX_TAG_JE_PERSON):
            x = self.faellig("snapchat", self.maya["id"], text=f"Text{n}")
            a.markieren(x, "geschickt")
        y = self.faellig("snapchat", self.maya["id"], text="Zu viel heute")
        for _ in range(erinnerungen.MAX_VERSUCHE):
            self.lauf()
        self.assertEqual(a.eintrag(y)["status"], "gescheitert")

    def test_auftrag_scheitert_erst_nach_den_versuchen_wird_aufgegeben(self):
        from kern.dienste import snapchat
        self.auftrag_mock.side_effect = snapchat.SnapchatFehler("Handy offline")
        e = self.faellig("snapchat")
        for _ in range(erinnerungen.MAX_VERSUCHE - 1):
            self.lauf()
        self.assertEqual(erinnerungen.standard().eintrag(e)["status"], "offen")
        self.lauf()
        self.assertEqual(erinnerungen.standard().eintrag(e)["status"], "gescheitert")
        self.push_mock.assert_called_once()                    # Rückfall an Eike, einmal

    def nachsehen(self, ergebnis, alter_min=1):
        e = self.faellig("snapchat", self.maya["id"])
        self.lauf()
        a = erinnerungen.standard()
        with mock.patch("kern.dienste.snapchat.ergebnis_fuer", return_value=ergebnis), \
             mock.patch("kern.erinnerungen._jetzt_utc", return_value=datetime.now(timezone.utc) + timedelta(minutes=alter_min)):
            asyncio.run(erinnerungen.nachsehen())
        return a.eintrag(e)

    def test_bestaetigung_gesendet(self):
        self.push_mock.reset_mock()
        r = self.nachsehen({"ergebnis": "gesendet"})
        self.assertEqual(r["bestaetigt"], "ja")
        self.push_mock.assert_not_called()

    def test_helfer_meldet_aufgegeben_rueckfall(self):
        from kern.anliegen import fach
        self.push_mock.reset_mock()
        r = self.nachsehen({"ergebnis": "aufgegeben", "grund": "Chat nicht gefunden"})
        self.assertEqual((r["bestaetigt"], r["status"]), ("nein", "gescheitert"))
        self.push_mock.assert_called_once()
        self.assertIn("nicht angekommen", self.push_mock.call_args.args[2])
        self.assertEqual(fach.standard().offene("eike")[0]["dringlichkeit"], "hoch")

    def test_ohne_ergebnis_wird_nach_30_minuten_zum_rueckfall_vorher_nicht(self):
        self.push_mock.reset_mock()
        self.assertIsNone(self.nachsehen(None, alter_min=10)["bestaetigt"])
        self.push_mock.assert_not_called()
        r = self.nachsehen(None, alter_min=erinnerungen.BESTAETIGUNG_MIN + 1)
        self.assertEqual(r["bestaetigt"], "nein")


class Ankunft(Mit_Uhr):
    """„Erinnere mich, wenn ich nach Hause komme“ — Auslöser ist die Ankunft, nicht die Uhr."""

    HAUS = {"adresse": "Teststraße 1", "breite": 52.0, "laenge": 9.0}

    def setUp(self):
        super().setUp()
        self.heimadresse = mock.patch.dict(os.environ, {"FRIDAY_HEIMADRESSE": "Beispielweg 1, 12345 Musterstadt"})
        self.heimadresse.start()
        self.addCleanup(self.heimadresse.stop)
        # Standortquellen freigegeben — sonst ist alles aus (Default-Deny).
        quellen = mock.patch("kern.konnektoren.benutzbar", side_effect=lambda k: k in ("standort", "personenstandort"))
        quellen.start()
        self.addCleanup(quellen.stop)
        self.push = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        self.push_mock = self.push.start()
        self.addCleanup(self.push.stop)

    def kandidat(self, weg_m, genau=20, alter_s=60):
        """Ein OwnTracks-Punkt `weg_m` Meter nördlich des Hauses (nur die Breite variieren)."""
        return [{"quelle": "OwnTracks", "breite": self.HAUS["breite"] + weg_m / 111320.0,
                 "laenge": self.HAUS["laenge"], "genau": genau, "alter_s": alter_s, "geraet": "Handy"}]

    def position(self, weg_m, genau=20, alter_s=60):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(standort, "heim", mock.AsyncMock(return_value=self.HAUS)))
        stack.enter_context(mock.patch.object(standort, "eigene_kandidaten",
                                              mock.AsyncMock(return_value=self.kandidat(weg_m, genau, alter_s))))
        return stack

    def test_wird_angelegt_ohne_uhrzeit_und_nicht_ueber_die_uhr_faellig(self):
        antwort = erinnerung.erinnerung("Milch", "Hol Milch.", ausloeser="ankunft")
        self.assertIn("zu Hause", antwort)
        self.assertIn("Milch", antwort)
        e = erinnerungen.standard().offene()[0]
        self.assertEqual(e["ausloeser"], "ankunft")
        self.assertEqual(erinnerungen.standard().faellige(), [])
        self.assertEqual(asyncio.run(erinnerungen.faellige_schicken()), 0)
        self.push_mock.assert_not_called()
        self.assertIn("bei Ankunft zu Hause", erinnerung.erinnerungen_zeigen())

    def test_ohne_heimadresse_und_ohne_standortquelle_wird_abgelehnt(self):
        with mock.patch.dict(os.environ, {"FRIDAY_HEIMADRESSE": ""}):
            self.assertIn("FRIDAY_HEIMADRESSE", erinnerung.erinnerung("Milch", "Hol Milch.", ausloeser="ankunft"))
        with mock.patch("kern.konnektoren.benutzbar", return_value=False):
            self.assertIn("Standort", erinnerung.erinnerung("Milch", "Hol Milch.", ausloeser="ankunft"))
        self.assertEqual(erinnerungen.standard().offene(), [])

    def test_nur_fuer_eike_selbst(self):
        self.assertIn("nur für dich", erinnerung.erinnerung("Milch", "Hol Milch.", ausloeser="ankunft", an="Kontakt B"))
        self.assertEqual(erinnerungen.standard().offene(), [])

    def test_feuert_erst_nach_dem_wegsein_und_dann_genau_einmal(self):
        a = erinnerungen.standard()
        eid = a.anlegen("Milch", "Hol Milch.", datetime.now(timezone.utc), ausloeser="ankunft")
        # Beim Anlegen schon zu Hause: nicht sofort.
        with self.position(0):
            self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 0)
        self.push_mock.assert_not_called()
        # Unterwegs: wird scharf, feuert aber nicht.
        with self.position(500):
            self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 0)
        self.push_mock.assert_not_called()
        self.assertEqual(a.eintrag(eid)["ankunft_bereit"], 1)
        # Zurück: genau eine Benachrichtigung.
        with self.position(0):
            self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 1)
        self.push_mock.assert_called_once()
        self.assertEqual((a.eintrag(eid)["status"], a.eintrag(eid)["ausloeser"]), ("geschickt", "ankunft"))
        with self.position(0):
            self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 0)
        self.push_mock.assert_called_once()

    def test_zu_ungenaue_oder_zu_alte_position_loest_nichts_aus(self):
        a = erinnerungen.standard()
        eid = a.anlegen("Milch", "Hol Milch.", datetime.now(timezone.utc), ausloeser="ankunft")
        a.ankunft_bereit_markieren()
        for genau, alter in ((500, 60), (20, standort.AKTUELL_S + 60)):
            with self.position(0, genau=genau, alter_s=alter):
                self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 0)
        self.push_mock.assert_not_called()
        self.assertEqual(a.eintrag(eid)["status"], "offen")


class AnkunftUeberHomeAssistant(Mit_Uhr):
    """Home Assistant als zusätzliche, rein lesende Standortquelle (30.10.2026).

    App und OwnTracks sind hier absichtlich aus — nur HA ist freigegeben. Die
    200-m/75-m-Logik bleibt unverändert."""

    HAUS = {"adresse": "Teststraße 1", "breite": 52.0, "laenge": 9.0}

    def setUp(self):
        super().setUp()
        self.heimadresse = mock.patch.dict(os.environ, {"FRIDAY_HEIMADRESSE": "Beispielweg 1, 12345 Musterstadt"})
        self.heimadresse.start()
        self.addCleanup(self.heimadresse.stop)
        quellen = mock.patch("kern.konnektoren.benutzbar", side_effect=lambda k: k == "homeassistant")
        quellen.start()
        self.addCleanup(quellen.stop)
        self.push = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        self.push_mock = self.push.start()
        self.addCleanup(self.push.stop)

    def kandidat(self, weg_m, genau=20, alter_s=60):
        return {"quelle": "Home Assistant", "breite": self.HAUS["breite"] + weg_m / 111320.0,
                "laenge": self.HAUS["laenge"], "genau": genau, "alter_s": alter_s, "geraet": ""}

    def position(self, weg_m, genau=20, alter_s=60):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(standort, "heim", mock.AsyncMock(return_value=self.HAUS)))
        stack.enter_context(mock.patch("kern.dienste.homeassistant.position",
                                       mock.AsyncMock(return_value=self.kandidat(weg_m, genau, alter_s))))
        return stack

    def test_erinnerung_laesst_sich_allein_mit_home_assistant_anlegen(self):
        antwort = erinnerung.erinnerung("Milch", "Hol Milch.", ausloeser="ankunft")
        self.assertIn("zu Hause", antwort)
        self.assertEqual(erinnerungen.standard().offene()[0]["ausloeser"], "ankunft")

    def test_ankunft_feuert_wenn_nur_home_assistant_die_position_liefert(self):
        a = erinnerungen.standard()
        eid = a.anlegen("Milch", "Hol Milch.", datetime.now(timezone.utc), ausloeser="ankunft")
        with self.position(500):
            self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 0)
        self.assertEqual(a.eintrag(eid)["ankunft_bereit"], 1)
        self.push_mock.assert_not_called()
        with self.position(0):
            self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 1)
        self.push_mock.assert_called_once()
        self.assertEqual(a.eintrag(eid)["status"], "geschickt")

    def test_grenzen_bleiben_200_und_75_meter(self):
        a = erinnerungen.standard()
        eid = a.anlegen("Milch", "Hol Milch.", datetime.now(timezone.utc), ausloeser="ankunft")
        with self.position(300):
            self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 0)
        self.assertEqual(a.eintrag(eid)["ankunft_bereit"], 1)      # 300 m: weiter als 200 m, also scharf
        for weg in (150, 80):                                      # zurück, aber noch nicht nah genug (75 m)
            with self.position(weg):
                self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 0)
        self.push_mock.assert_not_called()
        with self.position(70):
            self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 1)   # 70 m: zu Hause
        self.push_mock.assert_called_once()

    def test_zu_ungenaue_oder_zu_alte_ha_position_loest_nichts_aus(self):
        a = erinnerungen.standard()
        eid = a.anlegen("Milch", "Hol Milch.", datetime.now(timezone.utc), ausloeser="ankunft")
        a.ankunft_bereit_markieren()
        for genau, alter in ((500, 60), (20, standort.AKTUELL_S + 60)):
            with self.position(0, genau=genau, alter_s=alter):
                self.assertEqual(asyncio.run(erinnerungen.ankuenfte_pruefen()), 0)
        self.assertEqual(a.eintrag(eid)["status"], "offen")
        self.push_mock.assert_not_called()
