"""Profile für Freunde in der ÜSTRA-Garantie — ohne Netz, ohne Formular.

Wichtigste Regel, die hier geprüft wird: Was für einen Freund eingereicht wird, geht nur mit SEINEN
Daten raus, zählt nur für SEINE Höchstzahlen — und Eikes eigene Angaben bleiben, wie sie waren.
Die IBANs sind die bekannten Beispiele aus der Norm, keine Konten.
"""

import asyncio
import json
from datetime import timedelta
from unittest import mock

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import garantie as formular
from kern.dienste import gvh
from kern.garantie import ablage, abschicken, antrag, post, profile, ticket
from kern.main import app
from test import test_garantie as tg
from test import test_garantie_abschicken as ta

FREUND_IBAN = "DE02120300000000202051"
LEA = {"name": "Lea", "anrede": "Frau", "vorname": "Lea", "nachname": "Beispiel", "email": "lea@example.net",
       "strasse": "Lindenweg 4", "plz": "30449", "ort": "Hannover", "abo_nummer": "7654321", "anteil": 30,
       "auszahlung": "ich", "vollmacht": True}


class Ablage(tg.Basis):
    """Getrennte Angaben, Tickets, Anträge und Zähler je Person."""

    def setUp(self):
        super().setUp()
        self.a = ablage.standard()
        self.a.angaben_setzen({"anrede": "Herr", "vorname": "Max", "nachname": "Mustermann", "email": "max@example.org",
                               "strasse": "Musterweg 1", "plz": "30159", "ort": "Hannover",
                               "kontoinhaber": "Max Mustermann", "iban": tg.BEISPIEL_IBAN})
        self.fid = self.a.profil_anlegen(dict(LEA))

    def test_eikes_angaben_bleiben_unberuehrt(self):
        vorher = self.a.angaben()
        self.a.angaben_setzen({"vorname": "Lea2", "iban": FREUND_IBAN}, self.fid)
        self.assertEqual(self.a.angaben(), vorher)
        self.assertEqual(self.a.angaben(self.fid, roh=True)["vorname"], "Lea2")

    def test_konto_je_nach_auszahlung(self):
        self.assertEqual((self.a.angaben(self.fid)["kontoinhaber"], self.a.angaben(self.fid)["iban"]),
                         ("Max Mustermann", tg.BEISPIEL_IBAN))                            # „ich“: Eikes Konto
        self.assertNotIn("iban", self.a.angaben(self.fid, roh=True))                      # am Profil selbst keins
        self.a.profil_aendern(self.fid, {"auszahlung": "eigenes", "kontoinhaber": "Lea Beispiel", "iban": FREUND_IBAN})
        self.assertEqual(self.a.angaben(self.fid)["iban"], FREUND_IBAN)

    def test_leere_iban_behaelt_die_gespeicherte(self):
        self.a.profil_aendern(self.fid, {"iban": FREUND_IBAN})
        self.a.profil_aendern(self.fid, {"iban": ""})
        self.assertEqual(self.a.profil(self.fid)["iban"], FREUND_IBAN)

    def test_pflichtangaben_je_profil(self):
        self.assertEqual(self.a.fehlende_angaben(self.fid), [])                           # Konto kommt von Eike
        neu = self.a.profil_anlegen({"name": "Tom"})
        self.assertIn("nachname", self.a.fehlende_angaben(neu))
        self.assertEqual(self.a.fehlende_angaben(), [])                                   # Eike ist vollständig

    def test_sicher_fuer_den_browser_ohne_iban(self):
        self.a.profil_aendern(self.fid, {"auszahlung": "eigenes", "iban": FREUND_IBAN})
        s = self.a.angaben_sicher(self.fid)
        self.assertNotIn("iban", s)
        self.assertNotIn("0000202051", json.dumps(s))
        self.assertTrue(s["iban_verdeckt"].endswith("2051"))
        self.assertEqual((s["name"], s["anteil"], s["vollmacht"]), ("Lea", 30, True))

    def test_tickets_getrennt(self):
        eike = self.a.ticket_speichern("a.png", "image/png", b"gleiches bild", dict(tg.SEPTEMBER))
        lea = self.a.ticket_speichern("a.png", "image/png", b"gleiches bild", dict(tg.SEPTEMBER), self.fid)
        self.assertNotEqual(eike["id"], lea["id"])                       # dasselbe Bild bei einem Freund ist ein eigenes Ticket
        self.assertEqual([t["id"] for t in self.a.tickets()], [eike["id"]])
        self.assertEqual([t["id"] for t in self.a.tickets(self.fid)], [lea["id"]])
        self.assertEqual(self.a.ticket_fuer("2026-09-10", self.fid)["id"], lea["id"])
        self.assertIsNone(self.a.ticket_fuer("2026-10-10", self.fid))
        self.a.ticket_korrigieren(lea["id"], {"gueltig_bis": "2026-09-20"})
        self.assertEqual(self.a.ticket()["gueltig_bis"], "2026-09-30")   # Eikes blieb

    def test_eikes_alte_ticket_kennungen_bleiben(self):
        import hashlib
        t = self.a.ticket_speichern("a.png", "image/png", b"bild", dict(tg.SEPTEMBER))
        self.assertEqual(t["id"], hashlib.sha1(b"bild").hexdigest()[:10])
        self.assertEqual(t["profil"], 0)

    def test_antraege_und_eine_fahrt_je_person(self):
        e = {"reise": "r1", "linie": "360", "start": "A", "ziel": "B", "ab": tg._t(8), "an": tg._t(9)}
        eike = self.a.antrag_speichern(e)
        lea = self.a.antrag_speichern({**e, "profil": self.fid})
        self.assertEqual([x["id"] for x in self.a.antraege()], [eike])
        self.assertEqual([x["id"] for x in self.a.antraege(50, self.fid)], [lea])
        self.assertEqual(len(self.a.antraege(50, None)), 2)
        self.assertEqual(self.a.antrag_zur_reise("r1")["id"], eike)                       # im selben Bus: je ein Antrag
        self.assertEqual(self.a.antrag_zur_reise("r1", self.fid)["id"], lea)
        self.assertIsNone(self.a.antrag_zur_reise("r1", 99))

    def test_hoechstzahlen_gelten_je_person(self):
        an = tg._t(9, tag=15)
        aid = self.a.antrag_speichern({"reise": "r", "linie": "5", "start": "A", "ziel": "B", "ab": tg._t(8), "an": an,
                                       "profil": self.fid})
        for nr in range(2):
            self.a.vorgang_speichern({"vorgang": f"88000{nr}", "fahrtdatum": "2026-09-15", "status": "genehmigt", "cent": 500,
                                      "antrag": aid if nr == 0 else None, "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.assertEqual(len(antrag._genehmigt(profil=self.fid)), 1)                       # nur Leas Fall
        self.assertEqual(len(antrag._genehmigt()), 1)                                      # Eikes eigener (ohne Antrag)
        self.assertEqual((antrag.zaehler(self.fid)["heute"], antrag.zaehler()["heute"]), (1, 1))
        # zwei genehmigte für Lea an einem Tag → für Lea Limit, für Eike nicht
        self.a.vorgang_speichern({"vorgang": "880009", "fahrtdatum": "2026-09-15", "status": "genehmigt", "cent": 500,
                                  "antrag": aid, "letzte_mail": "2026-09-16T11:00:00+00:00"})
        self.assertTrue(any("2 Fahrten genehmigt" in h or "schon 2" in h for h in antrag._limits(an, profil=self.fid)))
        self.assertEqual(antrag._limits(an), [])

    def test_bewerten_nimmt_ticket_und_angaben_der_person(self):
        self.a.ticket_speichern("t.png", "image/png", b"eike", dict(tg.SEPTEMBER))
        e = {"reise": "r", "linie": "5", "start": "A", "ziel": "B", "ab": tg._t(8), "an": tg._t(9), "verspaetung": 30,
             "faellig_ab": tg._t(9, 30), "nachgeprueft": True, "hindernisse": []}
        eike, lea = self.a.antrag_speichern(e), self.a.antrag_speichern({**e, "reise": "r2", "profil": self.fid})
        self.assertEqual(antrag.bewerten(self.a.antrag(eike))["stand"], "bereit")
        stand = antrag.bewerten(self.a.antrag(lea))
        self.assertEqual(stand["stand"], "unvollständig")                                  # Leas Ticket fehlt — Eikes zählt nicht
        self.assertIn("Ticket für September", stand["fehlt"])

    def test_loeschen_nur_ohne_antrag(self):
        leer = self.a.profil_anlegen({"name": "Leer"})
        self.assertTrue(self.a.profil_loeschen(leer))
        self.a.antrag_speichern({"reise": "r", "linie": "5", "profil": self.fid})
        self.assertFalse(self.a.profil_loeschen(self.fid))                                 # Antrag hängt dran
        tom = self.a.profil_anlegen({"name": "Tom"})
        t = self.a.ticket_speichern("t.png", "image/png", b"tom", dict(tg.SEPTEMBER), tom)
        datei = self.a.ordner / "garantie" / "tickets" / t["datei"]
        self.assertTrue(datei.exists())
        self.assertTrue(self.a.profil_loeschen(tom))                                       # ein Ticket allein hält es nicht fest …
        self.assertFalse(datei.exists())                                                   # … es geht mit
        self.assertEqual(self.a.tickets(tom), [])
        self.assertEqual(len(self.a.tickets()), 0)                                         # Eikes Tickets blieben unberührt

    def test_profile_liegen_in_einer_datei_mit_rechten_600(self):
        import os, stat
        self.assertEqual(stat.S_IMODE(os.stat(self.a._profildatei()).st_mode), 0o600)


class Pruefen(tg.Basis):
    def test_gueltig(self):
        d = profile.pruefen({**LEA, "iban": "DE02 1203 0000 0000 2020 51", "anteil": "25,5"}, neu=True)
        self.assertEqual((d["iban"], d["anteil"], d["email"]), (FREUND_IBAN, 25.5, "lea@example.net"))

    def test_nur_mitgeschickte_felder(self):
        self.assertEqual(profile.pruefen({"anteil": 10}), {"anteil": 10.0})

    def test_ungueltiges(self):
        for d in ({"name": " "}, {"email": "kaputt"}, {"plz": "123"}, {"iban": "DE00"}, {"anrede": "Yo"},
                  {"anteil": 101}, {"anteil": "viel"}, {"auszahlung": "bar"}, {"abo_nummer": "12"}):
            with self.assertRaises(ValueError, msg=str(d)):
                profile.pruefen({"name": "X", **d}, neu=True)
        with self.assertRaises(ValueError):
            profile.pruefen({}, neu=True)                                                  # ohne Namen kein Profil


class Abrechnung(tg.Basis):
    def setUp(self):
        super().setUp()
        self.a = ablage.standard()
        self.fid = self.a.profil_anlegen({"name": "Lea", "anteil": 30})
        aid = self.a.antrag_speichern({"reise": "r", "linie": "5", "profil": self.fid})
        for nr, (cent, tag) in enumerate(((500, "2026-09-14"), (500, "2026-09-10"))):
            self.a.vorgang_speichern({"vorgang": f"8800{nr}1", "fahrtdatum": tag, "status": "genehmigt", "cent": cent,
                                      "antrag": aid, "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.a.vorgang_speichern({"vorgang": "880099", "fahrtdatum": "2026-09-11", "status": "abgelehnt",
                                  "antrag": aid, "letzte_mail": "2026-09-16T10:00:00+00:00"})

    def test_anteile_und_wer_wem_schuldet(self):
        r = profile.abrechnung(self.fid)
        self.assertEqual((r["anzahl"], r["summe_cent"], r["mein_cent"], r["freund_cent"]), (2, 1000, 300, 700))
        self.assertEqual([z["vorgang"] for z in r["zeilen"]], ["880001", "880011"])         # neueste Fahrt zuerst
        self.assertTrue(r["ich_zahle_aus"])
        self.assertEqual(r["offen_cent"], 700)                                             # Geld bei Eike: er schuldet Lea ihren Teil

    def test_erledigt_setzen(self):
        self.a.ausgezahlt_setzen(self.fid, "880001", True)
        self.assertEqual(profile.abrechnung(self.fid)["offen_cent"], 350)
        self.a.ausgezahlt_setzen(self.fid, "880001", False)
        self.assertEqual(profile.abrechnung(self.fid)["offen_cent"], 700)

    def test_geld_beim_freund_dann_schuldet_er_mir(self):
        self.a.profil_aendern(self.fid, {"auszahlung": "eigenes"})
        r = profile.abrechnung(self.fid)
        self.assertFalse(r["ich_zahle_aus"])
        self.assertEqual(r["offen_cent"], 300)

    def test_kein_cent_geht_verloren(self):
        self.a.profil_aendern(self.fid, {"anteil": 33.3})
        r = profile.abrechnung(self.fid)
        self.assertEqual(r["mein_cent"] + r["freund_cent"], r["summe_cent"])

    def test_eikes_eigene_zahlen_ohne_die_des_freundes(self):
        self.assertEqual(post.statistik()["insgesamt_cent"], 0)
        self.assertEqual(post.statistik(self.fid)["insgesamt_cent"], 1000)
        self.assertEqual(post.statistik(None)["insgesamt_cent"], 1000)


class Zuordnung(tg.Basis):
    """Mails der ÜSTRA: bei Freunden im selben Bus nie raten."""

    def setUp(self):
        super().setUp()
        self.a = ablage.standard()
        self.fid = self.a.profil_anlegen({"name": "Lea"})
        self.e = {"reise": "r", "linie": "360", "start": "Bennigsen Bahnhof", "ziel": "Hemmingen", "ab": tg._t(15, 52),
                  "an": tg._t(16, 19)}
        self.fall = {"fahrtdatum": "2026-09-15", "linie": "360", "start": "Bennigsen Bahnhof"}

    def test_ein_antrag_wird_zugeordnet(self):
        aid = self.a.antrag_speichern(self.e)
        self.assertEqual(post._passender_antrag(self.fall)["id"], aid)

    def test_mehrere_passende_werden_nicht_geraten(self):
        self.a.antrag_speichern(self.e)
        self.a.antrag_speichern({**self.e, "profil": self.fid})
        self.assertIsNone(post._passender_antrag(self.fall))

    def test_ein_antrag_eines_freundes_wird_gefunden(self):
        aid = self.a.antrag_speichern({**self.e, "profil": self.fid})
        self.assertEqual(post._passender_antrag(self.fall)["id"], aid)


class Vorbereiten(tg.Basis):
    def setUp(self):
        super().setUp()
        self.a = ablage.standard()
        self.rid = self.a.reise_merken(tg._ab("J1", "360", "Bus", 28, plan=tg._t(15, 52)))
        self.a.angaben_setzen({"kontoinhaber": "Max Mustermann", "iban": tg.BEISPIEL_IBAN})   # das Konto, auf das die Auszahlung geht
        self.fid = self.a.profil_anlegen(dict(LEA))
        self.a.ticket_speichern("l.pdf", "application/pdf", b"%PDF lea", dict(tg.SEPTEMBER), self.fid)

    def los(self, profil):
        a = mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=tg._verlauf()))
        b = mock.patch.object(formular, "stationen", mock.AsyncMock(return_value=tg.STATIONEN))
        c = mock.patch.object(formular, "fahrten", mock.AsyncMock(
            side_effect=[tg._formularfahrt(tg._t(15, 52), tg._t(16, 19), tg._t(16, 47))] + [[]] * 5))
        with a, b, c:
            return asyncio.run(antrag.vorbereiten(self.rid, tg.BENNIGSEN, tg.HEMMINGEN, profil))

    def test_antrag_gehoert_dem_freund_und_hat_sein_ticket(self):
        r = self.los(self.fid)
        self.assertTrue(r["ok"], r)
        self.assertIn("für Lea", r["text"])
        e = self.a.antrag(r["antrag"]["id"])
        self.assertEqual((e["profil"], e["fahrkarte"], r["antrag"]["stand"]), (self.fid, "Deutschlandticket", "bereit"))
        self.assertEqual(self.a.antraege(), [])                                            # Eikes Liste bleibt leer

    def test_dieselbe_fahrt_fuer_zwei_personen_aber_nicht_zweimal_fuer_eine(self):
        self.assertTrue(self.los(self.fid)["ok"])
        zweiter = self.los(self.fid)
        self.assertFalse(zweiter["ok"])
        self.assertIn("für Lea", zweiter["text"])
        self.a.ticket_speichern("e.pdf", "application/pdf", b"%PDF eike", dict(tg.SEPTEMBER))
        self.a.angaben_setzen({k: "x" for k in ablage.PFLICHT} | {"iban": tg.BEISPIEL_IBAN})
        self.assertTrue(self.los(0)["ok"])                                                 # Eike im selben Bus: eigener Antrag

    def test_unbekanntes_oder_archiviertes_profil(self):
        self.assertFalse(self.los(99)["ok"])
        self.a.profil_aendern(self.fid, {"archiviert": True})
        r = self.los(self.fid)
        self.assertFalse(r["ok"])
        self.assertIn("archiviert", r["text"])
        self.assertEqual(self.a.antraege(50, None), [])


class Freund(ta.Basis):
    """Abschicken für einen Freund: nur mit seinen Daten, nur nach seiner Zustimmung."""

    def setUp(self):
        super().setUp()
        self.fid = self.a.profil_anlegen(dict(LEA))
        self.leas = self.a.ticket_speichern("l.png", "image/png", b"leas bild", {
            "art": "Deutschlandticket", "bezeichnung": "Deutschlandticket Jugend Hannover", "herausgeber": "ÜSTRA",
            "gueltig_von": "2026-09-01", "gueltig_bis": "2026-09-30", "inhaber": "Lea Beispiel"}, self.fid)
        self.faid = self.a.antrag_speichern({
            "profil": self.fid, "reise": "r1", "jid": "J", "linie": "570", "start": "Barsinghausen Berliner Straße",
            "ziel": "Landringhausen", "start_ref": "de:1", "ziel_ref": "de:2", "ab": ta.AB, "an": ta.AN, "verspaetung": 46,
            "quelle": "vom GVH gemessen", "faellig_ab": ta.AN + timedelta(minutes=46), "nachgeprueft": True,
            "daten": {"fahrt": ta.FAHRT, "start": {"name": "S", "reference": "de:1"},
                      "ziel": {"name": "Z", "reference": "de:2"}}})

    def schicken_freund(self):
        return asyncio.run(abschicken.abschicken(self.faid))

    def nicht_gesendet(self, wort):
        d = self.schicken_freund()
        self.assertFalse(d["ok"], d)
        self.assertIn(wort, d["text"])
        self.assertEqual(self.gesendet, [])
        self.assertEqual(self.a.antrag(self.faid)["status"], "offen")

    def test_geht_nur_mit_seinen_daten_raus(self):
        d = self.schicken_freund()
        self.assertTrue(d["ok"], d)
        q = self.gesendet[0]
        c = q["clientDto"]
        self.assertEqual((c["firstName"], c["lastName"], c["email"], c["zip"], c["formOfAddress"]),
                         ("Lea", "Beispiel", "lea@example.net", "30449", "MS"))
        p = q["complaintPointDtos"][0]
        self.assertEqual(p["ticketDto"]["subscriptionCard"], "7654321")                    # Leas Abo, nicht Eikes 1234567
        self.assertEqual(p["bankData"], {"accountOwner": "Max Mustermann", "iban": ta.IBAN})   # Auszahlung: Eikes Konto
        # Nichts von Eikes Person im Antrag — außer dem Konto, wie gewählt
        roh = json.dumps(q)
        for eike in ("max@example.org", "Musterweg", "30159", "1234567"):
            self.assertNotIn(eike, roh)
        e = self.a.antrag(self.faid)
        self.assertEqual((e["status"], e["profil"]), ("abgeschickt", self.fid))

    def test_eigenes_konto_des_freundes(self):
        self.a.profil_aendern(self.fid, {"auszahlung": "eigenes", "kontoinhaber": "Lea Beispiel", "iban": FREUND_IBAN})
        self.assertTrue(self.schicken_freund()["ok"])
        self.assertEqual(self.gesendet[0]["complaintPointDtos"][0]["bankData"],
                         {"accountOwner": "Lea Beispiel", "iban": FREUND_IBAN})

    def test_ohne_zustimmung_nichts(self):
        self.a.profil_aendern(self.fid, {"vollmacht": False})
        self.nicht_gesendet("zugestimmt")

    def test_archiviertes_profil(self):
        self.a.profil_aendern(self.fid, {"archiviert": True})
        self.nicht_gesendet("archiviert")

    def test_eikes_email_fuer_einen_freund_geht_nicht(self):
        self.a.profil_aendern(self.fid, {"email": "Max@Example.org"})
        self.nicht_gesendet("deine eigene")

    def test_ticket_eines_anderen(self):
        self.a.profil_aendern(self.fid, {"nachname": "Anders"})
        self.nicht_gesendet("passt nicht")

    def test_eikes_ticket_zaehlt_nicht_fuer_den_freund(self):
        self.a.tickets(self.fid)                                                           # (Leas Ticket vorhanden)
        alle = self.a._alle_tickets()
        self.a._tickets_schreiben([t for t in alle if t.get("profil", 0) != self.fid])     # Leas Ticket weg
        self.nicht_gesendet("Ticket von Lea")

    def test_angaben_fehlen(self):
        self.a.profil_aendern(self.fid, {"strasse": ""})
        self.nicht_gesendet("Angaben von Lea")

    def test_bestaetigung_nennt_die_person(self):
        p = asyncio.run(abschicken.pruefen(self.faid))
        self.assertTrue(p["ok"], p)
        self.assertIn("ANTRAG FÜR LEA", p["punkte"][0])
        self.assertTrue(any("dein Konto" in x for x in p["punkte"]))

    def test_grenzen_zaehlen_je_person(self):
        # Eike hat den Tag mit zwei genehmigten Fahrten voll — Lea nicht
        for nr in range(2):
            self.a.vorgang_speichern({"vorgang": f"77000{nr}", "fahrtdatum": "2026-09-16", "status": "genehmigt",
                                      "cent": 500, "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.assertTrue(self.schicken_freund()["ok"])

    def test_eikes_eigener_antrag_geht_unveraendert(self):
        d = self.schicken()
        self.assertTrue(d["ok"], d)
        self.assertEqual(self.gesendet[0]["clientDto"]["email"], "max@example.org")

    def test_schnittstelle_prueft_die_person(self):
        c = TestClient(app)
        r = c.post(f"/api/garantie/antrag/{self.faid}/abschicken", json={"bestaetigt": True})           # Seite zeigte Eike
        self.assertEqual(r.status_code, 409)
        self.assertIn("anderen Person", r.json()["detail"])
        self.assertEqual(self.gesendet, [])
        r = c.post(f"/api/garantie/antrag/{self.faid}/abschicken", json={"bestaetigt": True, "profil": self.fid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"], r.json())


class Schnittstelle(tg.Basis):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)

    def anlegen(self, **extra):
        r = self.c.post("/api/garantie/profile", json={**LEA, **extra})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_anlegen_lesen_aendern_loeschen(self):
        pid = self.anlegen(iban=FREUND_IBAN, auszahlung="eigenes", kontoinhaber="Lea Beispiel")
        d = self.c.get(f"/api/garantie?profil={pid}").json()
        self.assertEqual((d["profil"], d["angaben"]["vorname"], d["angaben"]["name"]), (pid, "Lea", "Lea"))
        self.assertEqual([p["name"] for p in d["profile"]], ["Lea"])
        self.assertNotIn("0000202051", self.c.get(f"/api/garantie?profil={pid}").text)      # IBAN nie zurück an den Browser
        self.assertNotIn("0000202051", self.c.get("/api/garantie").text)
        self.assertEqual(self.c.put(f"/api/garantie/profile/{pid}", json={"ort": "Musterhausen"}).status_code, 200)
        self.assertEqual(self.c.get(f"/api/garantie?profil={pid}").json()["angaben"]["ort"], "Musterhausen")
        self.assertEqual(self.c.get(f"/api/garantie?profil={pid}").json()["angaben"]["strasse"], "Lindenweg 4")   # Rest blieb
        self.assertEqual(self.c.delete(f"/api/garantie/profile/{pid}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/garantie?profil={pid}").status_code, 404)

    def test_eikes_seite_bleibt_wie_sie_war(self):
        self.anlegen()
        d = self.c.get("/api/garantie").json()
        self.assertEqual((d["profil"], d["abrechnung"], d["antraege"], d["tickets"]), (0, None, [], []))
        self.assertEqual(d["angaben"]["fehlt"], list(ablage.PFLICHT))                        # Eikes Angaben: noch leer

    def test_eingaben_werden_geprueft(self):
        for daten in ({"name": ""}, {**LEA, "email": "kaputt"}, {**LEA, "iban": "DE00"}, {**LEA, "anteil": 200}):
            self.assertEqual(self.c.post("/api/garantie/profile", json=daten).status_code, 422, daten)
        pid = self.anlegen()
        self.assertEqual(self.c.put(f"/api/garantie/profile/{pid}", json={"plz": "12"}).status_code, 422)
        self.assertEqual(self.c.put("/api/garantie/profile/99", json={"ort": "X"}).status_code, 404)

    def test_ticket_hochladen_fuer_den_freund(self):
        pid = self.anlegen(vorname="", nachname="")
        erkannt = dict(tg.SEPTEMBER, ist_fahrkarte=True, inhaber="Lea Beispiel", abo_nummer="7654321")
        with mock.patch.object(ticket, "auslesen", mock.AsyncMock(return_value=erkannt)):
            r = self.c.post(f"/api/garantie/ticket?profil={pid}", files={"datei": ("t.pdf", b"%PDF-1.4", "application/pdf")})
        self.assertEqual(r.status_code, 200, r.text)
        a = ablage.standard()
        self.assertEqual([t["profil"] for t in a.tickets(pid)], [pid])
        self.assertEqual(a.tickets(), [])                                                    # Eikes Ticketliste unberührt
        self.assertEqual((a.angaben(pid)["vorname"], a.angaben(pid)["nachname"]), ("Lea", "Beispiel"))   # Name aus dem Ticket
        self.assertEqual(a.angaben(), {})                                                    # Eikes Angaben bekamen nichts
        self.assertEqual(self.c.post("/api/garantie/ticket?profil=99", files={"datei": ("t.pdf", b"%PDF", "application/pdf")}).status_code, 404)

    def test_ticket_korrigieren_gilt_fuer_das_profil(self):
        pid = self.anlegen()
        t = ablage.standard().ticket_speichern("t.pdf", "application/pdf", b"%PDF x", dict(tg.SEPTEMBER), pid)
        r = self.c.post(f"/api/garantie/ticket/{t['id']}", json={"gueltig_bis": "2026-09-29"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(ablage.standard().angaben(pid)["gueltig_bis"], "2026-09-29")
        self.assertEqual(ablage.standard().angaben().get("gueltig_bis"), None)

    def test_vorbereiten_fuer_unbekannte_oder_archivierte_person(self):
        konnektoren.setzen("uestra_garantie", True)
        pid = self.anlegen()
        daten = {"fahrt": "abcdef1234", "start": tg.BENNIGSEN, "ziel": tg.HEMMINGEN}
        self.assertEqual(self.c.post("/api/garantie/vorbereiten", json={**daten, "profil": 99}).status_code, 404)
        self.c.put(f"/api/garantie/profile/{pid}", json={"archiviert": True})
        r = self.c.post("/api/garantie/vorbereiten", json={**daten, "profil": pid})
        self.assertEqual(r.status_code, 409)
        self.assertIn("archiviert", r.json()["detail"])

    def test_abrechnung_und_erledigt(self):
        pid = self.anlegen(anteil=20)
        a = ablage.standard()
        aid = a.antrag_speichern({"reise": "r", "linie": "5", "profil": pid})
        a.vorgang_speichern({"vorgang": "880001", "fahrtdatum": "2026-09-14", "status": "genehmigt", "cent": 500,
                             "antrag": aid, "letzte_mail": "2026-09-16T10:00:00+00:00"})
        d = self.c.get(f"/api/garantie?profil={pid}").json()
        self.assertEqual((d["abrechnung"]["summe_cent"], d["abrechnung"]["mein_cent"], d["abrechnung"]["offen_cent"]), (500, 100, 400))
        self.assertEqual(d["profile"][0]["offen_cent"], 400)
        r = self.c.post(f"/api/garantie/profile/{pid}/erledigt", json={"vorgang": "880001", "erledigt": True})
        self.assertEqual(r.json()["abrechnung"]["offen_cent"], 0)
        self.assertEqual(self.c.delete(f"/api/garantie/profile/{pid}").status_code, 409)     # Antrag hängt dran

    def test_friday_sieht_nur_eikes_antraege(self):
        from kern.werkzeuge import garantie as werkzeug
        pid = self.anlegen()
        a = ablage.standard()
        a.antrag_speichern({"reise": "r", "linie": "999", "start": "Geheimhalt", "ziel": "X", "ab": tg._t(8), "an": tg._t(9),
                            "profil": pid})
        text = werkzeug.garantie_antraege()
        self.assertNotIn("999", text)
        self.assertNotIn("Geheimhalt", text)


if __name__ == "__main__":
    import unittest
    unittest.main()
