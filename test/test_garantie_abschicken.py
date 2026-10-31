"""Abschicken eines Garantie-Antrags — nur wenn wirklich alles stimmt. Ohne Netz."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import garantie as formular
from kern.garantie import ablage, abschicken, antrag
from kern.main import app

BERLIN = ZoneInfo("Europe/Berlin")
JETZT = datetime(2026, 9, 16, 18, 0, tzinfo=BERLIN)
AB = datetime(2026, 9, 16, 10, 50, tzinfo=BERLIN)
AN = datetime(2026, 9, 16, 10, 59, tzinfo=BERLIN)
IBAN = "DE89370400440532013000"
LEG = {"lineName": "570", "fromStationName": "Barsinghausen Berliner Straße", "toStationName": "Landringhausen",
       "scheduledFromDateTime": "2026-09-16T08:50:00Z", "scheduledToDateTime": "2026-09-16T08:59:00Z",
       "realToDateTime": "2026-09-16T09:45:00Z", "selectable": True}
FAHRT = {"signedId": None, "signature": "SIG", "entireObjectSigned": False, "legs": [LEG], "selectedLegIndex": None,
         "forwardContext": {}, "backwardContext": {}, "tariffLevels": [], "startStationLocalityRef": None,
         "selectedLeg": None}
ERSTATTUNG = {"refundIdSignature": "REFSIG", "ticketDetails": "Deutschlandticket (Hannover Jugend)",
              "subscriptionCustomerNumberAvailable": True, "ticketNumberAvailable": False}


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=JETZT)
        self.uhr.start()
        konnektoren.setzen("uestra_garantie", True)
        konnektoren.schreiben_setzen("uestra_garantie", True)
        self.a = ablage.standard()
        self.a.angaben_setzen({"anrede": "Herr", "vorname": "Max", "nachname": "Mustermann", "email": "max@example.org",
                               "strasse": "Musterweg 1", "plz": "30159", "ort": "Hannover",
                               "kontoinhaber": "Max Mustermann", "iban": IBAN, "abo_nummer": "1234567"})
        self.ticket = self.a.ticket_speichern("t.png", "image/png", b"bild", {
            "art": "Deutschlandticket", "bezeichnung": "Deutschlandticket Jugend Hannover", "herausgeber": "ÜSTRA",
            "gueltig_von": "2026-09-01", "gueltig_bis": "2026-09-30"})
        self.aid = self.a.antrag_speichern({
            "reise": "r1", "jid": "J", "linie": "570", "start": "Barsinghausen Berliner Straße", "ziel": "Landringhausen",
            "start_ref": "de:1", "ziel_ref": "de:2", "ab": AB, "an": AN, "verspaetung": 46,
            "quelle": "vom GVH gemessen", "faellig_ab": AN + timedelta(minutes=46), "nachgeprueft": True,
            "daten": {"fahrt": FAHRT, "start": {"name": "S", "reference": "de:1"},
                      "ziel": {"name": "Z", "reference": "de:2"}}})
        self.gesendet = []
        async def einreichen(anfrage):
            self.gesendet.append(anfrage)
            return {"id": "999"}
        self.patches = [
            mock.patch.object(antrag, "_im_formular", mock.AsyncMock(return_value=(FAHRT, LEG))),
            mock.patch.object(formular, "fahrkarten_typen", mock.AsyncMock(return_value=["Deutschlandticket (Chipkarte)", "Deutschlandticket"])),
            mock.patch.object(formular, "fahrkarten_details", mock.AsyncMock(return_value=["Deutschlandticket", "Deutschlandticket (Hannover Jugend)"])),
            mock.patch.object(formular, "erstattungsarten", mock.AsyncMock(return_value=[ERSTATTUNG])),
            mock.patch.object(formular, "hochladen", mock.AsyncMock(return_value="ANHANG")),
            mock.patch.object(formular, "einreichen", side_effect=einreichen),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        from test.test_garantie import ablagen_schliessen
        ablagen_schliessen()
        self.uhr.stop()
        self.env.stop()

    def schicken(self):
        return asyncio.run(abschicken.abschicken(self.aid))


class Erfolg(Basis):
    def test_anfrage_wie_im_mitschnitt(self):
        d = self.schicken()
        self.assertTrue(d["ok"], d)
        self.assertEqual(len(self.gesendet), 1)
        q = self.gesendet[0]
        self.assertEqual(q["clientDto"]["formOfAddress"], "MR")
        self.assertEqual(q["clientDto"]["privacyType"], "TEN_MIN")
        p = q["complaintPointDtos"][0]
        self.assertEqual(p["routeDetailsDto"]["delayValue"], 46)
        self.assertEqual(p["routeDetailsDto"]["dateOfTrip"], "2026-09-16")
        self.assertEqual(p["routeDetailsDto"]["route"]["selectedLegIndex"], 0)
        self.assertEqual(p["routeDetailsDto"]["route"]["selectedLeg"], LEG)
        self.assertEqual(p["dateOfEvent"], "2026-09-16T08:50:00.000Z")
        t = p["ticketDto"]
        self.assertEqual((t["refundIdSignature"], t["subscriptionCard"], t["attachmentIds"], t["ticketDetails"],
                          t["expiryDate"]),
                         ("REFSIG", "1234567", ["ANHANG"], "Deutschlandticket (Hannover Jugend)",
                          "2026-09-30T21:59:00.000Z"))
        self.assertEqual(p["bankData"], {"accountOwner": "Max Mustermann", "iban": IBAN})
        e = self.a.antrag(self.aid)
        self.assertEqual((e["status"], e["fahrkarte"]), ("abgeschickt", "Deutschlandticket"))
        # Zweites Mal: nichts mehr
        self.assertFalse(self.schicken()["ok"])
        self.assertEqual(len(self.gesendet), 1)


class NichtsFalsch(Basis):
    def pruefe_nicht_gesendet(self, wort):
        d = self.schicken()
        self.assertFalse(d["ok"])
        self.assertIn(wort, d["text"])
        self.assertEqual(self.gesendet, [])
        formular.hochladen.assert_not_called()
        self.assertEqual(self.a.antrag(self.aid)["status"], "offen")

    def test_ohne_schreib_freigabe(self):
        konnektoren.schreiben_setzen("uestra_garantie", False)
        self.pruefe_nicht_gesendet("Anträge abschicken")

    def test_altes_ticket(self):
        self.a.ticket_korrigieren(self.ticket["id"], {"gueltig_von": "2026-08-01", "gueltig_bis": "2026-08-31"})
        self.pruefe_nicht_gesendet("Ticket")

    def test_unsicher_gelesenes_ticket(self):
        self.a.ticket_merken(self.ticket["id"], zweifel="Datum verschwommen")
        self.pruefe_nicht_gesendet("unsicher")

    def test_falsche_iban(self):
        angaben = self.a.angaben()
        angaben["iban"] = "DE89370400440532013001"
        ablage._schreiben_600(self.a._angabendatei(), __import__("json").dumps(angaben).encode())
        self.pruefe_nicht_gesendet("IBAN")

    def test_ohne_abo_nummer(self):
        angaben = self.a.angaben()
        angaben["abo_nummer"] = ""
        ablage._schreiben_600(self.a._angabendatei(), __import__("json").dumps(angaben).encode())
        self.pruefe_nicht_gesendet("Abo-Kundennummer")

    def test_zu_wenig_verspaetung_laut_formular(self):
        antrag._im_formular.return_value = (FAHRT, {**LEG, "realToDateTime": "2026-09-16T09:15:00Z"})
        self.pruefe_nicht_gesendet("höchstens 20")

    def test_unklare_ticketart(self):
        self.a.ticket_korrigieren(self.ticket["id"], {"art": "Deutschlandticket"})
        tickets = self.a.tickets()
        tickets[0]["bezeichnung"] = "D-Ticket"
        self.a._tickets_schreiben(tickets)
        self.pruefe_nicht_gesendet("nicht eindeutig")

    def _schon_abgeschickt(self, n):
        # n Anträge dieses Monats (andere Tage), abgeschickt und ohne Bescheid.
        for i in range(n):
            tag = timedelta(days=-(i + 1))
            aid = self.a.antrag_speichern({
                "reise": f"alt{i}", "jid": f"J{i}", "linie": "570", "start": "S", "ziel": "Z", "start_ref": "de:1",
                "ziel_ref": "de:2", "ab": AB + tag, "an": AN + tag, "verspaetung": 46, "quelle": "vom GVH gemessen",
                "faellig_ab": AN + tag, "nachgeprueft": True})
            self.a.antrag_aendern(aid, status="abgeschickt")

    def test_hoechstzahl_zaehlt_offene_mit(self):
        # Eike, 15.10.2026: 3 genehmigt + 3 offen ging raus, erlaubt sind 5 — jetzt harte Sperre.
        self._schon_abgeschickt(5)
        self.pruefe_nicht_gesendet("erlaubt sind 5")

    def test_vier_offene_lassen_den_fuenften_zu(self):
        self._schon_abgeschickt(4)
        self.assertTrue(self.schicken()["ok"])

    def test_frist_vorbei(self):
        with mock.patch("kern.config.jetzt", return_value=JETZT + timedelta(days=20)):
            self.pruefe_nicht_gesendet("Frist")

    def test_abgelehnt_vom_formular_bleibt_offen(self):
        formular.einreichen.side_effect = None
        formular.einreichen.return_value = {"fehler": ["IBAN ungültig"]}
        d = self.schicken()
        self.assertFalse(d["ok"])
        self.assertIn("IBAN ungültig", d["text"])
        self.assertEqual(self.a.antrag(self.aid)["status"], "offen")

    def test_unklarer_ausgang_zaehlt_als_abgeschickt(self):
        formular.einreichen.side_effect = formular.UnklarerAusgang("Zeitüberschreitung")
        d = self.schicken()
        self.assertFalse(d["ok"])
        self.assertIn("NICHT noch einmal", d["text"])
        self.assertEqual(self.a.antrag(self.aid)["status"], "abgeschickt")


class Schnittstelle(Basis):
    def test_ohne_bestaetigung_nichts(self):
        c = TestClient(app)
        self.assertEqual(c.post(f"/api/garantie/antrag/{self.aid}/abschicken", json={}).status_code, 400)
        self.assertEqual(self.gesendet, [])

    def test_pruefen_sendet_nichts_und_zeigt_keine_ganze_iban(self):
        c = TestClient(app)
        r = c.get(f"/api/garantie/antrag/{self.aid}/pruefen")
        self.assertTrue(r.json()["ok"], r.json())
        self.assertNotIn("0532013000", r.text)
        self.assertEqual(self.gesendet, [])
        formular.hochladen.assert_not_called()


class Verlaufsspeicher(Basis):
    """Die Auskunft vergisst Echtzeit — Friday nicht (Eike, 16.09.)."""

    def test_echtzeit_bleibt_erhalten(self):
        from kern.dienste import gvh
        plan = datetime(2026, 9, 16, 17, 9, tzinfo=BERLIN)
        f = {"jid": "JX", "linie": "320", "kategorie": "Bus", "richtung": "Musterhausen", "plan": plan,
             "echt": plan + timedelta(minutes=45), "verspaetung": 45, "halt": "Musterhausen"}
        rid = self.a.reise_merken(f)

        def halt(ref, echt):
            return {"name": ref, "reference": ref, "an_plan": plan, "an_echt": echt, "ab_plan": plan,
                    "ab_echt": echt, "verspaetung": 45 if echt else None, "faellt_aus": False}
        mit = {"jid": "JX", "linie": "320", "kategorie": "Bus", "richtung": "Musterhausen",
               "halte": [halt("de:1", plan + timedelta(minutes=45))]}
        ohne = {**mit, "halte": [halt("de:1", None)]}
        with mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=mit)):
            asyncio.run(antrag.verlauf(rid))
        with mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=ohne)):
            v = asyncio.run(antrag.verlauf(rid))
        self.assertEqual(v["halte"][0]["an_echt"], plan + timedelta(minutes=45))
        self.assertTrue(v["halte"][0]["gespeichert"])
        with mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(side_effect=gvh.GvhFehler("weg"))):
            v = asyncio.run(antrag.verlauf(rid))
        self.assertEqual(v["halte"][0]["verspaetung"], 45)

    def test_echtzeit_der_auskunft_genuegt_zum_abschicken(self):
        self.a.antrag_aendern(self.aid, quelle="Echtzeit der GVH-Auskunft (gespeichert)")
        self.assertTrue(self.schicken()["ok"])


class Hochladen(unittest.TestCase):
    def test_kennung_aus_data(self):
        import httpx
        antwort = httpx.Response(200, json={"data": "eyJ.KENNUNG"})
        with mock.patch.object(formular, "_post", mock.AsyncMock(return_value=antwort)):
            self.assertEqual(asyncio.run(formular.hochladen("t.png", "image/png", b"x")), "eyJ.KENNUNG")
        with mock.patch.object(formular, "_post", mock.AsyncMock(return_value=httpx.Response(200, json={"x": 1}))):
            with self.assertRaises(formular.GarantieFehler):
                asyncio.run(formular.hochladen("t.png", "image/png", b"x"))


class Eingangsbestaetigung(Basis):
    def test_bestaetigung_gespeichert_und_mail_ordnet_zu(self):
        formular.einreichen.side_effect = None
        formular.einreichen.return_value = {"id": "8860731", "signiert": "x",
                                            "bestaetigung": "<p>Vielen Dank f&uuml;r Ihren Antrag</p>"}
        self.assertTrue(self.schicken()["ok"])
        e = antrag.bewerten(self.a.antrag(self.aid))
        self.assertEqual((e["vorgang"], e["stand"], e["gesendet"]["id"]), ("8860731", "eingegangen", "8860731"))
        self.assertEqual(e["gesendet"]["bestaetigung"], "Vielen Dank für Ihren Antrag")
        v = self.a.vorgang("8860731")
        self.assertEqual((v["status"], v["antrag"], v["linie"], v["fahrtdatum"]), ("eingang", self.aid, "570", "2026-09-16"))
        # Die Eingangsmail kommt später — ohne Verbindungszeile — und wird trotzdem zugeordnet.
        from kern.garantie import post
        from kern.dienste import push
        mail = {"uid": 1, "datum": "2026-09-16T19:00:00+02:00", "von": "info@uestra.de",
                "betreff": "ÜSTRA Garantie: Ihr Garantiefall 8860731 vom 16.09.2026",
                "text": "vielen Dank für Ihren Antrag … Vorgangsnummer 8860731"}
        with mock.patch.object(post, "_holen", return_value=[mail]), \
                mock.patch.object(push, "senden", return_value={}) as melden:
            asyncio.run(post.abgleich())
        melden.assert_not_called()                     # „eingegangen“ war schon bekannt
        v = self.a.vorgang("8860731")
        self.assertEqual((v["antrag"], v["linie"], v["mails"]), (self.aid, "570", 1))
