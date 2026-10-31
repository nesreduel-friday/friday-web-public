"""Gmail-Bescheide der ÜSTRA und die stündlichen Meldungen — ohne Netz.

Die Mailtexte haben den Aufbau der echten aus Eikes Postfach (Dezember 2025 bis
August 2026), Namen und Nummern sind verändert.
"""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from kern import konnektoren
from kern.dienste import gmail, push
from kern.garantie import ablage, antrag, post, sammler, suche

BERLIN = ZoneInfo("Europe/Berlin")
JETZT = datetime(2026, 9, 15, 18, 0, tzinfo=BERLIN)
BETREFF = "ÜSTRA Garantie: Ihr Garantiefall 8851675 vom 15.09.2026"

EINGANG = """Sehr geehrter Herr Muster,

vielen Dank für Ihren Antrag mit den folgenden Angaben:

*Fahrtdatum:* 15.09.2026
*Verbindung: *360 - Bennigsen Bahnhof - Hemmingen (15:52 - 16:19)
*Fahrkarte: *Deutschlandticket, Deutschlandticket (Hannover Jugend),

Ihren Antrag hat der ÜSTRA Verbund unter der Vorgangsnummer *8851675* aufgenommen und an uns als zuständiges
Verkehrsunternehmen weitergeleitet. Sobald Ihre Angaben geprüft sind, erhalten Sie von uns eine Nachricht.

Mit freundlichen Grüßen
Ihre ÜSTRA"""

GENEHMIGT = """Sehr geehrter Herr Muster,

Sie möchten für Ihre Fahrt am 15.09.2026 die ÜSTRA Pünktlichkeitsgarantie in Anspruch nehmen.

Die beanstandete Fahrt konnten wir nicht wie geplant durchführen. Wir bitten Sie um Entschuldigung.

Die Entschädigung in Höhe von 5,00 Euro werden wir in den nächsten Tagen auf das im Antrag angegebene Konto überweisen.

Bis bald und gute Fahrt im ÜSTRA Verbund!"""

PLANMAESSIG = """Sehr geehrter Herr Muster,

Sie möchten für Ihre Fahrt am 27.08.2026 die ÜSTRA Pünktlichkeitsgarantie in Anspruch nehmen.

Ihre beanstandete Verbindung haben wir geprüft. Tatsächlich ist die Fahrt planmäßig erfolgt. Deshalb können wir
leider keine Entschädigung vornehmen.

Bis bald und gute Fahrt im ÜSTRA Verbund!"""

DOPPELT = """Sie möchten für Ihre Fahrt am 19.08.2026 die ÜSTRA Pünktlichkeitsgarantie in Anspruch nehmen.

Sie haben bereits einen Antrag auf die Pünktlichkeitsgarantie mit der ID: 8851600 gestellt. Eine doppelte
Auszahlung ist nicht möglich.

Bis bald"""

RUECKFRAGE = """Sie möchten für Ihre Fahrt am 14.03.2026 die ÜSTRA Pünktlichkeitsgarantie in Anspruch nehmen.

Für die Bearbeitung benötigen wir Ihre Fahrkarte (Kopie, Scan oder Original). Bitte senden Sie uns diese innerhalb
von 14 Tagen zu.

Bis bald und gute Fahrt im ÜSTRA Verbund!

Mit freundlichen Grüßen
Transdev Hannover GmbH"""


def _mail(text, datum="2026-09-15T17:00:00+02:00", betreff=BETREFF):
    return {"uid": 1, "datum": datum, "von": "info <info@uestra.de>", "betreff": betreff, "text": text}


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=JETZT)
        self.uhr.start()

    def tearDown(self):
        from test.test_garantie import ablagen_schliessen
        ablagen_schliessen()
        self.uhr.stop()
        self.env.stop()


class Auswerten(unittest.TestCase):
    def test_eingang_mit_allen_angaben(self):
        v = post.auswerten(_mail(EINGANG))
        self.assertEqual((v["vorgang"], v["art"], v["fahrtdatum"], v["linie"], v["start"], v["ziel"], v["ab"], v["an"]),
                         ("8851675", "eingang", "2026-09-15", "360", "Bennigsen Bahnhof", "Hemmingen", "15:52", "16:19"))
        self.assertTrue(v["fahrkarte"].startswith("Deutschlandticket"))

    def test_genehmigt_mit_betrag(self):
        v = post.auswerten(_mail(GENEHMIGT))
        self.assertEqual((v["art"], v["cent"], v["fahrtdatum"]), ("genehmigt", 500, "2026-09-15"))

    def test_ablehnungen(self):
        for text in (PLANMAESSIG, DOPPELT, "Leider ist eine Änderung der Fahrdaten nicht möglich. Aus diesem Grund "
                                           "lehnen wir diesen Antrag ab."):
            with self.subTest(text[:30]):
                self.assertEqual(post.auswerten(_mail(text))["art"], "abgelehnt")
        self.assertIn("planmäßig erfolgt", post.auswerten(_mail(PLANMAESSIG))["grund"])

    def test_rueckfrage_und_bearbeiter(self):
        v = post.auswerten(_mail(RUECKFRAGE))
        self.assertEqual((v["art"], v["bearbeiter"]), ("rueckfrage", "Transdev"))

    def test_html_mail_mit_entitaeten(self):
        roh = (" \r\n Sehr geehrter Herr Muster,\n\nvielen Dank f&uuml;r Ihren Antrag mit den folgenden Angaben:\n\n"
               " Fahrtdatum: &nbsp;24.09.2025\n Verbindung:&nbsp; S5 - Hannover Hauptbahnhof - Weetzen Bahnhof "
               "(12:55 - 13:09) \n Fahrkarte:&nbsp; Monatskarte Sch&uuml;lerInnen &amp; Azubis,&nbsp;\n\n"
               "unter der Vorgangsnummer 8279296 &nbsp;aufgenommen\n\nTransdev Hannover GmbH")
        v = post.auswerten(_mail(roh, betreff="ÜSTRA Garantie: Ihr Garantiefall 8279296 vom 24.09.2025"))
        self.assertEqual((v["art"], v["linie"], v["ziel"], v["fahrtdatum"], v["antragstag"], v["bearbeiter"]),
                         ("eingang", "S5", "Weetzen Bahnhof", "2025-09-24", "2025-09-24", "Transdev"))
        self.assertEqual(v["fahrkarte"], "Monatskarte SchülerInnen & Azubis")
        v = post.auswerten(_mail(GENEHMIGT.replace("in Höhe von 5,00", "in H&ouml;he von&nbsp;5,00")))
        self.assertEqual((v["art"], v["cent"]), ("genehmigt", 500))

    def test_antwort_aufs_kontaktformular_ist_kein_bescheid(self):
        v = post.auswerten(_mail("vielen Dank für Ihren Antrag …", betreff="Re: [Extern] Re: " + BETREFF))
        self.assertEqual(v["art"], "sonstiges")

    def test_ohne_vorgangsnummer_keine_garantie_mail(self):
        self.assertIsNone(post.auswerten(_mail("Vielen Dank für Ihre Anfrage.", betreff="Re: Kontaktformular")))


class Abgleich(Basis):
    def _abgleich(self, mails):
        with mock.patch.object(post, "_holen", return_value=mails), \
                mock.patch.object(push, "senden", return_value={"gesendet": 1}) as melden:
            return asyncio.run(post.abgleich()), melden

    def test_erster_abgleich_still_danach_gemeldet(self):
        _, melden = self._abgleich([_mail(EINGANG)])
        melden.assert_not_called()                                         # alte Fälle nicht aufs Handy
        ergebnis, melden = self._abgleich([_mail(EINGANG), _mail(GENEHMIGT, "2026-09-16T09:00:00+02:00")])
        melden.assert_called_once()
        self.assertIn("+5,00 € Garantie genehmigt", melden.call_args.args[0])
        v = ablage.standard().vorgang("8851675")
        self.assertEqual((v["status"], v["cent"], v["linie"]), ("genehmigt", 500, "360"))
        _, melden = self._abgleich([_mail(EINGANG), _mail(GENEHMIGT, "2026-09-16T09:00:00+02:00")])
        melden.assert_not_called()                                         # nichts Neues: keine Meldung
        alt = _mail(GENEHMIGT, "2026-08-01T09:00:00+02:00", BETREFF.replace("8851675", "8800000"))
        _, melden = self._abgleich([alt])
        melden.assert_not_called()                                         # neu erkannt, aber alt: still

    def test_reihenfolge_der_mails_egal(self):
        mails = [_mail(GENEHMIGT, "2026-09-16T09:00:00+02:00"), _mail(RUECKFRAGE, "2026-09-15T20:00:00+02:00"),
                 _mail(EINGANG)]
        for folge in (mails, mails[::-1]):
            f = post.faelle(folge)["8851675"]
            self.assertEqual((f["status"], f["cent"], f["linie"], f["grund"]), ("genehmigt", 500, "360", ""))
            self.assertEqual(f["entschieden_am"], "2026-09-16T09:00:00+02:00")

    def test_statistik_nach_bescheidstag(self):
        zweiter = GENEHMIGT.replace("5,00 Euro", "7,50 Euro")
        self._abgleich([_mail(GENEHMIGT, "2026-09-16T09:00:00+02:00"),
                        _mail(zweiter, "2026-08-20T09:00:00+02:00", BETREFF.replace("8851675", "8851600")),
                        _mail(PLANMAESSIG, betreff=BETREFF.replace("8851675", "8853833"))])
        st = post.statistik()
        self.assertEqual((st["insgesamt_cent"], st["monat_cent"], st["monat_anzahl"], st["genehmigt"],
                          st["abgelehnt"], st["quote"]), (1250, 500, 1, 2, 1, 67))
        self.assertEqual([m["monat"] for m in st["monate"]], ["2026-09", "2026-08"])

    def test_einnahmen_mit_fridays_hilfe(self):
        a = ablage.standard()
        a.antrag_speichern({"reise": "r1", "linie": "360", "start": "Bennigsen Bahnhof", "ziel": "Hemmingen",
                            "an": datetime(2026, 9, 15, 16, 19, tzinfo=BERLIN), "nachgeprueft": True})
        ohne = GENEHMIGT.replace("15.09.2026", "01.09.2026")
        self._abgleich([_mail(EINGANG), _mail(GENEHMIGT, "2026-09-16T09:00:00+02:00"),
                        _mail(ohne, "2026-09-05T09:00:00+02:00", BETREFF.replace("8851675", "8851600"))])
        st = post.statistik()
        self.assertEqual((st["insgesamt_cent"], st["mit_friday_cent"], st["mit_friday_anzahl"]), (1000, 500, 1))

    def test_alter_eingang_ohne_antwort_ist_ohne_bescheid(self):
        self._abgleich([_mail(EINGANG, "2026-06-01T10:00:00+02:00")])
        self.assertEqual(post.oeffentlich(ablage.standard().vorgang("8851675"))["stand"], "ohne_bescheid")
        self.assertEqual(post.statistik()["offen"], 0)

    def test_entwurf_loeschen_abgeschickt_bleibt(self):
        a = ablage.standard()
        entwurf = a.antrag_speichern({"linie": "1", "an": JETZT})
        fertig = a.antrag_speichern({"linie": "2", "an": JETZT})
        a.antrag_aendern(fertig, status="abgeschickt")
        self.assertTrue(a.antrag_loeschen(entwurf))
        self.assertFalse(a.antrag_loeschen(fertig))
        self.assertIsNone(a.antrag(entwurf))
        self.assertIsNotNone(a.antrag(fertig))

    def test_eingang_verknuepft_den_antrag_und_setzt_ihn_auf_abgeschickt(self):
        a = ablage.standard()
        aid = a.antrag_speichern({"reise": "r1", "linie": "360", "start": "Bennigsen Bahnhof", "ziel": "Hemmingen",
                                  "an": datetime(2026, 9, 15, 16, 19, tzinfo=BERLIN), "nachgeprueft": True})
        self._abgleich([_mail(EINGANG)])
        e = antrag.bewerten(a.antrag(aid))
        self.assertEqual((e["status"], e["vorgang"], e["stand"]), ("abgeschickt", "8851675", "eingegangen"))

    def test_zaehler_mit_faellen_aus_gmail_abgelehnte_zaehlen_nicht(self):
        mails = [_mail(EINGANG), _mail(PLANMAESSIG, betreff="ÜSTRA Garantie: Ihr Garantiefall 8853833 vom 27.08.2026"),
                 _mail(EINGANG.replace("8851675", "8851676").replace("15.09.2026", "14.09.2026"),
                       betreff="ÜSTRA Garantie: Ihr Garantiefall 8851676 vom 14.09.2026")]
        self._abgleich(mails)
        z = antrag.zaehler()
        # Nur eingegangen, noch nicht genehmigt: zählt nicht, ist aber offen.
        self.assertEqual((z["monat"], z["dticket"], z["heute"], z["offen"]), (0, 0, 0, 2))
        st = post.statistik()
        self.assertEqual((st["abgelehnt"], st["offen"]), (1, 2))


class GmailNurLesend(unittest.TestCase):
    """Der Dienst sucht nur Mails der ÜSTRA, öffnet nur lesend und holt nur mit PEEK."""

    def test_suche_ordner_und_abruf(self):
        m = mock.MagicMock()
        m.list.return_value = ("OK", [b'(\\HasNoChildren \\All) "/" "[Gmail]/Alle Nachrichten"'])
        m.select.return_value = ("OK", [b"1"])
        roh = b"From: info <info@uestra.de>\r\nSubject: Test\r\nDate: Tue, 15 Sep 2026 17:00:00 +0200\r\n\r\nHallo"

        def uid(befehl, *args):
            if befehl == "SEARCH":
                return "OK", [b"7"]
            return "OK", [(b"7 (BODY[] {1})", roh), b")"]
        m.uid.side_effect = uid
        v = gmail.Verbindung.__new__(gmail.Verbindung)
        v.m = m
        mails = v.uestra_mails()
        m.select.assert_called_once_with('"[Gmail]/Alle Nachrichten"', readonly=True)
        suche = m.uid.call_args_list[0].args
        self.assertEqual(suche[:2], ("SEARCH", "X-GM-RAW"))
        self.assertIn("from:info@uestra.de", suche[2])
        self.assertIn("BODY.PEEK[]", m.uid.call_args_list[1].args[2])
        self.assertEqual(mails[0]["text"], "Hallo")

    def test_allgemeine_suche_nur_lesend_mit_literal(self):
        m = mock.MagicMock()
        m.list.return_value = ("OK", [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"'])
        m.select.return_value = ("OK", [b"1"])
        kopf = b"From: Ann <ann@example.org>\r\nSubject: =?utf-8?q?Gr=C3=BC=C3=9Fe?=\r\nDate: Tue, 15 Sep 2026 17:00:00 +0200\r\n\r\n"
        roh = kopf + b"Hallo Eike"

        def uid(befehl, *args):
            if befehl == "SEARCH":
                return "OK", [b"3 9"]
            if "RFC822.SIZE)" in args[1]:
                return "OK", [b"9 (UID 9 FLAGS () RFC822.SIZE 120)"]
            if "HEADER.FIELDS" in args[1]:
                return "OK", [(b"1 (UID 9 FLAGS (\\Seen) RFC822.SIZE 120 BODY[HEADER.FIELDS (FROM)] {1}", kopf), b")",
                              (b"2 (UID 3 FLAGS () RFC822.SIZE 99 BODY[HEADER.FIELDS (FROM)] {1}", kopf), b")"]
            return "OK", [(b"9 (BODY[] {1})", roh), b")"]
        m.uid.side_effect = uid
        v = gmail.Verbindung.__new__(gmail.Verbindung)
        v.m = m
        treffer = v.suchen('Grüße "von Ann"', 5)
        self.assertEqual(m.uid.call_args_list[0].args, ("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW"))
        self.assertEqual(m.literal, 'Grüße "von Ann"'.encode())
        self.assertEqual([(t["uid"], t["gelesen"], t["betreff"]) for t in treffer],
                         [(9, True, "Grüße"), (3, False, "Grüße")])
        einzeln = v.holen(9)
        self.assertEqual((einzeln["roh"], einzeln["gelesen"]), (roh, False))
        m.select.assert_called_once_with('"[Gmail]/All Mail"', readonly=True)
        for aufruf in m.uid.call_args_list:
            self.assertIn(aufruf.args[0], ("SEARCH", "FETCH"))
            if aufruf.args[0] == "FETCH":
                self.assertNotRegex(aufruf.args[2], r"BODY\[")    # nur PEEK, nie ein Gelesen-Flag
        for name in ("store", "copy", "move", "expunge", "append", "delete"):
            getattr(m, name).assert_not_called()

    def test_werkzeug_liest_eine_mail(self):
        from kern.werkzeuge import gmail as werkzeug
        roh = (b"From: Shop <shop@example.org>\r\nTo: eike@example.org\r\nSubject: Rechnung\r\n"
               b"Date: Tue, 15 Sep 2026 17:00:00 +0200\r\n\r\nBetrag 5 Euro")
        with mock.patch.object(werkzeug, "_holen", return_value={"uid": 4, "roh": roh, "gelesen": False,
                                                                 "groesse": 99, "nur_kopf": False}):
            text = asyncio.run(werkzeug.gmail_mail(4))
        self.assertIn("Betreff: Rechnung", text)
        self.assertIn("Betrag 5 Euro", text)
        with mock.patch.object(werkzeug, "_suchen", side_effect=gmail.AnmeldeFehler("abgewiesen")):
            self.assertEqual(asyncio.run(werkzeug.gmail_mails("from:shop")), "abgewiesen")


# ---------- Stündlicher Scan mit Meldung ----------

def _ab(jid, linie, verspaetung, kategorie="Bus"):
    plan = JETZT - timedelta(minutes=20)
    return {"jid": jid, "linie": linie, "kategorie": kategorie, "richtung": "Hannover", "plan": plan,
            "echt": plan + timedelta(minutes=verspaetung), "verspaetung": verspaetung, "faellt_aus": False,
            "halt": "Bennigsen Bahnhof"}


class Stuendlich(Basis):
    def _einmal(self, gebiet, netz):
        with mock.patch.object(suche, "haltestellen_im_gebiet", mock.AsyncMock(return_value=[{"name": "A", "gid": "g"}])), \
                mock.patch.object(suche, "abtasten", mock.AsyncMock(side_effect=[gebiet, netz])), \
                mock.patch.object(push, "senden", return_value={"gesendet": 1}) as melden:
            asyncio.run(sammler.einmal())
        return melden

    def test_meldet_einmal_je_fahrt_in_eikes_orten(self):
        melden = self._einmal([_ab("J1", "360", 28)], [_ab("J2", "7", 40)])
        melden.assert_called_once()
        self.assertIn("360", melden.call_args.args[0])
        self.assertIn("?garantie=", melden.call_args.args[2])             # öffnet die Fahrt in der App
        self._einmal([_ab("J1", "360", 31)], []).assert_not_called()       # dieselbe Fahrt: kein zweites Mal
        gemerkt = {r["linie"]: r["bereich"] for r in ablage.standard().reisen()}
        self.assertEqual(gemerkt, {"360": "deine Orte", "7": "ganzes Netz"})

    def test_viele_auf_einmal_eine_nachricht(self):
        melden = self._einmal([_ab(f"J{i}", str(300 + i), 25) for i in range(5)], [])
        melden.assert_called_once()
        self.assertIn("5 verspätete Fahrten", melden.call_args.args[0])

    def test_scan_nur_stuendlich(self):
        self.assertEqual(sammler.SCAN_ALLE_S, 3600)
        self.assertLessEqual(sammler.TAKT_S, 300)                          # Nachprüfen bleibt häufig


class AboNummer(unittest.TestCase):
    def test_nur_ziffern_sonst_leer(self):
        from kern.garantie import ticket
        for roh, erwartet in (("1450 920", "1450920"), ("Abo ?", ""), ("", "")):
            antwort = {"ist_fahrkarte": True, "art": "Deutschlandticket", "gueltig_von": "2026-09-01",
                       "gueltig_bis": "2026-09-30", "abo_nummer": roh}
            with mock.patch("kern.schule.briefe._gemini", mock.AsyncMock(return_value=(antwort, "m"))):
                self.assertEqual(asyncio.run(ticket.auslesen("image/png", b"x"))["abo_nummer"], erwartet)


class SucheSortierung(unittest.TestCase):
    def test_fernverkehr_raus_auch_als_zug(self):
        self.assertFalse(suche.beantragbar("Zug", "ICE 1234"))
        self.assertFalse(suche.beantragbar("Zug", "IC 2037"))
        self.assertTrue(suche.beantragbar("S-Bahn", "S5"))
        self.assertTrue(suche.beantragbar("R-Bahn", "RE2"))
        self.assertTrue(suche.beantragbar("Bus", "ICH-Linie"))            # nur ganze Kürzel

    def test_relevanz_nach_eikes_orten(self):
        def f(halt, v, richtung="Hannover"):
            return {"halt": halt, "verspaetung": v, "richtung": richtung}
        liste = [f("Hannover Kröpcke", 50), f("Musterhausen Bahnhof", 25), f("Musterdorf", 21),
                 f("Kröpcke", 30, richtung="Musterhausen Bahnhof"), f("Bennigsen Bahnhof", 15), f("Barsinghausen", 40)]
        reihe = [x["halt"] for x in sorted(liste, key=suche.relevanz)]
        self.assertEqual(reihe, ["Musterdorf", "Kröpcke", "Musterhausen Bahnhof", "Barsinghausen", "Hannover Kröpcke",
                                 "Bennigsen Bahnhof"])


class Freigabe(Basis):
    def test_gmail_konnektor_mit_hilfe(self):
        k = next(k for k in konnektoren.uebersicht() if k["id"] == "gmail")
        self.assertIn("apppasswords", k["hilfeUrl"])
        self.assertFalse(k["aktiv"])
        self.assertFalse(post.benutzbar())

    def test_werkzeuge_hinter_der_freigabe_und_fremd(self):
        from kern.werkzeuge import katalog
        namen = {e["name"] for t in katalog.gemini_tools() for e in t["functionDeclarations"]}
        self.assertNotIn("gmail_mails", namen)
        self.assertTrue(katalog.liefert_fremdes("gmail_mail"))


if __name__ == "__main__":
    unittest.main()
