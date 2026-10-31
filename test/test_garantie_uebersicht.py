"""Garantie über viele Personen (21.09.2026): Übersicht, Papiertickets und die Widerspruchsprüfung — ohne Netz.

Eike reicht für immer mehr Freunde ein. Geprüft wird hier, dass die Übersicht ehrlich sagt, wer noch kann und wer voll
ist, dass eine Jugendnetzkarte auf Papier erst nach Eikes Vergleich der Nummern rausgeht und dass nichts abgeschickt
wird, was sich widerspricht. Die IBANs sind die bekannten Beispiele aus der Norm, keine Konten.
"""

import asyncio
from datetime import timedelta
from unittest import mock

from fastapi.testclient import TestClient

from kern.dienste import garantie as formular
from kern.garantie import ablage, abschicken, antrag, ticket, uebersicht
from kern.main import app
from kern.schule import briefe
from test import test_garantie as tg
from test import test_garantie_abschicken as ta

LEA = {"name": "Lea", "anrede": "Frau", "vorname": "Lea", "nachname": "Beispiel", "email": "beispiel+lea@example.com",
       "strasse": "Lindenweg 4", "plz": "30449", "ort": "Hannover", "auszahlung": "ich", "anteil": 20, "vollmacht": True,
       "kundenkarte": "123456"}
JUGEND = {"art": "Monatskarte", "bezeichnung": "Jugendnetzkarte", "herausgeber": "GVH", "gueltig_von": "2026-09-01",
          "gueltig_bis": "2026-09-30", "inhaber": "Lea Beispiel", "kundenkarte": "123456", "ticketnummer": "2608127024",
          "preis": 18, "form": "digital"}
JUGEND_REGELN = {"refundIdSignature": "JSIG", "ticketDetails": "Jugendnetzkarte", "customerCardFormat": 6,
                 "ticketNumberAvailable": True, "subscriptionCustomerNumberAvailable": False}


def _gemini(roh):
    return mock.patch.object(briefe, "_gemini", mock.AsyncMock(return_value=(roh, "m")))


class Papier(tg.Basis):
    ROH = {"ist_fahrkarte": True, "art": "Monatskarte", "bezeichnung": "Jugendnetzkarte", "gueltig_von": "2026-09-01",
           "gueltig_bis": "2026-09-30", "kundenkarte": "123456", "ticketnummer": "3612345678",
           "ticketnummer_gedruckt": "36-12.345.678", "form": "papier", "kundenkarte_handschrift": True, "preis_euro": 18}

    def test_papier_wird_erkannt_und_die_nummer_wie_gedruckt_gemerkt(self):
        with _gemini(dict(self.ROH)):
            e = asyncio.run(ticket.auslesen("image/jpeg", b"x"))
        self.assertEqual((e["form"], e["nummer_gedruckt"], e["ticketnummer"], e["kundenkarte_handschrift"]),
                         ("papier", "36-12.345.678", "3612345678", True))
        with _gemini({**self.ROH, "form": "unklar"}):
            self.assertEqual(asyncio.run(ticket.auslesen("image/jpeg", b"x"))["form"], "")

    def test_zwei_lesarten_die_nicht_passen_ergeben_keine_nummer(self):
        with _gemini({**self.ROH, "ticketnummer_gedruckt": "36-12.345.679"}):
            e = asyncio.run(ticket.auslesen("image/jpeg", b"x"))
        self.assertEqual(e["ticketnummer"], "")
        self.assertIn("nicht eindeutig", e["zweifel"])
        # Gegenprobe: Gedruckt nur mit Trennzeichen anders — dieselbe Nummer.
        with _gemini(dict(self.ROH)):
            self.assertEqual(asyncio.run(ticket.auslesen("image/jpeg", b"x"))["ticketnummer"], "3612345678")

    def test_die_zahlenzeile_unten_auf_der_wertmarke(self):
        # So sieht die Papier-Jugendnetzkarte aus (Foto vom 21.09.2026): unten „63256   713801   15.09.26   07:46“.
        roh = {**self.ROH, "ticketnummer": "63256713801", "ticketnummer_gedruckt": "63256 713801"}
        with _gemini(roh):
            e = asyncio.run(ticket.auslesen("image/jpeg", b"x"))
        self.assertEqual((e["ticketnummer"], e["nummer_gedruckt"]), ("63256713801", "63256 713801"))
        # Nimmt das Modell Datum und Uhrzeit mit in die gedruckte Zeile, passt das nicht zur Nummer — dann lieber keine.
        with _gemini({**roh, "ticketnummer_gedruckt": "63256 713801 15.09.26 07:46"}):
            self.assertEqual(asyncio.run(ticket.auslesen("image/jpeg", b"x"))["ticketnummer"], "")

    def test_die_kundenkarte_allein_ist_kein_ticket(self):
        with _gemini({**self.ROH, "ist_kundenkarte": True, "gueltig_bis": "2028-07-31", "art": "Sonstige"}):
            e = asyncio.run(ticket.auslesen("image/jpeg", b"x"))
        self.assertEqual((e["gueltig_von"], e["gueltig_bis"], e["ticketnummer"], e["nummer_gedruckt"]), ("", "", "", ""))
        self.assertEqual(e["kundenkarte"], "123456")
        self.assertTrue(e["ist_kundenkarte"])
        a = ablage.standard()
        a.ticket_speichern("k.jpg", "image/jpeg", b"karte", e)
        self.assertIsNone(a.ticket_fuer(tg._t(9).date()))                                    # gilt an keinem Tag

    def test_nummer_nur_buchstaben_und_ziffern(self):
        self.assertEqual(ticket.nummer("36-12.345.678"), "3612345678")
        self.assertEqual(ticket.nummer(" 2608 127 024 "), "2608127024")
        self.assertEqual(ticket.nummer("36/123.456.789"), "36123456789")

    def test_wann_verglichen_werden_muss(self):
        papier = {**JUGEND, "form": "papier"}
        self.assertEqual(ticket.muss_verglichen_werden(papier), "Papierticket")
        self.assertEqual(ticket.muss_verglichen_werden({**papier, "nummern_geprueft": True}), "")
        self.assertEqual(ticket.muss_verglichen_werden(JUGEND), "")                           # App-Screenshot
        self.assertIn("unklar", ticket.muss_verglichen_werden({**JUGEND, "form": ""}))         # z. B. vor dem 21.09. gelesen
        self.assertEqual(ticket.muss_verglichen_werden({**JUGEND, "form": "", "nummern_geprueft": True}), "")
        self.assertEqual(ticket.muss_verglichen_werden({**JUGEND, "kundenkarte_handschrift": True}),
                         "handschriftliche Kundenkartennummer")
        self.assertEqual(ticket.muss_verglichen_werden({**tg.SEPTEMBER, "form": "papier"}), "")   # keine Nummern im Formular
        self.assertEqual(ticket.muss_verglichen_werden(None), "")


class Korrektur(tg.Basis):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)
        self.t = ablage.standard().ticket_speichern("j.jpg", "image/jpeg", b"jugend", {**JUGEND, "form": "papier"})

    def korrigieren(self, **felder):
        return self.c.post(f"/api/garantie/ticket/{self.t['id']}", json=felder)

    def ticket(self):
        return ablage.standard().tickets()[0]

    def test_vergleich_gilt_nur_fuer_die_gesehenen_nummern(self):
        self.assertFalse(self.ticket()["nummern_geprueft"])                                   # neu hochgeladen: nie verglichen
        self.assertEqual(self.korrigieren(nummern_geprueft=True).status_code, 200)
        self.assertTrue(self.ticket()["nummern_geprueft"])
        self.korrigieren(preis=18)                                                            # anderes Feld: bleibt verglichen
        self.assertTrue(self.ticket()["nummern_geprueft"])
        self.korrigieren(ticketnummer="36-12.345.678")                                        # Nummer geändert, kein Haken
        self.assertEqual((self.ticket()["ticketnummer"], self.ticket()["nummern_geprueft"]), ("3612345678", False))
        self.korrigieren(kundenkarte="654321", nummern_geprueft=True)                         # geändert UND verglichen
        self.assertTrue(self.ticket()["nummern_geprueft"])

    def test_name_und_form_lassen_sich_berichtigen(self):
        self.assertEqual(self.korrigieren(inhaber="Lea Beispiel", form="digital").status_code, 200)
        self.assertEqual((self.ticket()["inhaber"], self.ticket()["form"]), ("Lea Beispiel", "digital"))
        self.assertEqual(self.korrigieren(form="Pergament").status_code, 422)

    def test_ticket_ansehen(self):
        r = self.c.get(f"/api/garantie/ticket/{self.t['id']}/datei")
        self.assertEqual((r.status_code, r.content, r.headers["content-type"]), (200, b"jugend", "image/jpeg"))
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(self.c.get("/api/garantie/ticket/gibtsnicht/datei").status_code, 404)


class NamePasst(tg.Basis):
    def test_namen(self):
        passt = abschicken.name_passt
        self.assertTrue(passt("Lea Beispiel", "Lea", "Beispiel"))
        self.assertTrue(passt("BEISPIEL, LEA", "Lea", "Beispiel"))
        self.assertTrue(passt("L. Beispiel", "Lea", "Beispiel"))
        self.assertTrue(passt("Beispiel", "Lea", "Beispiel"))
        self.assertTrue(passt("Lea Marie Beispiel", "Lea Marie", "Beispiel"))
        self.assertTrue(passt("Jörg Müller", "Jörg", "Mueller"))
        self.assertTrue(passt("", "Lea", "Beispiel"))                                        # nichts zu vergleichen
        self.assertFalse(passt("Tom Beispiel", "Lea", "Beispiel"))                          # Geschwister
        self.assertFalse(passt("Lea Anders", "Lea", "Beispiel"))
        self.assertFalse(passt("T. Beispiel", "Lea", "Beispiel"))


class Widersprueche(ta.Basis):
    """Lea mit Jugendnetzkarte; das Formular verlangt Kundenkarte und Ticketnummer."""

    def setUp(self):
        super().setUp()
        self.fid = self.a.profil_anlegen(dict(LEA))
        self.t = self.a.ticket_speichern("l.png", "image/png", b"leas jugendnetzkarte", dict(JUGEND), self.fid)
        self.faid = self.antrag(self.fid, "r2")
        formular.fahrkarten_typen.return_value = ["Monatskarte SchülerInnen & Azubis"]
        formular.fahrkarten_details.return_value = ["Jugendnetzkarte"]
        formular.erstattungsarten.return_value = [JUGEND_REGELN]

    def antrag(self, pid, reise, ab=ta.AB, an=ta.AN, status=None):
        aid = self.a.antrag_speichern({
            "profil": pid, "reise": reise, "jid": "J", "linie": "570", "start": "Barsinghausen Berliner Straße",
            "ziel": "Landringhausen", "start_ref": "de:1", "ziel_ref": "de:2", "ab": ab, "an": an, "verspaetung": 46,
            "quelle": "vom GVH gemessen", "faellig_ab": an + timedelta(minutes=46), "nachgeprueft": True,
            "daten": {"fahrt": ta.FAHRT, "start": {"name": "S", "reference": "de:1"}, "ziel": {"name": "Z", "reference": "de:2"}}})
        if status:
            self.a.antrag_aendern(aid, status=status)
        return aid

    def pruefen(self, aid=None):
        return asyncio.run(abschicken.pruefen(aid or self.faid))

    def gesperrt(self, wort, aid=None):
        p = self.pruefen(aid)
        self.assertFalse(p["ok"], p)
        self.assertTrue(any(wort in x for x in p["probleme"]), p["probleme"])
        return p

    def test_grundfall_geht(self):
        p = self.pruefen()
        self.assertTrue(p["ok"], p["probleme"])
        self.assertIn("Ticketnummer: 2608127024", p["punkte"])

    def test_papier_erst_nach_dem_vergleich(self):
        self.a.ticket_korrigieren(self.t["id"], {"form": "papier"})
        self.gesperrt("Papierticket")
        self.a.ticket_korrigieren(self.t["id"], {"nummern_geprueft": True})
        self.assertTrue(self.pruefen()["ok"])
        # Wird eine Nummer danach geändert, gilt der Vergleich nicht mehr.
        self.a.ticket_korrigieren(self.t["id"], {"ticketnummer": "2608127025"})
        self.gesperrt("Papierticket")

    def test_papier_zeigt_der_antrag_schon_vorher(self):
        self.assertEqual(antrag.bewerten(self.a.antrag(self.faid))["stand"], "bereit")
        self.a.ticket_korrigieren(self.t["id"], {"form": "papier"})
        b = antrag.bewerten(self.a.antrag(self.faid))
        self.assertEqual((b["stand"], b["fehlt"]), ("unvollständig", ["Nummern mit dem Ticket vergleichen"]))

    def test_schickt_papier_nicht_ab(self):
        self.a.ticket_korrigieren(self.t["id"], {"form": "papier"})
        d = asyncio.run(abschicken.abschicken(self.faid))
        self.assertFalse(d["ok"])
        self.assertEqual(self.gesendet, [])

    def test_kundenkarte_auf_dem_ticket_widerspricht_der_person(self):
        self.a.ticket_korrigieren(self.t["id"], {"kundenkarte": "654321"})
        self.gesperrt("eine davon ist falsch")

    def test_eine_kundenkarte_bei_zwei_personen(self):
        self.a.angaben_setzen({"kundenkarte": "123456"})                                   # Eike hat dieselbe eingetragen
        self.gesperrt("gehört auch zu dir")

    def test_eine_ticketnummer_bei_zwei_personen(self):
        tom = self.a.profil_anlegen({**LEA, "name": "Tom", "vorname": "Tom", "email": "beispiel+tom@example.com", "kundenkarte": "777777"})
        self.a.ticket_speichern("t.png", "image/png", b"toms bild", {**JUGEND, "inhaber": "Tom Beispiel", "kundenkarte": "777777"}, tom)
        self.gesperrt("auch bei einem Ticket von Tom")

    def test_die_nummer_vom_vormonat(self):
        self.a.ticket_speichern("aug.png", "image/png", b"august", {**JUGEND, "gueltig_von": "2026-08-01",
                                                                    "gueltig_bis": "2026-08-31"}, self.fid)
        self.gesperrt("dieselbe wie beim Ticket bis 31.08.2026")
        self.a.ticket_korrigieren(self.t["id"], {"nummern_geprueft": True})                  # Eike hat hingesehen
        self.assertTrue(self.pruefen()["ok"])

    def test_monatskarte_die_monate_gilt(self):
        self.a.ticket_korrigieren(self.t["id"], {"gueltig_bis": "2026-12-31"})
        self.gesperrt("länger als ein Monat")

    def test_zwei_fahrten_zur_selben_zeit(self):
        self.antrag(self.fid, "r3", ab=ta.AB + timedelta(minutes=5), an=ta.AN + timedelta(minutes=5))
        self.gesperrt("Zur selben Zeit")

    def test_fahrten_nacheinander_sind_kein_widerspruch(self):
        self.antrag(self.fid, "r3", ab=ta.AN, an=ta.AN + timedelta(minutes=20))              # Umstieg an der Ankunft
        self.antrag(self.fid, "r4", ab=ta.AB, an=ta.AN, status="verworfen")                  # verworfen zählt nicht
        self.assertTrue(self.pruefen()["ok"], self.pruefen()["probleme"])

    def test_fahrt_liegt_schon_als_fall_bei_der_uestra(self):
        # Eike hat dieselbe Fahrt selbst auf der Webseite eingereicht (Fall ohne Antrag gehört ihm).
        self.a.vorgang_speichern({"vorgang": "4711001", "fahrtdatum": "2026-09-16", "linie": "570", "ab": "10:50",
                                  "status": "eingang", "letzte_mail": "2026-09-16T12:00:00+00:00"})
        self.gesperrt("Fall 4711001", self.aid)
        self.assertTrue(self.pruefen()["ok"])                                                # Lea betrifft das nicht

    def test_uhrzeit_ohne_fuehrende_null(self):
        self.assertEqual(abschicken._uhrzeit("9:05"), abschicken._uhrzeit("09:05"))
        self.assertIsNone(abschicken._uhrzeit(""))

    def test_name_auf_dem_ticket_auch_bei_eike(self):
        self.a.ticket_korrigieren(self.ticket["id"], {"inhaber": "Lea Beispiel"})            # Leas Ticket unter „Ich“
        self.gesperrt("passt nicht", self.aid)

    def test_geschwister_mit_gleichem_nachnamen(self):
        self.a.ticket_korrigieren(self.t["id"], {"inhaber": "Tom Beispiel"})
        self.gesperrt("passt nicht")

    def test_abtippen_warnt_mit_denselben_gruenden(self):
        self.assertEqual(abschicken.abtippen(self.faid)["warnungen"], [])
        self.a.ticket_korrigieren(self.t["id"], {"kundenkarte": "654321"})
        self.assertTrue(any("eine davon ist falsch" in w for w in abschicken.abtippen(self.faid)["warnungen"]))

    def test_sperre_wenn_offene_antraege_die_hoechstzahl_fuellen(self):
        for reise, stunde in (("r5", 7), ("r6", 8)):
            ab = ta.AB.replace(hour=stunde, minute=0)
            self.antrag(self.fid, reise, ab=ab, an=ab + timedelta(minutes=20), status="abgeschickt")
        p = self.pruefen()
        self.assertFalse(p["ok"])                                    # seit 15.10.2026 zählen offene mit: harte Sperre
        self.assertTrue(any("erlaubt sind" in x for x in p["probleme"]), p["probleme"])


class Uebersicht(ta.Basis):
    def setUp(self):
        super().setUp()
        self.fid = self.a.profil_anlegen(dict(LEA))
        self.t = self.a.ticket_speichern("l.png", "image/png", b"leas jugendnetzkarte", dict(JUGEND), self.fid)

    def genehmigt(self, pid_antrag, tage, cent=500, fahrkarte="Deutschlandticket"):
        for i, t in enumerate(tage):
            self.a.vorgang_speichern({"vorgang": f"8{pid_antrag or 0}{i:05d}", "fahrtdatum": t, "status": "genehmigt", "cent": cent,
                                      "antrag": pid_antrag, "fahrkarte": fahrkarte, "letzte_mail": "2026-09-16T10:00:00+00:00"})

    def person(self, pid):
        return uebersicht.person(pid)

    def test_eike_frei_dann_heute_voll_dann_monat_voll(self):
        p = self.person(0)
        self.assertEqual((p["ampel"], p["zaehler"]["frei_monat"], p["zaehler"]["grenze"]), ("frei", 5, 5))   # D-Ticket: 5
        self.genehmigt(None, ["2026-09-16", "2026-09-16"])
        self.assertEqual(self.person(0)["ampel"], "heute_voll")
        for i, t in enumerate(["2026-09-01", "2026-09-02", "2026-09-03"]):
            self.a.vorgang_speichern({"vorgang": f"81{i:05d}", "fahrtdatum": t, "status": "genehmigt", "cent": 500,
                                      "fahrkarte": "Deutschlandticket", "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.assertEqual(self.person(0)["ampel"], "voll")
        self.assertEqual(self.person(self.fid)["ampel"], "frei")                             # Leas Zähler sind ihre eigenen

    def test_offene_antraege_ziehen_von_frei_ab(self):
        self.a.antrag_speichern({"reise": "x", "linie": "5", "start": "A", "ziel": "B", "ab": ta.AB.replace(hour=7),
                                 "an": ta.AB.replace(hour=7, minute=30), "verspaetung": 30, "hindernisse": []})
        e = self.a.antraege(1, 0)[0]
        self.a.antrag_aendern(e["id"], status="abgeschickt")
        z = self.person(0)["zaehler"]
        self.assertEqual((z["monat_rest"], z["offen_monat"], z["frei_monat"], z["offen_heute"]), (5, 1, 4, 1))

    def abgeschickt(self, pid, stunde):
        aid = self.a.antrag_speichern({"profil": pid, "reise": f"x{stunde}", "linie": "5", "ab": ta.AB.replace(hour=stunde),
                                       "an": ta.AB.replace(hour=stunde, minute=30), "hindernisse": []})
        self.a.antrag_aendern(aid, status="abgeschickt")
        return aid

    def test_rest_wartet_auf_bescheide(self):
        for i, t in enumerate(["2026-09-01", "2026-09-02", "2026-09-03"]):
            self.a.vorgang_speichern({"vorgang": f"82{i:05d}", "fahrtdatum": t, "status": "genehmigt", "cent": 500,
                                      "fahrkarte": "Deutschlandticket", "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.abgeschickt(0, 6)
        self.assertEqual(self.person(0)["ampel"], "frei")
        self.abgeschickt(0, 7)                                                               # 3 genehmigt + 2 offen = 5
        p = self.person(0)
        self.assertEqual((p["ampel"], p["zaehler"]["monat_rest"], p["zaehler"]["frei_monat"]), ("wartet", 2, 0))

    def test_antrag_mit_bescheid_ist_nicht_offen(self):
        aid = self.abgeschickt(self.fid, 6)
        self.assertEqual(self.person(self.fid)["zaehler"]["offen_monat"], 1)
        self.a.vorgang_speichern({"vorgang": "8300001", "fahrtdatum": "2026-09-16", "status": "genehmigt", "cent": 500,
                                  "antrag": aid, "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.assertEqual(self.person(self.fid)["zaehler"]["offen_monat"], 0)
        self.assertEqual(antrag._offen(self.fid), 0)

    def test_kartenpreis_begrenzt_was_noch_frei_ist(self):
        # Lea: Jugendnetzkarte für 18 € — nach drei Fahrten (15 €) bringt die vierte nur noch 3 €.
        aid = self.a.antrag_speichern({"profil": self.fid, "reise": "y", "linie": "5", "ab": ta.AB, "an": ta.AN, "hindernisse": []})
        self.genehmigt(aid, ["2026-09-01", "2026-09-02", "2026-09-03"], fahrkarte="Monatskarte, Jugendnetzkarte")
        z = self.person(self.fid)["zaehler"]
        self.assertEqual((z["preis_cent"], z["preis_rest_cent"], z["frei_monat"]), (1800, 300, 1))
        self.genehmigt(aid, ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"], fahrkarte="Monatskarte")   # jetzt 20 €
        self.assertEqual(self.person(self.fid)["ampel"], "voll")

    def test_was_eine_person_aufhaelt(self):
        ohne = self.a.profil_anlegen({**LEA, "name": "Tom", "vorname": "Tom", "email": "beispiel+tom@example.com",
                                      "kundenkarte": "777777", "vollmacht": False})
        p = self.person(ohne)
        self.assertEqual(p["ampel"], "fehlt")
        self.assertIn("Ticket für September fehlt", p["gruende"])
        self.assertIn("Zustimmung fehlt", p["gruende"])
        self.a.ticket_korrigieren(self.t["id"], {"form": "papier"})
        self.assertTrue(any("Nummern vergleichen" in g for g in self.person(self.fid)["gruende"]))

    def test_alle_mit_archivierten_nur_als_name(self):
        tom = self.a.profil_anlegen({**LEA, "name": "Tom", "archiviert": True})
        liste = uebersicht.alle()
        self.assertEqual([p["id"] for p in liste], [0, self.fid, tom])
        self.assertEqual(liste[2], {"id": tom, "name": "Tom", "archiviert": True, "ampel": "archiviert"})

    def test_aufgaben_ueber_alle_personen(self):
        faid = self.a.antrag_speichern({
            "profil": self.fid, "reise": "r9", "jid": "J", "linie": "570", "start": "A", "ziel": "B", "start_ref": "de:1",
            "ziel_ref": "de:2", "ab": ta.AB - timedelta(days=10), "an": ta.AN - timedelta(days=10), "verspaetung": 40,
            "quelle": "vom GVH gemessen", "faellig_ab": ta.AN - timedelta(days=10), "nachgeprueft": True})
        auf = uebersicht.aufgaben()
        self.assertEqual([(e["id"], e["person"]) for e in auf["bereit"]], [(faid, "Lea"), (self.aid, "Ich")])  # knappste Frist zuerst
        self.assertEqual(auf["bereit"][0]["frist_tage"], 4)
        self.a.vorgang_speichern({"vorgang": "5550001", "status": "rueckfrage", "antrag": faid, "grund": "Fahrkarte fehlt",
                                  "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.assertEqual([(r["vorgang"], r["person"]) for r in uebersicht.aufgaben()["rueckfragen"]], [("5550001", "Lea")])

    def test_fuer_wen_geht_die_fahrt(self):
        rid = self.a.reise_merken({"jid": "JID-X", "plan": ta.AB, "linie": "570", "kategorie": "Bus", "richtung": "Z",
                                   "halt": "S", "echt": ta.AB + timedelta(minutes=30), "verspaetung": 30})
        tom = self.a.profil_anlegen({**LEA, "name": "Tom", "vorname": "Tom", "email": "beispiel+tom@example.com", "kundenkarte": "777777"})
        self.a.profil_anlegen({**LEA, "name": "Alt", "archiviert": True})
        self.genehmigt(None, ["2026-09-16", "2026-09-16"])                                   # Eike: Tag voll
        self.a.antrag_speichern({"profil": self.fid, "reise": rid, "linie": "570", "ab": ta.AB, "an": ta.AN, "hindernisse": []})
        je = {p["name"]: p for p in uebersicht.fuer_fahrt(rid)}
        self.assertEqual(set(je), {"Ich", "Lea", "Tom"})                                     # Archivierte nicht
        self.assertFalse(je["Ich"]["geht"])
        self.assertTrue(any("2 Fahrten genehmigt" in g for g in je["Ich"]["gruende"]), je["Ich"])
        self.assertFalse(je["Lea"]["geht"])
        self.assertEqual(je["Lea"]["antrag"], self.a.antrag_zur_reise(rid, self.fid)["id"])
        self.assertTrue(je["Tom"]["geht"])
        self.assertIn("kein Ticket für den 16.09.", je["Tom"]["warnungen"])
        self.assertEqual(uebersicht.fuer_fahrt(rid)[0]["id"], 0)                             # Eike steht oben
        self.assertTrue(tom)

    def test_schnittstellen(self):
        c = TestClient(app)
        r = c.get("/api/garantie/uebersicht")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual([p["name"] for p in d["personen"]], ["Ich", "Lea"])
        self.assertIn("bereit", d["aufgaben"])
        self.assertNotIn("0532013000", r.text)                                               # keine IBAN
        self.assertNotIn("Lindenweg", r.text)                                                # keine Adressen
        self.assertEqual(c.get("/api/garantie/fahrt/personen?id=gibtsnicht").status_code, 404)


class RueckfrageAbhaken(ta.Basis):
    """Eine alte Rückfrage (Eike, 21.09.: „die ist alt und nicht mehr relevant“) lässt sich abhaken — bis eine neue Mail kommt."""

    FALL = {"vorgang": "8851171", "fahrtdatum": "2026-09-10", "linie": "S5", "status": "rueckfrage",
            "grund": "nicht vollständig korrekte Angaben", "letzte_mail": "2026-09-12T21:16:00+02:00", "mails": 2}

    def setUp(self):
        super().setUp()
        self.c = TestClient(app)
        self.a.vorgang_speichern(dict(self.FALL))

    def abhaken(self, ja=True, nummer="8851171"):
        return self.c.post(f"/api/garantie/vorgang/{nummer}/erledigt", json={"erledigt": ja})

    def test_abhaken_blendet_aus_und_zaehlt_nicht_mehr_als_offen(self):
        self.assertEqual([r["vorgang"] for r in uebersicht.aufgaben()["rueckfragen"]], ["8851171"])
        self.assertEqual(antrag._offen(0), 1)
        r = self.abhaken()
        self.assertEqual((r.status_code, r.json()["stand"]), (200, "erledigt"))
        self.assertEqual(uebersicht.aufgaben()["rueckfragen"], [])
        self.assertEqual(antrag._offen(0), 0)
        self.assertEqual(self.abhaken(False).json()["stand"], "rueckfrage")                  # zurückholen geht
        self.assertEqual(len(uebersicht.aufgaben()["rueckfragen"]), 1)

    def test_neue_mail_holt_die_rueckfrage_zurueck_und_der_abgleich_behaelt_den_haken(self):
        from kern.garantie import post
        self.abhaken()
        with mock.patch.object(post, "_holen", return_value=[]), \
             mock.patch.object(post, "faelle", return_value={"8851171": dict(self.FALL)}):
            asyncio.run(post.abgleich())
        self.assertEqual(post.anzeige_stand(self.a.vorgang("8851171")), "erledigt")        # gleiche Mails: bleibt abgehakt
        neuer = {**self.FALL, "letzte_mail": "2026-09-16T09:00:00+02:00", "mails": 3}
        with mock.patch.object(post, "_holen", return_value=[]), \
             mock.patch.object(post, "faelle", return_value={"8851171": neuer}):
            asyncio.run(post.abgleich())
        self.assertEqual(post.anzeige_stand(self.a.vorgang("8851171")), "rueckfrage")      # neue Mail: wieder offen

    def test_nur_rueckfragen_und_nur_vorhandene(self):
        self.a.vorgang_speichern({**self.FALL, "vorgang": "8851172", "status": "genehmigt"})
        self.assertEqual(self.abhaken(nummer="8851172").status_code, 409)
        self.assertEqual(self.abhaken(nummer="9999999").status_code, 404)


class GemerkteFahrten(ta.Basis):
    def test_nur_die_letzten_48_stunden(self):
        for jid, stunden in (("NEU", 3), ("GESTERN", 30), ("ALT", 50), ("SEHR-ALT", 24 * 6)):
            plan = ta.JETZT - timedelta(hours=stunden)
            self.a.reise_merken({"jid": jid, "plan": plan, "linie": "120", "kategorie": "Bus", "richtung": "Z", "halt": "S",
                                 "echt": plan + timedelta(minutes=30), "verspaetung": 30})
        reisen = TestClient(app).get("/api/garantie/uebersicht").json()["reisen"]
        self.assertEqual(len(reisen), 2)
        self.assertEqual(len(self.a.reisen()), 4)                                            # gespeichert bleibt alles (Anträge)
