"""Jugendnetzkarte, E-Mail-Aliasse, Zum-Abtippen und die versionierte Startseite — ohne Netz.

Die Felder der Jugendnetzkarte (Kundenkarte 6 Ziffern, Ticketnummer 36-…, Monatskarte → SchülerInnen & Azubis) stammen
aus dem Formular der ÜSTRA (elma.gvh.de), gemessen am 20.09.2026 — siehe docs/LEHREN.md.
"""

import asyncio
import re
from datetime import timedelta
from unittest import mock

from fastapi.testclient import TestClient

from kern.garantie import ablage, abschicken, antrag, post, ticket
from kern.main import app
from kern.schule import briefe
from test import test_garantie as tg
from test import test_garantie_abschicken as ta

JUGEND = {"art": "Monatskarte", "bezeichnung": "Jugendnetzkarte", "herausgeber": "GVH", "gueltig_von": "2026-09-01",
          "gueltig_bis": "2026-09-30", "inhaber": "Lea Beispiel", "kundenkarte": "123456", "ticketnummer": "2608127024",
          "preis": 29.5, "form": "digital"}
LEA = {"name": "Lea", "anrede": "Frau", "vorname": "Lea", "nachname": "Beispiel", "email": "beispiel+lea@example.com",
       "strasse": "Lindenweg 4", "plz": "30449", "ort": "Hannover", "auszahlung": "ich", "anteil": 20, "vollmacht": True}


class Sorten(tg.Basis):
    def test_erkennt_die_karten_mit_eigenen_pflichtfeldern(self):
        s = ticket.sorte(JUGEND)
        self.assertEqual((s["name"], s["art"], s["typ"], s["detail"], s["automatisch"]),
                         ("Jugendnetzkarte", "Monatskarte", "Monatskarte SchülerInnen & Azubis", "Jugendnetzkarte", True))
        self.assertEqual(ticket.sorte({"art": "Monatskarte", "bezeichnung": "Ausbildungsnetzkarte"})["detail"], "Ausbildungsnetzkarte")
        self.assertIsNone(ticket.sorte(dict(tg.SEPTEMBER)))                                 # das Deutschlandticket bleibt wie es war
        self.assertIsNone(ticket.sorte(None))

    def test_fehlende_felder(self):
        self.assertEqual(ticket.fehlende_felder({**JUGEND, "kundenkarte": "", "ticketnummer": ""}),
                         ["Kundenkartennummer", "Ticketnummer"])
        self.assertEqual(ticket.fehlende_felder(JUGEND), [])
        self.assertEqual(ticket.fehlende_felder(dict(tg.SEPTEMBER)), [])

    def test_formate_wie_im_formular(self):
        # Das Formular (config/settings) erlaubt für die Ticketnummer alles aus Buchstaben und Ziffern.
        for ok in ("2608127024", "36123456789", "ABC123"):
            self.assertTrue(ticket.TICKETNUMMER.match(ok), ok)
        for falsch in ("", "36-12.345.678", "12 345"):
            self.assertFalse(ticket.TICKETNUMMER.match(falsch), falsch)
        self.assertTrue(ticket.KUNDENKARTE.match("123456"))
        self.assertFalse(ticket.KUNDENKARTE.match("12345"))
        self.assertFalse(ticket.KUNDENKARTE.match("1234567"))

    def test_auslesen_uebernimmt_nur_passende_werte(self):
        roh = {"ist_fahrkarte": True, "art": "Monatskarte", "bezeichnung": "Jugendnetzkarte", "gueltig_von": "2026-09-01",
               "gueltig_bis": "2026-09-30", "kundenkarte": "123 456", "ticketnummer": "2608 127 024", "preis_euro": 29.5}
        with mock.patch.object(briefe, "_gemini", mock.AsyncMock(return_value=(roh, "m"))):
            e = asyncio.run(ticket.auslesen("image/png", b"x"))
        self.assertEqual((e["kundenkarte"], e["ticketnummer"], e["preis"]), ("123456", "2608127024", 29.5))
        schlecht = {**roh, "kundenkarte": "12345", "ticketnummer": "ka putt!", "preis_euro": -1}
        with mock.patch.object(briefe, "_gemini", mock.AsyncMock(return_value=(schlecht, "m"))):
            e = asyncio.run(ticket.auslesen("image/png", b"x"))
        self.assertEqual((e["kundenkarte"], e["ticketnummer"], e["preis"]), ("", "", None))     # lieber leer als falsch


class Antraege(tg.Basis):
    def setUp(self):
        super().setUp()
        self.a = ablage.standard()
        self.a.angaben_setzen({"kontoinhaber": "Max Mustermann", "iban": tg.BEISPIEL_IBAN, "email": "beispiel+garantie@example.com"})
        self.fid = self.a.profil_anlegen(dict(LEA))
        self.t = self.a.ticket_speichern("j.png", "image/png", b"jugend", dict(JUGEND), self.fid)
        self.e = {"reise": "r", "linie": "5", "start": "Kröpcke", "ziel": "Hbf", "ab": tg._t(8), "an": tg._t(9),
                  "verspaetung": 30, "quelle": "vom GVH gemessen", "faellig_ab": tg._t(9, 30), "nachgeprueft": True,
                  "hindernisse": [], "profil": self.fid}

    def test_ohne_nummern_unvollstaendig(self):
        self.a.ticket_korrigieren(self.t["id"], {"kundenkarte": "", "ticketnummer": ""})
        s = antrag.bewerten(self.a.antrag(self.a.antrag_speichern(self.e)))
        self.assertEqual(s["stand"], "unvollständig")
        self.assertEqual(s["fehlt"], ["Kundenkartennummer", "Ticketnummer"])

    def test_mit_nummern_bereit(self):
        self.assertEqual(antrag.bewerten(self.a.antrag(self.a.antrag_speichern(self.e)))["stand"], "bereit")

    def test_abschicken_verlangt_kundenkarte_und_ticketnummer(self):
        """Fehlt eine der Nummern, die das Formular für diese Karte verlangt, geht nichts raus."""
        self.a.ticket_korrigieren(self.t["id"], {"ticketnummer": ""})
        aid = self.a.antrag_speichern(self.e)
        p = asyncio.run(abschicken.pruefen(aid))
        self.assertFalse(p["ok"])
        self.assertTrue(any("Nummer des Monatstickets" in x for x in p["probleme"]), p["probleme"])

    def test_abschicken_verlangt_die_kundenkarte(self):
        self.a.profil_aendern(self.fid, {"kundenkarte": ""})
        self.a.ticket_korrigieren(self.t["id"], {"kundenkarte": ""})
        aid = self.a.antrag_speichern(self.e)
        p = asyncio.run(abschicken.pruefen(aid))
        self.assertFalse(p["ok"])
        self.assertTrue(any("Kundenkartennummer" in x for x in p["probleme"]), p["probleme"])

    def test_kartenpreis_begrenzt_die_erstattung_pro_monat(self):
        an = tg._t(9)
        aid = self.a.antrag_speichern(self.e)
        def genehmigt(anzahl):
            for nr in range(anzahl):
                self.a.vorgang_speichern({"vorgang": f"99000{nr}", "fahrtdatum": f"2026-09-0{nr + 1}", "status": "genehmigt",
                                          "cent": 500, "antrag": aid, "letzte_mail": "2026-09-16T10:00:00+00:00"})
        # Karte kostet 29,50 €: nach 5 Fahrten (25,00 €) sind noch 4,50 € drin — möglich, aber es gibt weniger.
        genehmigt(5)
        self.assertEqual(antrag._preis_rest(an, self.fid), 450)
        self.assertEqual([h for h in antrag._limits(an, profil=self.fid) if "Kartenpreis" in h], [])
        stand = antrag.bewerten(self.a.antrag(aid))
        self.assertEqual(stand["stand"], "bereit")
        self.assertTrue(any("4,50 €" in h for h in stand["hinweise"]), stand["hinweise"])
        # Nach der sechsten ist der Preis ausgeschöpft: jetzt eine Grenze.
        genehmigt(6)
        limits = antrag._limits(an, profil=self.fid)
        self.assertTrue(any("Kartenpreis" in h for h in limits), limits)
        self.assertTrue(antrag._ist_grenze(limits[0]))
        self.a.ticket_korrigieren(self.t["id"], {"preis": 0})                              # Preis unbekannt → keine Preisgrenze
        self.assertIsNone(antrag._preis_rest(an, self.fid))
        self.assertEqual([h for h in antrag._limits(an, profil=self.fid) if "Kartenpreis" in h], [])

    def test_kartenpreis_zaehlt_nur_ab_dem_beginn_des_tickets(self):
        """Was mit dem alten Ticket erstattet wurde, mindert den Preis der neuen Karte nicht (Kontakt A, 27.10.2026)."""
        self.a.ticket_korrigieren(self.t["id"], {"gueltig_von": "2026-09-10", "gueltig_bis": "2026-10-09", "preis": 18})
        aid = self.a.antrag_speichern(self.e)
        for nr, tag in enumerate(("2026-09-02", "2026-09-03", "2026-09-09")):               # altes Ticket, gleicher Monat
            self.a.vorgang_speichern({"vorgang": f"98000{nr}", "fahrtdatum": tag, "status": "genehmigt", "cent": 500,
                                      "antrag": aid, "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.assertEqual(antrag._preis_rest(tg._t(9), self.fid), 1800)
        self.a.vorgang_speichern({"vorgang": "980009", "fahrtdatum": "2026-09-12", "status": "genehmigt", "cent": 500,
                                  "antrag": aid, "letzte_mail": "2026-09-16T10:00:00+00:00"})
        self.assertEqual(antrag._preis_rest(tg._t(9), self.fid), 1300)
        z = antrag.zaehler(self.fid)
        self.assertEqual((z["preis_rest_cent"], z["ticket_erstattet_cent"]), (1300, 500))
        self.assertEqual(z["monat"], 4)                                                      # die Monatszahl bleibt Kalendermonat

    def test_offener_antrag_des_alten_tickets_zaehlt_nicht_gegen_den_neuen_preis(self):
        self.a.ticket_korrigieren(self.t["id"], {"gueltig_von": "2026-09-10", "gueltig_bis": "2026-10-09", "preis": 18})
        alt = {**self.e, "reise": "alt", "ab": tg._t(8, tag=5), "an": tg._t(9, tag=5)}
        aid = self.a.antrag_speichern(alt)
        self.a.antrag_aendern(aid, status="abgeschickt")
        z = antrag.zaehler(self.fid)
        self.assertEqual(z["offen_monat"], 1)                                                # für die Monatsgrenze zählt er
        self.assertEqual((z["offen_ticket"], z["ticket_offen_cent"]), (0, 0))                # für den Kartenpreis nicht
        self.assertEqual(z["frei_monat"], 4)                                                 # 18 € Preis → 4 Fahrten, keine weniger

    def test_preisgrenze_gilt_nur_fuer_monatskarten(self):
        an = tg._t(9)
        d = self.a.ticket_speichern("d.png", "image/png", b"dt", {**tg.SEPTEMBER, "preis": 1}, self.fid)   # Deutschlandticket mit Preis
        self.assertEqual([h for h in antrag._limits(an, profil=self.fid) if "Kartenpreis" in h], [])
        self.assertTrue(d)

    def test_abtippen_nennt_alles_in_der_reihenfolge_des_formulars(self):
        aid = self.a.antrag_speichern(self.e)
        d = abschicken.abtippen(aid)
        self.assertEqual(d["person"], "Lea")
        gruppen = {g["titel"]: dict(g["zeilen"]) for g in d["gruppen"]}
        self.assertEqual(gruppen["Person"]["Vorname"], "Lea")
        self.assertEqual(gruppen["Person"]["E-Mail"], "beispiel+lea@example.com")
        self.assertEqual((gruppen["Fahrkarte"]["Typ"], gruppen["Fahrkarte"]["Genauer Name"]),
                         ("Monatskarte SchülerInnen & Azubis", "Jugendnetzkarte"))
        self.assertEqual((gruppen["Fahrkarte"]["Kundenkartennummer"], gruppen["Fahrkarte"]["Ticketnummer"]),
                         ("123456", "2608127024"))
        self.assertEqual(gruppen["Fahrkarte"]["Gültig bis"], "30.09.2026")
        self.assertEqual(gruppen["Fahrt"]["Verspätung am Ziel (Min)"], "30")
        konto = [w for k, w in gruppen["Konto für die Auszahlung"].items() if k.startswith("IBAN")][0]
        self.assertTrue(konto.endswith("3000"))
        self.assertNotIn("370400440532013000", str(d))                                     # die volle IBAN nie
        self.assertIsNone(abschicken.abtippen(9999))

    def test_abtippen_schnittstelle(self):
        aid = self.a.antrag_speichern(self.e)
        c = TestClient(app)
        r = c.get(f"/api/garantie/antrag/{aid}/abtippen")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("0532013000", r.text)
        self.assertEqual(c.get("/api/garantie/antrag/999/abtippen").status_code, 404)


class TicketKorrektur(tg.Basis):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)
        self.t = ablage.standard().ticket_speichern("j.png", "image/png", b"j", {**JUGEND, "kundenkarte": "", "ticketnummer": ""})

    def korrigieren(self, **felder):
        return self.c.post(f"/api/garantie/ticket/{self.t['id']}", json=felder)

    def test_felder_speichern(self):
        r = self.korrigieren(kundenkarte="123456", ticketnummer="2608 127 024", preis=29.5)
        self.assertEqual(r.status_code, 200, r.text)
        t = ablage.standard().tickets()[0]
        self.assertEqual((t["kundenkarte"], t["ticketnummer"], t["preis"]), ("123456", "2608127024", 29.5))
        self.assertEqual(self.korrigieren(preis=0).status_code, 200)
        self.assertIsNone(ablage.standard().tickets()[0]["preis"])                           # 0 = kein Preis
        self.assertEqual(ablage.standard().tickets()[0]["kundenkarte"], "123456")            # der Rest blieb

    def test_falsche_formate_werden_abgelehnt(self):
        self.assertEqual(self.korrigieren(kundenkarte="12345").status_code, 422)
        self.assertEqual(self.korrigieren(kundenkarte="12a456").status_code, 422)
        self.assertEqual(self.korrigieren(ticketnummer="nicht gültig!").status_code, 422)
        self.assertEqual(self.korrigieren(preis=-5).status_code, 422)
        self.assertEqual(ablage.standard().tickets()[0]["kundenkarte"], "")


class EMail(ta.Basis):
    """Jede Person braucht ihre eigene Adresse — sonst nichts abschicken."""

    def setUp(self):
        super().setUp()
        self.fid = self.a.profil_anlegen({**LEA, "abo_nummer": "7654321"})
        self.a.ticket_speichern("l.png", "image/png", b"leas bild", {
            "art": "Deutschlandticket", "bezeichnung": "Deutschlandticket Jugend Hannover", "herausgeber": "ÜSTRA",
            "gueltig_von": "2026-09-01", "gueltig_bis": "2026-09-30", "inhaber": "Lea Beispiel"}, self.fid)
        self.faid = self.a.antrag_speichern({
            "profil": self.fid, "reise": "r1", "jid": "J", "linie": "570", "start": "Barsinghausen Berliner Straße",
            "ziel": "Landringhausen", "start_ref": "de:1", "ziel_ref": "de:2", "ab": ta.AB, "an": ta.AN, "verspaetung": 46,
            "quelle": "vom GVH gemessen", "faellig_ab": ta.AN + timedelta(minutes=46), "nachgeprueft": True,
            "daten": {"fahrt": ta.FAHRT, "start": {"name": "S", "reference": "de:1"}, "ziel": {"name": "Z", "reference": "de:2"}}})

    def pruefen(self):
        return asyncio.run(abschicken.pruefen(self.faid))

    def test_eigener_alias_geht(self):
        self.a.angaben_setzen({"email": "beispiel+garantie@example.com"})                          # Eikes eigene Adresse mit Alias
        p = self.pruefen()
        self.assertTrue(p["ok"], p["probleme"])

    def test_dieselbe_adresse_wie_eike_geht_nicht(self):
        self.a.profil_aendern(self.fid, {"email": "MAX@example.org"})                         # Eikes Adresse aus dem Test-Aufbau
        p = self.pruefen()
        self.assertFalse(p["ok"])
        self.assertIn("deine eigene", " ".join(p["probleme"]))
        self.assertIn("+lea@gmail.com", " ".join(p["probleme"]))                              # mit dem Alias-Tipp

    def test_dieselbe_adresse_wie_ein_anderer_freund_geht_nicht(self):
        self.a.profil_anlegen({"name": "Tom", "email": "beispiel+lea@example.com"})
        p = self.pruefen()
        self.assertFalse(p["ok"])
        self.assertIn("die von Tom", " ".join(p["probleme"]))


class Bescheide(tg.Basis):
    """Freunde im selben Bus: Die Adresse, an die die Mail ging, verrät, wessen Bescheid es ist."""

    def setUp(self):
        super().setUp()
        self.a = ablage.standard()
        self.a.angaben_setzen({"email": "beispiel+garantie@example.com"})
        self.lea = self.a.profil_anlegen({"name": "Lea", "email": "beispiel+lea@example.com"})
        e = {"reise": "r", "linie": "360", "start": "Bennigsen Bahnhof", "ziel": "Hemmingen", "ab": tg._t(15, 52), "an": tg._t(16, 19)}
        self.eike_antrag = self.a.antrag_speichern(e)
        self.lea_antrag = self.a.antrag_speichern({**e, "profil": self.lea})
        self.fall = {"fahrtdatum": "2026-09-15", "linie": "360", "start": "Bennigsen Bahnhof"}

    def test_ohne_absender_bleibt_es_ungeraten(self):
        self.assertIsNone(post._passender_antrag(self.fall))

    def test_alias_ordnet_zu(self):
        self.assertEqual(post._passender_antrag({**self.fall, "empfaenger": ["beispiel+lea@example.com"]})["id"], self.lea_antrag)
        self.assertEqual(post._passender_antrag({**self.fall, "empfaenger": ["beispiel+garantie@example.com"]})["id"], self.eike_antrag)
        self.assertIsNone(post._passender_antrag({**self.fall, "empfaenger": ["jemand@else.de"]}))

    def test_empfaenger_kommen_aus_der_mail(self):
        mail = {"betreff": "ÜSTRA Garantie: Ihr Garantiefall 8851675 vom 19.08.2026", "datum": "2026-09-16T10:00:00+00:00",
                "an": ["Beispiel+Lea@Example.com"],
                "text": "vielen Dank für Ihren Antrag mit den folgenden Angaben: Fahrtdatum: 15.09.2026 "
                        "Verbindung: 360 - Bennigsen Bahnhof - Hemmingen (15:52 - 16:19) "
                        "unter der Vorgangsnummer *8851675* aufgenommen"}
        fall = post.faelle([mail])["8851675"]
        self.assertEqual(fall["empfaenger"], ["beispiel+lea@example.com"])
        self.assertEqual(post._passender_antrag(fall)["id"], self.lea_antrag)


class Startseite(tg.Basis):
    def test_skripte_haben_einen_stempel_und_die_seite_einen_stand(self):
        c = TestClient(app)
        for pfad in ("/", "/index.html"):
            r = c.get(pfad)
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/html", r.headers["content-type"])
            self.assertRegex(r.text, r'<script src="/garantie\.js\?v=\d+"></script>')
            self.assertRegex(r.text, r'href="/app\.css\?v=\d+"')
            self.assertRegex(r.text, r'<meta name="build" content="\d\d\.\d\d\. \d\d:\d\d">')
            self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(c.get("/garantie.js?v=123").status_code, 200)                       # der Stempel stört das Ausliefern nicht

    def test_stempel_ist_eine_zahl(self):
        from kern import main
        stempel, stand = main._aufbau()
        self.assertTrue(stempel.isdigit())
        self.assertRegex(stand, r"^\d\d\.\d\d\. \d\d:\d\d$")


class AusTicket(tg.Basis):
    """Neue Person: nur ein Name und die Tickets — der Rest wird gelesen."""

    def setUp(self):
        super().setUp()
        self.c = TestClient(app)
        self.a = ablage.standard()

    def hochladen(self, dateien, name="", erkannt=None, fehler=None):
        werte = erkannt if isinstance(erkannt, list) else [erkannt or dict(JUGEND, ist_fahrkarte=True)] * len(dateien)
        ki = mock.AsyncMock(side_effect=fehler or werte)
        with mock.patch.object(ticket, "auslesen", ki):
            return self.c.post("/api/garantie/profile/aus-ticket", data={"name": name},
                               files=[("dateien", (n, b, m)) for n, b, m in dateien])

    def test_legt_die_person_aus_dem_ticket_an(self):
        r = self.hochladen([("j.png", b"jugend", "image/png")], name="Lea")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["ok"])
        p = self.a.profil(d["id"])
        self.assertEqual((p["name"], p["vorname"], p["nachname"], p["auszahlung"], p["vollmacht"]),
                         ("Lea", "Lea", "Beispiel", "ich", False))                        # Zustimmung nie von selbst
        t = self.a.tickets(d["id"])[0]
        self.assertEqual((t["bezeichnung"], t["kundenkarte"], t["ticketnummer"]), ("Jugendnetzkarte", "123456", "2608127024"))
        self.assertEqual(self.a.angaben(d["id"], roh=True)["kundenkarte"], "123456")       # gehört zur Person
        self.assertEqual(self.a.angaben(d["id"], roh=True)["fahrkarte"], "Monatskarte")
        self.assertIn("Jugendnetzkarte", d["text"])
        self.assertIn("strasse", d["angaben"]["fehlt"])                                    # Adresse liest das Ticket nicht
        self.assertEqual((self.a.tickets(), self.a.angaben()), ([], {}))                   # Eikes Daten unberührt

    def test_ohne_eigenen_namen_nimmt_es_den_vom_ticket(self):
        d = self.hochladen([("j.png", b"jugend", "image/png")]).json()
        self.assertEqual(self.a.profil(d["id"])["name"], "Lea")
        d2 = self.hochladen([("x.png", b"x", "image/png")], erkannt=dict(JUGEND, ist_fahrkarte=True, inhaber="Tom Anders")).json()
        self.assertEqual(self.a.profil(d2["id"])["name"], "Tom")

    def test_mehrere_tickets_auf_einmal(self):
        sept = dict(tg.SEPTEMBER, ist_fahrkarte=True, inhaber="Max Muster", abo_nummer="7654321")
        okt = dict(tg.SEPTEMBER, ist_fahrkarte=True, inhaber="Max Muster", abo_nummer="7654321",
                   gueltig_von="2026-10-01", gueltig_bis="2026-10-31")
        r = self.hochladen([("s.png", b"sept", "image/png"), ("o.png", b"okt", "image/png")], name="Max", erkannt=[sept, okt])
        d = r.json()
        self.assertEqual(len(self.a.tickets(d["id"])), 2)
        self.assertEqual(self.a.angaben(d["id"], roh=True)["abo_nummer"], "7654321")
        self.assertIsNotNone(self.a.ticket_fuer("2026-10-15", d["id"]))                    # beide Monate abgedeckt

    def test_gibt_es_die_person_schon_wird_nichts_angelegt(self):
        self.hochladen([("j.png", b"jugend", "image/png")], name="Lea")
        r = self.hochladen([("j2.png", b"jugend zwei", "image/png")], name="Lea noch mal")
        d = r.json()
        self.assertFalse(d["ok"])
        self.assertEqual(d["vorhanden"]["name"], "Lea")
        self.assertEqual(len(self.a.profile()), 1)
        self.assertEqual(len(self.a.tickets(1)), 1)                                        # das zweite wurde nicht gespeichert

    def test_unlesbares_ticket_legt_trotzdem_an(self):
        r = self.hochladen([("j.png", b"jugend", "image/png")], name="Lea", fehler=RuntimeError("weg"))
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertIn("selbst ein", d["text"])
        self.assertEqual(self.a.profil(d["id"])["name"], "Lea")
        self.assertEqual(len(self.a.tickets(d["id"])), 1)

    def test_falsche_dateien(self):
        self.assertEqual(self.hochladen([("a.exe", b"MZ", "application/octet-stream")]).status_code, 400)
        self.assertEqual(self.hochladen([("a.png", b"", "image/png")]).status_code, 400)
        self.assertEqual(self.hochladen([(f"{i}.png", b"x", "image/png") for i in range(7)]).status_code, 400)
        self.assertEqual(self.a.profile(), [])                                             # nichts halb angelegt

    def test_abbrechen_raeumt_alles_auf(self):
        d = self.hochladen([("j.png", b"jugend", "image/png")], name="Lea").json()
        datei = self.a.ordner / "garantie" / "tickets" / self.a.tickets(d["id"])[0]["datei"]
        self.assertTrue(datei.exists())
        self.assertEqual(self.c.delete(f"/api/garantie/profile/{d['id']}").status_code, 200)
        self.assertEqual((self.a.profile(), self.a._alle_tickets(), datei.exists()), ([], [], False))


class FormularFahrt(tg.Basis):
    """Die Teilstrecke im Formular muss die sein, die Eike gewählt hat — nicht irgendeine derselben Linie.

    Am 20.09.2026 bot das Formular für „Ahlem → Kröpcke“ sowohl den Bus 120 durchgehend als auch eine
    Umsteigeverbindung an, deren erste Teilstrecke schon in Bernhard-Caspar-Straße endet. Der Code nahm die
    kürzere — der Antrag hätte eine andere Strecke genannt als die gefahrene.
    """

    START = {"name": "Hannover, Ahlem", "reference": "de:03241:537"}
    ZIEL = {"name": "Hannover, Kröpcke", "reference": "de:03241:11"}

    def leg(self, von, nach, linie="120", ab="2026-09-20T04:09:00.000+00:00", an="2026-09-20T04:41:00.000+00:00"):
        return {"lineName": linie, "fromStationName": von, "toStationName": nach,
                "scheduledFromDateTime": ab, "scheduledToDateTime": an, "selectable": True}

    def suchen(self, verbindungen):
        ab = antrag._zeit("2026-09-20T06:09:00+02:00")
        with mock.patch.object(antrag.formular, "fahrten", mock.AsyncMock(side_effect=[verbindungen] + [[]] * 5)):
            return asyncio.run(antrag._im_formular(self.START, self.ZIEL, ab, "120"))

    def test_nimmt_die_durchgehende_und_nicht_die_kurze_teilstrecke(self):
        umsteigen = {"signature": "sig1", "legs": [
            self.leg("Hannover Ahlem", "Hannover Bernhard-Caspar-Straße", an="2026-09-20T04:24:00.000+00:00"),
            self.leg("Hannover/Bernhard-Caspar-Straße", "Hannover/Kröpcke", linie="9",
                     ab="2026-09-20T04:28:00.000+00:00", an="2026-09-20T04:38:00.000+00:00")]}
        durchgehend = {"signature": "sig2", "legs": [self.leg("Hannover Ahlem", "Hannover Kröpcke")]}
        fahrt, leg = self.suchen([umsteigen, durchgehend])
        self.assertEqual(fahrt["signature"], "sig2")
        self.assertEqual(leg["toStationName"], "Hannover Kröpcke")

    def test_ohne_passende_teilstrecke_lieber_nichts(self):
        nur_kurz = {"signature": "sig1", "legs": [
            self.leg("Hannover Ahlem", "Hannover Bernhard-Caspar-Straße", an="2026-09-20T04:24:00.000+00:00")]}
        self.assertEqual(self.suchen([nur_kurz]), (None, None))

    def test_schreibweisen_gelten_als_gleich(self):
        for name in ("Hannover Kröpcke", "Hannover/Kröpcke", "Hannover, Kröpcke", "Kröpcke"):
            fahrt, leg = self.suchen([{"signature": "s", "legs": [self.leg("Hannover Ahlem", name)]}])
            self.assertIsNotNone(leg, name)
        for falsch in ("Hannover Steintor", "Hannover Bernhard-Caspar-Straße", ""):
            self.assertEqual(self.suchen([{"signature": "s", "legs": [self.leg("Hannover Ahlem", falsch)]}]), (None, None), falsch)

    def test_falsche_linie_oder_zeit_zaehlt_weiter_nicht(self):
        self.assertEqual(self.suchen([{"signature": "s", "legs": [self.leg("Hannover Ahlem", "Hannover Kröpcke", linie="200")]}]),
                         (None, None))
        spaet = self.leg("Hannover Ahlem", "Hannover Kröpcke", ab="2026-09-20T04:15:00.000+00:00")
        self.assertEqual(self.suchen([{"signature": "s", "legs": [spaet]}]), (None, None))

    def test_auch_der_start_muss_passen(self):
        falscher_start = self.leg("Hannover Steintor", "Hannover Kröpcke")
        self.assertEqual(self.suchen([{"signature": "s", "legs": [falscher_start]}]), (None, None))
