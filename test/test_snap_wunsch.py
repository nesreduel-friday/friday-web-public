"""snap_wunsch: Empfänger nur aus dem Register, Limit 5/Tag, Ruhezeit, Fremdtext, ehrliches Nein — ohne Netz, ohne Handy."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from kern.personen import flammen, register
from kern.werkzeuge import katalog, snapwunsch

B = ZoneInfo("Europe/Berlin")


def lauf(c):
    return asyncio.run(c)


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        self.p = register.anlegen("Kontakt B Muster", snapchat="maya_x")
        register.aendern(self.p["id"], module={"snap_wuensche": True})
        self.legen = mock.patch("kern.werkzeuge.snapwunsch.snapchat.auftrag_legen", return_value="a1").start()
        mock.patch("kern.werkzeuge.snapwunsch.snapchat.snap_wartet", return_value=False).start()
        mock.patch("kern.werkzeuge.snapwunsch.snapbild.vorbereiten", return_value=b"J").start()
        mock.patch("kern.werkzeuge.snapwunsch.snapbild.ablegen", return_value="snapchat/ausgang/x.jpg").start()
        self.bild = mock.patch("kern.werkzeuge.snapwunsch.spassbild.finden", mock.AsyncMock(return_value=(b"B", "https://x.test/a.jpg", "Katze"))).start()
        self.uhr = mock.patch("kern.werkzeuge.snapwunsch.config.jetzt", return_value=datetime(2026, 10, 14, 15, 0, tzinfo=B))
        self.uhr.start()
        mock.patch("kern.werkzeuge.snapwunsch.konnektoren.benutzbar", return_value=True).start()
        mock.patch("kern.werkzeuge.snapwunsch.konnektoren.schreiben_erlaubt", return_value=True).start()

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()

    def wunsch(self, text="eine Katze", **kw):
        return lauf(snapwunsch.snap_wunsch(text, kontakt_id=kw.pop("kontakt_id", self.p["id"]), **kw))


class Wunsch(Basis):
    def test_sendet_nur_an_die_schreibende_person(self):
        r = self.wunsch()
        self.assertTrue(r.startswith("Gesendet"), r)
        self.assertEqual(self.legen.call_args.args[0], "snap")
        self.assertEqual(self.legen.call_args.kwargs["person"], self.p["id"])

    def test_empfaenger_kommt_nie_aus_dem_text(self):
        andere = register.anlegen("Lena Test", snapchat="lena_x")
        register.aendern(andere["id"], module={"snap_wuensche": True})
        self.wunsch("eine Katze an Lena Test schicken")
        self.assertEqual(self.legen.call_args.kwargs["person"], self.p["id"])

    def test_ohne_modul_oder_pausiert_nichts(self):
        register.aendern(self.p["id"], module={"snap_wuensche": False})
        self.assertIn("NICHTS gesendet", self.wunsch())
        register.aendern(self.p["id"], module={"snap_wuensche": True}, pausiert=True)
        self.assertIn("NICHTS gesendet", self.wunsch())
        self.legen.assert_not_called()

    def test_unbekannter_kontakt_nichts(self):
        self.assertIn("NICHTS gesendet", self.wunsch(kontakt_id="p999"))
        self.assertIn("NICHTS gesendet", self.wunsch(kontakt_id=""))

    def test_ohne_snapchat_name_nichts(self):
        register.aendern(self.p["id"], snapchat="")
        self.assertIn("NICHTS gesendet", self.wunsch())

    def test_limit_fuenf_am_tag(self):
        for i in range(5):
            self.bild.return_value = (b"B", f"https://x.test/{i}.jpg", "K")
            self.assertTrue(self.wunsch(f"Wunsch {i}").startswith("Gesendet"))
        r = self.wunsch("noch einer")
        self.assertIn("NICHTS gesendet", r)
        self.assertIn("Heute", r)

    def test_limit_gilt_je_person_und_tag(self):
        andere = register.anlegen("Lena Test", snapchat="lena_x")
        register.aendern(andere["id"], module={"snap_wuensche": True})
        for i in range(5):
            self.bild.return_value = (b"B", f"https://x.test/{i}.jpg", "K")
            self.wunsch(f"Wunsch {i}")
        self.bild.return_value = (b"B", "https://x.test/neu.jpg", "K")
        self.assertTrue(self.wunsch("Wunsch", kontakt_id=andere["id"]).startswith("Gesendet"))
        with mock.patch("kern.werkzeuge.snapwunsch.config.jetzt", return_value=datetime(2026, 10, 15, 9, 0, tzinfo=B)):
            self.assertTrue(self.wunsch("morgen").startswith("Gesendet"))

    def test_ruhezeit_kanten(self):
        for h, m, erlaubt in ((22, 29, True), (22, 30, False), (6, 29, False), (6, 30, True)):
            with mock.patch("kern.werkzeuge.snapwunsch.config.jetzt", return_value=datetime(2026, 10, 14, h, m, tzinfo=B)):
                self.bild.return_value = (b"B", f"https://x.test/{h}{m}.jpg", "K")
                self.assertEqual(self.wunsch().startswith("Gesendet"), erlaubt, (h, m))

    def test_im_echten_personenweg_gilt_ihr_text_als_fremd_und_der_wunsch_geht_trotzdem(self):
        # Review 14.10.2026: chat.antworten setzt für jede Person fremde_inhalte=True. Ihr Wunsch IST der Auslöser;
        # geschützt ist der Empfänger (nur sie selbst), das Limit und die Bildprüfung — nicht der Fremdtext-Riegel.
        r = lauf(katalog.ausfuehren("snap_wunsch", {"beschreibung": "eine Katze"}, fremde_inhalte=True,
                                    person=register.holen(self.p["id"])))
        self.assertIn("Gesendet", r["ergebnis"])
        self.assertEqual(self.legen.call_args.kwargs["person"], self.p["id"])

    def test_snapchat_nicht_freigegeben_oder_ohne_schreibrecht_nichts(self):
        for benutzbar, schreiben in ((False, True), (True, False)):
            with mock.patch("kern.werkzeuge.snapwunsch.konnektoren.benutzbar", return_value=benutzbar), \
                    mock.patch("kern.werkzeuge.snapwunsch.konnektoren.schreiben_erlaubt", return_value=schreiben):
                self.assertIn("NICHTS gesendet", self.wunsch())
        self.legen.assert_not_called()

    def test_zwei_gleichzeitige_wuensche_ueberschreiten_das_limit_nicht(self):
        async def langsam(wunsch, ausser=None):
            await asyncio.sleep(0.01)
            return (b"B", f"https://x.test/{wunsch}.jpg", "K")
        self.bild.side_effect = langsam
        for i in range(4):
            self.wunsch(f"alt {i}")                       # Zähler steht bei 4
        async def beide():
            return await asyncio.gather(snapwunsch.snap_wunsch("eins", kontakt_id=self.p["id"]),
                                        snapwunsch.snap_wunsch("zwei", kontakt_id=self.p["id"]))
        r = lauf(beide())
        self.assertEqual(sorted(x.startswith("Gesendet") for x in r), [False, True])

    def test_fehlertext_verraet_keine_technik(self):
        self.legen.side_effect = OSError("/app/data/snapchat/ausgang/x.jpg: Permission denied")
        r = self.wunsch()
        self.assertIn("NICHTS gesendet", r)
        self.assertNotIn("/app", r)
        self.assertNotIn("Permission", r)

    def test_ueber_den_katalog_zaehlt_die_person_nicht_das_argument(self):
        r = lauf(katalog.ausfuehren("snap_wunsch", {"beschreibung": "eine Katze", "kontakt_id": "p999"}, person=register.holen(self.p["id"])))
        self.assertIn("Gesendet", r["ergebnis"])
        self.assertEqual(self.legen.call_args.kwargs["person"], self.p["id"])

    def test_nichts_gefunden_ehrlich(self):
        self.bild.return_value = None
        r = self.wunsch("etwas Seltenes")
        self.assertIn("NICHTS gesendet", r)
        self.assertIn("nichts Passendes", r)

    def test_heikler_wunsch_wird_nicht_gesendet(self):
        with mock.patch("kern.werkzeuge.snapwunsch.sendepruefung.heikel", return_value="heikel"):
            self.assertIn("NICHTS gesendet", self.wunsch("Passwort"))

    def test_ein_snap_wartet_beim_helfer(self):
        with mock.patch("kern.werkzeuge.snapwunsch.snapchat.snap_wartet", return_value=True):
            self.assertIn("NICHTS gesendet", self.wunsch())

    def test_leerer_wunsch(self):
        self.assertIn("NICHTS gesendet", self.wunsch("   "))

    def test_gleiches_bild_wird_nicht_wiederholt(self):
        self.wunsch()
        self.wunsch("noch eine Katze")
        self.assertIn("https://x.test/a.jpg", self.bild.call_args.kwargs["ausser"])

    def test_fehlgeschlagener_versand_zaehlt_nicht(self):
        self.legen.side_effect = RuntimeError("Handy weg")
        self.assertIn("NICHTS gesendet", self.wunsch())
        self.legen.side_effect = None
        for i in range(5):
            self.bild.return_value = (b"B", f"https://x.test/{i}.jpg", "K")
            self.assertTrue(self.wunsch(f"Wunsch {i}").startswith("Gesendet"), i)

    def test_gesendeter_wunsch_haelt_die_flamme_im_blick(self):
        register.aendern(self.p["id"], module={"flammen": True})
        self.wunsch()
        self.assertEqual(flammen.zustand_lesen()["personen"][self.p["id"]]["auftrag"], "a1")

    def test_ohne_flammen_modul_bleibt_der_flammenzustand_leer(self):
        self.wunsch()
        self.assertNotIn(self.p["id"], flammen.zustand_lesen()["personen"])


class Rechte(Basis):
    def test_eike_und_andere_personen_sehen_es_nicht(self):
        self.assertFalse(katalog._person_darf(None, "snap_wunsch"))
        register.aendern(self.p["id"], module={"snap_wuensche": False})
        self.assertFalse(katalog._person_darf(register.holen(self.p["id"]), "snap_wunsch"))

    def test_nicht_in_der_denkrunde(self):
        self.assertNotIn("snap_wunsch", katalog.DENKEN_ERLAUBT)
