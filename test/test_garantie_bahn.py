"""Bahn-Fahrgastrechte (21.09.2026) — ohne Netz, ohne Bahn, ohne Gemini.

Die Beträge und Grenzen stammen aus den Tarifbedingungen (Deutschlandtarif Teil A Nr. 8, Teil C Nr. 8, Stand 14.06.2026
und 14.12.2025) und der EU-Verordnung 2021/782, Art. 19 — siehe docs/LEHREN.md. Die IBAN ist das Beispiel aus der Norm.
"""

import asyncio
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import gvh, push
from kern.garantie import ablage, abschicken, antrag, bahnablage, bahnformular, fahrgastrechte as fgr
from kern.main import app
from test.test_garantie import ablagen_schliessen

BERLIN = ZoneInfo("Europe/Berlin")
JETZT = datetime(2026, 9, 21, 20, 0, tzinfo=BERLIN)
NACH_DEM_MONAT = datetime(2026, 10, 5, 12, 0, tzinfo=BERLIN)
BEISPIEL_IBAN = "DE89370400440532013000"
BENNIGSEN, WEETZEN, HBF = "de:03241:5844", "de:03241:5600", "de:03241:31"
DTICKET = {"art": "Deutschlandticket", "bezeichnung": "D-Ticket Hannover Jugend", "herausgeber": "ÜSTRA",
           "gueltig_von": "2026-09-01", "gueltig_bis": "2026-09-30", "inhaber": "", "zweifel": "", "preis": 35.4}


def _t(tag, stunde, minute=0):
    return datetime(2026, 9, tag, stunde, minute, tzinfo=BERLIN)


def _verlauf(ab, an, echt, linie="S5", kategorie="S-Bahn", betreiber="S-Bahn Hannover (Transdev)", ziel_faellt_aus=False):
    return {"jid": f"J-{linie}-{ab:%d%H%M}", "linie": linie, "kategorie": kategorie, "richtung": "Hannover Flughafen",
            "betreiber": betreiber, "halte": [
                {"name": "Bennigsen Bahnhof", "reference": BENNIGSEN, "an_plan": None, "an_echt": None,
                 "ab_plan": ab, "ab_echt": ab, "verspaetung": 0, "faellt_aus": False},
                {"name": "Weetzen Bahnhof", "reference": WEETZEN, "an_plan": ab + timedelta(minutes=8),
                 "an_echt": ab + timedelta(minutes=9), "ab_plan": ab + timedelta(minutes=9),
                 "ab_echt": ab + timedelta(minutes=10), "verspaetung": 1, "faellt_aus": False},
                {"name": "Hannover Hauptbahnhof", "reference": HBF, "an_plan": an, "an_echt": echt,
                 "ab_plan": an + timedelta(minutes=2), "ab_echt": echt, "verspaetung": None, "faellt_aus": ziel_faellt_aus}]}


class Basis(unittest.TestCase):
    jetzt = JETZT

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=self.jetzt)
        self.uhr.start()
        konnektoren.setzen(fgr.KONNEKTOR, True)

    def tearDown(self):
        ablagen_schliessen()
        self.uhr.stop()
        self.env.stop()

    def angaben(self, profil=0):
        ablage.standard().angaben_setzen({"anrede": "Herr", "vorname": "Max", "nachname": "Mustermann",
                                          "strasse": "Musterweg 12a", "plz": "30974", "ort": "Wennigsen",
                                          "email": "max+garantie@example.org", "kontoinhaber": "Max Mustermann",
                                          "iban": BEISPIEL_IBAN, "abo_nummer": "1234567"}, profil)

    def ticket(self, daten=DTICKET, profil=0):
        return ablage.standard().ticket_speichern("t.png", "image/png", b"\x89PNG" + str(daten).encode(), daten, profil)

    def fall(self, ab, an, echt, profil=0, **kw):
        """Eine gemerkte Fahrt und ein Fall darauf — gemessen wie in der Garantie (Echtzeit am Ziel)."""
        linie = kw.pop("linie", "S5")
        kategorie = kw.pop("kategorie", "S-Bahn")
        betreiber = kw.pop("betreiber", "S-Bahn Hannover (Transdev)")
        v = _verlauf(ab, an, echt, linie, kategorie, betreiber)
        rid = ablage.standard().reise_merken({"jid": v["jid"], "linie": linie, "kategorie": kategorie, "richtung": "x",
                                              "plan": ab, "echt": ab, "verspaetung": 25, "halt": "Bennigsen"})
        with mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=v)):
            r = asyncio.run(fgr.erfassen(rid, BENNIGSEN, HBF, profil, **kw))
        return rid, r


# ---------- Regeln ----------

class Regeln(unittest.TestCase):
    def test_einzelfahrkarte_25_und_50_prozent_auf_5_cent_aufgerundet(self):
        self.assertEqual(fgr.einzel_betrag(2990, 59), (0, 0))
        self.assertEqual(fgr.einzel_betrag(2990, 60), (750, 25))          # 7,475 € → 7,50 €
        self.assertEqual(fgr.einzel_betrag(2990, 119), (750, 25))
        self.assertEqual(fgr.einzel_betrag(2990, 120), (1495, 50))        # 14,95 € glatt
        self.assertEqual(fgr.einzel_betrag(5990, 74), (1500, 25))         # 14,975 € → 15,00 €
        self.assertEqual(fgr.einzel_betrag(None, 90), (0, 0))

    def test_bagatellgrenze(self):
        self.assertTrue(fgr.ausgezahlt("einzel", 400))                   # „unter 4 € nicht“ — 4 € schon
        self.assertFalse(fgr.ausgezahlt("einzel", 395))
        self.assertFalse(fgr.ausgezahlt("dticket", 400))                  # Teil C: muss 4 € ÜBERSTEIGEN
        self.assertTrue(fgr.ausgezahlt("dticket", 450))

    def _f(self, *minuten):
        return [{"id": i, "verspaetung": m} for i, m in enumerate(minuten, 1)]

    def test_deutschlandticket_sammelt_ab_20_minuten_je_volle_stunde(self):
        r = fgr.zeitkarte_rechnen("dticket", self._f(25, 30, 22, 40, 15), None)
        self.assertEqual((r["zaehlbar"], r["minuten"], r["einheiten"], r["betrag_cent"]), (4, 117, 1, 150))
        # Nie weniger als die Einzelfall-Rechnung: drei Fälle ab 60 Minuten sind mindestens drei volle Stunden.
        r = fgr.zeitkarte_rechnen("dticket", self._f(65, 70, 61), None)
        self.assertEqual((r["einheiten"], r["betrag_cent"]), (3, 450))

    def test_andere_zeitkarte_nur_ab_60_minuten_je_fall(self):
        r = fgr.zeitkarte_rechnen("zeitkarte", self._f(25, 59, 60, 130), None)
        self.assertEqual((r["zaehlbar"], r["einheiten"], r["betrag_cent"]), (2, 2, 300))

    def test_deckel_25_prozent_und_so_wenige_formulare_wie_moeglich(self):
        deckel = 3540 * 25 // 100                                          # D-Ticket Hannover Jugend: 8,85 €
        r = fgr.zeitkarte_rechnen("dticket", self._f(*([65] * 9)), deckel)
        self.assertEqual((r["einheiten"], r["gedeckelt"], r["betrag_cent"]), (9, True, 885))
        # Sechs Stunden bringen schon den Deckel — mehr Formulare zum Unterschreiben bringen nichts.
        self.assertEqual(len(r["benoetigt"]), 6)
        # 180 Minuten allein sind schon drei volle Stunden — die zwei kurzen Fälle braucht es nicht.
        r = fgr.zeitkarte_rechnen("dticket", self._f(180, 25, 30), deckel)
        self.assertEqual((r["benoetigt"], r["betrag_cent"]), ([1], 450))
        r = fgr.zeitkarte_rechnen("dticket", self._f(50, 40, 30), deckel)
        self.assertEqual((r["benoetigt"], r["einheiten_benoetigt"], r["betrag_cent"]), ([1, 2, 3], 2, 300))

    def test_fristen(self):
        f = fgr.fristen(datetime(2026, 8, 31).date())
        self.assertEqual((f["ab"], f["empfohlen"], f["spaetestens"]), ("2026-09-01", "2026-11-30", "2027-08-31"))

    def test_zuege_und_betreiber(self):
        for kategorie, linie, soll in (("S-Bahn", "S5", True), ("R-Bahn", "RE60", True), ("Zug", "ICE 643", True),
                                       ("ICE", "ICE 2903", True), ("Stadtbahn", "9", False), ("Bus", "360", False),
                                       ("Ersatzverkehr", "SEV", False)):
            with self.subTest(linie):
                self.assertEqual(fgr.ist_bahn(kategorie, linie), soll)
        self.assertTrue(fgr.ist_fernverkehr("Zug", "ICE 643"))
        self.assertFalse(fgr.ist_fernverkehr("R-Bahn", "RE2"))
        self.assertEqual([fgr.stelle_fuer(b) for b in ("S-Bahn Hannover (Transdev)", "metronom", "DB Regio AG Nord", "enno",
                                                       "Verkehrsgesellschaft Start Deutschland mbH", "DB Fernverkehr AG",
                                                       "WestfalenBahn", "", "Österreichische Bundesbahnen")],
                         ["transdev", "servicecenter", "servicecenter", "servicecenter", "servicecenter", "servicecenter",
                          "westfalenbahn", "unklar", "unklar"])

    def test_fernzug_ohne_betreiber_geht_ins_servicecenter(self):
        """Gemessen am 22.09.2026: „ICE 144“ kam ohne Betreiber. ICE/IC/EC fährt in Deutschland DB Fernverkehr."""
        self.assertEqual(fgr.stelle_fuer("", "ICE 144"), "servicecenter")
        self.assertEqual(fgr.stelle_fuer("", "IC 2446"), "servicecenter")
        self.assertEqual(fgr.stelle_fuer("", "NJ 40491"), "unklar")
        self.assertEqual(fgr.stelle_fuer("", "S5"), "unklar")

    def test_kartenart(self):
        self.assertEqual(fgr.kartenart(DTICKET), "dticket")
        self.assertEqual(fgr.kartenart({"art": "Monatskarte", "bezeichnung": "Jugendnetzkarte"}), "zeitkarte")
        self.assertIsNone(fgr.kartenart({"art": "Monatskarte", "ist_kundenkarte": True}))
        self.assertIsNone(fgr.kartenart({"art": "Einzelkarte"}))


# ---------- Fälle ----------

class Faelle(Basis):
    def test_gemessen_und_gesammelt(self):
        self.ticket()
        _, r = self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        f = r["fall"]
        self.assertTrue(r["ok"], r["text"])
        self.assertEqual((f["verspaetung"], f["stand"], f["art"], f["stelle"]), (67, "gesammelt", "dticket", "transdev"))
        self.assertIn("Eingereicht wird nichts", r["text"])
        self.assertNotIn("jid", {k for k, v in f.items() if v})              # die lange Kennung geht nicht an die App

    def test_eine_fahrt_nur_einmal_je_person(self):
        rid, _ = self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        v = _verlauf(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        with mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=v)):
            zweiter = asyncio.run(fgr.erfassen(rid, WEETZEN, HBF))
        self.assertFalse(zweiter["ok"])
        self.assertIn("nur einmal", zweiter["text"])

    def test_bus_hat_keine_fahrgastrechte(self):
        _, r = self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41), linie="360", kategorie="Bus", betreiber="regiobus")
        self.assertFalse(r["ok"])
        self.assertIn("ÜSTRA-Garantie", r["text"])

    def test_fernzug_mit_zeitkarte_kein_anspruch_mit_einzelfahrkarte_schon(self):
        self.ticket()
        _, r = self.fall(_t(15, 19, 31), _t(15, 21, 10), _t(15, 22, 27), linie="ICE 643", kategorie="Zug",
                         betreiber="DB Fernverkehr AG")
        self.assertEqual(r["fall"]["stand"], "kein Anspruch")
        self.assertIn("Fernzug", " ".join(r["fall"]["hindernisse"]))
        _, r = self.fall(_t(16, 19, 31), _t(16, 21, 10), _t(16, 22, 24), linie="ICE 645", kategorie="Zug",
                         betreiber="DB Fernverkehr AG", fahrkarte="einzel", preis_cent=5990, auftrag="ABC123")
        f = r["fall"]
        self.assertEqual((f["stand"], f["verspaetung"], f["prozent"], f["betrag_cent"], f["stelle"]),
                         ("bereit", 74, 25, 1500, "servicecenter"))
        self.assertIn("15,00 €", r["text"])

    def test_ziel_in_der_zukunft_wartet_und_wird_nachgemessen(self):
        self.ticket()
        _, r = self.fall(_t(21, 19, 30), _t(21, 19, 52), _t(21, 20, 50))
        self.assertEqual(r["fall"]["stand"], "wartet")
        fid = r["fall"]["id"]
        spaeter = JETZT + timedelta(hours=2)
        v = _verlauf(_t(21, 19, 30), _t(21, 19, 52), _t(21, 20, 56))
        with mock.patch("kern.config.jetzt", return_value=spaeter), \
                mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=v)):
            self.assertEqual(asyncio.run(fgr.nachpruefen()), 1)
            f = fgr.bewerten(bahnablage.fall(fid))
        self.assertEqual((f["verspaetung"], f["stand"]), (64, "gesammelt"))

    def test_ohne_messung_nach_stunden_kein_anspruch(self):
        _, r = self.fall(_t(21, 18, 0), _t(21, 18, 22), None)
        fid = r["fall"]["id"]
        v = _verlauf(_t(21, 18, 0), _t(21, 18, 22), None)
        with mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=v)):
            asyncio.run(fgr.nachpruefen())                                   # noch keine 6 Stunden: weiter warten
            self.assertFalse(bahnablage.fall(fid)["nachgeprueft"])
            with mock.patch("kern.config.jetzt", return_value=JETZT + timedelta(hours=7)):
                asyncio.run(fgr.nachpruefen())
                f = fgr.bewerten(bahnablage.fall(fid))
        self.assertEqual(f["stand"], "kein Anspruch")
        self.assertIn("keine gemessene Ankunft", " ".join(f["hindernisse"]))

    def test_unter_20_minuten_zaehlt_nicht(self):
        self.ticket()
        _, r = self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 7, 49))
        self.assertEqual(r["fall"]["stand"], "kein Anspruch")
        self.assertIn("ab 20 Minuten", " ".join(r["fall"]["hindernisse"]))


# ---------- Garantie ODER Fahrgastrechte ----------

class NieBeides(Basis):
    def test_garantie_antrag_sperrt_den_bahn_fall(self):
        v = _verlauf(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        rid = ablage.standard().reise_merken({"jid": v["jid"], "linie": "S5", "kategorie": "S-Bahn", "richtung": "x",
                                              "plan": _t(15, 7, 12), "verspaetung": 67, "halt": "Bennigsen"})
        ablage.standard().antrag_speichern({"reise": rid, "linie": "S5", "ab": _t(15, 7, 12), "an": _t(15, 7, 34)})
        with mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=v)):
            r = asyncio.run(fgr.erfassen(rid, BENNIGSEN, HBF))
        self.assertFalse(r["ok"])
        self.assertIn("nie beides", r["text"])

    def test_bahn_fall_sperrt_die_garantie(self):
        rid, r = self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        self.assertTrue(r["ok"])
        antwort = asyncio.run(antrag.vorbereiten(rid, BENNIGSEN, HBF))
        self.assertFalse(antwort["ok"])
        self.assertIn("Bahn-Fahrgastrecht", antwort["text"])
        # … und ein älterer Garantie-Antrag für dieselbe Fahrt geht nicht mehr raus.
        aid = ablage.standard().antrag_speichern({"reise": rid, "linie": "S5", "ab": _t(15, 7, 12), "an": _t(15, 7, 34)})
        self.assertIn("Bahn-Fahrgastrecht", " ".join(abschicken.widersprueche(aid)))
        # Und in der Personenwahl der Garantie steht es auch.
        with mock.patch("kern.garantie.uebersicht.antrag._grenzen", return_value=[]):
            personen = TestClient(app).get(f"/api/garantie/fahrt/personen?id={rid}").json()["personen"]
        ich = next(p for p in personen if p["id"] == 0)
        self.assertFalse(ich["geht"])
        self.assertIn("Bahn-Fahrgastrecht", " ".join(ich["gruende"]))

    def test_schon_als_garantiefall_bei_der_uestra(self):
        ablage.standard().vorgang_speichern({"vorgang": "8860001", "fahrtdatum": "2026-09-15", "ab": "7:12", "linie": "S5",
                                             "status": "eingang", "letzte_mail": JETZT.isoformat()})
        _, r = self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        self.assertEqual(r["fall"]["stand"], "kein Anspruch")
        self.assertIn("8860001", " ".join(r["fall"]["hindernisse"]))

    def test_niemand_sitzt_in_zwei_zuegen(self):
        self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        _, r = self.fall(_t(15, 7, 20), _t(15, 7, 50), _t(15, 9, 0), linie="RE2", kategorie="R-Bahn",
                         betreiber="DB Regio AG Nord")
        self.assertIn("zwei Zügen", " ".join(r["fall"]["hindernisse"]))

    def test_ticket_auf_einen_anderen_namen(self):
        self.angaben()
        self.ticket(dict(DTICKET, inhaber="Lea Schmidt"))
        _, r = self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        self.assertIn("Lea Schmidt", " ".join(r["fall"]["hindernisse"]))


# ---------- Sammelanträge ----------

class Sammelantrag(Basis):
    jetzt = NACH_DEM_MONAT

    def drei_stunden(self):
        self.angaben()
        self.ticket()
        for tag, echt in ((10, _t(10, 8, 39)), (15, _t(15, 8, 44)), (18, _t(18, 8, 35))):
            _, r = self.fall(_t(tag, 7, 12), _t(tag, 7, 34), echt)
            self.assertTrue(r["ok"], r["text"])

    def test_bereit_nach_ablauf_mit_rechnung(self):
        self.drei_stunden()
        (e,) = fgr.entwuerfe()
        self.assertEqual((e["stand"], e["art"], e["stelle"], e["betrag_cent"]), ("bereit", "dticket", "transdev", 450))
        self.assertEqual(e["rechnung"]["minuten"], 65 + 70 + 61)
        self.assertEqual(e["frist"]["empfohlen"], "2026-12-30")
        self.assertIn("Preis", " ".join(e["hinweise"]))                    # Nachweis des gezahlten Preises

    def test_vor_ablauf_wird_nur_gesammelt(self):
        self.drei_stunden()
        with mock.patch("kern.config.jetzt", return_value=JETZT):
            (e,) = fgr.entwuerfe()
        self.assertEqual(e["stand"], "sammelt")

    def test_zwei_stunden_sind_unter_4_euro(self):
        self.angaben()
        self.ticket()
        self.fall(_t(10, 7, 12), _t(10, 7, 34), _t(10, 8, 39))
        self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 44))
        (e,) = fgr.entwuerfe()
        self.assertEqual((e["stand"], e["betrag_cent"]), ("unter 4 €", 300))

    def test_ohne_preis_unvollstaendig(self):
        self.angaben()
        self.ticket(dict(DTICKET, preis=None))
        for tag in (10, 15, 18):
            self.fall(_t(tag, 7, 12), _t(tag, 7, 34), _t(tag, 8, 40))
        (e,) = fgr.entwuerfe()
        self.assertEqual(e["stand"], "unvollständig")
        self.assertIn("Preis der Karte", " ".join(e["fehlt"]))
        self.assertIn("35,40 €", " ".join(e["hinweise"]))

    def test_je_bahn_ein_antrag(self):
        self.angaben()
        self.ticket()
        self.fall(_t(10, 7, 12), _t(10, 7, 34), _t(10, 8, 39))
        self.fall(_t(11, 7, 12), _t(11, 7, 34), _t(11, 8, 39), linie="RE3", kategorie="R-Bahn", betreiber="metronom")
        self.assertEqual(sorted(e["stelle"] for e in fgr.entwuerfe()), ["servicecenter", "transdev"])

    def test_25_prozent_gelten_fuer_die_karte_nicht_je_bahn(self):
        """Gefunden am 22.09.2026 im Probelauf: zwei Bahnen im selben Monat bekamen je 25 % — die Grenze gilt für die Karte."""
        self.angaben()
        self.ticket()
        for tag in range(2, 29, 3):                                        # neun Fahrten mit je 65 Minuten je Bahn
            self.fall(_t(tag, 7, 12), _t(tag, 7, 34), _t(tag, 8, 39))
            self.fall(_t(tag, 17, 12), _t(tag, 17, 34), _t(tag, 18, 39), linie="RE3", kategorie="R-Bahn",
                      betreiber="metronom")
        liste = fgr.entwuerfe()
        self.assertEqual(len(liste), 2)
        zahlbar = [e for e in liste if fgr.ausgezahlt(e["art"], e["betrag_cent"])]
        self.assertEqual(sum(e["betrag_cent"] for e in zahlbar), 885)      # 25 % von 35,40 € — einmal, nicht zweimal
        (erster,) = zahlbar
        fgr.einreichen(erster["schluessel"])
        # Übrig bleiben die nicht gebrauchten Fälle der ersten Bahn (als „schon eingereicht“) und die zweite Bahn — leer.
        (rest,) = [e for e in fgr.entwuerfe() if e["stand"] != "schon eingereicht"]
        self.assertEqual((rest["betrag_cent"], rest["stand"]), (0, "unter 4 €"))
        self.assertIn("insgesamt", " ".join(rest["hinweise"]))

    def test_einreichen_bindet_die_faelle(self):
        self.drei_stunden()
        (e,) = fgr.entwuerfe()
        eingereicht = fgr.einreichen(e["schluessel"])
        self.assertEqual((eingereicht["status"], eingereicht["erwartet_cent"]), ("eingereicht", 450))
        self.assertEqual(fgr.entwuerfe(), [])
        with self.assertRaises(LookupError):
            fgr.einreichen(e["schluessel"])
        fid = eingereicht["faelle"][0]
        self.assertFalse(bahnablage.fall_loeschen(fid))
        self.assertEqual(fgr.bewerten(bahnablage.fall(fid))["stand"], "eingereicht")
        fertig = fgr.bescheid(eingereicht["id"], "genehmigt", 450)
        self.assertEqual(fertig["erhalten"], "4,50 €")
        self.assertEqual(fgr.summen()["genehmigt_cent"], 450)

    def test_nur_bereit_laesst_sich_einreichen(self):
        self.angaben()
        self.ticket()
        self.fall(_t(10, 7, 12), _t(10, 7, 34), _t(10, 8, 39))
        (e,) = fgr.entwuerfe()
        with self.assertRaises(ValueError):
            fgr.einreichen(e["schluessel"])

    def test_freund_braucht_zustimmung(self):
        a = ablage.standard()
        pid = a.profil_anlegen({"name": "Lea", "vorname": "Lea", "nachname": "Schmidt"})
        self.angaben(pid)
        self.ticket(profil=pid)
        for tag in (10, 15, 18):
            _, r = self.fall(_t(tag, 7, 12), _t(tag, 7, 34), _t(tag, 8, 40), profil=pid)
            self.assertTrue(r["ok"], r["text"])
        (e,) = fgr.entwuerfe(pid)
        self.assertEqual(e["stand"], "unvollständig")
        self.assertIn("Zustimmung", " ".join(e["fehlt"]))
        self.assertEqual(fgr.entwuerfe(0), [])                              # Eike sieht Leas Fälle nicht

    def test_erinnern_genau_einmal(self):
        self.drei_stunden()
        with mock.patch.object(push, "senden", return_value={"gesendet": 1}) as senden:
            self.assertEqual(asyncio.run(fgr.erinnern()), 1)
            self.assertEqual(asyncio.run(fgr.erinnern()), 0)
        self.assertIn("4,50 €", senden.call_args[0][1])


# ---------- Formular und Abtippen ----------

class Formular(Basis):
    jetzt = NACH_DEM_MONAT

    def test_vorlage_hat_die_erwarteten_felder(self):
        """Kommt eine neue Fassung des DB-Formulars, soll das auffallen — nicht still leere Formulare erzeugen."""
        from pypdf import PdfReader
        felder = PdfReader(str(bahnformular.VORLAGE)).get_fields()
        erwartet = {"journey", "planned_day", "planned_departure_station", "planned_destination_station",
                    "arrived_hours", "ticket_season", "ticket_season_number", "compensation", "compensation_iban",
                    "personal", "personal_firstname", "personal_lastname", "personal_street", "personal_postcode",
                    "further_other", "further_text", "signature"}
        self.assertTrue(erwartet <= set(felder), erwartet - set(felder))

    def test_bahnhof_und_strasse(self):
        self.assertEqual(bahnformular.bahnhof("Hannover, Hauptbahnhof"), "Hannover Hbf")
        self.assertEqual(bahnformular.bahnhof("S+U Berlin Hauptbahnhof [Gleis 1-8]"), "Berlin Hbf")
        self.assertEqual(bahnformular.bahnhof("S+U Berlin Hauptbahnhof [Gleis 40003200|0|2"), "Berlin Hbf")
        self.assertEqual(bahnformular.bahnhof("Bennigsen Bahnhof"), "Bennigsen")
        self.assertEqual(bahnformular.strasse_teilen("Musterweg 12a"), ("Musterweg", "12a"))
        self.assertEqual(bahnformular.strasse_teilen("Am Markt 3 - 5"), ("Am Markt", "3-5"))

    def test_felder_ehrlich_ausgefuellt(self):
        self.angaben()
        self.ticket()
        _, r = self.fall(_t(10, 7, 12), _t(10, 7, 34), _t(10, 8, 39))
        _, kurz = self.fall(_t(12, 7, 12), _t(12, 7, 34), _t(12, 8, 1))
        e = {"titel": "Deutschlandticket 01.09.–30.09.2026"}
        a = ablage.standard()
        t, k, notizen = bahnformular.felder(r["fall"], e, a.angaben(0), a.ticket(0), 1, 2)
        self.assertEqual((t["planned_departure_station"], t["planned_destination_station"], t["arrived_hours"],
                          t["arrived_minutes"], t["personal_housenumber"], t["ticket_season_number"]),
                         ("Bennigsen", "Hannover Hbf", "08", "39", "12a", "1234567"))
        self.assertEqual(k["journey"], "/Versp")                            # „Verspätung am Ziel (mind. 60 Minuten)“
        self.assertNotIn("compensation_iban", t)                            # die IBAN schreibt die Person selbst
        # 27 Minuten: „mind. 60 Minuten“ wäre falsch — stattdessen „Sonstiges“ mit Verweis aufs Anschreiben.
        t2, k2, _ = bahnformular.felder(kurz["fall"], e, a.angaben(0), a.ticket(0), 2, 2)
        self.assertNotIn("journey", k2)
        self.assertEqual((k2["further_other"], t2["further_text"]), ("/Ja", "27 Min, Anschreiben 2/2"))
        self.assertEqual(notizen, [])

    def test_zu_lang_wird_nicht_gekuerzt(self):
        self.angaben()
        ablage.standard().angaben_setzen({"vorname": "Maximilian-Alexander-Friedrich"})
        self.ticket()
        _, r = self.fall(_t(10, 7, 12), _t(10, 7, 34), _t(10, 8, 39))
        t, _, notizen = bahnformular.felder(r["fall"], {"titel": "x"}, ablage.standard().angaben(0), None, 1, 1)
        self.assertEqual(t["personal_firstname"], "")
        self.assertIn("Maximilian-Alexander-Friedrich", " ".join(notizen))

    def test_formular_werte_und_knoepfe_im_pdf(self):
        from pypdf import PdfReader
        roh = bahnformular._formular_bytes({"planned_departure_station": "Bennigsen", "personal_lastname": "Müller"},
                                           {"journey": "/Versp", "personal": "/Frau", "compensation": "/Geld",
                                            "ticket_season": "/Ja"})
        felder = PdfReader(io.BytesIO(roh)).get_fields()
        self.assertEqual(felder["planned_departure_station"]["/V"], "Bennigsen")
        self.assertEqual(felder["personal_lastname"]["/V"], "Müller")
        self.assertTrue(str(felder["journey"]["/V"]).startswith("/Versp"))   # nicht „Reise nicht angetreten“
        self.assertEqual(felder["personal"]["/V"], "/Frau")
        self.assertTrue(str(felder["compensation"]["/V"]).startswith("/Geld"))
        self.assertEqual(felder["ticket_season"]["/V"], "/Ja")

    def _servicecenter_bereit(self):
        self.angaben()
        self.ticket()
        for tag, echt in ((10, _t(10, 8, 39)), (15, _t(15, 8, 44)), (18, _t(18, 8, 35))):
            self.fall(_t(tag, 7, 12), _t(tag, 7, 34), echt, linie="RE3", kategorie="R-Bahn", betreiber="metronom")
        (e,) = fgr.entwuerfe()
        self.assertEqual(e["stand"], "bereit")
        return e

    def test_pdf_ohne_iban_mit_formular_je_fall(self):
        from pypdf import PdfReader
        e = self._servicecenter_bereit()
        inhalt, name = bahnformular.pdf(e["schluessel"])
        seiten = PdfReader(io.BytesIO(inhalt)).pages
        self.assertEqual(len(seiten), 1 + 1 + 3 * 2)                        # Hinweis, Anschreiben, je Fall 2 Seiten
        text = seiten[1].extract_text()
        self.assertIn("Sammelantrag", text)
        self.assertIn("4,50", text)
        self.assertNotIn("0532013000", seiten[0].extract_text() + text)
        self.assertNotIn(b"0532013000", inhalt)
        self.assertTrue(name.endswith(".pdf"))

    def test_unfertig_wird_nicht_gedruckt(self):
        self.angaben()
        self.ticket()
        self.fall(_t(10, 7, 12), _t(10, 7, 34), _t(10, 8, 39), linie="RE3", kategorie="R-Bahn", betreiber="metronom")
        (e,) = fgr.entwuerfe()
        with self.assertRaises(ValueError):
            bahnformular.pdf(e["schluessel"])

    def test_abtippen_fuer_die_s_bahn(self):
        self.angaben()
        self.ticket()
        for tag in (10, 15, 18):
            self.fall(_t(tag, 7, 12), _t(tag, 7, 34), _t(tag, 8, 40))
        (e,) = fgr.entwuerfe()
        d = bahnformular.abtippen(e["schluessel"])
        zeilen = {z[0]: z[1] for g in d["gruppen"] for z in g["zeilen"]}
        self.assertEqual((zeilen["Zielbahnhof"], zeilen["Fahrkartenart"], zeilen["Erstattungsart"]),
                         ("Hannover Hbf", "Zeitkarte Nahverkehr", "Überweisung"))
        iban = next(v for k, v in zeilen.items() if k.startswith("IBAN"))
        self.assertEqual(iban, "DE•• … 3000")
        self.assertNotIn("0532013000", str(d))


# ---------- Schnittstelle ----------

class Schnittstelle(Basis):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)

    def test_ohne_freigabe_nichts(self):
        konnektoren.setzen(fgr.KONNEKTOR, False)
        self.assertEqual(self.c.get("/api/garantie/bahn").json()["aktiv"], False)
        with mock.patch.object(gvh, "_ruf", mock.AsyncMock()) as netz:
            self.assertEqual(self.c.get("/api/garantie/bahn/zuege?halt=Hannover%20Hbf&zeit=2026-09-21T19:00").status_code, 409)
            self.assertEqual(self.c.get("/api/garantie/bahn/verlauf?id=abcdef1234").status_code, 409)
            r = self.c.post("/api/garantie/bahn/fall", json={"fahrt": "abcdef1234", "start": BENNIGSEN, "ziel": HBF})
            self.assertEqual(r.status_code, 409)
            self.assertEqual(self.c.get("/api/garantie/bahn/antrag/p0-zk-abc-transdev/pdf").status_code, 409)
        netz.assert_not_awaited()

    def test_vorschau_auf_der_freigaben_seite_an_und_aus(self):
        """Gefunden am 22.09.2026 beim Live-Check: Die Vorschau stürzte bei ausgeschaltetem Konnektor ab (500)."""
        konnektoren.setzen(fgr.KONNEKTOR, False)
        aus = self.c.get("/api/konnektoren/fahrgastrechte/vorschau")
        self.assertEqual(aus.status_code, 200)
        self.assertIn("aus", aus.json()["text"])
        konnektoren.setzen(fgr.KONNEKTOR, True)
        an = self.c.get("/api/konnektoren/fahrgastrechte/vorschau")
        self.assertEqual(an.status_code, 200)
        self.assertIn("Bahn-Fälle gesammelt", an.json()["text"])

    def test_standardmaessig_aus(self):
        konnektoren.setzen(fgr.KONNEKTOR, False)
        self.assertFalse(konnektoren.benutzbar(fgr.KONNEKTOR))
        self.assertFalse(konnektoren.KONNEKTOREN[fgr.KONNEKTOR].get("schreibbar"))   # nichts, was hinausgeht

    def test_stand_ohne_iban(self):
        self.angaben()
        self.ticket()
        self.fall(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        d = self.c.get("/api/garantie/bahn").json()
        self.assertTrue(d["aktiv"])
        self.assertEqual(len(d["faelle"]), 1)
        self.assertNotIn("0532013000", self.c.get("/api/garantie/bahn").text)
        self.assertNotIn("0532013000", self.c.get("/api/garantie").text)
        self.assertIn("bahn", self.c.get("/api/garantie/uebersicht").json())

    def test_zuege_nur_in_einem_sinnvollen_zeitraum(self):
        r = self.c.get("/api/garantie/bahn/zuege?halt=Hannover%20Hbf&zeit=2026-08-01T12:00")
        self.assertEqual(r.status_code, 400)

    def test_eingereicht_braucht_bestaetigung_und_schluessel_wird_geprueft(self):
        self.assertEqual(self.c.post("/api/garantie/bahn/antrag/p0-zk-abc-transdev/eingereicht", json={}).status_code, 400)
        self.assertEqual(self.c.get("/api/garantie/bahn/antrag/..%2Fetc/pdf").status_code, 404)

    def test_fall_anlegen_und_verwerfen(self):
        self.ticket()
        v = _verlauf(_t(15, 7, 12), _t(15, 7, 34), _t(15, 8, 41))
        rid = ablage.standard().reise_merken({"jid": v["jid"], "linie": "S5", "kategorie": "S-Bahn", "richtung": "x",
                                              "plan": _t(15, 7, 12), "verspaetung": 67, "halt": "Bennigsen"})
        with mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=v)):
            r = self.c.post("/api/garantie/bahn/fall", json={"fahrt": rid, "start": BENNIGSEN, "ziel": HBF}).json()
            verlauf = self.c.get(f"/api/garantie/bahn/verlauf?id={rid}").json()
        self.assertTrue(r["ok"], r)
        self.assertEqual((verlauf["zug"], verlauf["stelle"]), (True, "S-Bahn Hannover (Transdev)"))
        fid = r["fall"]["id"]
        self.assertEqual(self.c.post(f"/api/garantie/bahn/fall/{fid}", json={"status": "verworfen"}).json()["fall"]["stand"],
                         "verworfen")
        self.assertEqual(self.c.delete(f"/api/garantie/bahn/fall/{fid}").status_code, 200)

    def test_einzelfahrkarte_braucht_preis(self):
        r = self.c.post("/api/garantie/bahn/fall", json={"fahrt": "abcdef1234", "start": BENNIGSEN, "ziel": HBF,
                                                          "fahrkarte": "einzel"}).json()
        self.assertFalse(r["ok"])
        self.assertIn("Preis", r["text"])


if __name__ == "__main__":
    unittest.main()
