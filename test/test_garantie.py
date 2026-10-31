"""ÜSTRA-Garantie — ohne Netz, ohne Formular, ohne Gemini.

Die Antworten haben den Aufbau der echten vom 15.09.2026 (docs/LEHREN.md).
Die IBAN in den Tests ist das allgemein bekannte Beispiel aus der Norm, kein Konto.
"""

import asyncio
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.dienste import garantie as formular
from kern.dienste import gvh, push, uestra
from kern.garantie import ablage, antrag, sammler, suche, ticket
from kern.main import app
from kern.schule import briefe
from kern.werkzeuge import garantie as werkzeug
from kern.werkzeuge import katalog

BERLIN = ZoneInfo("Europe/Berlin")
JETZT = datetime(2026, 9, 15, 18, 0, tzinfo=BERLIN)
BEISPIEL_IBAN = "DE89370400440532013000"
BENNIGSEN, LUEDERSEN, HEMMINGEN = "de:03241:5844", "de:03241:5816", "de:03241:5323"
SEPTEMBER = {"art": "Deutschlandticket", "bezeichnung": "Deutschlandticket", "herausgeber": "ÜSTRA",
             "gueltig_von": "2026-09-01", "gueltig_bis": "2026-09-30", "inhaber": "", "zweifel": ""}


def _t(stunde, minute=0, tag=15):
    return datetime(2026, 9, tag, stunde, minute, tzinfo=BERLIN)


def _utc(dt):
    return dt.astimezone(timezone.utc).isoformat()


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp})
        self.env.start()
        self.uhr = mock.patch("kern.config.jetzt", return_value=JETZT)
        self.uhr.start()

    def tearDown(self):
        ablagen_schliessen()
        self.uhr.stop()
        self.env.stop()


def ablagen_schliessen() -> None:
    """Jeder Test hat seinen eigenen Datenordner und damit eine eigene SQLite-Ablage — offen gelassen, lief die ganze Suite
    am 21.09.2026 in „Too many open files“ (bei gut 1000 Tests). Nach jedem Test schließen."""
    for inst in ablage._instanzen.values():
        inst._db.close()
    ablage._instanzen.clear()


# ---------- GVH-Auskunft ----------

class Auskunft(unittest.TestCase):
    def test_zeit_ueber_mitternacht(self):
        with mock.patch("kern.config.jetzt", return_value=JETZT):
            self.assertEqual(gvh.zeitpunkt("20260915", "1002300"), datetime(2026, 9, 16, 0, 23, tzinfo=BERLIN))
            self.assertIsNone(gvh.zeitpunkt("20260915", None))

    def test_kennung_ohne_steig(self):
        self.assertEqual(gvh.referenz("de:03241:5844:2:"), BENNIGSEN)


# ---------- Suche ----------

def _ab(jid, linie, kategorie, verspaetung, plan=None, faellt_aus=False):
    plan = plan or _t(17, 40)
    return {"jid": jid, "linie": linie, "kategorie": kategorie, "richtung": "Hannover", "plan": plan,
            "echt": plan + timedelta(minutes=verspaetung) if verspaetung is not None else None,
            "verspaetung": verspaetung, "faellt_aus": faellt_aus}

    def test_halt_passt_strassenabkuerzung(self):
        # Formular „Bismarckstr.“, GVH „Bismarckstraße“ — Anträge #60/#66/#73 (15.10.2026) fielen deshalb durch.
        from kern.garantie import antrag
        self.assertTrue(antrag.halt_passt("Hannover Bahnhof Bismarckstr.", "Hannover, Bahnhof Bismarckstraße"))
        self.assertTrue(antrag.halt_passt("Hannover Bismarckstrasse", "Hannover, Bismarckstraße"))
        self.assertFalse(antrag.halt_passt("Hannover Bahnhof Bismarckstr.", "Hannover, Bahnhof Kleefeld"))


class Suche(Basis):
    def test_haltestellen_im_gebiet(self):
        alle = [{"id": "1", "name": "Empelde Am Wischacker", "gid": "de:03241:1"},
                {"id": "2", "name": "Empelde", "gid": "de:03241:2"},
                {"id": "3", "name": "Musterhausen Schulzentrum Süd", "gid": "de:03241:3"},
                {"id": "4", "name": "Musterhausen Bahnhof", "gid": "de:03241:4"},
                {"id": "5", "name": "Musterhausen Zentrum", "gid": "de:03241:5"},
                {"id": "6", "name": "Musterhausen Rathaus", "gid": ""}]              # ohne Kennung: nicht abfragbar
        with mock.patch.object(uestra, "haltestellen", mock.AsyncMock(return_value=alle)):
            namen = [h["name"] for h in asyncio.run(suche.haltestellen_im_gebiet())]
        self.assertEqual(namen, ["Musterhausen Bahnhof", "Musterhausen Zentrum", "Empelde", "Empelde Am Wischacker"])

    def test_s_bahn_zaehlt_fernverkehr_nicht(self):
        """Die Garantie gilt für alle Fahrten mit ÜSTRA-Fahrausweis (Bedingungen 01.06.2026)."""
        for kategorie, soll in (("Bus", True), ("Stadtbahn", True), ("S-Bahn", True), ("Ersatzverkehr", True),
                                ("ICE", False), ("IC", False), ("sprinti", False), ("", False)):
            with self.subTest(kategorie):
                self.assertEqual(suche.beantragbar(kategorie), soll)

    def test_je_fahrt_einmal_mit_der_hoechsten_verspaetung(self):
        tafeln = [[_ab("J1", "360", "Bus", 22), _ab("S", "S5", "S-Bahn", 29), _ab("I", "ICE 5", "ICE", 40)],
                  [_ab("J1", "360", "Bus", 28), _ab("J2", "9", "Stadtbahn", 12), _ab("J3", "380", "Bus", 5),
                   _ab("J4", "381", "Bus", 40, faellt_aus=True), _ab("J5", "382", "Bus", 30, plan=_t(20, 0))]]
        halte = [{"name": "A", "gid": "g1"}, {"name": "B", "gid": "g2"}]
        with mock.patch.object(gvh, "tafel_roh", mock.AsyncMock(side_effect=tafeln)):
            fahrten = asyncio.run(suche.abtasten(halte, mindest=10))
        self.assertEqual([(f["jid"], f["verspaetung"]) for f in fahrten], [("S", 29), ("J1", 28), ("J2", 12)])

    def test_ohne_treffer_die_nachbarorte_dann_nicht_weiter(self):
        stufen = {"deine": [{"name": "Musterhausen Bahnhof", "gid": "g1"}], "nachbar": [{"name": "Ronnenberg", "gid": "g2"}]}

        async def gebiet(orte=suche.ORTE, pro_ort=suche.PRO_ORT):
            return stufen["deine"] if orte == suche.ORTE else stufen["nachbar"]

        async def tafel(lid, von, anzahl=80):
            return [_ab("N1", "500", "Bus", 25)] if lid == "g2" else [_ab("K", "360", "Bus", 12)]

        with mock.patch.object(suche, "haltestellen_im_gebiet", gebiet), \
                mock.patch.object(gvh, "tafel_roh", tafel), mock.patch.object(suche, "ganzes_netz") as netz:
            ergebnis = asyncio.run(suche.suchen())
        self.assertEqual(ergebnis["bereich"], "Nachbarorte")
        self.assertEqual([(f["linie"], f["bereich"]) for f in ergebnis["fahrten"]],
                         [("500", "Nachbarorte"), ("360", "deine Orte")])
        netz.assert_not_called()                                          # es fand sich etwas über 20

    def test_suchen_merkt_und_die_kennung_ist_kurz(self):
        with mock.patch.object(suche, "haltestellen_im_gebiet", mock.AsyncMock(return_value=[{"name": "A", "gid": "g"}])), \
                mock.patch.object(gvh, "tafel_roh", mock.AsyncMock(return_value=[_ab("J" * 148, "360", "Bus", 25)])):
            gefunden = asyncio.run(suche.suchen())["fahrten"]
        self.assertEqual(len(gefunden[0]["id"]), 10)
        self.assertEqual(ablage.standard().reise(gefunden[0]["id"])["verspaetung"], 25)


# ---------- Ticket ----------

class Ticket(Basis):
    def test_auslesen_und_unlesbares_bleibt_leer(self):
        roh = {"ist_fahrkarte": True, "art": "Deutschlandticket", "bezeichnung": "Deutschlandticket Jugend",
               "herausgeber": "ÜSTRA", "gueltig_von": "2026-09-01", "gueltig_bis": "30.09.2026", "inhaber": "Max M"}
        with mock.patch.object(briefe, "_gemini", mock.AsyncMock(return_value=(roh, "m"))):
            t = asyncio.run(ticket.auslesen("image/jpeg", b"\xff\xd8bild"))
        self.assertEqual((t["art"], t["gueltig_von"], t["gueltig_bis"]), ("Deutschlandticket", "2026-09-01", ""))

    def test_herausgeber(self):
        self.assertTrue(ticket.ist_uestra("ÜSTRA Hannoversche Verkehrsbetriebe"))
        self.assertFalse(ticket.ist_uestra("Deutsche Bahn"))
        self.assertIsNone(ticket.ist_uestra(""))

    def test_alte_tickets_bleiben_und_das_passende_gilt(self):
        a = ablage.standard()
        a.ticket_speichern("sep.pdf", "application/pdf", b"%PDF sep", SEPTEMBER)
        a.ticket_speichern("okt.pdf", "application/pdf", b"%PDF okt",
                           dict(SEPTEMBER, gueltig_von="2026-10-01", gueltig_bis="2026-10-31"))
        a.ticket_speichern("ohne.png", "image/png", b"\x89PNG ohne", {})
        self.assertEqual(len(a.tickets()), 3)
        self.assertEqual(a.ticket_fuer(_t(12).date())["name"], "sep.pdf")
        self.assertIsNone(a.ticket_fuer(datetime(2026, 11, 2).date()))   # ohne erkannte Gültigkeit keins
        datei = next(Path(self.tmp, "garantie", "tickets").iterdir())
        self.assertEqual(stat.S_IMODE(datei.stat().st_mode), 0o600)

    def test_erinnerung_genau_einmal(self):
        ablage.standard().ticket_speichern("aug.pdf", "application/pdf", b"%PDF aug",
                                           dict(SEPTEMBER, gueltig_von="2026-08-01", gueltig_bis="2026-08-31"))
        with mock.patch.object(push, "senden", return_value={"gesendet": 1}) as melden:
            self.assertTrue(asyncio.run(sammler.ticket_erinnern()))
            self.assertFalse(asyncio.run(sammler.ticket_erinnern()))
        melden.assert_called_once()


# ---------- Vorbereiten ----------

def _verlauf(ziel_plan=None, ziel_echt=None):
    ziel_plan = ziel_plan or _t(16, 19)
    return {"jid": "J1", "linie": "360", "kategorie": "Bus", "richtung": "Hannover Peiner Straße", "halte": [
        {"name": "Bennigsen Bahnhof", "reference": BENNIGSEN, "an_plan": None, "an_echt": None,
         "ab_plan": _t(15, 52), "ab_echt": _t(15, 52), "verspaetung": 0, "faellt_aus": False},
        {"name": "Musterdorf Denkmal", "reference": LUEDERSEN, "an_plan": _t(15, 56), "an_echt": _t(16, 0),
         "ab_plan": _t(15, 56), "ab_echt": _t(16, 0), "verspaetung": 4, "faellt_aus": False},
        {"name": "Hemmingen", "reference": HEMMINGEN, "an_plan": ziel_plan, "an_echt": ziel_echt,
         "ab_plan": ziel_plan, "ab_echt": ziel_echt, "verspaetung": None, "faellt_aus": False}]}


STATIONEN = [{"name": "Bennigsen, Bahnhof", "reference": BENNIGSEN},
             {"name": "Musterdorf, Denkmal", "reference": LUEDERSEN}, {"name": "Hemmingen", "reference": HEMMINGEN}]


def _formularfahrt(ab, an, echt, von="Bennigsen, Bahnhof", nach="Hemmingen"):
    # Wie die echten Antworten des Formulars: mit den Namen der Halte — daran erkennt der Code, ob die
    # Teilstrecke wirklich die gewählte Strecke ist (siehe antrag.halt_passt).
    return [{"signature": "sig", "legs": [{"lineName": "360", "scheduledFromDateTime": _utc(ab),
                                            "scheduledToDateTime": _utc(an), "realToDateTime": _utc(echt) if echt else None,
                                            "fromStationName": von, "toStationName": nach,
                                            "selectable": True, "cancelled": False}]}]


class Vorbereiten(Basis):
    def setUp(self):
        super().setUp()
        self.rid = ablage.standard().reise_merken(_ab("J1", "360", "Bus", 28, plan=_t(15, 52)))

    def _komplett(self, ticket_daten=SEPTEMBER):
        a = ablage.standard()
        a.angaben_setzen({k: "x" for k in ablage.PFLICHT} | {"iban": BEISPIEL_IBAN})
        a.ticket_speichern("t.pdf", "application/pdf", b"%PDF" + str(ticket_daten).encode(), ticket_daten)

    def _mit(self, verlauf, formularfahrten):
        return (mock.patch.object(gvh, "fahrt_verlauf", mock.AsyncMock(return_value=verlauf)),
                mock.patch.object(formular, "stationen", mock.AsyncMock(return_value=STATIONEN)),
                mock.patch.object(formular, "fahrten", mock.AsyncMock(side_effect=[formularfahrten] + [[]] * 5)))

    def _vorbereiten(self, verlauf, formularfahrten, start=BENNIGSEN, ziel=HEMMINGEN):
        a, b, c = self._mit(verlauf, formularfahrten)
        with a, b, c:
            return asyncio.run(antrag.vorbereiten(self.rid, start, ziel))

    def test_gemessen_und_bereit(self):
        self._komplett()
        r = self._vorbereiten(_verlauf(), _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 47)))
        e = r["antrag"]
        self.assertEqual((e["verspaetung"], e["quelle"], e["stand"], e["fahrkarte"]),
                         (28, "vom GVH gemessen", "bereit", "Deutschlandticket"))
        self.assertIn("Abgeschickt wird nichts", r["text"])

    def test_ohne_ticket_fuer_den_monat_unvollstaendig(self):
        e = self._vorbereiten(_verlauf(), _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 47)))["antrag"]
        self.assertEqual(e["stand"], "unvollständig")
        self.assertIn("Ticket für September", e["fehlt"])

    def test_deutschlandticket_eines_anderen_herausgebers(self):
        self._komplett(dict(SEPTEMBER, herausgeber="Deutsche Bahn"))
        e = self._vorbereiten(_verlauf(), _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 47)))["antrag"]
        self.assertEqual(e["stand"], "nicht beantragbar")
        self.assertIn("von der ÜSTRA ausgegebene", " ".join(e["hindernisse"]))

    def test_ziel_in_der_zukunft_wartet_und_das_formular_sucht_spaeter(self):
        """Eike darf vorbereiten — abgeben geht erst nach der Ankunft, und die Fahrtsuche
        des Formulars findet vorher gar nichts. Das ist noch kein Hindernis."""
        e = self._vorbereiten(_verlauf(_t(17, 50), _t(18, 15)), [])["antrag"]
        self.assertEqual((e["stand"], e["verspaetung"], e["quelle"]), ("wartet", 25, "Echtzeit der GVH-Auskunft"))
        self.assertEqual(e["hindernisse"], [])
        self.assertIn(antrag.IM_FORMULAR_SPAETER, e["hinweise"])

    def test_zu_wenig_am_ziel(self):
        e = self._vorbereiten(_verlauf(), _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 29)))["antrag"]
        self.assertEqual(e["stand"], "nicht beantragbar")
        self.assertIn("über 20 Minuten", " ".join(e["hindernisse"]))

    def test_nach_der_ankunft_nicht_im_formular(self):
        e = self._vorbereiten(_verlauf(ziel_echt=_t(16, 47)), [])["antrag"]
        self.assertIn("bietet diese Fahrt nicht an", " ".join(e["hindernisse"]))

    def test_eine_fahrt_nur_einmal(self):
        fahrt = _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 47))
        self.assertTrue(self._vorbereiten(_verlauf(), fahrt)["ok"])
        zweiter = self._vorbereiten(_verlauf(), fahrt, start=LUEDERSEN)
        self.assertFalse(zweiter["ok"])
        self.assertIn("nur einmal", zweiter["text"])

    def test_ziel_vor_start(self):
        self.assertIn("nach dem Start", self._vorbereiten(_verlauf(), [], start=HEMMINGEN, ziel=BENNIGSEN)["text"])

    def _abgeschickt(self, an, fahrkarte="Deutschlandticket"):
        """Ein GENEHMIGTER Fall — nur die zählen für die Höchstzahlen."""
        a = ablage.standard()
        a.vorgang_speichern({"vorgang": f"9{an:%d%H%M}", "fahrtdatum": an.date().isoformat(), "status": "genehmigt",
                             "fahrkarte": fahrkarte, "letzte_mail": an.isoformat(), "cent": 500})

    def test_hoechstens_zwei_am_tag(self):
        self._abgeschickt(_t(8))
        self._abgeschickt(_t(12))
        e = self._vorbereiten(_verlauf(), _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 47)))["antrag"]
        self.assertIn("schon 2 Fahrten", " ".join(e["hindernisse"]))

    def test_deutschlandticket_fuenf_im_monat(self):
        self._komplett()
        for tag in range(1, 6):
            self._abgeschickt(_t(9, tag=tag))
        e = self._vorbereiten(_verlauf(), _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 47)))["antrag"]
        self.assertIn("höchstens 5 Fahrten", " ".join(e["hindernisse"]))

    def test_nachpruefen_nach_der_ankunft(self):
        self._komplett()
        e = self._vorbereiten(_verlauf(_t(17, 50), _t(18, 15)), [])["antrag"]
        spaeter = JETZT.replace(hour=19)
        a, b, c = self._mit(_verlauf(_t(17, 50), _t(18, 16)), _formularfahrt(_t(15, 52), _t(17, 50), _t(18, 17)))
        with mock.patch("kern.config.jetzt", return_value=spaeter), a, b, c, \
                mock.patch.object(push, "senden", return_value={"gesendet": 1}) as melden:
            self.assertEqual(asyncio.run(antrag.nachpruefen()), 1)
            neu = antrag.bewerten(ablage.standard().antrag(e["id"]))
        self.assertEqual((neu["stand"], neu["verspaetung"], neu["quelle"]), ("bereit", 27, "vom GVH gemessen"))
        melden.assert_called_once()

    def test_abgeschickt_nur_wenn_bereit_und_zaehlt_die_fahrkarte(self):
        e = self._vorbereiten(_verlauf(), _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 47)))["antrag"]
        with self.assertRaises(ValueError):
            antrag.status_setzen(e["id"], "abgeschickt")               # noch kein Ticket
        self._komplett()                                              # Ticket kommt nach dem Vorbereiten
        self.assertEqual(antrag.status_setzen(e["id"], "abgeschickt")["stand"], "abgeschickt")
        z = antrag.zaehler()
        # Abgeschickt ist noch nicht genehmigt: zählt nicht, steht aber als offen da.
        self.assertEqual((z["heute"], z["monat"], z["dticket"], z["offen"]), (0, 0, 0, 1))

    def test_nur_genehmigte_zaehlen_und_limit_sperrt(self):
        self._komplett()
        e = self._vorbereiten(_verlauf(), _formularfahrt(_t(15, 52), _t(16, 19), _t(16, 47)))["antrag"]
        self.assertNotEqual(antrag.bewerten(ablage.standard().antrag(e["id"]))["stand"], "Limit erreicht")
        a = ablage.standard()
        a.vorgang_speichern({"vorgang": "8000001", "fahrtdatum": _t(8).date().isoformat(), "status": "abgelehnt",
                             "fahrkarte": "Deutschlandticket", "letzte_mail": JETZT.isoformat()})
        a.vorgang_speichern({"vorgang": "8000002", "fahrtdatum": _t(8).date().isoformat(), "status": "eingang",
                             "fahrkarte": "Deutschlandticket", "letzte_mail": JETZT.isoformat()})
        self.assertNotEqual(antrag.bewerten(a.antrag(e["id"]))["stand"], "Limit erreicht")
        self._abgeschickt(_t(8))
        self._abgeschickt(_t(12))
        neu = antrag.bewerten(a.antrag(e["id"]))
        self.assertEqual(neu["stand"], "Limit erreicht")
        self.assertIn("schon 2 Fahrten genehmigt", " ".join(neu["hindernisse"]))
        z = antrag.zaehler()
        self.assertEqual((z["heute"], z["heute_rest"], z["offen"], z["limit"]), (2, 0, 1, 1))


# ---------- Angaben ----------

class Angaben(Basis):
    def test_iban_nie_im_klartext_und_600(self):
        a = ablage.standard()
        a.angaben_setzen({"vorname": "Max", "iban": "de89 3704 0044 0532 0130 00"})
        sicher = a.angaben_sicher()
        self.assertNotIn("iban", sicher)
        self.assertEqual(sicher["iban_verdeckt"], "DE•• … 3000")
        self.assertEqual(stat.S_IMODE(Path(self.tmp, "garantie_angaben.json").stat().st_mode), 0o600)

    def test_angaben_bleiben_und_leere_iban_behaelt_die_alte(self):
        ablage.standard().angaben_setzen({"iban": BEISPIEL_IBAN, "nachname": "Mustermann"})
        ablage._instanzen.clear()                                       # wie nach einem Neustart
        a = ablage.standard()
        a.angaben_setzen({"iban": "", "vorname": "Max"})
        self.assertEqual((a.angaben()["iban"], a.angaben()["nachname"]), (BEISPIEL_IBAN, "Mustermann"))

    def test_iban_pruefsumme(self):
        self.assertTrue(ablage.iban_gueltig(BEISPIEL_IBAN))
        self.assertFalse(ablage.iban_gueltig("DE89370400440532013001"))

    def test_sicherung_nimmt_die_angaben_mit(self):
        from kern.gedaechtnis import rueckblick
        ablage.standard().angaben_setzen({"vorname": "Max"})
        with mock.patch("kern.config.jetzt", return_value=JETZT.replace(hour=4)):
            rueckblick.sichern()
        kopien = list(Path(self.tmp, "sicherung").glob("garantie_angaben-*.json"))
        self.assertEqual(len(kopien), 1)
        self.assertEqual(stat.S_IMODE(kopien[0].stat().st_mode), 0o600)
        self.assertTrue(list(Path(self.tmp, "sicherung").glob("garantie-*.db")))


# ---------- Werkzeuge und Grenzen ----------

class Werkzeuge(Basis):
    def test_halt_nur_auf_dieser_fahrt(self):
        v = _verlauf()
        self.assertEqual(werkzeug.halt_im_verlauf(v, "Musterdorf"), LUEDERSEN)
        with self.assertRaises(LookupError):
            werkzeug.halt_im_verlauf(v, "Musterhausen")

    def test_nur_mit_freigabe_sichtbar(self):
        namen = lambda: {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertNotIn("garantie_suchen", namen())
        konnektoren.setzen("uestra_garantie", True)
        self.assertTrue({"garantie_suchen", "garantie_fahrt", "garantie_vorbereiten", "garantie_antraege"} <= namen())

    def test_region_filter_der_stationssuche(self):
        roh = [{"name": "Alt Bennebek", "reference": "de:01059:1"}, {"name": "Bennigsen, Bahnhof", "reference": BENNIGSEN}]
        with mock.patch.object(formular, "_holen", mock.AsyncMock(return_value=roh)):
            self.assertEqual([s["reference"] for s in asyncio.run(formular.stationen("Bennigsen"))], [BENNIGSEN])

    def test_keine_falsche_s_bahn_aussage_mehr(self):
        """Stand bis 15.09. abends: „die S-Bahn gehört nicht zur ÜSTRA-Garantie“ — falsch."""
        wurzel = Path(__file__).resolve().parent.parent
        for datei in (wurzel / "web" / "garantie.js", wurzel / "kern" / "konnektoren.py",
                      wurzel / "kern" / "werkzeuge" / "garantie.py"):
            with self.subTest(datei.name):
                self.assertNotIn("gehört nicht zur ÜSTRA-Garantie", datei.read_text(encoding="utf-8"))

    def test_abschicken_nur_an_einer_stelle(self):
        """Seit 16.09. (Eikes ausdrückliche Erlaubnis) gibt es den Abschick-Pfad — aber nur im
        Formular-Dienst, aufgerufen nur von abschicken.py, und nie als Werkzeug fürs Modell."""
        wurzel = Path(__file__).resolve().parent.parent / "kern"
        for datei in wurzel.rglob("*.py"):
            text = datei.read_text(encoding="utf-8")
            with self.subTest(datei.name):
                if datei != wurzel / "dienste" / "garantie.py":
                    self.assertNotIn("complaint/create", text)
                if datei != wurzel / "garantie" / "abschicken.py":
                    self.assertNotIn("formular.einreichen", text)
                    self.assertNotIn("garantie.einreichen", text)
        for datei in (wurzel / "werkzeuge").rglob("*.py"):
            self.assertNotIn("abschicken", datei.read_text(encoding="utf-8").replace("nicht abschicken", ""))


class Schnittstelle(Basis):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)

    def test_iban_verlaesst_den_server_nie(self):
        self.assertEqual(self.c.post("/api/garantie/angaben", json={"iban": BEISPIEL_IBAN}).status_code, 200)
        self.assertNotIn("0532013000", self.c.get("/api/garantie").text)

    def test_falsche_eingaben(self):
        self.assertEqual(self.c.post("/api/garantie/angaben", json={"iban": "DE89370400440532013001"}).status_code, 400)
        falsch = self.c.post("/api/garantie/ticket", files={"datei": ("a.exe", b"MZ", "application/octet-stream")})
        self.assertEqual(falsch.status_code, 400)

    def test_ticket_hochladen_wird_ausgelesen_und_uebernommen(self):
        erkannt = dict(SEPTEMBER, ist_fahrkarte=True, inhaber="Max Mustermann")
        with mock.patch.object(ticket, "auslesen", mock.AsyncMock(return_value=erkannt)):
            r = self.c.post("/api/garantie/ticket", files={"datei": ("t.pdf", b"%PDF-1.4", "application/pdf")}).json()
        self.assertIn("Deutschlandticket", r["text"])
        self.assertIn("01.09.–30.09.2026", r["text"])
        angaben = ablage.standard().angaben()
        self.assertEqual((angaben["fahrkarte"], angaben["vorname"], angaben["nachname"]),
                         ("Deutschlandticket", "Max", "Mustermann"))
        korrigiert = self.c.post(f"/api/garantie/ticket/{r['ticket']['id']}", json={"gueltig_bis": "2026-09-29"})
        self.assertEqual(korrigiert.json()["ticket"]["gueltig_bis"], "2026-09-29")

    def test_ticket_unlesbar_wird_trotzdem_gespeichert(self):
        with mock.patch.object(ticket, "auslesen", mock.AsyncMock(side_effect=RuntimeError("weg"))):
            r = self.c.post("/api/garantie/ticket", files={"datei": ("t.png", b"\x89PNG", "image/png")}).json()
        self.assertIn("selbst ein", r["text"])
        self.assertEqual(len(ablage.standard().tickets()), 1)

    def test_ohne_freigabe_kein_kontakt(self):
        with mock.patch.object(gvh, "_ruf", mock.AsyncMock()) as netz:
            self.assertEqual(self.c.post("/api/garantie/suchen").status_code, 409)
            self.assertEqual(self.c.get("/api/garantie/fahrt?id=abcdef1234").status_code, 409)
            r = self.c.post("/api/garantie/vorbereiten", json={"fahrt": "abcdef1234", "start": BENNIGSEN, "ziel": HEMMINGEN})
            self.assertEqual(r.status_code, 409)
        netz.assert_not_awaited()

    def test_stand_zaehler_und_seite(self):
        d = self.c.get("/api/garantie").json()
        self.assertEqual((d["zaehler"]["tag_max"], d["tickets"]), (2, []))
        self.assertIn("Weetzen", d["orte"])
        seite = self.c.get("/").text
        for id_ in ("r-studio", "r-garantie", "garantie-inhalt", "studio-liste"):
            self.assertIn(f'id="{id_}"', seite)


if __name__ == "__main__":
    unittest.main()
