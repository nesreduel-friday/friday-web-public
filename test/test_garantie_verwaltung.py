"""Garantie-Verwaltung (11.10.2026): feste Status, Übergänge, Verfolgung bis zum Bescheid, Nachfassen — ohne Netz.

Geprüft wird vor allem, was nie passieren darf: kein Absenden, keine Nachricht an Dritte, keine Veränderung der
bisherigen Tabellen, kein Erinnerungs-Spam.
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta
from unittest import mock

from fastapi.testclient import TestClient

from kern import konnektoren
from kern.anliegen import fach
from kern.garantie import ablage, bahnablage, verwaltung
from kern.main import app
from kern.werkzeuge import garantie as werkzeug
from kern.werkzeuge import katalog
from test import test_garantie as tg

JETZT = tg.JETZT          # 15.09.2026 18:00 Berlin


def tage(n: int) -> str:
    return (JETZT + timedelta(days=n)).isoformat(timespec="seconds")


def antrag(a, status="offen", an_tage=-1, profil=0, **mehr) -> int:
    an = JETZT + timedelta(days=an_tage)
    aid = a.antrag_speichern({"reise": f"r{an_tage}{status}{profil}{mehr.get('vorgang', '')}", "jid": "j", "linie": "360",
                              "start": "Bennigsen", "ziel": "Hemmingen", "ab": an - timedelta(minutes=30), "an": an,
                              "verspaetung": 25, "quelle": "gemessen", "nachgeprueft": True, "profil": profil})
    felder = {k: v for k, v in mehr.items() if k in ("vorgang", "entscheidung", "abgeschickt_am")}
    a.antrag_aendern(aid, status=status, **felder)
    return aid


def vorgang(a, nummer, status, antrag_id=None, vor=0, **mehr) -> None:
    a.vorgang_speichern({"vorgang": nummer, "fahrtdatum": "2026-09-10", "linie": "360", "start": "Bennigsen",
                         "ziel": "Hemmingen", "status": status, "antrag": antrag_id,
                         "eingang_am": tage(vor), "letzte_mail": tage(vor),
                         "entschieden_am": tage(vor) if status in ("genehmigt", "abgelehnt") else None, **mehr})


class Basis(tg.Basis):
    def setUp(self):
        super().setUp()
        self.a = ablage.standard()
        self.sende = mock.patch("kern.dienste.benachrichtigungen.senden", return_value={"gesendet": 1})
        self.senden = self.sende.start()
        self.ruhe = mock.patch("kern.anliegen.fach._ist_ruhezeit", return_value=False)
        self.ruhe.start()

    def tearDown(self):
        self.sende.stop()
        self.ruhe.stop()
        super().tearDown()


class Maschine(Basis):
    def test_erlaubte_und_unerlaubte_uebergaenge(self):
        ok = verwaltung.uebergang_erlaubt
        self.assertTrue(ok("offen", "vorbereitet"))
        self.assertTrue(ok("abgeschickt", "eingang_bestaetigt"))
        self.assertTrue(ok("abgeschickt", "bescheid_positiv"))       # Eingangsmail verpasst: kein Grund zu blockieren
        self.assertTrue(ok("rueckfrage_offen", "eingang_bestaetigt"))
        self.assertTrue(ok("bescheid_abgelehnt", "widerspruch"))
        self.assertTrue(ok("bescheid_positiv", "erledigt"))
        self.assertTrue(ok("verworfen", "offen"))                     # wie in der App: zurück auf offen
        self.assertFalse(ok("offen", "abgeschickt"))                  # Abschicken nur über den Knopf-Weg
        self.assertFalse(ok("bescheid_positiv", "bescheid_abgelehnt"))
        self.assertFalse(ok("erledigt", "offen"))
        self.assertFalse(ok("bescheid_positiv", "widerspruch"))
        self.assertFalse(ok("offen", "gibt_es_nicht"))

    def test_jede_phase_hat_eine_uebergangszeile_und_einen_text(self):
        for p in verwaltung.PHASEN:
            self.assertIn(p, verwaltung.UEBERGAENGE)
            self.assertIn(p, verwaltung.PHASE_TEXT)
        for ziele in verwaltung.UEBERGAENGE.values():
            self.assertTrue(set(ziele) <= set(verwaltung.PHASEN))


class Uebernahme(Basis):
    def test_bestehende_werte_werden_gemappt(self):
        entwurf = antrag(self.a, "offen")
        verworfen = antrag(self.a, "verworfen", an_tage=-2)
        gesendet = antrag(self.a, "abgeschickt", an_tage=-3, abgeschickt_am=tage(-3))
        ein = antrag(self.a, "abgeschickt", an_tage=-4, vorgang="111111")
        vorgang(self.a, "111111", "eingang", ein, vor=-3)
        gen = antrag(self.a, "abgeschickt", an_tage=-5, vorgang="222222")
        vorgang(self.a, "222222", "genehmigt", gen, vor=-2)
        abg = antrag(self.a, "abgeschickt", an_tage=-6, vorgang="333333")
        vorgang(self.a, "333333", "abgelehnt", abg, vor=-2)
        rueck = antrag(self.a, "abgeschickt", an_tage=-7, vorgang="444444")
        vorgang(self.a, "444444", "rueckfrage", rueck, vor=-1)
        vorgang(self.a, "555555", "eingang", None, vor=-1)           # selbst eingereicht, ohne Antrag in Friday
        verwaltung.abgleichen(JETZT)
        p = verwaltung.phase
        self.assertEqual(p(f"A{entwurf}"), "offen")                  # ohne Ticket/Angaben: unvollständig
        self.assertEqual(p(f"A{verworfen}"), "verworfen")
        self.assertEqual(p(f"A{gesendet}"), "abgeschickt")
        self.assertEqual(p(f"A{ein}"), "eingang_bestaetigt")
        self.assertEqual(p(f"A{gen}"), "bescheid_positiv")
        self.assertEqual(p(f"A{abg}"), "bescheid_abgelehnt")
        self.assertEqual(p(f"A{rueck}"), "rueckfrage_offen")
        self.assertEqual(p("V555555"), "eingang_bestaetigt")
        self.assertIsNone(p("V111111"))                              # gehört schon zu Antrag A…, nicht doppelt

    def test_vorbereitet_haengt_nicht_fest_wenn_der_antrag_schon_entschieden_ist(self):
        """Der Antrag ging über die App raus, ohne dass die Phase mitzog: Der Bestand holt sie nach (27.10.2026)."""
        aid = antrag(self.a, "offen")
        with mock.patch("kern.garantie.antrag.bewerten", return_value={"stand": "bereit"}):
            verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(f"A{aid}"), "vorbereitet")
        self.a.antrag_aendern(aid, status="abgeschickt", vorgang="777777", abgeschickt_am=tage(-1))
        vorgang(self.a, "777777", "genehmigt", aid, vor=0)
        ergebnis = verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(f"A{aid}"), "bescheid_positiv")
        self.assertEqual(ergebnis["abgelehnt"], 0)
        with sqlite3.connect(self.a.ordner / "garantie.db") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM garantie_historie WHERE ok = 0").fetchone()[0], 0)

    def test_eike_darf_weiterhin_nicht_von_vorbereitet_zum_bescheid_springen(self):
        aid = antrag(self.a, "offen")
        with mock.patch("kern.garantie.antrag.bewerten", return_value={"stand": "bereit"}):
            verwaltung.abgleichen(JETZT)
        with self.assertRaises(ValueError):
            verwaltung.setzen(f"A{aid}", "bescheid_positiv", "Eike", quelle="eike")

    def test_bereiter_entwurf_ist_vorbereitet(self):
        aid = antrag(self.a, "offen")
        with mock.patch("kern.garantie.antrag.bewerten", return_value={"stand": "bereit"}):
            verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(f"A{aid}"), "vorbereitet")
        with mock.patch("kern.garantie.antrag.bewerten", return_value={"stand": "unvollständig"}):
            verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(f"A{aid}"), "offen")       # Ticket abgelaufen: wieder offen, Übergang erlaubt

    def test_die_bisherigen_tabellen_bleiben_unveraendert(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111")
        vorgang(self.a, "111111", "genehmigt", aid)
        antrag(self.a, "offen", an_tage=-2)

        def abzug():
            db = sqlite3.connect(self.a.ordner / "garantie.db")
            try:
                return {t: db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                        for t in ("antraege", "vorgaenge", "reisen")}
            finally:
                db.close()
        vorher = abzug()
        verwaltung.abgleichen(JETZT)
        verwaltung.abgleichen(JETZT)
        self.assertEqual(abzug(), vorher)

    def test_zweiter_abgleich_aendert_nichts_und_schreibt_keine_historie_dazu(self):
        aid = antrag(self.a, "abgeschickt", abgeschickt_am=tage(-1))
        verwaltung.abgleichen(JETZT)
        n = len(verwaltung.historie(f"A{aid}"))
        r = verwaltung.abgleichen(JETZT)
        self.assertEqual((r["neu"], r["geaendert"]), (0, 0))
        self.assertEqual(len(verwaltung.historie(f"A{aid}")), n)

    def test_neue_gmail_lage_schiebt_die_phase_weiter_mit_historie(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111", abgeschickt_am=tage(-5))
        vorgang(self.a, "111111", "eingang", aid, vor=-4)
        verwaltung.abgleichen(JETZT)
        vorgang(self.a, "111111", "abgelehnt", aid, vor=0, grund="planmäßig")
        verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(f"A{aid}"), "bescheid_abgelehnt")
        h = verwaltung.historie(f"A{aid}")
        self.assertEqual([(z["von"], z["nach"], z["ok"]) for z in h][-1], ("eingang_bestaetigt", "bescheid_abgelehnt", 1))
        self.assertTrue(h[-1]["zeit"] and h[-1]["grund"])

    def test_ausgezahlt_bei_freunden_ist_erledigt(self):
        pid = self.a.profil_anlegen({"name": "Lea"})
        aid = antrag(self.a, "abgeschickt", vorgang="222222", an_tage=-9, profil=pid)
        vorgang(self.a, "222222", "genehmigt", aid, vor=-5)
        verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(f"A{aid}"), "bescheid_positiv")
        self.a.ausgezahlt_setzen(pid, "222222", True)
        verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(f"A{aid}"), "erledigt")

    def test_bahnantraege_werden_mit_uebernommen(self):
        b1 = bahnablage.antrag_speichern({"profil": 0, "schluessel": "x", "art": "einzel", "status": "eingereicht",
                                          "eingereicht_am": tage(-2)})
        b2 = bahnablage.antrag_speichern({"profil": 0, "schluessel": "y", "art": "einzel", "status": "genehmigt",
                                          "eingereicht_am": tage(-9), "entschieden_am": tage(-1), "erhalten_cent": 800})
        verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(f"B{b1}"), "abgeschickt")
        self.assertEqual(verwaltung.phase(f"B{b2}"), "bescheid_positiv")


class Setzen(Basis):
    def einrichten(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111", abgeschickt_am=tage(-5))
        vorgang(self.a, "111111", "eingang", aid, vor=-4)
        verwaltung.abgleichen(JETZT)
        return f"A{aid}"

    def test_gueltiger_uebergang_wird_gesetzt_und_protokolliert(self):
        ref = self.einrichten()
        verwaltung.setzen(ref, "bescheid_positiv", "Eike: Bescheid kam", quelle="eike", jetzt=JETZT)
        self.assertEqual(verwaltung.phase(ref), "bescheid_positiv")
        z = verwaltung.historie(ref)[-1]
        self.assertEqual((z["von"], z["nach"], z["ok"], z["grund"]), ("eingang_bestaetigt", "bescheid_positiv", 1, "Eike: Bescheid kam"))

    def test_unzulaessiger_uebergang_wird_abgelehnt_und_protokolliert(self):
        ref = self.einrichten()
        with self.assertRaises(ValueError):
            verwaltung.setzen(ref, "widerspruch", "x", quelle="eike", jetzt=JETZT)
        self.assertEqual(verwaltung.phase(ref), "eingang_bestaetigt")
        z = verwaltung.historie(ref)[-1]
        self.assertEqual((z["von"], z["nach"], z["ok"]), ("eingang_bestaetigt", "widerspruch", 0))

    def test_unbekannte_phase_und_unbekannter_vorgang(self):
        ref = self.einrichten()
        with self.assertRaises(ValueError):
            verwaltung.setzen(ref, "quatsch", "", quelle="eike", jetzt=JETZT)
        with self.assertRaises(LookupError):
            verwaltung.setzen("A99999", "erledigt", "", quelle="eike", jetzt=JETZT)

    def test_manuell_vor_der_mail_bleibt_und_die_mail_stuetzt_es_spaeter(self):
        ref = self.einrichten()
        verwaltung.setzen(ref, "bescheid_positiv", "", quelle="eike", jetzt=JETZT)
        verwaltung.abgleichen(JETZT)                                  # Gmail sagt noch „eingang“
        self.assertEqual(verwaltung.phase(ref), "bescheid_positiv")
        n = len(verwaltung.historie(ref))
        verwaltung.abgleichen(JETZT)
        self.assertEqual(len(verwaltung.historie(ref)), n)            # auch kein Protokoll-Spam

    def test_rueckfrage_beantwortet_haengt_nicht_wieder_an_der_alten_mail(self):
        aid = antrag(self.a, "abgeschickt", vorgang="444444")
        vorgang(self.a, "444444", "rueckfrage", aid, vor=-1)
        verwaltung.abgleichen(JETZT)
        ref = f"A{aid}"
        verwaltung.setzen(ref, "eingang_bestaetigt", "Ticket nachgereicht", quelle="eike", jetzt=JETZT)
        verwaltung.abgleichen(JETZT)
        self.assertEqual(verwaltung.phase(ref), "eingang_bestaetigt")
        self.assertTrue(self.a.vorgang("444444")["erledigt"])        # derselbe Haken wie in der App

    def test_bahn_genehmigt_braucht_den_betrag_und_schreibt_den_bescheid(self):
        b = bahnablage.antrag_speichern({"profil": 0, "schluessel": "x", "art": "einzel", "status": "eingereicht",
                                         "eingereicht_am": tage(-2)})
        verwaltung.abgleichen(JETZT)
        with self.assertRaises(ValueError):
            verwaltung.setzen(f"B{b}", "bescheid_positiv", "", quelle="eike", jetzt=JETZT)
        verwaltung.setzen(f"B{b}", "bescheid_positiv", "", quelle="eike", jetzt=JETZT, betrag_cent=800)
        self.assertEqual(bahnablage.antrag(b)["status"], "genehmigt")
        self.assertEqual(bahnablage.antrag(b)["erhalten_cent"], 800)


class Faellig(Basis):
    def schritte(self, jetzt=JETZT):
        return {f["ref"]: f["schritt"] for f in verwaltung.faellig(jetzt)}

    def test_antragsfrist_laeuft_ab(self):
        knapp = antrag(self.a, "offen", an_tage=-12)       # Frist: Fahrt + 14 Tage = in 2 Tagen
        frisch = antrag(self.a, "offen", an_tage=-1)
        vorbei = antrag(self.a, "offen", an_tage=-20)
        s = self.schritte()
        self.assertEqual(s.get(f"A{knapp}"), "antragsfrist")
        self.assertNotIn(f"A{frisch}", s)
        self.assertNotIn(f"A{vorbei}", s)                  # abgelaufen: nichts mehr zu retten, nicht ständig erinnern
        f = next(f for f in verwaltung.faellig(JETZT) if f["ref"] == f"A{knapp}")
        self.assertEqual(f["frist"], (JETZT + timedelta(days=-12)).date().__add__(timedelta(days=14)).isoformat())

    def test_abgeschickt_ohne_eingangsbestaetigung(self):
        neu = antrag(self.a, "abgeschickt", abgeschickt_am=tage(-1), an_tage=-2)
        alt = antrag(self.a, "abgeschickt", abgeschickt_am=tage(-4), an_tage=-5)
        s = self.schritte()
        self.assertEqual(s.get(f"A{alt}"), "eingang_nachfassen")
        self.assertNotIn(f"A{neu}", s)

    def test_eingang_ohne_bescheid(self):
        a1 = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-30)
        vorgang(self.a, "111111", "eingang", a1, vor=-22)
        a2 = antrag(self.a, "abgeschickt", vorgang="222222", an_tage=-10)
        vorgang(self.a, "222222", "eingang", a2, vor=-5)
        s = self.schritte()
        self.assertEqual(s.get(f"A{a1}"), "bescheid_nachfassen")
        self.assertNotIn(f"A{a2}", s)

    def test_rueckfrage_unbeantwortet_mit_frist_14_tage(self):
        a1 = antrag(self.a, "abgeschickt", vorgang="444444", an_tage=-9)
        vorgang(self.a, "444444", "rueckfrage", a1, vor=-6)
        f = next(f for f in verwaltung.faellig(JETZT) if f["ref"] == f"A{a1}")
        self.assertEqual(f["schritt"], "rueckfrage_antworten")
        self.assertEqual(f["frist"], (JETZT + timedelta(days=8)).date().isoformat())

    def test_abgelehnt_widerspruchsfrist_und_geld_pruefen(self):
        ab = antrag(self.a, "abgeschickt", vorgang="333333", an_tage=-12)
        vorgang(self.a, "333333", "abgelehnt", ab, vor=-3)
        ge = antrag(self.a, "abgeschickt", vorgang="222222", an_tage=-20)
        vorgang(self.a, "222222", "genehmigt", ge, vor=-12)
        s = self.schritte()
        self.assertEqual(s.get(f"A{ab}"), "widerspruch_pruefen")
        self.assertEqual(s.get(f"A{ge}"), "geld_pruefen")

    def test_widerspruch_eingelegt_ohne_antwort(self):
        ab = antrag(self.a, "abgeschickt", vorgang="333333", an_tage=-40)
        vorgang(self.a, "333333", "abgelehnt", ab, vor=-30)
        verwaltung.abgleichen(JETZT)
        verwaltung.setzen(f"A{ab}", "widerspruch", "per Mail", quelle="eike", jetzt=JETZT - timedelta(days=25))
        self.assertEqual(self.schritte().get(f"A{ab}"), "widerspruch_nachfassen")

    def test_widerspruchsfrist_vorbei_wird_nicht_mehr_gemeldet(self):
        ab = antrag(self.a, "abgeschickt", vorgang="333333", an_tage=-50)
        vorgang(self.a, "333333", "abgelehnt", ab, vor=-40)
        self.assertEqual(self.schritte(), {})

    def test_erledigte_und_verworfene_sind_nie_faellig(self):
        ge = antrag(self.a, "abgeschickt", vorgang="222222", an_tage=-30)
        vorgang(self.a, "222222", "genehmigt", ge, vor=-20)
        verwaltung.abgleichen(JETZT)
        verwaltung.setzen(f"A{ge}", "erledigt", "Geld ist da", quelle="eike", jetzt=JETZT)
        antrag(self.a, "verworfen", an_tage=-12)
        self.assertEqual(self.schritte(), {})

    def test_offene_zeigt_auch_noch_nicht_faellige_mit_naechstem_schritt(self):
        antrag(self.a, "abgeschickt", abgeschickt_am=tage(-1), an_tage=-2)
        o = verwaltung.offene(JETZT)
        self.assertEqual(len(o), 1)
        self.assertEqual(o[0]["schritt"], "eingang_nachfassen")
        self.assertFalse(o[0]["faellig"])


class Erinnern(Basis):
    def faelliger_antrag(self, profil=0):
        aid = antrag(self.a, "abgeschickt", abgeschickt_am=tage(-4), an_tage=-5, profil=profil)
        return f"A{aid}"

    def test_einmal_je_schritt_nicht_noch_einmal(self):
        self.a.vorgang_speichern  # noqa: B018 — Ablage ist da
        verwaltung.abgleichen(JETZT)                # Grundlinie: nichts vorhanden
        ref = self.faelliger_antrag()
        n1 = verwaltung.erinnern(JETZT)
        n2 = verwaltung.erinnern(JETZT)
        n3 = verwaltung.erinnern(JETZT + timedelta(days=1))
        self.assertEqual((n1, n2, n3), (1, 0, 0))
        self.assertEqual(self.senden.call_count, 1)
        offen = fach.standard().offene("eike")
        self.assertEqual(len(offen), 1)
        self.assertIn("Entwurf", offen[0]["text"])
        self.assertTrue(verwaltung.erinnert(ref, "eingang_nachfassen"))

    def test_erste_erfassung_schweigt_ueber_altbestand(self):
        self.faelliger_antrag()                      # schon da, bevor die Verwaltung je lief
        self.assertEqual(verwaltung.erinnern(JETZT), 0)
        self.senden.assert_not_called()
        self.assertEqual(fach.standard().offene("eike"), [])
        self.assertEqual(len(verwaltung.faellig(JETZT)), 1)   # sichtbar bleibt es trotzdem

    def test_sehr_lange_ueberfaelliges_wird_nicht_mehr_gepusht(self):
        verwaltung.abgleichen(JETZT)
        antrag(self.a, "abgeschickt", abgeschickt_am=tage(-60), an_tage=-61)
        self.assertEqual(verwaltung.erinnern(JETZT), 0)
        self.assertEqual(len(verwaltung.faellig(JETZT)), 1)

    def test_nur_an_eike_und_nur_eigene_kanaele(self):
        verwaltung.abgleichen(JETZT)
        self.faelliger_antrag()
        with mock.patch("kern.dienste.snapchat.auftrag_legen", create=True) as snap, \
                mock.patch("kern.dienste.gmail.Verbindung", create=True) as mail:
            verwaltung.erinnern(JETZT)
        snap.assert_not_called()
        mail.assert_not_called()
        self.assertEqual({a["empfaenger"] for a in fach.standard()._alle("SELECT * FROM anliegen")}, {"eike"})

    def test_fall_eines_freundes_nennt_weder_namen_noch_nummer(self):
        pid = self.a.profil_anlegen({"name": "Lea Beispielfreundin"})
        verwaltung.abgleichen(JETZT)
        antrag(self.a, "abgeschickt", abgeschickt_am=tage(-4), an_tage=-5, profil=pid, vorgang="987654")
        self.assertEqual(verwaltung.erinnern(JETZT), 1)
        text = " ".join(str(c) for c in self.senden.call_args_list) + str(fach.standard()._alle("SELECT * FROM anliegen"))
        self.assertNotIn("Lea", text)
        self.assertNotIn("987654", text)

    def test_hoechstens_drei_je_lauf_der_rest_beim_naechsten(self):
        verwaltung.abgleichen(JETZT)
        for i in range(5):
            antrag(self.a, "abgeschickt", abgeschickt_am=tage(-4), an_tage=-5 - i)
        self.assertEqual(verwaltung.erinnern(JETZT), verwaltung.MAX_JE_LAUF)
        self.assertEqual(verwaltung.erinnern(JETZT), 2)
        self.assertEqual(verwaltung.erinnern(JETZT), 0)

    def test_scheitert_die_zustellung_wird_nicht_als_erinnert_gemerkt(self):
        verwaltung.abgleichen(JETZT)
        ref = self.faelliger_antrag()
        with mock.patch("kern.anliegen.fach.Fach.anlegen", side_effect=RuntimeError("kaputt")):
            self.assertEqual(verwaltung.erinnern(JETZT), 0)
        self.assertFalse(verwaltung.erinnert(ref, "eingang_nachfassen"))
        self.assertEqual(verwaltung.erinnern(JETZT), 1)

    def test_neue_rueckfrage_im_selben_fall_erinnert_neu(self):
        verwaltung.abgleichen(JETZT)
        aid = antrag(self.a, "abgeschickt", vorgang="444444", an_tage=-20)
        vorgang(self.a, "444444", "rueckfrage", aid, vor=-6)
        self.assertEqual(verwaltung.erinnern(JETZT), 1)
        verwaltung.setzen(f"A{aid}", "eingang_bestaetigt", "", quelle="eike", jetzt=JETZT)
        vorgang(self.a, "444444", "rueckfrage", aid, vor=1, mails=2)    # neue Rückfrage-Mail, jünger als Eikes Meldung
        self.a.vorgang_setzen("444444", erledigt=None)
        verwaltung.abgleichen(JETZT + timedelta(days=1))
        self.assertEqual(verwaltung.phase(f"A{aid}"), "rueckfrage_offen")

    def test_der_entwurf_ist_nur_text_und_nennt_keine_privaten_angaben(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-5, abgeschickt_am=tage(-4))
        vorgang(self.a, "111111", "eingang", aid, vor=-4)
        self.a.angaben_setzen({"iban": tg.BEISPIEL_IBAN, "strasse": "Geheimweg 1", "vorname": "Eike"})
        verwaltung.abgleichen(JETZT)
        text = verwaltung.nachfass_entwurf(f"A{aid}", JETZT)
        self.assertIn("111111", text)
        self.assertNotIn("DE89", text)
        self.assertNotIn("Geheimweg", text)
        self.senden.assert_not_called()


class WerkzeugTest(Basis):
    def setUp(self):
        super().setUp()
        k = mock.patch.object(konnektoren, "benutzbar", return_value=True)
        k.start()
        self.addCleanup(k.stop)

    def aus(self, name, fremd=False, eingabe="", **args):
        return asyncio.run(katalog.ausfuehren(name, args, fremde_inhalte=fremd, eingabe=eingabe))

    def test_stand_zeigt_offene_mit_status_naechstem_schritt_und_frist(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-30)
        vorgang(self.a, "111111", "eingang", aid, vor=-22)
        text = werkzeug.garantie_stand()
        self.assertIn("111111", text)
        self.assertIn("Eingang bestätigt", text)
        self.assertIn("Bescheid", text)

    def test_stand_zeigt_keine_freunde(self):
        pid = self.a.profil_anlegen({"name": "Lea Beispielfreundin"})
        antrag(self.a, "abgeschickt", abgeschickt_am=tage(-4), an_tage=-5, profil=pid, vorgang="987654")
        text = werkzeug.garantie_stand()
        self.assertNotIn("Lea", text)
        self.assertNotIn("987654", text)

    def test_stand_leer(self):
        self.assertIn("Nichts offen", werkzeug.garantie_stand())

    def test_stand_und_status_setzen_sind_registriert_nur_eines_in_der_denkrunde(self):
        self.assertIn("garantie_stand", katalog.DENKEN_ERLAUBT)
        self.assertNotIn("garantie_status_setzen", katalog.DENKEN_ERLAUBT)
        self.assertIn("garantie_status_setzen", werkzeug.NICHT_NACH_FREMDTEXT)
        namen = {e["name"] for t in katalog.gemini_tools() for e in t["functionDeclarations"]}
        self.assertTrue({"garantie_stand", "garantie_status_setzen"} <= namen)
        denken = {e["name"] for t in katalog.gemini_tools(modus="denken") for e in t["functionDeclarations"]}
        self.assertIn("garantie_stand", denken)
        self.assertNotIn("garantie_status_setzen", denken)
        fremd = {e["name"] for t in katalog.gemini_tools(person={"werkzeuge": ["garantie_stand"]})
                 for e in t["functionDeclarations"]}
        self.assertFalse({"garantie_stand", "garantie_status_setzen"} & fremd)

    def test_status_setzen_per_vorgangsnummer(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-5)
        vorgang(self.a, "111111", "eingang", aid, vor=-4)
        r = self.aus("garantie_status_setzen", vorgang="111111", status="bescheid_abgelehnt", grund="Bescheid kam")
        self.assertNotIn("fehler", r)
        self.assertEqual(verwaltung.phase(f"A{aid}"), "bescheid_abgelehnt")

    def test_status_setzen_wirkung(self):
        """P0 12.10.2026: Erfolg und Fehlschlag tragen status/art/quittung."""
        from test.test_wirkung import pruefe_wirkung
        aid = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-5)
        vorgang(self.a, "111111", "eingang", aid, vor=-4)
        pruefe_wirkung(self, self.aus("garantie_status_setzen", vorgang="111111", status="erledigt_quatsch"), "nicht", "antrag", "unbekannt")
        r = self.aus("garantie_status_setzen", vorgang="111111", status="bescheid_abgelehnt", grund="Bescheid kam")
        pruefe_wirkung(self, r, "ok", "antrag", "erfolg")
        self.assertTrue(r["quittung"].startswith("Vermerkt: A"), r["quittung"])
        pruefe_wirkung(self, self.aus("garantie_status_setzen", fremd=True, vorgang="111111", status="bescheid_positiv"), "nicht", "antrag",
                       "Fremdtext")

    def test_status_setzen_unzulaessig_sagt_warum(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-5)
        vorgang(self.a, "111111", "eingang", aid, vor=-4)
        text = werkzeug.garantie_status_setzen("111111", "erledigt_quatsch")
        self.assertIn("nicht", text.lower())
        text = werkzeug.garantie_status_setzen("111111", "widerspruch")
        self.assertIn("nicht", text.lower())
        self.assertEqual(verwaltung.phase(f"A{aid}"), "eingang_bestaetigt")

    def test_status_setzen_kann_nicht_abschicken(self):
        aid = antrag(self.a, "offen", an_tage=-1)
        verwaltung.abgleichen(JETZT)
        text = werkzeug.garantie_status_setzen(f"A{aid}", "abgeschickt")
        self.assertIn("nicht", text.lower())
        self.assertEqual(self.a.antrag(aid)["status"], "offen")

    def test_status_setzen_nach_fremdtext_gesperrt_ausser_eike_verlangt_es(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-5)
        vorgang(self.a, "111111", "eingang", aid, vor=-4)
        r = self.aus("garantie_status_setzen", fremd=True, vorgang="111111", status="bescheid_positiv")
        self.assertIn("fehler", r)
        self.assertEqual(verwaltung.phase(f"A{aid}") or "eingang_bestaetigt", "eingang_bestaetigt")

    def test_freund_wird_ueber_das_werkzeug_nicht_veraendert(self):
        pid = self.a.profil_anlegen({"name": "Lea"})
        aid = antrag(self.a, "abgeschickt", vorgang="987654", an_tage=-5, profil=pid)
        text = werkzeug.garantie_status_setzen(f"A{aid}", "bescheid_positiv")
        self.assertIn("nicht", text.lower())
        self.assertNotEqual(verwaltung.phase(f"A{aid}"), "bescheid_positiv")


class Anzeige(Basis):
    def test_uebersicht_traegt_verwaltung_mit_status_und_frist(self):
        aid = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-30)
        vorgang(self.a, "111111", "eingang", aid, vor=-22)
        with mock.patch.object(konnektoren, "benutzbar", return_value=True):
            r = TestClient(app).get("/api/garantie/uebersicht")
        self.assertEqual(r.status_code, 200)
        v = r.json()["verwaltung"]
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["phase"], "eingang_bestaetigt")
        self.assertEqual(v[0]["schritt"], "bescheid_nachfassen")
        self.assertIn("phase_text", v[0])
        self.assertNotIn("iban", str(v).lower())


class PhasenRoute(Basis):
    """POST /api/garantie/verwaltung/{ref}/phase: Eikes Knöpfe in der Übersicht. Sendet nie, mailt nie."""

    def setUp(self):
        super().setUp()
        k = mock.patch.object(konnektoren, "benutzbar", return_value=True)
        k.start()
        self.addCleanup(k.stop)
        self.c = TestClient(app)
        aid = antrag(self.a, "abgeschickt", vorgang="111111", an_tage=-30, abgeschickt_am=tage(-29))
        vorgang(self.a, "111111", "eingang", aid, vor=-22)
        self.ref = f"A{aid}"
        verwaltung.abgleichen(JETZT)

    def post(self, phase, ref=None, **mehr):
        return self.c.post(f"/api/garantie/verwaltung/{ref or self.ref}/phase",
                           json={"phase": phase, "bestaetigt": True, **mehr})

    def test_setzt_erlaubte_folgephase_und_protokolliert(self):
        r = self.post("bescheid_positiv", grund="Mail kam")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["phase"], "bescheid_positiv")
        self.assertEqual(verwaltung.phase(self.ref), "bescheid_positiv")
        z = verwaltung.historie(self.ref)[-1]
        self.assertEqual((z["nach"], z["ok"], z["grund"], z["quelle"]), ("bescheid_positiv", 1, "Mail kam", "eike"))

    def test_ohne_bestaetigung_400_und_nichts_geaendert(self):
        r = self.c.post(f"/api/garantie/verwaltung/{self.ref}/phase", json={"phase": "bescheid_positiv"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(verwaltung.phase(self.ref), "eingang_bestaetigt")

    def test_unzulaessiger_uebergang_ist_409_mit_klartext(self):
        r = self.post("widerspruch")
        self.assertEqual(r.status_code, 409)
        self.assertIn("nicht vorgesehen", r.json()["detail"])
        self.assertEqual(verwaltung.phase(self.ref), "eingang_bestaetigt")
        self.assertEqual(verwaltung.historie(self.ref)[-1]["ok"], 0)

    def test_abgeschickt_nie_ueber_diese_route(self):
        aid = antrag(self.a, "offen", an_tage=-1)
        verwaltung.abgleichen(JETZT)
        verwaltung.setzen(f"A{aid}", "vorbereitet", jetzt=JETZT)
        r = self.post("abgeschickt", ref=f"A{aid}")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Abschicken", r.json()["detail"])
        self.assertEqual(verwaltung.phase(f"A{aid}"), "vorbereitet")
        self.assertEqual(self.a.antrag(aid)["status"], "offen")

    def test_alle_abhaken_beendet_alles_ohne_senden(self):
        aid = antrag(self.a, "offen", an_tage=-1)
        verwaltung.abgleichen(JETZT)
        verwaltung.setzen(f"A{aid}", "vorbereitet", jetzt=JETZT)
        r = self.c.post("/api/garantie/verwaltung/alle-abschliessen", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(verwaltung.phase(self.ref), "eingang_bestaetigt")
        r = self.c.post("/api/garantie/verwaltung/alle-abschliessen", json={"bestaetigt": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["erledigt"], r.json()["verworfen"]), (1, 1))
        self.assertEqual(verwaltung.phase(self.ref), "erledigt")
        self.assertEqual(verwaltung.phase(f"A{aid}"), "verworfen")
        self.assertEqual(self.a.antrag(aid)["status"], "offen")           # nichts abgeschickt
        self.assertEqual(verwaltung.historie(self.ref)[-1]["grund"], "Sammelabschluss durch Eike")
        self.assertEqual(verwaltung.offene(JETZT), [])

    def test_unbekannte_phase_und_unbekannter_vorgang(self):
        self.assertEqual(self.post("quatsch").status_code, 400)
        self.assertEqual(self.post("erledigt", ref="A99999").status_code, 404)

    def test_freigabe_aus_ist_409(self):
        with mock.patch.object(konnektoren, "benutzbar", return_value=False):
            r = self.post("bescheid_positiv")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(verwaltung.phase(self.ref), "eingang_bestaetigt")

    def test_sendet_nichts_und_aendert_den_bestand_nicht(self):
        with mock.patch("kern.garantie.abschicken.abschicken") as ab:
            self.post("rueckfrage_offen")
        ab.assert_not_called()
        self.senden.assert_not_called()
        self.assertEqual(self.a.antrag(int(self.ref[1:]))["status"], "abgeschickt")

    def test_uebersicht_nennt_die_erlaubten_folgephasen_ohne_abgeschickt(self):
        v = TestClient(app).get("/api/garantie/uebersicht").json()["verwaltung"][0]
        self.assertEqual([f["phase"] for f in v["folgephasen"]], list(verwaltung.UEBERGAENGE["eingang_bestaetigt"]))
        self.assertTrue(all(f["text"] for f in v["folgephasen"]))
        self.assertNotIn("abgeschickt", [f["phase"] for f in verwaltung.folgephasen("vorbereitet")])
        self.assertIn("freigegeben_wartet", [f["phase"] for f in verwaltung.folgephasen("vorbereitet")])

    def test_bahn_bescheid_positiv_braucht_betrag(self):
        with mock.patch("kern.garantie.fahrgastrechte.bescheid") as b:
            r = self.c.post("/api/garantie/verwaltung/B1/phase", json={"phase": "bescheid_positiv", "bestaetigt": True})
        self.assertIn(r.status_code, (404, 409))
        b.assert_not_called()


class Hintergrund(Basis):
    def test_dauerlauf_ruft_die_verwaltung_auf_und_sie_ist_abschaltbar_wie_der_rest(self):
        import inspect
        from kern.garantie import sammler
        quelle = inspect.getsource(sammler.dauerlauf)
        self.assertIn("verwaltung", quelle)
