"""Zeit als Auslöser einer Regel (15.10.2026): Zeitangaben lesen, Fälligkeit, Planer-Schritt, Lauf — ohne Netz."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from kern import config, konnektoren
from kern.dienste import benachrichtigungen, whatsapp
from kern.regeln import lauf, speicher, zeit

CHAT = "4915100000000@c.us"


def run(c):
    return asyncio.run(c)


def antwort(*teile):
    return {"candidates": [{"content": {"parts": list(teile)}}]}


class Skript:
    def __init__(self, *antworten, fehler=None):
        self.antworten, self.fehler, self.aufrufe = list(antworten), fehler, []

    async def __call__(self, inhalt, bevorzugt=None, schluessel_liste=None):
        self.aufrufe.append(inhalt)
        if self.fehler:
            raise self.fehler
        return self.antworten.pop(0), "modell"


def tz(j, m, t, h, mi=0):
    return datetime(j, m, t, h, mi).astimezone()


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.patches = [
            mock.patch.object(whatsapp, "frei", return_value=[{"id": CHAT, "name": "Kontakt F", "senden": True}]),
            mock.patch.object(whatsapp, "darf_senden", return_value=True),
            mock.patch.object(whatsapp, "senden", mock.AsyncMock(return_value={"text": "x"})),
            mock.patch.object(konnektoren, "benutzbar", return_value=True),
            mock.patch.object(konnektoren, "schreiben_erlaubt", return_value=True),
            mock.patch.object(benachrichtigungen, "senden", return_value={"gesendet": 1}),
        ]
        self.m = [p.start() for p in self.patches]
        self.senden = self.m[2]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()

    def regel(self, wann, aktionen=("antwort_an_empfaenger",), auftrag="Schreib Kontakt F, dass ich heute nicht komme.", **kw):
        r = speicher.anlegen(kw.pop("name", "Absage"), "zeit", wann, auftrag,
                             kw.pop("empfaenger", [{"art": "chat", "id": CHAT, "name": "Kontakt F"}]), list(aktionen), **kw)
        with mock.patch.object(config, "jetzt", return_value=tz(2026, 10, 14, 12)):   # bestätigt VOR den Testzeiten, unabhängig von der echten Uhr
            return speicher.bestaetigen(r["id"])


class WannLesen(unittest.TestCase):
    def test_einmalig_taeglich_und_wochentage(self):
        j = tz(2026, 10, 15, 12)
        self.assertEqual(zeit.wann_lesen("2026-10-15T18:00", j), {"typ": "einmalig", "zeit": "2026-10-15T18:00"})
        self.assertEqual(zeit.wann_lesen("2026-10-15 18:00", j)["zeit"], "2026-10-15T18:00")
        self.assertEqual(zeit.wann_lesen("täglich 08:00", j), {"typ": "taeglich", "uhrzeit": "08:00"})
        self.assertEqual(zeit.wann_lesen("taeglich 8:05", j), {"typ": "taeglich", "uhrzeit": "08:05"})
        self.assertEqual(zeit.wann_lesen("mo,mi 17:30", j), {"typ": "woechentlich", "tage": ["mo", "mi"], "uhrzeit": "17:30"})
        self.assertEqual(zeit.wann_lesen("Montag, Mittwoch 17:30", j)["tage"], ["mo", "mi"])

    def test_ungueltiges_und_vergangenes_wird_abgelehnt(self):
        j = tz(2026, 10, 15, 12)
        for s in ("", "irgendwann", "täglich 25:00", "xx 10:00", "mo 10:75", "2026-10-15T11:00", "2026-13-40T10:00", None):
            with self.assertRaises(speicher.RegelFehler, msg=s):
                zeit.wann_lesen(s, j)

    def test_beschreibung_klartext(self):
        self.assertIn("18:00", zeit.beschreibung({"typ": "einmalig", "zeit": "2026-10-15T18:00"}))
        self.assertIn("jeden tag", zeit.beschreibung({"typ": "taeglich", "uhrzeit": "08:00"}).casefold())
        self.assertIn("Mo", zeit.beschreibung({"typ": "woechentlich", "tage": ["mo", "mi"], "uhrzeit": "17:30"}))


class Faelligkeit(unittest.TestCase):
    def test_einmalig(self):
        w = {"typ": "einmalig", "zeit": "2026-10-15T18:00"}
        seit = tz(2026, 10, 15, 10)
        self.assertIsNone(zeit.letzte_faelligkeit(w, tz(2026, 10, 15, 17, 59), seit))
        self.assertEqual(zeit.letzte_faelligkeit(w, tz(2026, 10, 15, 18, 0), seit), tz(2026, 10, 15, 18))
        self.assertEqual(zeit.letzte_faelligkeit(w, tz(2026, 10, 16, 9), seit), tz(2026, 10, 15, 18))
        self.assertIsNone(zeit.letzte_faelligkeit(w, tz(2026, 10, 16, 9), tz(2026, 10, 15, 19)))      # vor der Bestätigung gab es nichts

    def test_taeglich_nimmt_die_letzte_vergangene_zeit(self):
        w = {"typ": "taeglich", "uhrzeit": "08:00"}
        seit = tz(2026, 10, 1, 0)
        self.assertEqual(zeit.letzte_faelligkeit(w, tz(2026, 10, 15, 8, 5), seit), tz(2026, 10, 15, 8))
        self.assertEqual(zeit.letzte_faelligkeit(w, tz(2026, 10, 15, 7, 59), seit), tz(2026, 10, 14, 8))
        self.assertIsNone(zeit.letzte_faelligkeit(w, tz(2026, 10, 15, 7, 59), tz(2026, 10, 14, 9)))

    def test_wochentage(self):
        w = {"typ": "woechentlich", "tage": ["mo", "mi"], "uhrzeit": "17:30"}      # 15.10.2026 ist ein Donnerstag
        seit = tz(2026, 10, 1, 0)
        self.assertEqual(zeit.letzte_faelligkeit(w, tz(2026, 10, 15, 12), seit), tz(2026, 10, 14, 17, 30))   # Mittwoch
        self.assertEqual(zeit.letzte_faelligkeit(w, tz(2026, 10, 12, 18), seit), tz(2026, 10, 12, 17, 30))    # Montag


class Planer(Basis):
    def jetzt(self, h, mi=0, t=15):
        return tz(2026, 10, t, h, mi)

    def test_einmalige_regel_laeuft_zur_zeit_einmal_und_ist_danach_erledigt(self):
        r = self.regel({"typ": "einmalig", "zeit": "2026-10-15T18:00"})
        with mock.patch.object(config, "jetzt", return_value=self.jetzt(17, 59)):
            self.assertEqual(run(zeit.schritt(anfrage=Skript())), [])
        s = Skript(antwort({"functionCall": {"name": "regel_antwort", "args": {"empfaenger": "Kontakt F", "text": "Ich komme heute nicht."}}}),
                   antwort({"text": "ok"}))
        with mock.patch.object(config, "jetzt", return_value=self.jetzt(18, 0)):
            erg = run(zeit.schritt(anfrage=s))
        self.assertEqual([e["status"] for e in erg], ["ok"])
        self.senden.assert_awaited_once_with(CHAT, "Ich komme heute nicht.")
        self.assertEqual(speicher.holen(r["id"])["status"], "erledigt")
        with mock.patch.object(config, "jetzt", return_value=self.jetzt(18, 1)):
            self.assertEqual(run(zeit.schritt(anfrage=Skript())), [])
        self.assertEqual(self.senden.await_count, 1)

    def test_prompt_nennt_die_uhrzeit_und_keinen_fremden_text(self):
        self.regel({"typ": "einmalig", "zeit": "2026-10-15T18:00"})
        s = Skript(antwort({"text": "ok"}))
        with mock.patch.object(config, "jetzt", return_value=self.jetzt(18, 0)):
            run(zeit.schritt(anfrage=s))
        prompt = s.aufrufe[0]["contents"][0]["parts"][0]["text"]
        self.assertIn("[AUFTRAG VON EIKE]\nSchreib Kontakt F", prompt)
        self.assertIn("[AUSLÖSER", prompt)
        self.assertNotIn("FREMDER TEXT", prompt)
        namen = {e["name"] for t in s.aufrufe[0]["tools"] for e in t["functionDeclarations"]}
        self.assertEqual(namen, {"regel_antwort"})

    def test_taegliche_regel_laeuft_jeden_tag_einmal(self):
        r = self.regel({"typ": "taeglich", "uhrzeit": "08:00"}, aktionen=("push",), empfaenger=[])
        for t, h, mi, soll in ((16, 8, 0, 1), (16, 8, 10, 0), (17, 8, 1, 1)):
            with mock.patch.object(config, "jetzt", return_value=tz(2026, 10, t, h, mi)):
                erg = run(zeit.schritt(anfrage=Skript(antwort({"text": "ok"}))))
            self.assertEqual(len(erg), soll, (t, h, mi))
        self.assertEqual(speicher.holen(r["id"])["status"], "aktiv")

    def test_verpasste_zeit_wird_nicht_nachgeholt_aber_vermerkt(self):
        r = self.regel({"typ": "einmalig", "zeit": "2026-10-15T18:00"})
        s = Skript()
        with mock.patch.object(config, "jetzt", return_value=self.jetzt(20, 0)):             # 2 h zu spät (Server war aus)
            erg = run(zeit.schritt(anfrage=s))
        self.assertEqual([e["status"] for e in erg], ["uebersprungen"])
        self.assertEqual(s.aufrufe, [])
        self.senden.assert_not_awaited()
        self.assertIn("verpasst", speicher.laeufe()[0]["aktionen"][0]["kurz"])
        self.assertEqual(speicher.holen(r["id"])["status"], "erledigt")

    def test_nur_aktive_zeitregeln_laufen_und_widerrufene_nicht(self):
        r = self.regel({"typ": "einmalig", "zeit": "2026-10-15T18:00"})
        speicher.setzen(r["id"], "widerrufen")
        with mock.patch.object(config, "jetzt", return_value=self.jetzt(18, 0)):
            self.assertEqual(run(zeit.schritt(anfrage=Skript())), [])


class Anlegen(Basis):
    def test_zeitregel_hat_keinen_absender_und_braucht_empfaenger_fuer_antworten(self):
        w = {"typ": "taeglich", "uhrzeit": "08:00"}
        for kw in ({"aktionen": ("antwort_an_ausloeser",)}, {"aktionen": ("antwort_an_empfaenger",), "empfaenger": []}):
            with self.assertRaises(speicher.RegelFehler, msg=kw):
                self.regel(w, **kw)
        self.assertEqual(self.regel(w, aktionen=("push",), empfaenger=[])["kanal"], "zeit")
        with self.assertRaises(speicher.RegelFehler):
            speicher.anlegen("x", "zeit", {"typ": "kaputt"}, "Auftrag", [], ["push"])

    def test_zeitregeln_sind_keine_nachrichtenregeln(self):
        self.regel({"typ": "taeglich", "uhrzeit": "08:00"}, aktionen=("push",), empfaenger=[])
        self.assertEqual(lauf.passende("whatsapp", CHAT), [])
        self.assertEqual(len(speicher.aktive("zeit")), 1)

    def test_empfaenger_ohne_name_in_zeitregel_wird_zum_ziel(self):
        from kern.regeln import kontext
        from kern.werkzeuge import katalog
        r = self.regel({"typ": "taeglich", "uhrzeit": "08:00"}, aktionen=("antwort_an_empfaenger",))
        k = kontext.Kontext(regel=r, ereignis={"kanal": "zeit", "absender_id": "", "absender_name": "Uhrzeit", "text": "x", "ereignis_id": "z"})
        with kontext.setzen(k):
            leer = run(katalog.ausfuehren("regel_antwort", {"text": "Hi"}, fremde_inhalte=False, modus="regel"))
            kontakt_f = run(katalog.ausfuehren("regel_antwort", {"empfaenger": "Kontakt F", "text": "Hi"}, fremde_inhalte=False, modus="regel"))
        self.assertEqual((leer["status"], kontakt_f["status"]), ("nicht", "ok"))


class Loeschen(Basis):
    def test_loeschen_entfernt_regel_und_laeufe_wirklich(self):
        r = self.regel({"typ": "taeglich", "uhrzeit": "08:00"}, aktionen=("push",), empfaenger=[])
        lid = speicher.lauf_neu(r["id"], "z1", "x")
        speicher.lauf_ende(lid, "ok", [])
        andere = self.regel({"typ": "taeglich", "uhrzeit": "09:00"}, aktionen=("push",), empfaenger=[], name="Andere")
        self.assertTrue(speicher.loeschen(r["id"]))
        self.assertIsNone(speicher.holen(r["id"]))
        self.assertEqual([x["id"] for x in speicher.liste()], [andere["id"]])
        self.assertEqual(speicher.laeufe(), [])
        self.assertFalse(speicher.loeschen(r["id"]))

    def test_geloeschte_regel_im_laufenden_lauf_sendet_nichts_mehr(self):
        from kern.regeln import kontext
        from kern.werkzeuge import katalog
        r = self.regel({"typ": "taeglich", "uhrzeit": "08:00"}, aktionen=("antwort_an_empfaenger",))
        speicher.loeschen(r["id"])
        k = kontext.Kontext(regel=r, ereignis={"kanal": "zeit", "absender_id": "", "absender_name": "Uhrzeit", "text": "x", "ereignis_id": "z"})
        with kontext.setzen(k):
            erg = run(katalog.ausfuehren("regel_antwort", {"empfaenger": "Kontakt F", "text": "Hi"}, modus="regel"))
        self.assertEqual(erg["status"], "nicht")
        self.senden.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
