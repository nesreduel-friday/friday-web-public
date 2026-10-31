"""Kontakte auf dem UI-Baum erkennen — an echten, anonymisierten Geräte-Dumps (29.09.2026)."""
import sys
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host-helfer"))
import snap_helfer  # noqa: E402

DATEN = Path(__file__).resolve().parent / "daten"


_sperre = None
_daten_env = None
_zufall_aus = None


def setUpModule():
    """Kein Test dieses Moduls darf das echte Handy (adb) anfassen. Der Tipp-Zufall ist aus (07.10.2026): die Tests prüfen exakte Mitten."""
    global _sperre, _zufall_aus, _daten_env
    _sperre = mock.patch.object(snap_helfer.subprocess, "run",
                                side_effect=AssertionError("Ein Test greift auf das echte Handy (adb) durch"))
    _sperre.start()
    _zufall_aus = mock.patch.object(snap_helfer, "ZUFALL_MAX_PX", 0)
    _zufall_aus.start()
    import os, tempfile
    _daten_env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})   # nie die echten Live-Daten (07.10.2026)
    _daten_env.start()


def tearDownModule():
    _sperre.stop()
    _zufall_aus.stop()
    _daten_env.stop()


def _patche(testfall, ziel, name, **kw):
    """Ein Patch, der nur für diesen Test gilt (`mock.patch.stopall` würde auch den Guard oben abschalten)."""
    p = mock.patch.object(ziel, name, **kw)
    m = p.start()
    testfall.addCleanup(p.stop)
    return m


def dump(name: str) -> str:
    return (DATEN / name).read_text(encoding="utf-8")


LISTE = dump("snap_chatliste.xml")
CHAT = dump("snap_chatbild.xml")
SENDTO = dump("snap_sendto.xml")
SENDTO_RASTER = dump("snap_sendto_raster.xml")      # Best Friends in zwei Spalten (Snapchat, 10.10.2026, am Gerät gespeichert)
AUSWAHL = dump("snap_sendto_auswahl.xml")


def mit_snap(anzeige="Eike"):
    """Die echte Chatliste, aber die Zeile von `anzeige` meldet einen ungeöffneten Snap (so sieht die Zeile bei einem Snap aus).
    Per XML-Bearbeitung statt Textersatz: Das XML kodiert Emojis als Zeichencodes, ein Ersatz auf den Klartext griffe ins Leere
    (und ein Test darauf wäre vakuum-grün)."""
    wurzel = ET.fromstring(LISTE)
    for zeile in wurzel.iter("node"):
        if not (zeile.get("resource-id") or "").endswith("ff_item"):
            continue
        namen = [k.get("text") for k in zeile.iter("node") if k.get("text")]
        if anzeige not in namen:
            continue
        alt = zeile.get("content-desc") or ""
        zeile.set("content-desc", "New Snap")
        for k in zeile.iter("node"):
            if k.get("text") and alt and k.get("text").startswith(alt.split()[0]):
                k.set("text", "New Snap  ·  40m")
    return ET.tostring(wurzel, encoding="unicode")


class Namen(unittest.TestCase):
    def test_norm_vergleicht_ohne_unsichtbares_gross_klein_und_vollbreite(self):
        for a, b in (("Eike", "eike"), ("E​ike", "Eike"), ("Ｅｉｋｅ", "Eike"), (" Test  ", "test"),
                     ("Ma­ya", "maya")):
            self.assertEqual(snap_helfer._norm(a), snap_helfer._norm(b), (a, b))
        self.assertNotEqual(snap_helfer._norm("Test"), snap_helfer._norm("Test 2"))
        self.assertNotEqual(snap_helfer._norm("Eike"), snap_helfer._norm("Eike2"))

    def test_norm_ist_dieselbe_regel_wie_register_schluessel(self):
        from kern.personen import register
        for name in ("Eike", "E​ike", "Ｅｉｋｅ", "  Ma  ya ", "Ma­ya️", "MAYA_X", "Test 🎉", "ß", ""):
            self.assertEqual(snap_helfer._norm(name), register.schluessel(name), repr(name))

    def test_eike_name_aus_der_umgebung(self):
        import os
        import tempfile
        with unittest.mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}):     # keine echte kontakte.json
            self.assertEqual(snap_helfer.eike_name(), "Eike")
            with unittest.mock.patch.dict(os.environ, {"SNAPCHAT_EIKE_NAME": " Eike D "}):
                self.assertEqual(snap_helfer.eike_name(), "Eike D")


class Chatliste(unittest.TestCase):
    def test_zeilen_mit_namen_status_und_mitte(self):
        z = {x["anzeige"]: x for x in snap_helfer.chat_zeilen(LISTE)}
        self.assertEqual(list(z), ["My AI", "Eike", "Test", "Team Snapchat"])     # „Find friends“ ist keine Zeile
        self.assertEqual(z["Eike"]["status"], "New Chat")
        self.assertEqual(z["Test"]["status"], "You are now friends 🎉")
        self.assertEqual(z["Eike"]["mitte"], (540, 641))

    def test_chat_zeile_findet_nur_den_exakten_kontakt(self):
        self.assertEqual(snap_helfer.chat_zeile(LISTE, "eike")["anzeige"], "Eike")
        self.assertIsNone(snap_helfer.chat_zeile(LISTE, "Eik"))
        self.assertIsNone(snap_helfer.chat_zeile(LISTE, "Person A"))     # steht nur unter „Find friends“
        self.assertIsNone(snap_helfer.chat_zeile("<kaputt", "Eike"))
        self.assertIsNone(snap_helfer.chat_zeile(LISTE, ""))

    def test_neuer_snap_nur_in_der_zeile_des_kontakts(self):
        snap = mit_snap()
        self.assertIsNotNone(snap_helfer.neuer_snap_knoten(snap, "Eike"))
        self.assertIsNone(snap_helfer.neuer_snap_knoten(snap, "Test"))        # Eikes Snap gehört nicht Test
        self.assertIsNone(snap_helfer.neuer_snap_knoten(LISTE, "Eike"))       # „New Chat“ ist kein Snap
        # ohne Kontakt gilt weiter der alte Weg (jeder Text „New Snap“)
        self.assertEqual(snap_helfer.neuer_snap_knoten(
            '<hierarchy><node text="New Snap" bounds="[100,600][300,700]"/></hierarchy>'), (200, 650))


class ChatKopf(unittest.TestCase):
    def test_kopfname_des_offenen_chats(self):
        self.assertEqual(snap_helfer.chat_kopfname(CHAT), "Eike")

    def test_kein_chat_kein_kopfname(self):
        self.assertIsNone(snap_helfer.chat_kopfname(LISTE))
        self.assertIsNone(snap_helfer.chat_kopfname("<kaputt"))


class SendTo(unittest.TestCase):
    def test_kreis_fuer_eike_und_test(self):
        self.assertEqual(snap_helfer.sendto_zeile(SENDTO, "Eike"), (967, 1709))     # zweizeilige Zeile: Mitte von Titel+Untertitel
        self.assertEqual(snap_helfer.sendto_zeile(SENDTO, "test"), (967, 2162))     # einzeilig
        self.assertIsNone(snap_helfer.sendto_zeile(SENDTO, "Niemand"))

    def test_der_untertitel_unter_last_snap_ist_keine_auswahlzeile(self):
        # „Last Snap / Eike“ steht unter „Best Friends / Eike“: Es darf nur die Zeile mit dem Titel „Eike“ gewählt werden.
        ohne_best = SENDTO.replace('text="Eike"', 'text="Anders"', 1)     # die erste „Eike“-Zeile (Best Friends) verschwindet
        self.assertIsNone(snap_helfer.sendto_zeile(ohne_best, "Eike"))

    def test_zwei_spalten_raster_tippt_den_kreis_der_eigenen_spalte(self):
        # Am Gerät: „Eike“ links, „Test“ rechts. Ein Tipp bei 0,895 × Breite (967) traf die rechte Spalte und wählte „Test“.
        (x_eike, y_eike), (x_test, y_test) = snap_helfer.sendto_zeile(SENDTO_RASTER, "Eike"), snap_helfer.sendto_zeile(SENDTO_RASTER, "Test")
        self.assertEqual((x_eike, y_eike), (485, 1338))       # Kreis in Eikes Zelle (Mitte der linken Spalte, rechts vom Namen)
        self.assertEqual((x_test, y_test), (988, 1338))
        self.assertLess(x_eike, 540)                          # nie in der rechten Spalte
        self.assertEqual(snap_helfer.sendto_zeile(SENDTO_RASTER, "Kontakt O")[0], 967)    # Listenzeile über die ganze Breite wie bisher

    def test_teilnamen_und_aehnliche_namen_treffen_nicht(self):
        for name in ("Tes", "Test 2", "Eike2", "ei"):
            self.assertIsNone(snap_helfer.sendto_zeile(SENDTO, name), name)

    def test_die_auswahlleiste_nennt_die_gewaehlten_empfaenger(self):
        self.assertEqual(snap_helfer.sendto_ausgewaehlt(AUSWAHL), ["Test"])
        # Neue Leiste (09.10.2026): Zähler und „Add a Chat …“ sind keine Empfänger; ein zweiter Name bleibt einer.
        leiste = ('<hierarchy><node resource-id="com.snap.android:id/send_to_confirmation_bar">'
                  '<node text="2"/><node text="Add a Chat ..."/><node text="Kontakt M"/></node></hierarchy>')
        self.assertEqual(snap_helfer.sendto_ausgewaehlt(leiste), ["Kontakt M"])
        zwei = leiste.replace('<node text="2"/>', '<node text="Kontakt A"/>')
        self.assertEqual(snap_helfer.sendto_ausgewaehlt(zwei), ["Kontakt A", "Kontakt M"])
        self.assertEqual(snap_helfer.sendto_ausgewaehlt(SENDTO), [])          # noch nichts gewählt
        self.assertEqual(snap_helfer.sendto_ausgewaehlt("<kaputt"), [])



class Abloeufe(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        self.tippen = _patche(self, snap_helfer, "tippen")
        _patche(self, snap_helfer, "_pause")
        _patche(self, snap_helfer.time, "sleep")

    def tearDown(self):
        self.env.stop()

    def _dumps(self, *folge):
        return mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=list(folge))

    def test_chat_oeffnen_tippt_die_zeile_und_prueft_den_kopf(self):
        with self._dumps(LISTE, CHAT):
            snap_helfer.kontakt_chat_oeffnen("Eike")
        self.tippen.assert_called_once_with(540, 641)

    def test_falscher_kopf_bricht_ab_bevor_getippt_oder_gesendet_wird(self):
        falsch = CHAT.replace(">Eike<", ">Test<").replace('text="Eike"', 'text="Test"')
        with self._dumps(LISTE, falsch):
            with self.assertRaises(snap_helfer.SnapHelferFehler) as f:
                snap_helfer.kontakt_chat_oeffnen("Eike")
        self.assertIn("Falscher Chat", str(f.exception))

    def test_chat_ohne_zeile_und_ungeoeffneter_snap_kein_tipp(self):
        with self._dumps(LISTE):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.kontakt_chat_oeffnen("Person A")            # steht nur unter „Find friends“
        with self._dumps(mit_snap()):
            with self.assertRaises(snap_helfer.SnapHelferFehler) as f:
                snap_helfer.kontakt_chat_oeffnen("Eike")
        self.assertIn("ungeöffneter Snap", str(f.exception))
        self.tippen.assert_not_called()                                 # ein Tipp hätte den Snap verbraucht

    def test_sendto_waehlt_und_prueft_die_leiste(self):
        with self._dumps(SENDTO, AUSWAHL):
            snap_helfer.sendto_waehlen("Test")
        self.tippen.assert_called_once_with(967, 2162)

    def test_sendto_falscher_empfaenger_in_der_leiste_bricht_ab(self):
        falsch = AUSWAHL.replace('text="Test"', 'text="Eike"')
        with self._dumps(SENDTO, falsch):
            with self.assertRaises(snap_helfer.SnapHelferFehler) as f:
                snap_helfer.sendto_waehlen("Test")
        self.assertIn("Falscher Empfänger", str(f.exception))

    def test_sendto_mehrere_gewaehlte_sind_ein_fehler(self):
        zwei = AUSWAHL.replace('text="Test"', 'text="Test, Eike"')
        with self._dumps(SENDTO, zwei):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.sendto_waehlen("Test")

    def test_sendto_kontakt_nicht_in_der_liste(self):
        with self._dumps(SENDTO, SENDTO, SENDTO, SENDTO, SENDTO, SENDTO), mock.patch.object(snap_helfer, "_adb"), \
             mock.patch.object(snap_helfer, "bildschirmgroesse", return_value=(1080, 2400)):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.sendto_waehlen("Niemand")
        self.tippen.assert_not_called()


class Empfaenger(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env.stop()

    def test_ohne_kontaktliste_darf_nur_an_eike_gesendet_werden(self):
        self.assertEqual(snap_helfer.erlaubte_empfaenger(), {"eike"})

    def test_mit_kontaktliste_gilt_sie_normalisiert(self):
        snap_helfer._schreiben("kontakte.json", {"kontakte": [{"anzeige": "Eike", "person": None},
                                                              {"anzeige": "Kontakt B_X", "person": "p1"}]})
        self.assertEqual(snap_helfer.erlaubte_empfaenger(), {"eike", "kontakt b_x"})

    def test_kaputte_kontaktliste_faellt_auf_eike_zurueck(self):
        (snap_helfer.daten_ordner() / "snapchat" / "kontakte.json").write_text("{kaputt", encoding="utf-8")
        self.assertEqual(snap_helfer.erlaubte_empfaenger(), {"eike"})

    def _einmal(self, auftrag):
        snap_helfer._schreiben("auftrag.json", auftrag)
        gesendet = []
        with mock.patch.object(snap_helfer, "_konnektor_aktiv", return_value=True), \
             mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=lambda b, t, e=None: gesendet.append((t, e))), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        return gesendet, snap_helfer._lesen("zustand.json", {})

    def test_auftrag_ohne_empfaenger_wird_verworfen_nicht_an_eike_umgeleitet(self):
        gesendet, zustand = self._einmal({"art": "text", "text": "Hi", "bild_pfad": None})
        self.assertEqual(gesendet, [])
        self.assertIn("Empfänger", zustand["letzter_fehler"])
        self.assertIsNone(snap_helfer._lesen("auftrag.json", None))       # Platz frei, nichts blockiert

    def test_auftrag_an_unbekannten_empfaenger_wird_verworfen(self):
        snap_helfer._schreiben("kontakte.json", {"kontakte": [{"anzeige": "Eike", "person": None}]})
        gesendet, zustand = self._einmal({"art": "text", "text": "Hi", "bild_pfad": None, "empfaenger": "Fremder"})
        self.assertEqual(gesendet, [])
        self.assertIn("Kontaktliste", zustand["letzter_fehler"])

    def test_auftrag_an_einen_kontakt_der_liste_wird_mit_empfaenger_gesendet(self):
        snap_helfer._schreiben("kontakte.json", {"kontakte": [{"anzeige": "Eike", "person": None},
                                                              {"anzeige": "Test", "person": "p2"}]})
        gesendet, zustand = self._einmal({"art": "text", "text": "Hallo", "bild_pfad": None, "empfaenger": "Test",
                                          "person": "p2"})
        self.assertEqual(gesendet, [("Hallo", "Test")])
        eigene = zustand.get("eigene_texte", [])
        self.assertNotIn("Hallo", eigene)          # Eikes „eigene Texte“ bleiben von Sendungen an andere unberührt

    def test_sendung_an_eike_bleibt_wie_bisher_und_merkt_den_text(self):
        gesendet, zustand = self._einmal({"art": "text", "text": "Hi Eike", "bild_pfad": None, "empfaenger": "Eike"})
        self.assertEqual(gesendet, [("Hi Eike", "Eike")])
        self.assertIn("Hi Eike", zustand["eigene_texte"])


class SnapsAbholen(unittest.TestCase):
    def test_ein_snap_von_test_wird_nie_als_eikes_geoeffnet(self):
        nur_test = mit_snap("Test")                                       # Testkontakt hat einen Snap, Eike keinen
        self.assertIsNotNone(snap_helfer.neuer_snap_knoten(nur_test, "Test"))   # die Zeile trägt wirklich einen Snap (kein vakuum-grüner Test)
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value=nur_test), \
             mock.patch.object(snap_helfer, "tippen") as tippen, mock.patch.object(snap_helfer, "_pause"):
            self.assertEqual(snap_helfer.snaps_abholen(), 0)
        tippen.assert_not_called()

    def test_eikes_snap_wird_wie_bisher_geoeffnet(self):
        betrachter = ('<hierarchy><node resource-id="com.snapchat.android:id/opera_viewer" bounds="[0,0][1,1]"/>'
                      '<node resource-id="com.snapchat.android:id/base_image_layer_container" bounds="[0,1][4,3]"/></hierarchy>')
        dumps = [mit_snap(), betrachter, betrachter, LISTE]
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=dumps + [LISTE] * 6), \
             mock.patch.object(snap_helfer, "tippen") as tippen, mock.patch.object(snap_helfer, "_pause"), \
             mock.patch.object(snap_helfer, "bildschirmgroesse", return_value=(1080, 2400)), \
             mock.patch.object(snap_helfer, "snap_bild_holen", return_value="a" * 32 + ".png"), \
             mock.patch.object(snap_helfer, "_auf_snap_warten", return_value=True), \
             mock.patch.object(snap_helfer, "_eingang_anhaengen") as anhaengen:
            self.assertGreaterEqual(snap_helfer.snaps_abholen(), 1)
        self.assertTrue(tippen.called)
        self.assertEqual(anhaengen.call_args.args[0][0]["art"], "snap")


import struct  # noqa: E402
import zlib  # noqa: E402


def _png(breite, hoehe, pixel):
    """Kleines RGB-PNG ohne Filter; pixel(x, y) -> (r, g, b)."""
    roh = b""
    for y in range(hoehe):
        roh += b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(breite))

    def block(typ, daten):
        return struct.pack(">I", len(daten)) + typ + daten + struct.pack(">I", zlib.crc32(typ + daten) & 0xffffffff)

    return (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0))
            + block(b"IDAT", zlib.compress(roh)) + block(b"IEND", b""))


B, H = 108, 240           # ein Zehntel der echten 1080×2400: gleiche Anteile, kleines Bild
KAMERA = _png(B, H, lambda x, y: (0, 0, 0))
EDITOR = _png(B, H, lambda x, y: (255, 252, 0) if (x >= 70 and y >= 218) else (0, 0, 0))   # gelber Knopf unten rechts


class EntwurfVerwerfen(unittest.TestCase):
    def setUp(self):
        self.tippen = _patche(self, snap_helfer, "tippen")
        _patche(self, snap_helfer, "_pause")
        _patche(self, snap_helfer, "bildschirmgroesse", return_value=(1080, 2400))
        _patche(self, snap_helfer, "bildschirm_dump", return_value=LISTE)

    def test_editor_wird_ueber_x_und_abandon_verworfen(self):
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", side_effect=[EDITOR, KAMERA]):
            snap_helfer._entwurf_aufraeumen()
        self.assertEqual([c.args for c in self.tippen.call_args_list], [(68, 161), (540, 1296)])

    def test_abandon_und_nie_save_oder_cancel(self):
        x, y = snap_helfer.BLINDE_KOORDINATEN["abandon_knopf"]
        self.assertAlmostEqual(x, 0.5)
        self.assertTrue(0.52 <= y <= 0.56)          # „Save & Close“ liegt bei ≈ 0,47, „Cancel“ bei ≈ 0,61

    def test_ohne_entwurf_tut_es_nichts(self):
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=KAMERA):
            snap_helfer._entwurf_aufraeumen()
        self.tippen.assert_not_called()

    def test_bleibt_der_editor_nach_abandon_offen_gibt_es_einen_fehler(self):
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", side_effect=[EDITOR, EDITOR]):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer._entwurf_aufraeumen()

    def test_das_bisherige_verwerfen_vor_preview_bleibt(self):
        vor = '<hierarchy><node resource-id="com.snapchat.android:id/cancel_button" bounds="[100,1000][300,1100]"/></hierarchy>'
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value=vor), \
             mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=KAMERA):
            snap_helfer._entwurf_aufraeumen()
        self.tippen.assert_called_once_with(200, 1050 - 159)


if __name__ == "__main__":
    unittest.main()
