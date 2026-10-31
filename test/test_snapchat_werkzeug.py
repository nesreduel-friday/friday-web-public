import asyncio, os, tempfile, unittest
from pathlib import Path
from unittest import mock

from kern import konnektoren
from kern.dienste import snapchat
from kern.gedaechtnis import bilder as gedaechtnis_bilder
from kern.personen import register
from kern.werkzeuge import katalog, snapchat as werkzeug
from kern.werkzeuge import snapchat as swerkzeug


EIN = "schick mir einen Snap"


def lauf(coro):
    return asyncio.run(coro)


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        konnektoren.setzen("snapchat", True)

    def tearDown(self):
        self.env.stop()


class WerkzeugTest(Basis):
    def test_ohne_schreibrecht_nicht_ausfuehrbar_ueber_katalog(self):
        aus = lauf(katalog.ausfuehren("schicke_snap", {"text": "Hi"}))
        self.assertIn("Schreibrecht", aus.get("fehler", ""))

    def _png_datei(self, name="abc123.png"):
        from io import BytesIO
        from PIL import Image
        pfad = Path(self.ordner) / "bilder" / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        buf = BytesIO()
        Image.new("RGB", (300, 600), (10, 100, 200)).save(buf, "PNG")
        pfad.write_bytes(buf.getvalue())
        return pfad

    def test_zweiter_snap_wird_hinten_angestellt_statt_abgewiesen(self):
        with mock.patch.object(werkzeug.snapchat, "snap_wartet", return_value=True), \
             mock.patch.object(gedaechtnis_bilder, "suchen", return_value=[{"id": "abc123"}]), \
             mock.patch.object(gedaechtnis_bilder, "pfad", return_value=self._png_datei()), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(suche="Monitor", eingabe=EIN))
        m_legen.assert_called_once()
        self.assertIn("liegt beim Handy", satz)
        self.assertIn("in der Schlange", satz)

    def test_gezeigtes_bild_wird_geschickt_statt_neu_zu_suchen(self):
        from kern.dienste import bildsuche
        bildsuche.zuletzt_setzen("gedaechtnis", "abc123", "Pinke Schlange")
        with mock.patch.object(gedaechtnis_bilder, "suchen") as m_suche, \
             mock.patch.object(gedaechtnis_bilder, "pfad", return_value=self._png_datei()), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(quelle="gezeigt", eingabe=EIN))
        m_suche.assert_not_called()
        m_legen.assert_called_once()
        self.assertIn("Pinke Schlange", satz)

    def test_gezeigtes_bild_ohne_vorher_gezeigtes_sagt_es_ehrlich(self):
        from kern.dienste import bildsuche
        with mock.patch.object(bildsuche, "_zuletzt", None), mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(quelle="gezeigt", eingabe=EIN))
        self.assertIn("kein Bild gezeigt", satz)
        m_legen.assert_not_called()

    def test_snap_an_mich_ist_ein_snap_an_eike_nicht_an_einen_kontakt(self):
        from kern.dienste import bildsuche
        bildsuche.zuletzt_setzen("gedaechtnis", "abc123", "Schlange")
        with mock.patch.object(gedaechtnis_bilder, "pfad", return_value=self._png_datei()), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(quelle="gezeigt", person="Eike", eingabe="schick das an mich"))
        self.assertNotIn("kenne ich nicht", satz)
        self.assertIsNone(m_legen.call_args.kwargs.get("person"))

    def test_ohne_bild_gibt_es_keinen_snap(self):
        with mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(text="Na du!", eingabe=EIN))
        self.assertIn("Ohne Bild", satz)
        m_legen.assert_not_called()

    def test_gedaechtnis_bild_wird_aufbereitet_und_als_bildauftrag_gelegt(self):
        with mock.patch.object(gedaechtnis_bilder, "suchen", return_value=[{"id": "abc123"}]), \
             mock.patch.object(gedaechtnis_bilder, "pfad", return_value=self._png_datei()), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(suche="Monitor", text="Denk dran!", eingabe=EIN))
        self.assertIn("liegt beim Handy", satz)
        self.assertNotIn("unterwegs", satz)
        self.assertIn("Denk dran!", satz)
        args = m_legen.call_args
        self.assertEqual(args.args, ("snap",))
        self.assertEqual(args.kwargs["text"], "")  # der Text steckt schon im Bild
        rel = args.kwargs["bild_pfad"]
        self.assertTrue(rel.startswith("snapchat/ausgang/") and rel.endswith(".jpg"))
        self.assertEqual((Path(self.ordner) / rel).read_bytes()[:2], b"\xff\xd8")

    def test_erfolg_landet_als_kontext_im_snapchat_faden(self):
        # Antwortet Eike auf dem Snap in Snapchat, muss Friday wissen, was sie geschickt hat.
        bilder = {"id": "abc123", "name": "Kontakt Q", "beschreibung": "Eine Frau auf einem Motorrad."}
        with mock.patch.object(gedaechtnis_bilder, "suchen", return_value=[bilder]), \
             mock.patch.object(gedaechtnis_bilder, "pfad", return_value=self._png_datei()), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen"), \
             mock.patch.object(werkzeug.snapchat, "kontext_vermerken") as m_kontext:
            satz = lauf(werkzeug.schicke_snap(suche="Kontakt Q", text="Hallo Kontakt Q", eingabe=EIN))
        vermerk = m_kontext.call_args.args[0]
        self.assertIn("Kontakt Q", vermerk)
        self.assertIn("Motorrad", vermerk)
        self.assertIn("Hallo Kontakt Q", vermerk)
        self.assertIn("Motorrad", satz)              # steht auch im Ergebnis, also im Protokoll des Web-Chats

    def test_scheitert_das_legen_wird_nichts_vermerkt(self):
        with mock.patch.object(gedaechtnis_bilder, "suchen", return_value=[{"id": "abc123"}]), \
             mock.patch.object(gedaechtnis_bilder, "pfad", return_value=self._png_datei()), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen",
                               side_effect=werkzeug.snapchat.SnapchatFehler("voll")), \
             mock.patch.object(werkzeug.snapchat, "kontext_vermerken") as m_kontext:
            lauf(werkzeug.schicke_snap(suche="Monitor", eingabe=EIN))
        m_kontext.assert_not_called()

    def test_text_wird_auf_das_bild_gelegt(self):
        with mock.patch.object(gedaechtnis_bilder, "suchen", return_value=[{"id": "abc123"}]), \
             mock.patch.object(gedaechtnis_bilder, "pfad", return_value=self._png_datei()), \
             mock.patch.object(werkzeug.snapbild, "vorbereiten", wraps=werkzeug.snapbild.vorbereiten) as m_vor, \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen"):
            lauf(werkzeug.schicke_snap(suche="Monitor", text="Alles Gute!", eingabe=EIN))
        self.assertEqual(m_vor.call_args.args[1], "Alles Gute!")

    def test_kein_treffer_im_gedaechtnis_sagt_das_und_sendet_nichts(self):
        with mock.patch.object(gedaechtnis_bilder, "suchen", return_value=[]), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(suche="Einhorn", eingabe=EIN))
        self.assertIn("kein Bild", satz)
        m_legen.assert_not_called()

    def test_internet_bild_wird_geholt_und_geschickt(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (200, 300), (200, 30, 30)).save(buf, "JPEG")

        async def suchen(begriff, anzahl=1):
            return [{"bild": "https://beispiel.test/turm.jpg", "titel": "Turm"}]

        async def holen(url):
            return "0123456789abcdef"

        with mock.patch.object(werkzeug.bildsuche, "kandidaten", suchen), \
             mock.patch.object(werkzeug.bildsuche, "pruefen", mock.AsyncMock(return_value=None)), \
             mock.patch.object(werkzeug.bildsuche, "holen", holen), \
             mock.patch.object(werkzeug.bildsuche, "bild", return_value=(buf.getvalue(), "image/jpeg")), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(suche="Eiffelturm", quelle="internet", eingabe=EIN))
        self.assertIn("aus dem Netz", satz)
        m_legen.assert_called_once()

    def test_internet_ohne_treffer_sendet_nichts(self):
        async def suchen(begriff, anzahl=1):
            return []

        with mock.patch.object(werkzeug.bildsuche, "kandidaten", suchen), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(suche="Nichts", quelle="internet", eingabe=EIN))
        self.assertIn("kein passendes freies Bild", satz)
        m_legen.assert_not_called()

    def test_unbekannte_quelle_wird_abgelehnt(self):
        self.assertIn("Quelle", lauf(werkzeug.schicke_snap(suche="x", quelle="irgendwo", eingabe=EIN)))

    def test_mit_person_ohne_treffer_sagt_das(self):
        with mock.patch.object(gedaechtnis_bilder, "mit_namen", return_value=[]), \
             mock.patch.object(gedaechtnis_bilder, "suchen", return_value=[]):
            satz = lauf(werkzeug.schicke_snap(personen_name="Omi", eingabe=EIN))
        self.assertIn("kein Bild", satz)

    def test_mit_person_und_treffer_haengt_das_bild_an(self):
        with mock.patch.object(gedaechtnis_bilder, "mit_namen", return_value=[{"id": "abc123"}]), \
             mock.patch.object(gedaechtnis_bilder, "pfad", return_value=self._png_datei()), \
             mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            satz = lauf(werkzeug.schicke_snap(personen_name="Omi", text="Denk dran!", eingabe=EIN))
        self.assertIn("liegt beim Handy", satz)
        self.assertTrue(m_legen.call_args.kwargs["bild_pfad"].startswith("snapchat/ausgang/"))

    def test_nach_fremdem_text_im_selben_zug_gesperrt(self):
        konnektoren.schreiben_setzen("snapchat", True)
        with mock.patch.object(werkzeug.snapchat, "auftrag_legen") as m_legen:
            aus = lauf(katalog.ausfuehren("schicke_snap", {"suche": "x"}, fremde_inhalte=True))
        m_legen.assert_not_called()
        self.assertTrue(aus.get("fehler") or aus.get("ergebnis"))


class KatalogTest(Basis):
    def test_schreibschalter_ist_auf_der_freigaben_seite_sichtbar(self):
        # Ohne `schreibbar` zeigt die Seite keinen Schalter, und Friday sieht `schicke_snap` nie (29.09.2026).
        eintrag = [k for k in konnektoren.uebersicht() if k["id"] == "snapchat"][0]
        self.assertTrue(eintrag["schreibbar"])
        self.assertTrue(eintrag["schreibenText"])

    def test_schreibschalter_laesst_sich_ueber_die_route_setzen_und_macht_das_werkzeug_sichtbar(self):
        from fastapi.testclient import TestClient
        from kern.main import app
        namen = lambda: [e["name"] for t in katalog.gemini_tools() for e in t["functionDeclarations"]]
        self.assertNotIn("schicke_snap", namen())
        self.assertEqual(TestClient(app).post("/api/konnektoren/snapchat/schreiben", json={"aktiv": True}).status_code, 200)
        self.assertIn("schicke_snap", namen())

    def test_default_deny_ohne_konnektor(self):
        konnektoren.setzen("snapchat", False)
        namen = [e["name"] for t in katalog.gemini_tools() for e in t["functionDeclarations"]]
        self.assertNotIn("schicke_snap", namen)


class SchickeNachrichtTest(Basis):
    """07.10.2026: Eikes Auftrag ist die Freigabe — Friday formuliert selbst und sendet direkt (kein Vorlesen, kein Ja)."""

    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("snapchat", True)

    def _los(self, **angaben):
        ctx = {k: angaben.pop(k) for k in ("fremde_inhalte", "zustimmung", "eingabe") if k in angaben}
        ctx.setdefault("eingabe", "Schick Kontakt C eine Nachricht")      # Eikes Auftrag im Zug (Audit 11.10.2026)
        return lauf(katalog.ausfuehren("schicke_nachricht", angaben, **ctx))

    def test_sendet_direkt_ohne_ja(self):
        pid = register.anlegen("Kontakt C", snapchat="kontakt_c_y")["id"]
        r = self._los(person="Kontakt C", text="Eikes Stundenplan für morgen: Englisch")["ergebnis"]
        self.assertIn("Auftrag liegt beim Handy", r)
        self.assertEqual(snapchat.auftrag_lesen()["person"], pid)
        self.assertEqual(snapchat.auftrag_lesen()["text"], "Eikes Stundenplan für morgen: Englisch")

    def test_mehrdeutig_pausiert_unbekannt_kein_auftrag(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        register.anlegen("Kontakt B", snapchat="kontakt_b_x")
        self.assertIn("meintest du", self._los(person="Kontakt", text="Hi")["ergebnis"])
        p = register.anlegen("Kontakt D", snapchat="kontakt_d_x")
        register.aendern(p["id"], pausiert=True)
        self.assertIn("pausiert", self._los(person="Kontakt D", text="Hi")["ergebnis"])
        self.assertIn("kenne ich nicht", self._los(person="Unbekannt", text="Hi")["ergebnis"])
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_nach_fremdtext_nichts(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        r = self._los(person="Kontakt C", text="Hallo", fremde_inhalte=True, eingabe="Wie spät ist es?")
        self.assertIn("fremder Text", r["fehler"])
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_nach_fremdtext_mit_eikes_auftrag_und_empfaenger_geht_es(self):
        """28.10.2026: Songtext aus dem Netz an Kontakt M/Kontakt C schicken."""
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        r = self._los(person="Kontakt C", text="Zeile eins", fremde_inhalte=True, eingabe="Such den Songtext und schick ihn Kontakt C")
        self.assertIn("Auftrag liegt beim Handy", r["ergebnis"])
        self.assertEqual(snapchat.auftraege_anzahl(), 1)

    def test_nach_fremdtext_empfaenger_muss_von_eike_genannt_sein(self):
        """Der Inhalt darf aus dem Netz kommen, der Empfänger nie (Fremdtext kann Eikes Wort nicht schreiben)."""
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        r = self._los(person="Kontakt C", text="Zeile eins", fremde_inhalte=True, eingabe="Such den Songtext und schick ihn weiter")
        self.assertIn("nur an jemanden", r["ergebnis"])
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_nach_fremdtext_heikler_inhalt_bleibt_gesperrt(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        r = self._los(person="Kontakt C", text="Mein Passwort ist hunter2", trotzdem=True, fremde_inhalte=True,
                      eingabe="Such den Songtext und schick ihn Kontakt C")["ergebnis"]
        self.assertIn("Zugangsdaten", r)
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_heikler_inhalt_nur_mit_trotzdem_und_eikes_ja(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        r = self._los(person="Kontakt C", text="Mein Passwort ist hunter2")["ergebnis"]
        self.assertIn("Zugangsdaten", r)
        self.assertEqual(snapchat.auftraege_anzahl(), 0)
        # Review 07.10.2026: `trotzdem` setzt das Modell — ohne Eikes Ja in SEINER Äußerung (Code wertet es aus) gilt es nicht.
        r = self._los(person="Kontakt C", text="Mein Passwort ist hunter2", trotzdem=True)["ergebnis"]
        self.assertIn("Ja", r)
        self.assertEqual(snapchat.auftraege_anzahl(), 0)
        r = self._los(person="Kontakt C", text="Mein Passwort ist hunter2", trotzdem=True, fremde_inhalte=True)
        self.assertEqual(snapchat.auftraege_anzahl(), 0)       # Fremdtext: auch mit Ja nicht
        r = self._los(person="Kontakt C", text="Mein Passwort ist hunter2", trotzdem=True, zustimmung=True)["ergebnis"]
        self.assertIn("Auftrag liegt beim Handy", r)

    def test_aehnlicher_name_wird_nicht_geraten(self):
        """Review 07.10.2026: „Tom“ wurde als Kontakt A verstanden und die Nachricht ging sofort raus — Senden ist nicht rückholbar."""
        register.anlegen("Kontakt A", snapchat="kontakt_a_k")
        register.anlegen("Max Müller", snapchat="max_m")
        r = self._los(person="Kontakt Aa", text="Bis später")["ergebnis"]
        self.assertIn("Kontakt A", r)
        self.assertEqual(snapchat.auftraege_anzahl(), 0)
        r = self._los(person="Lea Müller", text="Bis später")["ergebnis"]
        self.assertEqual(snapchat.auftraege_anzahl(), 0)
        self._los(person="Kontakt A", text="Bis später")                  # der genaue Name geht raus
        self.assertEqual(snapchat.auftraege_anzahl(), 1)

    def test_leer_und_zu_lang(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        self.assertIn("Was soll ich schreiben", self._los(person="Kontakt C", text="  ")["ergebnis"])
        self.assertIn("zu lang", self._los(person="Kontakt C", text="x" * 400)["ergebnis"])
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_doppelter_aufruf_sendet_einmal(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        self._los(person="Kontakt C", text="Hallo")
        self.assertIn("schon geschickt", self._los(person="Kontakt C", text="Hallo")["ergebnis"])
        self.assertEqual(snapchat.auftraege_anzahl(), 1)

    def test_normale_nachricht_braucht_kein_ja(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        r = self._los(person="Kontakt C", text="Hallo")["ergebnis"]            # ohne Zustimmung
        self.assertIn("Auftrag liegt beim Handy", r)
        self.assertEqual(snapchat.auftraege_anzahl(), 1)

    def test_ohne_schreibrecht_unsichtbar(self):
        with mock.patch.object(konnektoren, "schreiben_erlaubt", return_value=False):
            namen = {f["name"] for f in katalog.gemini_tools()[0]["functionDeclarations"]}
            self.assertNotIn("schicke_nachricht", namen)
        namen = {f["name"] for f in katalog.gemini_tools()[0]["functionDeclarations"]}
        self.assertIn("schicke_nachricht", namen)
        self.assertNotIn("schreibe_person", namen)
        self.assertNotIn("person_senden", namen)

    def test_schicke_snap_raet_keinen_empfaenger(self):
        register.anlegen("Kontakt A", snapchat="kontakt_a_k")
        with mock.patch("kern.werkzeuge.snapchat._aus_dem_netz", mock.AsyncMock(return_value=(b"bild", "", "Eiffelturm"))), \
             mock.patch("kern.werkzeuge.snapchat.snapbild.vorbereiten", return_value=b"fertig"), \
             mock.patch("kern.werkzeuge.snapchat.snapbild.ablegen", return_value="snapchat/ausgang/test.png"):
            r = lauf(katalog.ausfuehren("schicke_snap", {"suche": "Eiffelturm", "quelle": "internet", "person": "Kontakt Aa"}, eingabe="Schick Kontakt Aa einen Snap"))
        self.assertIn("Kontakt A", r["ergebnis"])
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_schicke_snap_an_person_legt_auftrag_mit_person(self):
        # 04.10.2026, Eike: kein Merken mehr — angelegt + Ja genügt.
        pid = register.anlegen("Kontakt C", snapchat="kontakt_c_y")["id"]
        with mock.patch("kern.werkzeuge.snapchat._aus_dem_netz", mock.AsyncMock(return_value=(b"bild", "", "Eiffelturm"))), \
             mock.patch("kern.werkzeuge.snapchat.snapbild.vorbereiten", return_value=b"fertig"), \
             mock.patch("kern.werkzeuge.snapchat.snapbild.ablegen", return_value="snapchat/ausgang/test.png"):
            r = lauf(katalog.ausfuehren("schicke_snap", {"suche": "Eiffelturm", "quelle": "internet", "person": "Kontakt C"}, eingabe="Schick Kontakt C einen Snap"))
        self.assertIn("liegt beim Handy", r["ergebnis"])
        auftrag = snapchat.auftrag_lesen()
        self.assertEqual(auftrag["person"], pid)


class LageSatzTest(Basis):
    """Nachts und bei stillem Helfer liegt der Auftrag nur — das sagt Friday dazu (02.10.2026)."""

    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("snapchat", True)

    def test_nachts_morgens_um_sieben(self):
        with mock.patch.object(werkzeug.snapchat, "helfer_lage", return_value="nacht"):
            self.assertIn("morgens um 7", werkzeug._lage_satz())

    def test_pausiert_sagt_kurze_pause(self):
        with mock.patch.object(werkzeug.snapchat, "helfer_lage", return_value="pausiert"):
            self.assertIn("Pause", werkzeug._lage_satz())

    def test_still_dauert_noch(self):
        with mock.patch.object(werkzeug.snapchat, "helfer_lage", return_value="still"):
            self.assertIn("dauert noch", werkzeug._lage_satz())

    def test_schicke_nachricht_nachts_ohne_gesendet_luege(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        with mock.patch.object(werkzeug.snapchat, "helfer_lage", return_value="nacht"):
            fertig = lauf(katalog.ausfuehren("schicke_nachricht", {"person": "Kontakt C", "text": "Hallo"}, eingabe="Schick Kontakt C Hallo"))["ergebnis"]
        self.assertIn("morgens um 7", fertig)
        self.assertNotIn("Gesendet", fertig)


class AnfrageWerkzeugTest(Basis):
    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("snapchat", True)
        swerkzeug._anfrage_vorschlag = None

    def test_anfrage_erst_vorlesen_dann_genau_ein_auftrag_mit_ja(self):
        register.anlegen("Mara", snapchat="mara.v")
        vor = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"}))["ergebnis"]
        self.assertIn("mara.v", vor)
        self.assertEqual(snapchat.auftraege_anzahl(), 0)
        fertig = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertIn("Anfrage liegt beim Handy", fertig)
        self.assertNotIn("Gesendet", fertig)
        danach = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertNotIn("Anfrage liegt beim Handy", danach)

    def test_genauer_benutzername_sucht_nicht_noch_einmal(self):
        # 11.10.2026 (Kontakt E-Fall): „Imnot_kontakt_e“ löste jedes Mal eine neue Suche aus statt der Anfrage.
        vor = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Imnot_kontakt_e"}))["ergebnis"]
        self.assertIn("Imnot_kontakt_e", vor)
        self.assertNotIn("suche gerade", vor)
        self.assertEqual(snapchat.auftraege_anzahl(), 0)
        fertig = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertIn("Anfrage liegt beim Handy", fertig)

    def test_register_person_mit_namen_nimmt_diesen(self):
        pid = register.anlegen("Mara", snapchat="mara.v")["id"]
        vor = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"}))["ergebnis"]
        self.assertIn("mara.v", vor)
        register.aendern(pid, pausiert=True)
        self.assertIn("pausiert", lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"}))["ergebnis"].lower())

    def test_mehrdeutig_leer_und_ohne_ja_geht_nichts_raus(self):
        register.anlegen("Kontakt C", snapchat="kontakt_c_y")
        register.anlegen("Kontakt B", snapchat="kontakt_b_x")
        self.assertIn("meintest du", lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Kontakt"}))["ergebnis"])
        self.assertIn("Wen soll ich anfragen", lauf(katalog.ausfuehren("kontakt_anfragen", {}))["ergebnis"])
        self.assertIn("zustimm", lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=False))["ergebnis"].lower())
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_unbekannte_person_ohne_snapchat_name_sucht_statt_zu_fragen(self):
        """„Füge Kontakt S hinzu“: kein Register-Treffer, kein Snapchat-Name — das Handy sucht, nichts wird angefragt."""
        text = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Kontakt S"},
                                       eingabe="Füge Kontakt S hinzu"))["ergebnis"]
        self.assertIn("suche gerade", text)
        self.assertEqual(snapchat.auftraege_anzahl(), 1)
        self.assertEqual(snapchat.auftrag_lesen()["art"], "suche")

    def test_person_im_register_ohne_snapchat_name_wird_gesucht(self):
        register.anlegen("Mara Veen")
        text = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"},
                                       eingabe="Füge Mara auf Snapchat hinzu"))["ergebnis"]
        self.assertIn("suche gerade", text)
        self.assertIn("Mara Veen", text)

    def test_danach_text_wandert_vom_beauftragen_bis_in_den_vermerk(self):
        from kern.dienste import snapchat as dienst
        swerkzeug._danach_vorgemerkt = ("", 0.0)
        lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Kontakt S", "danach": "Hey, ich bin Friday."},
                                eingabe="Füge Kontakt S hinzu"))
        dienst.suchergebnis_schreiben("Kontakt S Grünewald", [{"titel": "Kontakt S", "name": "titus_g", "grund": ""}])
        lauf(katalog.ausfuehren("anfrage_auswaehlen", {"nummer": 1}))
        self.assertEqual(dienst.anfragen()[0]["danach"], "Hey, ich bin Friday.")

    def test_snapchat_kontakte_zeigt_kontakte_und_anfragen(self):
        register.anlegen("Kontakt A", snapchat="kontakt_a_kudla")
        snapchat.anfrage_legen("titus_g")
        text = lauf(katalog.ausfuehren("snapchat_kontakte", {}))["ergebnis"]
        self.assertIn("Kontakt A (@kontakt_a_kudla)", text)
        self.assertIn("titus_g: wartet auf Annahme", text)

    def test_freundschaftsstand_steht_beim_kontakt_und_nie_angenommen_ohne_beweis(self):
        from kern.dienste import snapchat as dienst
        register.anlegen("Kontakt A", snapchat="kontakt_a_kudla")
        text = lauf(katalog.ausfuehren("snapchat_kontakte", {}))["ergebnis"]
        self.assertIn("noch nicht auf dem Handy nachgesehen", text)
        dienst._schreiben("freundschaft.json", {"kontakt_a_kudla": {"anzeige": "kontakt_a_kudla", "stand": "einseitig", "zeit": 1.0}})
        text = lauf(katalog.ausfuehren("snapchat_kontakte", {}))["ergebnis"]
        self.assertIn("NOCH NICHT hinzugefügt", text)
        dienst._schreiben("freundschaft.json", {"kontakt_a_kudla": {"anzeige": "kontakt_a_kudla", "stand": "kein_hinweis", "zeit": 1.0}})
        self.assertIn("nicht bewiesen", lauf(katalog.ausfuehren("snapchat_kontakte", {}))["ergebnis"])

    def test_freundschaft_pruefen_legt_nur_einen_ansehen_auftrag(self):
        register.anlegen("Kontakt A", snapchat="kontakt_a_kudla")
        text = lauf(katalog.ausfuehren("freundschaft_pruefen", {"person": "Kontakt A"}))["ergebnis"]
        self.assertIn("schaut gleich", text)
        self.assertEqual(snapchat.auftrag_lesen()["art"], "stand")

    def test_snapchat_kontakte_ohne_anfragen_sagt_es(self):
        register.anlegen("Kontakt A", snapchat="kontakt_a_kudla")
        text = lauf(katalog.ausfuehren("snapchat_kontakte", {}))["ergebnis"]
        self.assertIn("keine vermerkt", text)

    def test_snapchat_kontakte_leer_ist_ehrlich(self):
        self.assertIn("noch niemanden", lauf(katalog.ausfuehren("snapchat_kontakte", {}))["ergebnis"])

    def test_nach_fremdtext_und_nach_verfall_keine_anfrage(self):
        register.anlegen("Mara", snapchat="mara.v")
        lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"}))
        self.assertIn("nicht mehr", lauf(katalog.ausfuehren("anfrage_senden", {}, fremde_inhalte=True, zustimmung=True))["fehler"])
        swerkzeug._anfrage_vorschlag["zeit"] -= 700
        self.assertIn("zu alt", lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"])

    def test_pausierte_person_auch_ueber_freien_namen_nicht_anfragbar(self):
        p = register.anlegen("Mara", snapchat="mara.v")
        register.aendern(p["id"], pausiert=True)
        self.assertIn("pausiert", lauf(katalog.ausfuehren("kontakt_anfragen", {"snapchat": "mara.v"}))["ergebnis"].lower())
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_doppelter_versand_meldet_schon_raus_statt_kein_vorschlag(self):
        register.anlegen("Mara", snapchat="mara.v")
        lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"}))
        self.assertIn("Anfrage liegt beim Handy", lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"])
        schon = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertIn("schon raus", schon)
        self.assertNotIn("keine vorbereitete", schon.lower())

    def test_rueckzug_im_gespraech_zieht_auftrag_und_vermerk_zurueck(self):
        register.anlegen("Mara", snapchat="mara.v")
        lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"}))
        lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))
        r = lauf(katalog.ausfuehren("anfrage_zurueckziehen", {"name": "mara.v"}))["ergebnis"]
        self.assertIn("zurückgezogen", r)
        self.assertEqual(snapchat.auftraege_anzahl(), 0)
        self.assertEqual(snapchat.anfragen(), [])

    def test_vollep_schlange_laesst_keinen_verwaisten_vermerk(self):
        register.anlegen("Mara", snapchat="mara.v")
        lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"}))
        with mock.patch.object(snapchat, "auftrag_legen", side_effect=snapchat.SnapchatFehler("Schlange voll")):
            r = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertIn("Schlange voll", r)
        self.assertEqual(snapchat.anfragen(), [])

    def test_ohne_vorschlag_meldet_gescheitert_ehrlich(self):
        snapchat.anfrage_legen("mara.v")
        snapchat.anfrage_setzen("mara.v", "gescheitert")
        swerkzeug._anfrage_vorschlag = None   # Neustart/zweites Ja: Vorschlag weg, Vermerk bleibt
        r = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertIn("gescheitert", r.lower())
        self.assertNotIn("keine vorbereitete", r.lower())

    def test_ohne_vorschlag_meldet_angenommen(self):
        snapchat.anfrage_legen("mara.v")
        snapchat.anfrage_setzen("mara.v", "angenommen")
        swerkzeug._anfrage_vorschlag = None
        r = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertIn("angenommen", r.lower())


class SucheDialogTest(Basis):
    """„Füge Kontakt S hinzu“ ohne Register-Treffer: Suche → Liste → Nummer (04.10.2026)."""

    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("snapchat", True)
        swerkzeug._anfrage_vorschlag = None

    def _treffer(self, begriff="Kontakt S"):
        return snapchat.suchergebnis_schreiben(
            begriff, [{"titel": "Kontakt S", "name": "titus_1", "grund": "IN MY CONTACTS"},
                      {"titel": "Kontakt S M.", "name": "titus_m", "grund": ""}])

    def test_freitext_ohne_register_startet_suche_statt_vorschlag(self):
        r = lauf(katalog.ausfuehren("kontakt_anfragen", {"snapchat": "Kontakt S"},
                                    eingabe="Füge Kontakt S hinzu"))["ergebnis"]
        self.assertIn("suche", r.lower())
        self.assertIsNone(swerkzeug._anfrage_vorschlag)   # kein Vorschlag, nichts geraten
        self.assertEqual(snapchat.anfragen(), [])          # kein Vermerk
        auftrag = snapchat.auftrag_lesen()
        self.assertEqual(auftrag["art"], "suche")
        self.assertEqual(auftrag["empfaenger"], "Kontakt S")

    def test_blosse_erwaehnung_startet_keine_handy_suche(self):
        """30.10.2026: Friday suchte Titus, obwohl Eike ihn nur erwähnte — der Push „Suche fertig“
        kam später ohne erkennbaren Auslöser. Der Server prüft Eikes Wort, nicht das Modell."""
        r = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Titus"},
                                    eingabe="Titus hat erzählt, dass er nächste Woche umzieht."))["ergebnis"]
        self.assertIn("nicht verlangt", r)
        self.assertIsNone(snapchat.auftrag_lesen())
        self.assertEqual(snapchat.auftraege_anzahl(), 0)

    def test_ausdrueckliche_bitte_startet_die_suche_weiterhin(self):
        for eingabe in ("Füge Titus hinzu", "Such Titus auf Snapchat", "Kannst du Titus auf Snapchat anfragen?"):
            r = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Titus"}, eingabe=eingabe))["ergebnis"]
            self.assertIn("suche gerade", r.lower(), eingabe)
            self.assertEqual(snapchat.auftrag_lesen()["art"], "suche", eingabe)
            # Platz wieder frei machen (wie nach getaner Arbeit des Helfers) für den nächsten Durchlauf
            (snapchat._ordner() / "auftrag.json").unlink(missing_ok=True)

    def test_ein_ja_auf_fridays_rueckfrage_startet_die_suche(self):
        r = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Titus"},
                                    eingabe="ja, mach das", zustimmung=True))["ergebnis"]
        self.assertIn("suche gerade", r.lower())
        self.assertEqual(snapchat.auftrag_lesen()["art"], "suche")

    def test_zweite_suche_desselben_namens_legt_keine_neue_an(self):
        """Mehrere Aufrufe für denselben Namen erzeugten mehrere Suchen und damit mehrere
        „Suche fertig“-Pushes (30.10.2026)."""
        lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Titus"}, eingabe="Füge Titus hinzu"))
        erster = snapchat.auftrag_lesen()
        r = lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Titus"}, eingabe="Füge Titus hinzu"))["ergebnis"]
        self.assertIn("suche schon", r.lower())
        self.assertEqual(snapchat.auftraege_anzahl(), 1)
        self.assertEqual(snapchat.auftrag_lesen()["id"], erster["id"])

    def test_trefferliste_ist_nummeriert(self):
        self._treffer()
        r = lauf(katalog.ausfuehren("anfrage_treffer", {"suchbegriff": "Kontakt S"}))["ergebnis"]
        self.assertIn("1.", r)
        self.assertIn("titus_1", r)
        self.assertIn("2.", r)

    def test_nummer_waehlt_genau_diesen_und_schickt_raus(self):
        self._treffer()
        r = lauf(katalog.ausfuehren("anfrage_auswaehlen", {"nummer": 2}))["ergebnis"]
        self.assertIn("titus_m", r)
        self.assertIn("Handy", r)
        self.assertEqual(snapchat.anfragen()[0]["snapchat"], "titus_m")

    def test_unbekannte_nummer_fragt_nochmal(self):
        self._treffer()
        r = lauf(katalog.ausfuehren("anfrage_auswaehlen", {"nummer": 9}))["ergebnis"]
        self.assertIn("1 bis 2", r)
        self.assertEqual(snapchat.anfragen(), [])

    def test_verfallene_suche_schickt_nichts(self):
        self._treffer()
        alle = snapchat._lesen("suchergebnisse.json", {})
        alle[snapchat.suche_schluessel("Kontakt S")]["zeit"] -= snapchat.SUCH_GILT_S + 10
        snapchat._schreiben("suchergebnisse.json", alle)
        r = lauf(katalog.ausfuehren("anfrage_auswaehlen", {"nummer": 1}))["ergebnis"]
        self.assertIn("zu alt", r.lower())
        self.assertEqual(snapchat.anfragen(), [])

    def test_nach_fremdtext_kein_auswaehlen(self):
        self._treffer()
        aus = lauf(katalog.ausfuehren("anfrage_auswaehlen", {"nummer": 1}, fremde_inhalte=True))
        self.assertIn("nicht mehr", aus.get("fehler", ""))
        self.assertEqual(snapchat.anfragen(), [])

    def test_leere_suche_meldet_das_ehrlich(self):
        snapchat.suchergebnis_schreiben("Niemand", [], ohne_knopf=2)
        r = lauf(katalog.ausfuehren("anfrage_treffer", {"suchbegriff": "Niemand"}))["ergebnis"]
        self.assertIn("Hinzufügen-Knopf", r)

    def test_handy_grund_wird_uebersetzt_kein_schon_drin(self):
        # Gespräch 446: „IN MY CONTACTS“ hieß plötzlich „bereits in Kontakten“.
        snapchat.suchergebnis_schreiben("tim_kudla", [{"titel": "Kontakt A", "name": "tim_kudla",
                                                       "grund": "IN MY CONTACTS"}])
        r = lauf(katalog.ausfuehren("anfrage_treffer", {"suchbegriff": "tim_kudla"}))["ergebnis"]
        self.assertIn("Handy-Kontakten", r)
        self.assertNotIn("IN MY CONTACTS", r)

    def test_ja_zum_einzigen_treffer_ist_nummer_eins(self):
        mittel = [e for m in katalog._MODULE for e in m.ERKLAERUNGEN if e["name"] == "anfrage_auswaehlen"][0]
        self.assertIn("Nummer 1", mittel["description"])

    def test_stand_meldet_zustand_und_grund_statt_raten(self):
        snapchat.anfrage_legen("tim_kudla")
        r = lauf(katalog.ausfuehren("anfrage_stand", {"name": "tim_kudla"}))["ergebnis"]
        self.assertIn("wartet auf Annahme", r)
        self.assertIn("tim_kudla", r)
        snapchat.anfrage_setzen("tim_kudla", "gescheitert")
        r = lauf(katalog.ausfuehren("anfrage_stand", {"name": "tim_kudla"}))["ergebnis"]
        self.assertIn("gescheitert", r)
        self.assertIn("keine Anfrage", lauf(katalog.ausfuehren(
            "anfrage_stand", {"name": "unbekannt_xyz"}))["ergebnis"])
        snapchat.anfrage_zurueckziehen("tim_kudla")
        self.assertIn("keine Anfrage", lauf(katalog.ausfuehren("anfrage_stand", {}))["ergebnis"])

    def test_freier_name_ohne_doppelname_im_ergebnis(self):
        # Gespräch 439: „tim_kudla an tim_kudla“ hielt das Modell für Fridays Konto.
        register.anlegen("Mara", snapchat="mara.v")
        lauf(katalog.ausfuehren("kontakt_anfragen", {"person": "Mara"}))
        r = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertIn("mara.v an Mara", r)
        snapchat.anfrage_zurueckziehen("mara.v")
        swerkzeug._anfrage_vorschlag = {"name": "tim_kudla", "snapchat": "tim_kudla",
                                        "pid": None, "zeit": swerkzeug.uhr.monotonic()}
        r = lauf(katalog.ausfuehren("anfrage_senden", {}, zustimmung=True))["ergebnis"]
        self.assertIn("Anfrage an tim_kudla", r)
        self.assertNotIn("tim_kudla an tim_kudla", r)


class AnfrageAnnehmenTest(Basis):
    def setUp(self):
        super().setUp()
        konnektoren.schreiben_setzen("snapchat", True)

    def test_legt_annehmen_auftrag_und_meldet_ergebnis_des_helfers(self):
        orig = swerkzeug.ANNEHMEN_WARTE_S
        swerkzeug.ANNEHMEN_WARTE_S = 10
        async def ohne_warten(_s):
            pass
        try:
            with mock.patch.object(swerkzeug.asyncio, "sleep", ohne_warten):
                with mock.patch.object(snapchat, "ergebnis_fuer", return_value={"ergebnis": "gesendet"}):
                    ergebnis = lauf(katalog.ausfuehren("anfrage_annehmen", {"name": "Kontakt E"}, eingabe="nimm Kontakt E an"))["ergebnis"]
        finally:
            swerkzeug.ANNEHMEN_WARTE_S = orig
        self.assertIn("Angenommen", ergebnis)
        auftrag = snapchat.auftrag_lesen()
        self.assertEqual((auftrag["art"], auftrag["empfaenger"]), ("annehmen", "Kontakt E"))

    def test_frage_nach_anfrage_nimmt_nicht_an(self):
        """18.10.2026: „hat Tjard dir eine Anfrage geschickt?“ ist keine Bitte, ihn anzunehmen."""
        for frage in ("hat Tjard dir eine Anfrage geschickt auf snap?", "doch", "", "nimm ihn nicht an"):
            with self.subTest(frage=frage):
                with mock.patch.object(snapchat, "auftrag_legen") as legen:
                    ergebnis = lauf(katalog.ausfuehren("anfrage_annehmen", {"name": "Tjard"}, eingabe=frage))["ergebnis"]
                legen.assert_not_called()
                self.assertIn("Nichts angenommen", ergebnis)

    def test_verworfen_wird_ehrlich_weitergegeben(self):
        async def ohne_warten(_s):
            pass
        with mock.patch.object(swerkzeug.asyncio, "sleep", ohne_warten):
            with mock.patch.object(snapchat, "ergebnis_fuer",
                                   return_value={"ergebnis": "verworfen", "grund": "„X“ steht nicht unter „Added Me“."}):
                ergebnis = lauf(katalog.ausfuehren("anfrage_annehmen", {"name": "X"}, eingabe="nimm X an"))["ergebnis"]
        self.assertIn("Nicht angenommen", ergebnis)
        self.assertNotIn("Angenommen", ergebnis)
