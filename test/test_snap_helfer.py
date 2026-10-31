import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host-helfer"))
import snap_helfer  # noqa: E402


_sperre = None
_daten_env = None
_zufall_aus = None
_WARTEN_ECHT = snap_helfer._auf_oberflaeche_warten    # die echte Wartefunktion, getestet wird sie gezielt damit
_warten_aus = None


def setUpModule():
    """Kein Test darf das echte Handy (adb) anfassen: Zwei Tests taten es ungemockt (`einmal()` → `gespraech_halten` → echte Dumps
    und `svc power stayon`), bis ein Review am 30.09.2026 es bemerkte. Tests, die `subprocess.run` brauchen, patchen es selbst.
    Der Tipp-Zufall ist hier aus (07.10.2026): die alten Tests prüfen exakte Koordinaten; `ZufallTest` schaltet ihn selbst ein."""
    global _sperre, _zufall_aus, _daten_env, _warten_aus
    _warten_aus = mock.patch.object(snap_helfer, "_auf_oberflaeche_warten")   # Tests warten nie echte 30 s auf Snapchat
    _warten_aus.start()
    _sperre = mock.patch.object(snap_helfer.subprocess, "run",
                                side_effect=AssertionError("Ein Test greift auf das echte Handy (adb) durch"))
    _sperre.start()
    _zufall_aus = mock.patch.object(snap_helfer, "ZUFALL_MAX_PX", 0, create=True)
    _zufall_aus.start()
    import os, tempfile
    _daten_env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})   # nie die echten Live-Daten (07.10.2026)
    _daten_env.start()


def tearDownModule():
    _sperre.stop()
    _zufall_aus.stop()
    _daten_env.stop()
    _warten_aus.stop()

from test.test_snap_kontakte import mit_snap  # noqa: E402  (die echte Chatliste mit einem Snap in Eikes Zeile)


DUMP_KAMERA = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" resource-id="com.snapchat.android:id/ngs_memories_icon"
        bounds="[100,200][300,400]" />
  <node text="Director Mode" resource-id="" bounds="[50,50][150,90]" />
</hierarchy>"""

# Kamera-Modus-Karussell: mehrere gleiche resource-ids ohne eigenen Text — index=-1 muss das
# letzte/unterste ("More") treffen, nicht das erste.
DUMP_KAMERA_MODUS = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" resource-id="com.snapchat.android:id/camera_mode_icon_image_view"
        bounds="[962,119][1046,203]" />
  <node text="" resource-id="com.snapchat.android:id/camera_mode_icon_image_view"
        bounds="[962,235][1046,319]" />
  <node text="" resource-id="com.snapchat.android:id/camera_mode_icon_image_view"
        bounds="[962,580][1046,664]" />
</hierarchy>"""

# Echte Struktur am Gerät bestätigt (28.09.2026): Nachrichtentexte haben keine resource-id,
# aber direkt davor steht ein GROSSGESCHRIEBENES Absender-Label ("ME" bzw. der Anzeigename).
DUMP_CHAT = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="EIKE" resource-id="" bounds="[0,0][50,20]" />
  <node text="Na du!" resource-id="" bounds="[0,20][200,40]" />
  <node text="ME" resource-id="" bounds="[0,40][50,60]" />
  <node text="Klar, bin dabei!" resource-id="" bounds="[0,60][200,80]" />
  <node text="EIKE" resource-id="" bounds="[0,80][50,100]" />
  <node text="Wann genau?" resource-id="" bounds="[0,100][200,120]" />
</hierarchy>"""

# Untere Navigation mit Chat-Tab-Symbol — live gesehen (28.09.2026): eine Zahl direkt daneben
# ist das Ungelesen-Badge, unabhängig davon, auf welchem Bildschirm die Navigation sichtbar ist.
DUMP_NAV_MIT_BADGE = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" resource-id="com.snapchat.android:id/ngs_chat_icon_container"
        bounds="[241,2171][440,2356]" />
  <node text="1" resource-id="" bounds="[357,2214][376,2254]" />
</hierarchy>"""

DUMP_NAV_OHNE_BADGE = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" resource-id="com.snapchat.android:id/ngs_chat_icon_container"
        bounds="[241,2171][440,2356]" />
</hierarchy>"""


_CHAT_MIT_HI = '<hierarchy><node text="Hi" bounds="[0,0][1,1]"/></hierarchy>'


class BoundsTest(unittest.TestCase):
    def test_mitte_wird_berechnet(self):
        self.assertEqual(snap_helfer.bounds_mitte("[100,200][300,400]"), (200, 300))

    def test_ungueltige_bounds_geben_none(self):
        self.assertIsNone(snap_helfer.bounds_mitte(""))
        self.assertIsNone(snap_helfer.bounds_mitte("kaputt"))


class ElementFindenTest(unittest.TestCase):
    def test_findet_ueber_resource_id(self):
        mitte = snap_helfer.element_finden(
            DUMP_KAMERA, resource_id="com.snapchat.android:id/ngs_memories_icon")
        self.assertEqual(mitte, (200, 300))

    def test_findet_ueber_text(self):
        mitte = snap_helfer.element_finden(DUMP_KAMERA, text="Director Mode")
        self.assertEqual(mitte, (100, 70))

    def test_nicht_gefunden_gibt_none(self):
        self.assertIsNone(snap_helfer.element_finden(DUMP_KAMERA, text="Gibt es nicht"))

    def test_kaputtes_xml_gibt_none_statt_zu_werfen(self):
        self.assertIsNone(snap_helfer.element_finden("<kaputt", text="egal"))

    def test_index_minus_eins_nimmt_das_letzte_von_mehreren_treffern(self):
        mitte = snap_helfer.element_finden(
            DUMP_KAMERA_MODUS, resource_id="com.snapchat.android:id/camera_mode_icon_image_view",
            index=-1)
        self.assertEqual(mitte, (1004, 622))  # unterstes der drei Symbole ("More")

    def test_index_ausserhalb_gibt_none(self):
        self.assertIsNone(snap_helfer.element_finden(
            DUMP_KAMERA_MODUS, resource_id="com.snapchat.android:id/camera_mode_icon_image_view",
            index=99))

    def test_elemente_finden_gibt_alle_treffer_in_dokumentreihenfolge(self):
        treffer = snap_helfer.elemente_finden(
            DUMP_KAMERA_MODUS, resource_id="com.snapchat.android:id/camera_mode_icon_image_view")
        self.assertEqual(treffer, [(1004, 161), (1004, 277), (1004, 622)])


class TextEscapenTest(unittest.TestCase):
    def test_leerzeichen_werden_zu_prozent_s(self):
        self.assertEqual(snap_helfer.adb_text_escapen("Na du"), "Na%sdu")

    def test_sonderzeichen_werden_entfernt(self):
        self.assertNotIn("!", snap_helfer.adb_text_escapen("Hallo!"))

    def test_umlaute_werden_ersetzt_statt_getippt(self):
        # `adb shell input text` wirft bei Umlauten eine NullPointerException (29.09.2026).
        self.assertEqual(snap_helfer.adb_text_escapen("Klär das für Größe"), "Klaer%sdas%sfuer%sGroesse")
        self.assertEqual(snap_helfer.adb_text_escapen("café"), "caf")

    def test_zeilenumbruch_wird_zu_leerzeichen(self):
        self.assertEqual(snap_helfer.adb_text_escapen("Zeile1\nZeile2"), "Zeile1%sZeile2")

    def test_tab_wird_zu_leerzeichen(self):
        self.assertEqual(snap_helfer.adb_text_escapen("A\tB"), "A%sB")

    def test_mehrfache_leerzeichen_werden_zu_einem(self):
        self.assertEqual(snap_helfer.adb_text_escapen("A    B"), "A%sB")


class NeueNachrichtenTest(unittest.TestCase):
    def test_ohne_zuletzt_gesehen_kommt_alles_von_der_gegenseite(self):
        self.assertEqual(snap_helfer.neue_nachrichten(DUMP_CHAT, ""), ["Na du!", "Wann genau?"])

    def test_eigene_nachrichten_werden_nicht_mitgezaehlt(self):
        self.assertNotIn("Klar, bin dabei!", snap_helfer.neue_nachrichten(DUMP_CHAT, ""))

    def test_nur_das_was_danach_kam(self):
        self.assertEqual(snap_helfer.neue_nachrichten(DUMP_CHAT, "Na du!"), ["Wann genau?"])

    def test_alles_schon_gesehen_gibt_nichts(self):
        self.assertEqual(snap_helfer.neue_nachrichten(DUMP_CHAT, "Wann genau?"), [])

    def test_zuletzt_gesehen_nicht_im_dump_gibt_alles(self):
        self.assertEqual(snap_helfer.neue_nachrichten(DUMP_CHAT, "Text, den es nicht gibt"),
                         ["Na du!", "Wann genau?"])


class ZweiWortNameTest(unittest.TestCase):
    """Chat-Abzug von Kontakt E (15.10.2026): Das Label heißt „KONTAKT E“, und die Antwort auf Fridays Satz („Beispielnachricht 3“) steht
    hinter einem Zitat. Vorher las der Helfer von allen Kontakten mit Vor- und Nachnamen keinen einzigen Text."""
    DUMP = (Path(__file__).parent / "daten" / "snap_chat_zweiwortname.xml").read_text(encoding="utf-8")

    def test_texte_mit_zweiwortlabel_werden_gelesen(self):
        self.assertEqual(snap_helfer.fremde_texte(self.DUMP),
                         ["Beispielnachricht 2", "Beispielnachricht 3", "Beispielnachricht 4"])

    def test_zitat_antwort_und_lage(self):
        lagen = snap_helfer.fremde_texte_mit_lage(self.DUMP)
        self.assertEqual([l for _, l in lagen], [True, True, False])    # nur „Beispielnachricht 4“ steht unter Fridays letzter Nachricht

    def test_kein_gruppenchat_wegen_des_zweiwortlabels(self):
        self.assertEqual(snap_helfer.chat_fremde_labels(self.DUMP, "Kontakt E"), [])

    def test_systemzeilen_sind_kein_label(self):
        for t in ("YOU AND KONTAKT E STARTED A STREAK 🔥", "KONTAKT M SAVED A SNAP IN CHAT!", "Kontakt E", "TODAY X Y Z A"):
            self.assertFalse(snap_helfer._ABSENDER_RE.match(t), t)
        for t in ("ME", "KONTAKT C", "KONTAKT E", "ANNA LENA WOLF"):
            self.assertTrue(snap_helfer._ABSENDER_RE.match(t), t)


class UngeleseneChatAnzahlTest(unittest.TestCase):
    def test_liest_badge_neben_dem_chat_symbol(self):
        self.assertEqual(snap_helfer.ungelesene_chat_anzahl(DUMP_NAV_MIT_BADGE), 1)

    def test_ohne_badge_gibt_null(self):
        self.assertEqual(snap_helfer.ungelesene_chat_anzahl(DUMP_NAV_OHNE_BADGE), 0)

    def test_ohne_chat_symbol_gibt_null(self):
        self.assertEqual(snap_helfer.ungelesene_chat_anzahl("<hierarchy></hierarchy>"), 0)

    def test_kaputtes_xml_gibt_null_statt_zu_werfen(self):
        self.assertEqual(snap_helfer.ungelesene_chat_anzahl("<kaputt"), 0)

    def test_weit_entfernte_zahl_zaehlt_nicht_als_badge(self):
        dump = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" resource-id="com.snapchat.android:id/ngs_chat_icon_container"
        bounds="[241,2171][440,2356]" />
  <node text="42" resource-id="" bounds="[0,0][20,20]" />
</hierarchy>"""
        self.assertEqual(snap_helfer.ungelesene_chat_anzahl(dump), 0)


import subprocess
from unittest import mock


class AdbAufrufTest(unittest.TestCase):
    def _erfolg(self, stdout="", returncode=0):
        return subprocess.CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr="")

    def test_geraet_verbunden_erkennt_device(self):
        with mock.patch.object(snap_helfer, "_adb",
                               return_value=self._erfolg("List of devices attached\nABC123\tdevice\n")):
            self.assertTrue(snap_helfer.geraet_verbunden())

    def test_geraet_nicht_verbunden_ohne_zeilen(self):
        with mock.patch.object(snap_helfer, "_adb",
                               return_value=self._erfolg("List of devices attached\n")):
            self.assertFalse(snap_helfer.geraet_verbunden())

    def test_app_starten_wirft_bei_fehler(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(returncode=1)):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.app_starten()

    def test_tippen_ruft_input_tap_auf(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg()) as m:
            snap_helfer.tippen(10, 20)
        m.assert_called_once_with("shell", "input", "tap", "10", "20")

    def test_schritt_ausfuehren_ohne_treffer_wirft(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy></hierarchy>"):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.schritt_ausfuehren("director_mode_eintrag")

    def test_schritt_ausfuehren_tippt_gefundenes_element(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value=DUMP_KAMERA), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen:
            snap_helfer.schritt_ausfuehren("director_mode_eintrag")
        m_tippen.assert_called_once_with(100, 70)

    def test_schritt_ausfuehren_mit_index_nimmt_das_letzte_element(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value=DUMP_KAMERA_MODUS), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen:
            snap_helfer.schritt_ausfuehren("kamera_mehr_umschalten")
        m_tippen.assert_called_once_with(1004, 622)

    def test_snap_senden_ohne_geraet_wirft(self):
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=False):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.snap_senden(None, "Hi")

    def test_snap_senden_weckt_den_bildschirm(self):
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "bildschirm_aufwecken") as m_wecken, \
             mock.patch.object(snap_helfer, "app_zuruecksetzen"), \
             mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "schritt_ausfuehren"), \
             mock.patch.object(snap_helfer, "kontakt_chat_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=_CHAT_MIT_HI), \
             mock.patch.object(snap_helfer.time, "sleep"), \
             mock.patch.object(snap_helfer, "chat_senden"):
            snap_helfer.snap_senden(None, "Hi")
        m_wecken.assert_called_once()

    def test_snap_senden_text_pfad_sendet_ueber_enter(self):
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "bildschirm_aufwecken"), \
             mock.patch.object(snap_helfer, "app_zuruecksetzen"), \
             mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "schritt_ausfuehren") as m_schritt, \
             mock.patch.object(snap_helfer, "kontakt_chat_oeffnen") as m_kontakt, \
             mock.patch.object(snap_helfer, "text_eingeben") as m_text, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=_CHAT_MIT_HI), \
             mock.patch.object(snap_helfer.time, "sleep"), \
             mock.patch.object(snap_helfer, "chat_senden") as m_senden:
            ordnung = mock.Mock()
            ordnung.attach_mock(m_schritt, "schritt")
            ordnung.attach_mock(m_kontakt, "kontakt")
            snap_helfer.snap_senden(None, "Hi")
        # Reihenfolge: Chat-Tab, Chat des Empfängers (mit Kopfprüfung), erst dann tippen
        self.assertEqual([(c[0], c[1][0] if c[1] else None) for c in ordnung.mock_calls],
                         [("schritt", "chat_tab"), ("kontakt", "Eike"), ("schritt", "texteingabe")])
        m_text.assert_called_once_with("Hi")
        m_senden.assert_called_once()

    def test_chat_senden_ruft_enter_taste(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg()) as m:
            snap_helfer.chat_senden()
        m.assert_called_once_with("shell", "input", "keyevent", "KEYCODE_ENTER")

    def test_chat_senden_wirft_bei_fehler(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(returncode=1)):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.chat_senden()

    def test_bildschirm_aufwecken_wirft_bei_fehler(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(returncode=1)):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.bildschirm_aufwecken()

    def test_app_zuruecksetzen_ruft_force_stop(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg()) as m:
            snap_helfer.app_zuruecksetzen()
        m.assert_called_once_with("shell", "am", "force-stop", "com.snapchat.android")

    def test_app_zuruecksetzen_wirft_bei_fehler(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(returncode=1)):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.app_zuruecksetzen()


class BildUebertragenTest(unittest.TestCase):
    def _erfolg(self, stdout="", returncode=0):
        return subprocess.CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr="")

    def test_erfolgreiche_uebertragung_gibt_zielpfad(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg()) as m:
            ziel = snap_helfer.bild_uebertragen(Path("/tmp/bild.jpg"))
        self.assertTrue(ziel.startswith("/sdcard/DCIM/friday_snap_"))
        self.assertEqual(m.call_args_list[0].args[0], "push")
        self.assertEqual(m.call_args_list[1].args[0], "shell")

    def test_push_fehlschlag_wirft(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(returncode=1)):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.bild_uebertragen(Path("/tmp/bild.jpg"))

    def test_medienscan_fehlschlag_wirft(self):
        aufrufe = [self._erfolg(), self._erfolg(returncode=1)]
        with mock.patch.object(snap_helfer, "_adb", side_effect=aufrufe):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.bild_uebertragen(Path("/tmp/bild.jpg"))


class BildEntfernenTest(unittest.TestCase):
    """Die Übertragungskopie im Ordner DCIM wurde nie gelöscht (146 alte Bilder, 14.10.2026)."""

    def _ok(self, stdout=""):
        return subprocess.CompletedProcess(args=["adb"], returncode=0, stdout=stdout, stderr="")

    def test_loescht_die_datei_und_meldet_es_der_galerie(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._ok()) as m:
            snap_helfer.bild_entfernen("/sdcard/DCIM/friday_snap_1790000000.jpg")
        self.assertEqual(m.call_args_list[0].args[:3], ("shell", "rm", "-f"))
        self.assertEqual(m.call_args_list[0].args[3], "/sdcard/DCIM/friday_snap_1790000000.jpg")
        self.assertEqual(m.call_args_list[1].args[:2], ("shell", "am"))

    def test_fasst_nur_eigene_uebertragungskopien_an(self):
        for fremd in ("/sdcard/DCIM/Camera/IMG_1.jpg", "/sdcard/DCIM/friday_snap_1.jpg.bak", "/sdcard/DCIM/../x.jpg",
                      "/sdcard/DCIM/friday_snap_.jpg", "friday_snap_1.jpg", "/sdcard/DCIM/x.jpg"):
            with mock.patch.object(snap_helfer, "_adb") as m:
                snap_helfer.bild_entfernen(fremd)
            m.assert_not_called()

    def test_fehler_beim_loeschen_sind_nie_kritisch(self):
        with mock.patch.object(snap_helfer, "_adb", side_effect=OSError("adb weg")):
            snap_helfer.bild_entfernen("/sdcard/DCIM/friday_snap_1790000000.jpg")

    def test_aufraeumen_entfernt_nur_alte_kopien(self):
        jetzt = 1_790_000_000
        liste = "\n".join([f"friday_snap_{jetzt - 7200}.jpg", f"friday_snap_{jetzt - 60}.jpg", "Camera", "IMG_0001.jpg",
                           f"friday_snap_{jetzt - 90000}.jpg", "friday_snap_abc.jpg"])
        entfernt = []
        with mock.patch.object(snap_helfer, "_adb", return_value=self._ok(liste)), \
             mock.patch.object(snap_helfer, "bild_entfernen", side_effect=entfernt.append), \
             mock.patch.object(snap_helfer.time, "time", return_value=jetzt):
            n = snap_helfer.alte_bilder_aufraeumen()
        self.assertEqual(n, 2)
        self.assertEqual(sorted(entfernt), sorted([f"/sdcard/DCIM/friday_snap_{jetzt - 7200}.jpg", f"/sdcard/DCIM/friday_snap_{jetzt - 90000}.jpg"]))

    def test_aufraeumen_scheitert_still(self):
        with mock.patch.object(snap_helfer, "_adb", side_effect=OSError("weg")):
            self.assertEqual(snap_helfer.alte_bilder_aufraeumen(), 0)


class BildschirmDumpTest(unittest.TestCase):
    def _erfolg(self, stdout="", returncode=0):
        return subprocess.CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr="")

    def test_erfolgreicher_dump_gibt_stdout(self):
        aufrufe = [self._erfolg(), self._erfolg(stdout="<hierarchy></hierarchy>")]
        with mock.patch.object(snap_helfer, "_adb", side_effect=aufrufe):
            self.assertEqual(snap_helfer.bildschirm_dump(), "<hierarchy></hierarchy>")

    def test_dump_fehlschlag_wirft(self):
        aufrufe = [self._erfolg(), self._erfolg(returncode=1)]
        with mock.patch.object(snap_helfer, "_adb", side_effect=aufrufe):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.bildschirm_dump()

    def test_leerer_dump_wirft(self):
        aufrufe = [self._erfolg(), self._erfolg(stdout="")]
        with mock.patch.object(snap_helfer, "_adb", side_effect=aufrufe):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.bildschirm_dump()


class TextEingebenTest(unittest.TestCase):
    def _ergebnis(self, args, returncode=0, stdout=""):
        return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr="")

    def _ohne_adb_tastatur(self, aufrufe):
        def adb(*args, **kw):
            aufrufe.append(args)
            if args[:4] == ("shell", "settings", "get", "secure"):
                return self._ergebnis(args, stdout="com.google.latin/.IME\n")
            if args[:3] == ("shell", "ime", "set"):
                return self._ergebnis(args, returncode=1)
            return self._ergebnis(args)
        return adb

    def test_text_wird_ohne_adb_tastatur_escaped_und_gesendet(self):
        aufrufe = []
        with mock.patch.object(snap_helfer, "_adb", side_effect=self._ohne_adb_tastatur(aufrufe)):
            snap_helfer.text_eingeben("Na du")
        self.assertIn(("shell", "input", "text", "Na%sdu"), aufrufe)

    def test_fehlschlag_wirft(self):
        def adb(*args, **kw):
            if args[:4] == ("shell", "settings", "get", "secure"):
                return self._ergebnis(args, stdout="com.android.adbkeyboard/.AdbIME\n")
            return self._ergebnis(args, returncode=1)

        with mock.patch.object(snap_helfer, "_adb", side_effect=adb):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.text_eingeben("Hi")

    def test_text_eingeben_nutzt_die_adb_tastatur_mit_base64(self):
        import base64
        aufrufe = []

        def adb(*args, **kw):
            aufrufe.append(args)
            return self._ergebnis(args, stdout="com.android.adbkeyboard/.AdbIME\n")

        with mock.patch.object(snap_helfer, "_adb", side_effect=adb):
            snap_helfer.text_eingeben("Gr\u00f6\u00dfe \U0001F604\nzwei")
        senden = [a for a in aufrufe if "ADB_INPUT_B64" in a][0]
        self.assertEqual(base64.b64decode(senden[senden.index("--es") + 2]).decode("utf-8"),
                         "Gr\u00f6\u00dfe \U0001F604 zwei")

    def test_text_eingeben_faellt_ohne_adb_tastatur_auf_input_text_zurueck(self):
        aufrufe = []
        with mock.patch.object(snap_helfer, "_adb", side_effect=self._ohne_adb_tastatur(aufrufe)):
            snap_helfer.text_eingeben("Hi du")
        self.assertTrue(any(a[:3] == ("shell", "input", "text") and a[3] == "Hi%sdu" for a in aufrufe))


class MitNeuversuchTest(unittest.TestCase):
    def test_erfolg_im_ersten_versuch_ruft_nur_einmal(self):
        funktion = mock.Mock(return_value="ok")
        with mock.patch.object(snap_helfer, "app_zuruecksetzen") as m_reset, \
             mock.patch.object(snap_helfer, "app_starten") as m_start:
            ergebnis = snap_helfer.mit_neuversuch(funktion, "a", "b")
        self.assertEqual(ergebnis, "ok")
        funktion.assert_called_once_with("a", "b")
        m_reset.assert_not_called()
        m_start.assert_not_called()

    def test_fehlschlag_dann_erfolg_setzt_zurueck_und_versucht_erneut(self):
        funktion = mock.Mock(side_effect=[snap_helfer.SnapHelferFehler("nö"), "ok"])
        with mock.patch.object(snap_helfer, "app_zuruecksetzen") as m_reset, \
             mock.patch.object(snap_helfer, "app_starten") as m_start, \
             mock.patch.object(snap_helfer, "_pause"), mock.patch.object(snap_helfer, "_auf_oberflaeche_warten"), \
             mock.patch.object(snap_helfer, "_entwurf_aufraeumen") as m_entwurf:
            ergebnis = snap_helfer.mit_neuversuch(funktion)
        self.assertEqual(ergebnis, "ok")
        self.assertEqual(funktion.call_count, 2)
        m_reset.assert_called_once()
        m_start.assert_called_once()
        m_entwurf.assert_called_once()

    def test_zweiter_fehlschlag_schlaegt_durch(self):
        funktion = mock.Mock(side_effect=[snap_helfer.SnapHelferFehler("erster"),
                                          snap_helfer.SnapHelferFehler("zweiter")])
        with mock.patch.object(snap_helfer, "app_zuruecksetzen"), \
             mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "_pause"), mock.patch.object(snap_helfer, "_auf_oberflaeche_warten"), \
             mock.patch.object(snap_helfer, "_entwurf_aufraeumen"):
            with self.assertRaises(snap_helfer.SnapHelferFehler) as ctx:
                snap_helfer.mit_neuversuch(funktion)
        self.assertEqual(str(ctx.exception), "zweiter")

    def test_zuruecksetzen_schlaegt_selbst_fehl(self):
        funktion = mock.Mock(side_effect=snap_helfer.SnapHelferFehler("nö"))
        with mock.patch.object(snap_helfer, "app_zuruecksetzen",
                               side_effect=snap_helfer.SnapHelferFehler("kein Gerät")):
            with self.assertRaises(snap_helfer.SnapHelferFehler) as ctx:
                snap_helfer.mit_neuversuch(funktion)
        self.assertEqual(str(ctx.exception), "kein Gerät")
        funktion.assert_called_once()


class EntwurfAufraeumenTest(unittest.TestCase):
    DUMP_MIT_DIALOG = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="Discard Entire Video?" resource-id="com.snapchat.android:id/alert_dialog_title"
        bounds="[280,864][800,951]" />
  <node text="" resource-id="com.snapchat.android:id/cancel_button" bounds="[217,1404][863,1535]" />
</hierarchy>"""

    def test_dialog_gefunden_tippt_discard_ueber_cancel(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value=self.DUMP_MIT_DIALOG), \
             mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=_png(4, 4, lambda x, y: (0, 0, 0))), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen, \
             mock.patch.object(snap_helfer, "_pause"):
            snap_helfer._entwurf_aufraeumen()
        m_tippen.assert_called_once_with(540, 1469 - 159)

    def test_kein_dialog_tut_nichts(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy></hierarchy>"), \
             mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=_png(4, 4, lambda x, y: (0, 0, 0))), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen:
            snap_helfer._entwurf_aufraeumen()
        m_tippen.assert_not_called()


class SnapSendenBildTest(unittest.TestCase):
    def _grundmocks(self):
        return [
            mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True),
            mock.patch.object(snap_helfer, "bildschirm_aufwecken"),
            mock.patch.object(snap_helfer, "app_zuruecksetzen"),
            mock.patch.object(snap_helfer, "app_starten"),
            mock.patch.object(snap_helfer, "bild_uebertragen", return_value="/sdcard/DCIM/x.jpg"),
            mock.patch.object(snap_helfer, "_zur_kamera"),
            mock.patch.object(snap_helfer, "_auf_oberflaeche_warten"),
            mock.patch.object(snap_helfer, "schritt_ausfuehren"),
            mock.patch.object(snap_helfer, "_blind_tippen"),
            mock.patch.object(snap_helfer, "_gelben_knopf_tippen"),
            mock.patch.object(snap_helfer, "sendto_waehlen"),
            mock.patch.object(snap_helfer, "text_eingeben"),
        ]

    def test_kopie_wird_nach_dem_senden_und_auch_nach_einem_fehler_entfernt(self):
        import contextlib
        for fehler in (None, snap_helfer.SnapHelferFehler("kaputt")):
            with contextlib.ExitStack() as stack:
                for m in self._grundmocks():
                    if getattr(m, "attribute", "") == "bild_uebertragen":
                        continue
                    stack.enter_context(m)
                stack.enter_context(mock.patch.object(snap_helfer, "bild_uebertragen", return_value="/sdcard/DCIM/friday_snap_1790000000.jpg"))
                entfernen = stack.enter_context(mock.patch.object(snap_helfer, "bild_entfernen"))
                aufraeumen = stack.enter_context(mock.patch.object(snap_helfer, "alte_bilder_aufraeumen"))
                stack.enter_context(mock.patch.object(snap_helfer, "_pause"))
                if fehler:
                    stack.enter_context(mock.patch.object(snap_helfer, "_gelben_knopf_tippen", side_effect=fehler))
                    with self.assertRaises(snap_helfer.SnapHelferFehler):
                        snap_helfer._bild_snap_ablauf("/tmp/x.jpg", "")
                else:
                    snap_helfer._bild_snap_ablauf("/tmp/x.jpg", "")
            entfernen.assert_called_once_with("/sdcard/DCIM/friday_snap_1790000000.jpg")
            aufraeumen.assert_called_once()

    def test_zur_kamera_wechselt_ueber_den_tab_wenn_snapchat_woanders_steht(self):
        kamera = '<hierarchy><node resource-id="com.snapchat.android:id/camera_mode_icon_image_view" bounds="[900,1500][1000,1600]"/></hierarchy>'
        spotlight = '<hierarchy><node resource-id="com.snapchat.android:id/ngs_camera_icon_container" bounds="[400,2200][500,2300]"/></hierarchy>'
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=[spotlight, kamera]), \
             mock.patch.object(snap_helfer, "tippen") as tippen, mock.patch.object(snap_helfer, "_adb") as adb, \
             mock.patch.object(snap_helfer, "_pause"):
            snap_helfer._zur_kamera()
        tippen.assert_called_once_with(450, 2250)
        adb.assert_not_called()

    def test_zur_kamera_geht_zurueck_wenn_kein_tab_da_ist(self):
        kamera = '<hierarchy><node resource-id="com.snapchat.android:id/camera_mode_icon_image_view" bounds="[900,1500][1000,1600]"/></hierarchy>'
        aufnahme = '<hierarchy><node text="Add a Sound" bounds="[400,200][500,300]"/></hierarchy>'
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=[aufnahme, kamera]), \
             mock.patch.object(snap_helfer, "tippen") as tippen, mock.patch.object(snap_helfer, "_adb") as adb, \
             mock.patch.object(snap_helfer, "_pause"):
            snap_helfer._zur_kamera()
        tippen.assert_not_called()
        self.assertEqual(adb.call_args.args, ("shell", "input", "keyevent", "4"))

    def test_zur_kamera_tut_nichts_wenn_die_kamera_schon_da_ist(self):
        kamera = '<hierarchy><node resource-id="com.snapchat.android:id/camera_mode_icon_image_view" bounds="[900,1500][1000,1600]"/></hierarchy>'
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value=kamera), \
             mock.patch.object(snap_helfer, "tippen") as tippen, mock.patch.object(snap_helfer, "_adb") as adb:
            snap_helfer._zur_kamera()
        tippen.assert_not_called()
        adb.assert_not_called()

    def test_warten_auf_die_oberflaeche_endet_sobald_die_leiste_da_ist(self):
        splash = "<hierarchy/>"
        fertig = '<hierarchy><node resource-id="com.snapchat.android:id/ngs_chat_icon_container" bounds="[241,2171][440,2356]"/></hierarchy>'
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=[snap_helfer.SnapHelferFehler("leer"), splash, fertig]) as dump, \
             mock.patch.object(snap_helfer.time, "sleep"):
            _WARTEN_ECHT()
        self.assertEqual(dump.call_count, 3)

    def test_warten_auf_die_oberflaeche_gibt_nach_der_frist_auf(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), \
             mock.patch.object(snap_helfer.time, "sleep"), \
             mock.patch.object(snap_helfer.time, "monotonic", side_effect=[0, 1, 2, 31]):
            _WARTEN_ECHT()          # kein Fehler: der Ablauf meldet seinen eigenen

    def test_mit_bild_nutzt_director_mode_ablauf(self):
        stapel = self._grundmocks()
        for p in stapel:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in stapel])
        m_schritt = snap_helfer.schritt_ausfuehren
        m_blind = snap_helfer._blind_tippen
        m_gelb = snap_helfer._gelben_knopf_tippen
        m_text = snap_helfer.text_eingeben

        snap_helfer.snap_senden("/tmp/bild.jpg", "Denk dran!")

        schritte = [c.args[0] for c in m_schritt.call_args_list]
        self.assertEqual(schritte, ["kamera_mehr_umschalten", "director_mode_eintrag", "snap_endgueltig_senden"])
        snap_helfer.sendto_waehlen.assert_called_once_with("Eike")     # der Empfänger wird gewählt UND geprüft
        blinde = [c.args[0] for c in m_blind.call_args_list]
        self.assertEqual(blinde, ["import_media_knopf", "neuestes_foto_zelle",
                                  "foto_auswahl_bestaetigen", "text_werkzeug_knopf",
                                  "text_platzieren"])
        bereiche = [c.args[0] for c in m_gelb.call_args_list]
        self.assertEqual(bereiche, [snap_helfer.BEREICH_PREVIEW, snap_helfer.BEREICH_SEND_TO])
        m_text.assert_called_once_with("Denk dran!")

    def test_mit_bild_ohne_text_ueberspringt_bildunterschrift(self):
        stapel = self._grundmocks()
        for p in stapel:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in stapel])
        m_blind = snap_helfer._blind_tippen
        m_text = snap_helfer.text_eingeben

        snap_helfer.snap_senden("/tmp/bild.jpg", "")

        m_text.assert_not_called()
        blinde = [c.args[0] for c in m_blind.call_args_list]
        self.assertNotIn("text_werkzeug_knopf", blinde)
        self.assertNotIn("text_platzieren", blinde)


class BildschirmgroesseTest(unittest.TestCase):
    def _erfolg(self, stdout="", returncode=0):
        return subprocess.CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr="")

    def test_liest_breite_und_hoehe(self):
        with mock.patch.object(snap_helfer, "_adb",
                               return_value=self._erfolg("Physical size: 1080x2400\n")):
            self.assertEqual(snap_helfer.bildschirmgroesse(), (1080, 2400))

    def test_unlesbare_ausgabe_wirft(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg("kaputt")):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.bildschirmgroesse()


class BlindTippenTest(unittest.TestCase):
    def test_tippt_anteil_der_bildschirmgroesse(self):
        with mock.patch.object(snap_helfer, "bildschirmgroesse", return_value=(1000, 2000)), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen, \
             mock.patch.object(snap_helfer, "_pause"):
            snap_helfer._blind_tippen("neuestes_foto_zelle")
        m_tippen.assert_called_once_with(125, 250)  # (0.125, 0.125) von (1000, 2000)


def _test_png_bauen(pixel_zeilen: list[list[tuple[int, int, int]]]) -> bytes:
    """Baut ein minimales, unkomprimiertes 8-Bit-RGB-PNG für Tests — ohne Pillow/fremde
    Abhängigkeit, passend zu snap_helfer._png_zeilen (nur Filtertyp 0, keine Vorhersage)."""
    import struct as _struct
    import zlib as _zlib
    hoehe = len(pixel_zeilen)
    breite = len(pixel_zeilen[0])
    roh = bytearray()
    for zeile in pixel_zeilen:
        roh.append(0)  # Filtertyp 0 = keine Vorhersage
        for r, g, b in zeile:
            roh += bytes((r, g, b))

    def chunk(typ: bytes, daten: bytes) -> bytes:
        return (_struct.pack(">I", len(daten)) + typ + daten
               + _struct.pack(">I", _zlib.crc32(typ + daten) & 0xffffffff))

    ihdr = _struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0)
    idat = _zlib.compress(bytes(roh))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat)
           + chunk(b"IEND", b""))


class FarbeFindenTest(unittest.TestCase):
    def test_findet_gelben_bereich(self):
        schwarz = (0, 0, 0)
        gelb = (255, 252, 0)
        zeilen = [[schwarz] * 6 for _ in range(6)]
        for y in (2, 3):
            for x in (3, 4):
                zeilen[y][x] = gelb
        png = _test_png_bauen(zeilen)
        mitte = snap_helfer.farbe_finden(png)
        self.assertEqual(mitte, (3, 2))

    def test_kein_treffer_gibt_none(self):
        png = _test_png_bauen([[(0, 0, 0)] * 4 for _ in range(4)])
        self.assertIsNone(snap_helfer.farbe_finden(png))

    def test_suchfenster_grenzt_ein(self):
        schwarz = (0, 0, 0)
        gelb = (255, 252, 0)
        zeilen = [[schwarz] * 10 for _ in range(10)]
        zeilen[1][1] = gelb  # außerhalb des Suchfensters unten
        png = _test_png_bauen(zeilen)
        self.assertIsNone(snap_helfer.farbe_finden(png, y_anteil=(0.5, 1.0)))


class SendeknopfFarbeTest(unittest.TestCase):
    """Snapchat hat den „Send to“-Knopf am 14.10.2026 von Gelb auf Blau (15, 173, 255) umgestellt — beide Farben gelten."""

    def _bild(self, farbe):
        zeilen = [[(0, 0, 0)] * 10 for _ in range(10)]
        for y in (9,):
            for x in (7, 8):
                zeilen[y][x] = farbe
        return _test_png_bauen(zeilen)

    def test_findet_gelb_und_blau(self):
        bereich = {"y_anteil": (0.85, 1.0), "x_anteil": (0.6, 1.0)}
        for farbe in ((255, 252, 0), (15, 173, 255)):
            self.assertEqual(snap_helfer._sendeknopf_finden(self._bild(farbe), **bereich), (7, 9), farbe)

    def test_andere_farben_zaehlen_nicht(self):
        bereich = {"y_anteil": (0.85, 1.0), "x_anteil": (0.6, 1.0)}
        for farbe in ((255, 0, 0), (0, 200, 0), (200, 200, 200)):
            self.assertIsNone(snap_helfer._sendeknopf_finden(self._bild(farbe), **bereich), farbe)

    def test_editor_ist_auch_mit_blauem_knopf_offen(self):
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=self._bild((15, 173, 255))), \
             mock.patch.object(snap_helfer, "BEREICH_SEND_TO", {"y_anteil": (0.85, 1.0), "x_anteil": (0.6, 1.0)}):
            self.assertTrue(snap_helfer._editor_offen())


class GelbenKnopfTippenTest(unittest.TestCase):
    def test_kein_treffer_wirft(self):
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=b"x"), \
             mock.patch.object(snap_helfer, "_sendeknopf_finden", return_value=None):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer._gelben_knopf_tippen(snap_helfer.BEREICH_PREVIEW)

    def test_treffer_tippt_und_pausiert(self):
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=b"x"), \
             mock.patch.object(snap_helfer, "_sendeknopf_finden", return_value=(42, 99)), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen, \
             mock.patch.object(snap_helfer, "_pause") as m_pause:
            snap_helfer._gelben_knopf_tippen(snap_helfer.BEREICH_PREVIEW)
        m_tippen.assert_called_once_with(42, 99)
        m_pause.assert_called_once()


import json
import tempfile
import os


class EmpfangUndZustandTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _konnektor_aktivieren(self, an: bool = True) -> None:
        pfad = snap_helfer.daten_ordner() / "konnektoren.json"
        pfad.write_text(json.dumps({"snapchat": {"aktiv": an}}), encoding="utf-8")

    def test_eingang_pruefen_ohne_geraet_wirft(self):
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=False):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.eingang_pruefen("")

    def test_eingang_pruefen_findet_neue_nachrichten(self):
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "bildschirm_aufwecken"), \
             mock.patch.object(snap_helfer, "app_zuruecksetzen"), \
             mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "schritt_ausfuehren"), \
             mock.patch.object(snap_helfer, "kontakt_chat_oeffnen"), \
             mock.patch.object(snap_helfer, "_kontakte_lesen") as andere, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=DUMP_CHAT):
            neue, zuletzt = snap_helfer.eingang_pruefen("")
        self.assertEqual(neue, ["Na du!", "Wann genau?"])
        self.assertEqual(zuletzt, "Wann genau?")
        andere.assert_not_called()                                     # die anderen Kontakte kommen in `einmal`, NACH Eikes Eingang und Gespräch

    def test_zustand_schreiben_liest_sich_wieder_ein(self):
        snap_helfer.zustand_schreiben({"verbunden": True, "zeit": 123.0, "letzter_fehler": None})
        pfad = snap_helfer.daten_ordner() / "snapchat" / "zustand.json"
        self.assertTrue(json.loads(pfad.read_text(encoding="utf-8"))["verbunden"])

    def test_einmal_ohne_auftrag_und_ohne_geraet_schreibt_fehler_zustand(self):
        self._konnektor_aktivieren()
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=False), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        pfad = snap_helfer.daten_ordner() / "snapchat" / "zustand.json"
        self.assertFalse(json.loads(pfad.read_text(encoding="utf-8"))["verbunden"])

    def test_einmal_ohne_geraet_behaelt_zuletzt_gesehen(self):
        """Wenn das Handy getrennt ist, darf der zuletzt_gesehen-Wert nicht verloren gehen.
        Sonst würden beim Wiederherstellen der Verbindung alte Nachrichten als neu eingeordnet."""
        self._konnektor_aktivieren()
        zustand_pfad = snap_helfer.daten_ordner() / "snapchat" / "zustand.json"
        zustand_pfad.parent.mkdir(parents=True, exist_ok=True)
        zustand_pfad.write_text(json.dumps({"zuletzt_gesehen": "Wann genau?"}), encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=False), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        zustand = json.loads(zustand_pfad.read_text(encoding="utf-8"))
        self.assertEqual(zustand["zuletzt_gesehen"], "Wann genau?")

    def test_einmal_mit_wartendem_auftrag_sendet_und_raeumt_auf(self):
        self._konnektor_aktivieren()
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.write_text(json.dumps({"art": "text", "text": "Hi", "bild_pfad": None, "empfaenger": "Eike"}),
                                 encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden") as m_senden, \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        m_senden.assert_called_once_with(None, "Hi", "Eike")
        self.assertFalse(auftrag_datei.exists())

    def test_ergebnis_zeile_traegt_die_auftrags_id(self):
        import json
        snap_helfer.ergebnis_protokollieren({"id": "abc123", "art": "text", "empfaenger": "Eike", "person": None, "text": "Hi"}, "gesendet")
        zeile = json.loads(((snap_helfer.daten_ordner() / "snapchat") / "ergebnisse.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((zeile["id"], zeile["ergebnis"]), ("abc123", "gesendet"))

    def test_ergebnisprotokoll_traegt_gelegt_und_ausloeser(self):
        snap_helfer.ergebnis_protokollieren({"art": "text", "empfaenger": "Eike", "person": None, "text": "Hi",
                                             "gelegt": 100.0, "ausloeser": 90.0}, "gesendet")
        zeile = json.loads((snap_helfer.daten_ordner() / "snapchat" / "ergebnisse.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((zeile["gelegt"], zeile["ausloeser"]), (100.0, 90.0))

    # ---- Heimat-Modus (07.10.2026, Spec „schnell“) ----
    def _heimat(self, an=True):
        d = snap_helfer.daten_ordner() / "snapchat"
        d.mkdir(parents=True, exist_ok=True)
        (d / "einstellungen.json").write_text(json.dumps({"heimat": an}), encoding="utf-8")

    def test_heimat_standard_aus_und_per_datei_einschaltbar(self):
        self.assertFalse(snap_helfer.heimat_aktiv())
        self._heimat(True)
        self.assertTrue(snap_helfer.heimat_aktiv())
        self._heimat(False)
        self.assertFalse(snap_helfer.heimat_aktiv())

    def test_heimat_vorbereiten_ohne_neustart_wenn_chatliste_steht(self):
        """`_zur_chatliste` prüft selbst (ein Dump) — `_heimat_vorbereiten` macht keinen zweiten (Messung 07.10.2026: ein Dump ≈ 3 s)."""
        with mock.patch.object(snap_helfer, "bildschirm_aufwecken"), \
             mock.patch.object(snap_helfer, "bildschirm_dump") as dump, \
             mock.patch.object(snap_helfer, "_zur_chatliste") as zur_liste, \
             mock.patch.object(snap_helfer, "app_zuruecksetzen") as reset, \
             mock.patch.object(snap_helfer, "app_starten") as start:
            snap_helfer._heimat_vorbereiten()
        zur_liste.assert_called_once()
        dump.assert_not_called()
        reset.assert_not_called()
        start.assert_not_called()

    def test_heimat_vorbereiten_faellt_auf_neustart_zurueck(self):
        with mock.patch.object(snap_helfer, "bildschirm_aufwecken"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<x/>"), \
             mock.patch.object(snap_helfer, "chat_zeilen", return_value=[]), \
             mock.patch.object(snap_helfer, "_zur_chatliste", side_effect=snap_helfer.SnapHelferFehler("nicht da")), \
             mock.patch.object(snap_helfer, "_pause"), \
             mock.patch.object(snap_helfer, "app_zuruecksetzen") as reset, \
             mock.patch.object(snap_helfer, "app_starten") as start:
            snap_helfer._heimat_vorbereiten()
        reset.assert_called_once()
        start.assert_called_once()

    def test_snap_senden_ohne_neustart_startet_die_app_nicht_neu(self):
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "_heimat_vorbereiten") as heimat, \
             mock.patch.object(snap_helfer, "app_zuruecksetzen") as reset, \
             mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "_chat_text_ablauf") as ablauf:
            snap_helfer.snap_senden(None, "Hi", "Eike", neustart=False)
        heimat.assert_called_once()
        reset.assert_not_called()
        ablauf.assert_called_once()

    def test_eingang_pruefen_ohne_neustart(self):
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "_heimat_vorbereiten") as heimat, \
             mock.patch.object(snap_helfer, "app_zuruecksetzen") as reset, \
             mock.patch.object(snap_helfer, "mit_neuversuch", return_value=([], "")):
            snap_helfer.eingang_pruefen("", neustart=False)
        heimat.assert_called_once()
        reset.assert_not_called()

    def _einmal_mit(self, neue):
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=(neue, "".join(neue))) as eingang, \
             mock.patch.object(snap_helfer, "gespraech_halten") as gespraech, \
             mock.patch.object(snap_helfer, "_kontakte_lesen"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<x/>"), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        return eingang, gespraech

    def test_einmal_im_heimat_modus_begrenzter_gespraechsmodus_fuer_eike_ohne_neustart(self):
        """Live-Befund 07.10.2026: Ohne Gesprächsmodus brauchte Eikes Antwort 85–104 s (lesen, andere Kontakte, dann 40 s Sendeweg).
        Für Eike bleibt er, aber kurz (`HEIMAT_WARTE_S`/`HEIMAT_NACHLAUF_S`); Kontakte bekommen ihn im Heimat-Modus nicht."""
        self._heimat(True)
        self._konnektor_aktivieren()
        eingang, gespraech = self._einmal_mit(["Hallo Friday"])
        gespraech.assert_called_once()
        self.assertEqual(gespraech.call_args.kwargs, {"warte_s": snap_helfer.HEIMAT_WARTE_S, "nachlauf_s": snap_helfer.HEIMAT_NACHLAUF_S})
        self.assertEqual(eingang.call_args.kwargs.get("neustart"), False)
        self.assertLessEqual(snap_helfer.HEIMAT_WARTE_S, 45)
        self.assertLessEqual(snap_helfer.HEIMAT_NACHLAUF_S, 30)

    def test_einmal_ohne_heimat_bleibt_wie_es_war(self):
        self._konnektor_aktivieren()
        eingang, gespraech = self._einmal_mit(["Hallo"])
        gespraech.assert_called_once()
        self.assertEqual(eingang.call_args.kwargs, {})

    def test_nur_auftraege_liest_nichts(self):
        self._heimat(True)
        self._auftrag_mit_id("a1")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden") as senden, \
             mock.patch.object(snap_helfer, "eingang_pruefen") as eingang, \
             mock.patch.object(snap_helfer, "_kontakte_lesen") as kontakte, \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal(nur_auftraege=True)
        senden.assert_called_once()
        self.assertEqual(senden.call_args.kwargs.get("neustart"), False)
        eingang.assert_not_called()
        kontakte.assert_not_called()

    def test_zwei_auftraege_an_verschiedene_personen_nacheinander_ohne_neustart(self):
        """Review Focus: der zweite Chat ist der richtige — jeder Auftrag trägt seinen Empfänger (`_chat_text_ablauf` prüft den Kopf)."""
        self._heimat(True)
        self._konnektor_aktivieren()
        d = snap_helfer.daten_ordner() / "snapchat"
        d.mkdir(parents=True, exist_ok=True)
        (d / "kontakte.json").write_text(json.dumps({"kontakte": [
            {"anzeige": "Eike", "person": None}, {"anzeige": "maya_x", "person": "p1"}, {"anzeige": "paul_y", "person": "p2"}]}),
            encoding="utf-8")
        (d / "auftrag.json").write_text(json.dumps({"art": "text", "text": "A", "empfaenger": "maya_x", "person": "p1", "id": "i1"}),
                                        encoding="utf-8")
        (d / "warteschlange").mkdir(exist_ok=True)
        (d / "warteschlange" / "1.json").write_text(json.dumps(
            {"art": "text", "text": "B", "empfaenger": "paul_y", "person": "p2", "id": "i2"}), encoding="utf-8")
        gesendet = []
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden",
                               side_effect=lambda b, t, e, **kw: gesendet.append((t, e, kw.get("neustart")))), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal(nur_auftraege=True)
            snap_helfer.einmal(nur_auftraege=True)
        self.assertEqual(gesendet, [("A", "maya_x", False), ("B", "paul_y", False)])

    def test_antwort_erwartet_nur_bei_ungelesenem_eingang(self):
        d = snap_helfer.daten_ordner() / "snapchat"
        d.mkdir(parents=True, exist_ok=True)
        self.assertFalse(snap_helfer._antwort_erwartet())
        (d / "eingang.json").write_text(json.dumps([{"id": "1", "gelesen": True}]), encoding="utf-8")
        self.assertFalse(snap_helfer._antwort_erwartet())
        (d / "eingang.json").write_text(json.dumps([{"id": "1", "gelesen": True}, {"id": "2", "gelesen": False}]), encoding="utf-8")
        self.assertTrue(snap_helfer._antwort_erwartet())

    def test_einmal_meldet_klemmendes_handy_als_false(self):
        self._konnektor_aktivieren()
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=False), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            self.assertFalse(snap_helfer.einmal())

    def test_einmal_meldet_erfolg_als_true(self):
        self._konnektor_aktivieren()
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "_kontakte_lesen"), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            self.assertTrue(snap_helfer.einmal())

    def test_einmal_scheiternder_auftrag_zaehlt_als_klemmen(self):
        self._auftrag_mit_id("k1")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=snap_helfer.SnapHelferFehler("klemmt")), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "_kontakte_lesen"), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            self.assertFalse(snap_helfer.einmal())

    # ---- Review-Fixes (07.10.2026) ----
    def test_nur_auftraege_speichert_mitgelesene_texte(self):
        """Eike schreibt „Und?“, während Fridays Antwort noch in der Schlange liegt: Beim Senden sieht der Helfer den Text beim Öffnen
        seines Chats — Snapchat löscht ihn danach. Ging im schnellen Auftragslauf verloren (Review)."""
        self._heimat(True)
        self._konnektor_aktivieren()
        self._auftrag_mit_id("m1")

        def senden(b, t, e, **kw):
            snap_helfer._beim_senden_gesehen.append("Und?")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=senden), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal(nur_auftraege=True)
        self.assertIn("Und?", [e["text"] for e in snap_helfer._lesen("eingang.json", [])])
        self.assertTrue(snap_helfer._lesen("zustand.json", {}).get("verbunden"))

    def test_nach_dem_senden_zurueck_zur_chatliste_nur_im_heimat_modus(self):
        sichtbar = '<hierarchy><node text="Hi" bounds="[0,0][1,1]"/></hierarchy>'
        for heimat, erwartet in ((True, 1), (False, 0)):
            with mock.patch.object(snap_helfer, "schritt_ausfuehren"), mock.patch.object(snap_helfer, "kontakt_chat_oeffnen"), \
                 mock.patch.object(snap_helfer, "text_eingeben"), mock.patch.object(snap_helfer, "chat_senden"), \
                 mock.patch.object(snap_helfer, "_pause"), mock.patch.object(snap_helfer.time, "sleep"), \
                 mock.patch.object(snap_helfer, "bildschirm_dump", return_value=sichtbar), \
                 mock.patch.object(snap_helfer, "heimat_aktiv", return_value=heimat), \
                 mock.patch.object(snap_helfer, "_zur_chatliste") as zur:
                snap_helfer._chat_text_ablauf("Hi")
            self.assertEqual(zur.call_count, erwartet, heimat)

    def test_unbestaetigtes_senden_startet_die_app_neu_damit_kein_entwurf_bleibt(self):
        self._auftrag_mit_id("e1")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=snap_helfer.SendenUnbestaetigt("nicht bestätigt")), \
             mock.patch.object(snap_helfer, "app_zuruecksetzen") as reset, mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "_kontakte_lesen"), mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        reset.assert_called()

    def test_bild_snap_bekommt_im_heimat_modus_den_neustart(self):
        """Der Bild-Ablauf erwartet die Kamera, `_heimat_vorbereiten` stellt die Chatliste her (Review)."""
        self._heimat(True)
        self._konnektor_aktivieren()
        d = snap_helfer.daten_ordner() / "snapchat"
        d.mkdir(parents=True, exist_ok=True)
        (d / "auftrag.json").write_text(json.dumps({"art": "snap", "text": "", "bild_pfad": "snapchat/ausgang/a.jpg",
                                                     "empfaenger": "Eike", "person": None, "id": "b1"}), encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "_bild_pfad_aufloesen", return_value=Path("/tmp/a.jpg")), \
             mock.patch.object(snap_helfer, "snap_senden") as senden, \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal(nur_auftraege=True)
        self.assertNotIn("neustart", senden.call_args.kwargs)

    def test_kontakt_lesen_haelt_im_heimat_modus_keinen_gespraechsmodus(self):
        for heimat, erwartet in ((True, 0), (False, 1)):
            with mock.patch.object(snap_helfer, "symbole_vermerken"), mock.patch.object(snap_helfer, "chat_bilder_holen", return_value=0), \
                 mock.patch.object(snap_helfer, "_ungelesenes_suchen", side_effect=lambda d: d), \
                 mock.patch.object(snap_helfer, "_kontakt_mitlesen", return_value=1), \
                 mock.patch.object(snap_helfer, "fremde_texte", return_value=[]), \
                 mock.patch.object(snap_helfer, "heimat_aktiv", return_value=heimat), \
                 mock.patch.object(snap_helfer, "gespraech_halten") as gespraech:
                snap_helfer._offenen_kontakt_lesen("maya_x", "p1", "<x/>")
            self.assertEqual(gespraech.call_count, erwartet, heimat)

    def test_ungelesenes_wird_durch_hochscrollen_gefunden(self):
        # Snapchat (Eike, 09.10.2026): War der Chat schon offen, rutschen neue Nachrichten nach oben aus dem Bild.
        texte = {"<leer/>": [], "<oben/>": ["Wo ist eike gerade?"]}
        with mock.patch.object(snap_helfer, "fremde_texte", side_effect=lambda d: texte[d]), \
             mock.patch.object(snap_helfer, "_chat_hochscrollen") as hoch, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<oben/>"):
            self.assertEqual(snap_helfer._ungelesenes_suchen("<leer/>"), "<oben/>")
        hoch.assert_called_once()

    def test_leerer_chat_scrollt_hoechstens_dreimal(self):
        with mock.patch.object(snap_helfer, "fremde_texte", return_value=[]), \
             mock.patch.object(snap_helfer, "_chat_hochscrollen") as hoch, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<leer/>"):
            snap_helfer._ungelesenes_suchen("<leer/>")
        self.assertEqual(hoch.call_count, snap_helfer.CHAT_HOCH_MAX)

    def test_gesendete_id_wird_gemerkt_und_ist_begrenzt(self):
        snap_helfer._id_vermerken("abc")
        self.assertTrue(snap_helfer._id_gesendet("abc"))
        self.assertFalse(snap_helfer._id_gesendet("xyz"))
        self.assertFalse(snap_helfer._id_gesendet(""))
        for i in range(250):
            snap_helfer._id_vermerken(f"id{i}")
        self.assertFalse(snap_helfer._id_gesendet("id0"))
        self.assertTrue(snap_helfer._id_gesendet("id249"))

    def _auftrag_mit_id(self, auftrag_id):
        self._konnektor_aktivieren()
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.write_text(json.dumps({"art": "text", "text": "Hi", "bild_pfad": None, "empfaenger": "Eike",
                                             "id": auftrag_id}), encoding="utf-8")
        return auftrag_datei

    def test_einmal_vermerkt_die_id_nach_dem_senden(self):
        auftrag_datei = self._auftrag_mit_id("neu1")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden") as m_senden, \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        m_senden.assert_called_once()
        self.assertTrue(snap_helfer._id_gesendet("neu1"))
        self.assertFalse(auftrag_datei.exists())

    def test_einmal_sendet_eine_schon_gesendete_id_nicht_noch_einmal(self):
        """Absturz nach dem Senden, vor dem Löschen: Der Auftrag liegt nach dem Neustart wieder da — er darf nicht erneut raus."""
        snap_helfer._id_vermerken("alt1")
        auftrag_datei = self._auftrag_mit_id("alt1")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden") as m_senden, \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        m_senden.assert_not_called()
        self.assertFalse(auftrag_datei.exists())

    def test_gedrueckt_aber_unbestaetigt_wird_nicht_wiederholt(self):
        """Review 07.10.2026: Senden gedrückt, Bestätigung nicht gesehen → `mit_neuversuch` und `MAX_VERSUCHE` tippten dieselbe
        Nachricht bis zu 8-mal. Jetzt: ein eigener Fehler, der durch den Neuversuch hindurchgeht."""
        leer = '<hierarchy><node text="Eike" bounds="[0,0][1,1]"/></hierarchy>'
        with mock.patch.object(snap_helfer, "schritt_ausfuehren"), \
             mock.patch.object(snap_helfer, "kontakt_chat_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "chat_senden") as senden, \
             mock.patch.object(snap_helfer, "_pause"), \
             mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "bildschirm_aufwecken"), \
             mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "app_zuruecksetzen") as reset, \
             mock.patch.object(snap_helfer.time, "sleep"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=leer):
            with self.assertRaises(snap_helfer.SendenUnbestaetigt):
                snap_helfer.snap_senden(None, "Hi")
        senden.assert_called_once()                    # einmal getippt, kein zweiter Versuch
        reset.assert_called_once()                     # nur das normale Zurücksetzen vor dem Senden, keins vom Neuversuch

    def test_einmal_beendet_unbestaetigten_auftrag_ohne_neuversuch(self):
        auftrag_datei = self._auftrag_mit_id("unb1")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=snap_helfer.SendenUnbestaetigt("nicht bestätigt")) as m, \
             mock.patch.object(snap_helfer, "app_zuruecksetzen"), mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        m.assert_called_once()
        self.assertFalse(auftrag_datei.exists())                     # nicht erneut versuchen
        self.assertTrue(snap_helfer._id_gesendet("unb1"))            # und nie wieder senden
        zeilen = (snap_helfer.daten_ordner() / "snapchat" / "ergebnisse.jsonl").read_text(encoding="utf-8")
        self.assertIn("unbestaetigt", zeilen)

    def test_senden_ohne_bestaetigung_im_chat_scheitert(self):
        leer = '<hierarchy><node text="Eike" bounds="[0,0][1,1]"/></hierarchy>'
        with mock.patch.object(snap_helfer, "schritt_ausfuehren"), \
             mock.patch.object(snap_helfer, "kontakt_chat_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "chat_senden"), \
             mock.patch.object(snap_helfer, "_pause"), \
             mock.patch.object(snap_helfer.time, "sleep"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=leer):
            with self.assertRaises(snap_helfer.SendenUnbestaetigt):       # bewusst kein SnapHelferFehler (kein Neuversuch)
                snap_helfer._chat_text_ablauf("Hi")

    def test_eigener_text_sichtbar_vergleicht_mit_dem_getippten_text(self):
        dump = '<hierarchy><node text="Test bestanden Was geht, Eike" bounds="[0,0][1,1]"/></hierarchy>'
        self.assertTrue(snap_helfer.eigener_text_sichtbar(dump, "Test bestanden! Was geht, Eike? \U0001F60E"))

    def test_eigener_text_sichtbar_toleriert_tastatur_korrekturen(self):
        # Am Gerät gesehen: getippt „Hey Eike Was gibts“, im Chat steht „Hey Eike  Was gibt's“.
        dump = '<hierarchy><node text="Hey Eike  Was gibt\'s" bounds="[0,0][1,1]"/></hierarchy>'
        self.assertTrue(snap_helfer.eigener_text_sichtbar(dump, "Hey Eike! \U0001F44B Was gibt's?"))

    def test_snap_symbol_ist_keine_textnachricht(self):
        dump = ('<hierarchy><node text="EIKE" bounds="[0,0][1,1]"/>'
                '<node text="snap_envelop" bounds="[0,0][1,1]"/></hierarchy>')
        self.assertEqual(snap_helfer.fremde_texte(dump), [])
        self.assertEqual(snap_helfer.neue_nachrichten(dump, ""), [])

    def test_eigenes_label_ist_kein_text_der_gegenseite(self):
        # Ein Eintrag von Eike ohne Text (z. B. ein Snap-Kasten) und danach eine eigene Zeile: Im Baum folgt auf das
        # Label „EIKE“ direkt das Label „ME“ — das wurde 29.09.2026 als Nachricht „ME“ von Eike gelesen.
        dump = ('<hierarchy><node text="EIKE" bounds="[0,0][1,1]"/><node text="ME" bounds="[0,0][1,1]"/>'
                '<node text="Hallo" bounds="[0,0][1,1]"/></hierarchy>')
        self.assertEqual(snap_helfer.fremde_texte(dump), [])
        self.assertEqual(snap_helfer.neue_nachrichten(dump, ""), [])

    def test_gleichlautende_nachricht_zweimal_ist_zweimal_neu(self):
        dump = ('<hierarchy><node text="EIKE" bounds="[0,0][1,1]"/>'
                '<node text="Hey" bounds="[0,0][1,1]"/></hierarchy>')
        with mock.patch.object(snap_helfer, "schritt_ausfuehren"), \
             mock.patch.object(snap_helfer, "kontakt_chat_oeffnen"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=dump):
            self.assertEqual(snap_helfer._eingang_pruefen_ablauf("")[0], ["Hey"])
            self.assertEqual(snap_helfer._eingang_pruefen_ablauf("Hey")[0], ["Hey"])

    def test_beim_senden_neu_eingetroffene_nachricht_wird_gerettet(self):
        self._konnektor_aktivieren()
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.write_text(json.dumps({"art": "text", "text": "Hi", "bild_pfad": None, "empfaenger": "Eike"}),
                                 encoding="utf-8")

        def senden(bild, text, empfaenger=None):
            snap_helfer._beim_senden_gesehen.append("Noch eine Frage")

        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=senden), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), \
             mock.patch.object(snap_helfer, "gespraech_halten") as m_halten, \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        m_halten.assert_called_once()          # neue Texte → der Gesprächsmodus startet (gemockt: nie am echten Handy)
        eingang = json.loads((snap_helfer.daten_ordner() / "snapchat" / "eingang.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual([e["text"] for e in eingang], ["Noch eine Frage"])

    def test_auftrag_wird_nach_max_versuchen_verworfen(self):
        self._konnektor_aktivieren()
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.write_text(json.dumps({"art": "text", "text": "Hi", "bild_pfad": None, "empfaenger": "Eike",
                                             "versuche": snap_helfer.MAX_VERSUCHE - 1}),
                                 encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden",
                               side_effect=snap_helfer.SnapHelferFehler("kaputt")), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        self.assertFalse(auftrag_datei.exists())

    def test_neu_im_chat_zaehlt_vielfachheit(self):
        self.assertEqual(snap_helfer._neu_im_chat(["Hey", "Hey"], ["Hey"]), ["Hey"])
        self.assertEqual(snap_helfer._neu_im_chat(["A"], ["A"]), [])

    def test_gespraech_sendet_liegenden_auftrag_im_offenen_chat_ohne_neustart(self):
        ordner = snap_helfer.daten_ordner() / "snapchat"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "auftrag.json").write_text(
            json.dumps({"art": "text", "text": "Antwort", "bild_pfad": None, "empfaenger": "Eike"}), encoding="utf-8")
        eigene = []
        uhr = iter([0.0, 1.0, 1.0, 1.0, 1000.0, 1000.0, 1000.0])
        with mock.patch.object(snap_helfer, "_adb"), \
             mock.patch.object(snap_helfer, "_im_offenen_chat_senden") as m_senden, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), \
             mock.patch.object(snap_helfer, "app_zuruecksetzen") as m_reset, \
             mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer.gespraech_halten(eigene, [], jetzt_fn=lambda: next(uhr))
        m_senden.assert_called_once_with("Antwort")
        m_reset.assert_not_called()
        self.assertFalse((ordner / "auftrag.json").exists())
        self.assertEqual(eigene, ["Antwort"])

    def test_gespraech_gibt_bild_auftraege_an_den_normalen_weg(self):
        ordner = snap_helfer.daten_ordner() / "snapchat"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "auftrag.json").write_text(
            json.dumps({"art": "snap", "text": "", "bild_pfad": "x.jpg"}), encoding="utf-8")
        with mock.patch.object(snap_helfer, "_adb"), \
             mock.patch.object(snap_helfer, "_im_offenen_chat_senden") as m_senden:
            snap_helfer.gespraech_halten([], [])
        m_senden.assert_not_called()
        self.assertTrue((ordner / "auftrag.json").exists())

    def test_gespraech_liest_folgenachricht_live_in_den_eingang(self):
        ordner = snap_helfer.daten_ordner() / "snapchat"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "auftrag.json").unlink(missing_ok=True)
        dump = ('<hierarchy><node text="EIKE" bounds="[0,0][1,1]"/>'
                '<node text="Und noch was" bounds="[0,0][1,1]"/></hierarchy>')
        uhr = iter([0.0, 1.0, 1.0, 1.0, 1.0, 1000.0, 1000.0])
        with mock.patch.object(snap_helfer, "_adb"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=dump), \
             mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(uhr))
        eingang = json.loads((ordner / "eingang.json").read_text(encoding="utf-8"))
        self.assertEqual([e["text"] for e in eingang], ["Und noch was"])

    def _gespraech_auftrag(self, auftrag_id="g1"):
        ordner = snap_helfer.daten_ordner() / "snapchat"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "auftrag.json").write_text(json.dumps({"art": "text", "text": "Antwort", "bild_pfad": None,
                                                         "empfaenger": "Eike", "person": None, "id": auftrag_id}), encoding="utf-8")
        return ordner

    def test_gespraech_nutzt_die_kurze_wartezeit(self):
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        (snap_helfer.daten_ordner() / "snapchat" / "auftrag.json").unlink(missing_ok=True)
        uhr = iter([0.0, 11.0])           # nach 11 s ist Schluss, wenn `warte_s=10` gilt; mit 120 s ginge der Takt weiter (StopIteration)
        with mock.patch.object(snap_helfer, "_adb"), mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), \
             mock.patch.object(snap_helfer, "_im_offenen_chat_senden") as senden:
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(uhr), warte_s=10, nachlauf_s=5)
        senden.assert_not_called()

    def test_gespraech_vermerkt_die_id_nach_dem_senden(self):
        self._gespraech_auftrag("g2")
        uhr = iter([0.0, 1.0, 1.0, 1.0, 1000.0, 1000.0, 1000.0])
        with mock.patch.object(snap_helfer, "_adb"), mock.patch.object(snap_helfer, "_im_offenen_chat_senden"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(uhr))
        self.assertTrue(snap_helfer._id_gesendet("g2"))

    def test_gespraech_sendet_eine_schon_gesendete_id_nicht_noch_einmal(self):
        ordner = self._gespraech_auftrag("g3")
        snap_helfer._id_vermerken("g3")
        uhr = iter([0.0, 1.0, 1.0, 1.0, 1000.0, 1000.0, 1000.0])
        with mock.patch.object(snap_helfer, "_adb"), mock.patch.object(snap_helfer, "_im_offenen_chat_senden") as senden, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(uhr))
        senden.assert_not_called()
        self.assertFalse((ordner / "auftrag.json").exists())

    def test_gespraech_unbestaetigt_wird_nie_wiederholt(self):
        """Der alte Weg ließ den Auftrag liegen und sendete ihn über den normalen Weg ein zweites Mal."""
        ordner = self._gespraech_auftrag("g4")
        uhr = iter([0.0, 1.0, 1.0, 1.0, 1000.0, 1000.0, 1000.0])
        with mock.patch.object(snap_helfer, "_adb"), \
             mock.patch.object(snap_helfer, "_im_offenen_chat_senden", side_effect=snap_helfer.SendenUnbestaetigt("nicht bestätigt")), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), mock.patch.object(snap_helfer.time, "sleep"), \
             mock.patch.object(snap_helfer, "app_zuruecksetzen") as reset, mock.patch.object(snap_helfer, "app_starten"):
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(uhr))
        self.assertFalse((ordner / "auftrag.json").exists())
        self.assertTrue(snap_helfer._id_gesendet("g4"))
        reset.assert_called()
        zeilen = (ordner / "ergebnisse.jsonl").read_text(encoding="utf-8")
        self.assertIn("unbestaetigt", zeilen)

    def test_im_offenen_chat_senden_ohne_bestaetigung_wirft_senden_unbestaetigt(self):
        with mock.patch.object(snap_helfer, "schritt_ausfuehren"), mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "chat_senden"), mock.patch.object(snap_helfer, "_pause"), \
             mock.patch.object(snap_helfer.time, "sleep"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value='<hierarchy><node text="Eike" bounds="[0,0][1,1]"/></hierarchy>'):
            with self.assertRaises(snap_helfer.SendenUnbestaetigt):
                snap_helfer._im_offenen_chat_senden("Hi")

    def test_auftrag_wartet_erkennt_bereitliegenden_auftrag(self):
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.unlink(missing_ok=True)
        self.assertFalse(snap_helfer.auftrag_wartet())
        auftrag_datei.write_text(json.dumps({"art": "text", "text": "Hi", "bild_pfad": None, "empfaenger": "Eike"}),
                                 encoding="utf-8")
        self.assertTrue(snap_helfer.auftrag_wartet())

    def test_einmal_ohne_aktiven_konnektor_tut_nichts(self):
        """Eike hat Snapchat auf der Freigaben-Seite ausgeschaltet — der Helfer darf einen noch
        wartenden Auftrag dann nicht mehr ausführen und keine neuen Antworten mehr abholen."""
        self._konnektor_aktivieren(an=False)
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.write_text(json.dumps({"art": "text", "text": "Hi", "bild_pfad": None, "empfaenger": "Eike"}),
                                 encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden") as m_senden, \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        m_senden.assert_not_called()
        self.assertTrue(auftrag_datei.exists())  # bleibt liegen, geht nicht verloren

    def test_einmal_auftrag_bleibt_erhalten_wenn_handy_getrennt(self):
        """Ein Auftrag darf nicht stillschweigend verschwinden, nur weil das Handy gerade nicht dran ist."""
        self._konnektor_aktivieren()
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.write_text(json.dumps({"art": "text", "text": "Hi", "bild_pfad": None, "empfaenger": "Eike"}),
                                 encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=False), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        self.assertTrue(auftrag_datei.exists())
        zustand = json.loads((snap_helfer.daten_ordner() / "snapchat" / "zustand.json")
                             .read_text(encoding="utf-8"))
        self.assertFalse(zustand["verbunden"])

    def test_einmal_legt_bildschirm_am_ende_wieder_schlafen(self):
        self._konnektor_aktivieren()
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen") as m_schlafen:
            snap_helfer.einmal()
        m_schlafen.assert_called_once()


class BildPfadAufloesenTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_relativer_pfad_wird_gegen_host_datenordner_aufgeloest(self):
        (Path(self.ordner) / "bilder").mkdir(parents=True, exist_ok=True)
        ergebnis = snap_helfer._bild_pfad_aufloesen("bilder/abc123.jpg")
        self.assertEqual(ergebnis, (Path(self.ordner) / "bilder" / "abc123.jpg").resolve())

    def test_ausbruchsversuch_wird_abgelehnt(self):
        with self.assertRaises(snap_helfer.SnapHelferFehler):
            snap_helfer._bild_pfad_aufloesen("../../etc/passwd")


class EigeneTexteFilterTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _konnektor_aktivieren(self):
        (snap_helfer.daten_ordner() / "konnektoren.json").write_text(
            json.dumps({"snapchat": {"aktiv": True}}), encoding="utf-8")

    def test_gesendeter_text_wird_gemerkt(self):
        self._konnektor_aktivieren()
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.write_text(json.dumps({"art": "text", "text": "Klar, mach ich!", "bild_pfad": None, "empfaenger": "Eike"}),
                                 encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden"), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        zustand = json.loads((snap_helfer.daten_ordner() / "snapchat" / "zustand.json")
                             .read_text(encoding="utf-8"))
        self.assertIn("Klar, mach ich!", zustand["eigene_texte"])

    def test_eigener_text_wird_nicht_als_eingang_uebernommen(self):
        self._konnektor_aktivieren()
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        (snap_helfer.daten_ordner() / "snapchat" / "zustand.json").write_text(
            json.dumps({"eigene_texte": ["Klar, mach ich!"], "zuletzt_gesehen": "Kommst du mit?"}),
            encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "eingang_pruefen",
                               return_value=(["Klar, mach ich!", "Wie war dein Tag?"], "Wie war dein Tag?")), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), \
             mock.patch.object(snap_helfer, "gespraech_halten"), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        eingang = json.loads((snap_helfer.daten_ordner() / "snapchat" / "eingang.json")
                             .read_text(encoding="utf-8"))
        texte = [e["text"] for e in eingang]
        self.assertNotIn("Klar, mach ich!", texte)
        self.assertIn("Wie war dein Tag?", texte)


class NeueBenachrichtigungDaTest(unittest.TestCase):
    def _erfolg(self, stdout="", returncode=0):
        return subprocess.CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr="")

    DUMP_MIT_NACHRICHT = (
        "    StatusBarNotification(pkg=com.snapchat.android ...)\n"
        "            HistoricalNotification{, key =0|com.snapchat.android|-566339706|null|10289, type =2, "
        "mPostedTimeMs=1790594046342(2026-09-28 13:14:06.342+0200), mIsChecked =false, mUri =null, "
        "mWhen=1790594045918(...), mChannelName='Snaps and Chats', mChannelId='sc_0a1...', mUserId=0}\n"
        "            HistoricalNotification{, key =0|com.snapchat.android|1431325696|null|10289, type =0, "
        "mPostedTimeMs=1790592113649(2026-09-28 12:41:53.649+0200), mIsChecked =true, mUri =null, "
        "mWhen=1790592113373(...), mChannelName='Silent Notifications', mChannelId='general_group_silent', mUserId=0}\n"
    )

    DUMP_NUR_STILL = (
        "            HistoricalNotification{, key =0|com.snapchat.android|1431325696|null|10289, type =0, "
        "mPostedTimeMs=1790592113649(2026-09-28 12:41:53.649+0200), mIsChecked =true, mUri =null, "
        "mWhen=1790592113373(...), mChannelName='Silent Notifications', mChannelId='general_group_silent', mUserId=0}\n"
    )

    def test_erkennt_neue_nachrichten_benachrichtigung(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(self.DUMP_MIT_NACHRICHT)):
            gefunden, neuester = snap_helfer.neue_benachrichtigung_da(0)
        self.assertTrue(gefunden)
        self.assertEqual(neuester, 1790594046342)

    def test_ignoriert_stille_sammelbenachrichtigung(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(self.DUMP_NUR_STILL)):
            gefunden, neuester = snap_helfer.neue_benachrichtigung_da(0)
        self.assertFalse(gefunden)
        self.assertEqual(neuester, 0)

    def test_ignoriert_bereits_gesehene_benachrichtigung(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(self.DUMP_MIT_NACHRICHT)):
            gefunden, neuester = snap_helfer.neue_benachrichtigung_da(1790594046342)
        self.assertFalse(gefunden)
        self.assertEqual(neuester, 1790594046342)

    def test_adb_fehler_gibt_false_ohne_zu_werfen(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=self._erfolg(returncode=1)):
            gefunden, neuester = snap_helfer.neue_benachrichtigung_da(42)
        self.assertFalse(gefunden)
        self.assertEqual(neuester, 42)


import struct
import zlib


def _png(breite, hoehe, pixel):
    """Kleines RGB-PNG ohne Filter; pixel(x, y) -> (r, g, b)."""
    roh = b""
    for y in range(hoehe):
        roh += b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(breite))

    def block(typ, daten):
        c = zlib.crc32(typ + daten) & 0xffffffff
        return struct.pack(">I", len(daten)) + typ + daten + struct.pack(">I", c)

    return (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0))
            + block(b"IDAT", zlib.compress(roh)) + block(b"IEND", b""))


class SnapBildTest(unittest.TestCase):
    def test_zuschnitt_behaelt_nur_den_ausschnitt(self):
        png = _png(4, 4, lambda x, y: (x * 10, y * 10, 0))
        klein = snap_helfer.png_zuschneiden(png, 1, 1, 3, 4)
        breite, hoehe, kanaele, zeilen = snap_helfer._png_zeilen(klein)
        self.assertEqual((breite, hoehe, kanaele), (2, 3, 3))
        self.assertEqual(zeilen[0][:3], bytes((10, 10, 0)))

    def test_zuschnitt_ausserhalb_wirft(self):
        png = _png(4, 4, lambda x, y: (0, 0, 0))
        with self.assertRaises(snap_helfer.SnapHelferFehler):
            snap_helfer.png_zuschneiden(png, 5, 5, 9, 9)

    def test_bildflaeche_aus_dem_baum(self):
        dump = ('<hierarchy><node resource-id="com.snapchat.android:id/base_image_layer_container" '
                'bounds="[0,88][1080,2104]"/></hierarchy>')
        self.assertEqual(snap_helfer.bildflaeche(dump), (0, 88, 1080, 2104))
        self.assertIsNone(snap_helfer.bildflaeche("<hierarchy/>"))

    def test_snap_offen_erkennt_den_betrachter(self):
        offen = '<hierarchy><node resource-id="com.snapchat.android:id/opera_viewer" bounds="[0,0][1,1]"/></hierarchy>'
        self.assertTrue(snap_helfer.snap_offen(offen))
        self.assertFalse(snap_helfer.snap_offen("<hierarchy/>"))

    def test_neuer_snap_knoten_findet_die_zeile(self):
        dump = '<hierarchy><node text="New Snap" bounds="[100,600][300,700]"/></hierarchy>'
        self.assertEqual(snap_helfer.neuer_snap_knoten(dump), (200, 650))
        self.assertIsNone(snap_helfer.neuer_snap_knoten(
            '<hierarchy><node text="Received" bounds="[0,0][2,2]"/></hierarchy>'))


class SnapAbholenTest(unittest.TestCase):
    def test_senden_holt_erst_snaps_dann_oeffnet_es_den_chat(self):
        reihenfolge = []
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(snap_helfer, "schritt_ausfuehren", side_effect=lambda n: reihenfolge.append(n)), \
             mock.patch.object(snap_helfer, "snaps_abholen", side_effect=lambda: reihenfolge.append("snaps") or 0), \
             mock.patch.object(snap_helfer, "kontakt_chat_oeffnen", side_effect=lambda a: reihenfolge.append("kontakt_chat")), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), \
             mock.patch.object(snap_helfer, "text_eingeben"), mock.patch.object(snap_helfer, "chat_senden"), \
             mock.patch.object(snap_helfer, "eigener_text_sichtbar", return_value=True), \
             mock.patch.object(snap_helfer, "_pause"), mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer._chat_text_ablauf("Hi")
        self.assertEqual(reihenfolge[:3], ["chat_tab", "snaps", "kontakt_chat"])

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _eingang(self):
        return json.loads((snap_helfer.daten_ordner() / "snapchat" / "eingang.json").read_text(encoding="utf-8"))

    def test_symbolname_wird_vermerkt_und_nicht_als_text_gelesen(self):
        dump = ('<hierarchy><node text="EIKE" bounds="[0,0][1,1]"/>'
                '<node text="voice_note_bubble" bounds="[0,0][1,1]"/></hierarchy>')
        self.assertEqual(snap_helfer.fremde_texte(dump), [])
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        snap_helfer.symbole_vermerken(dump)
        snap_helfer.symbole_vermerken(dump)
        zeilen = (snap_helfer.daten_ordner() / "snapchat" / "unbekannt.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(zeilen), 1)
        self.assertIn("voice_note_bubble", zeilen[0])

    def test_snap_bild_holen_speichert_zugeschnittenes_png(self):
        dump = ('<hierarchy><node resource-id="com.snapchat.android:id/base_image_layer_container" '
                'bounds="[0,1][4,3]"/></hierarchy>')
        png = _png(4, 4, lambda x, y: (x, y, 0))
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=png):
            name = snap_helfer.snap_bild_holen(dump)
        datei = snap_helfer.daten_ordner() / "snapchat" / "bilder" / name
        self.assertEqual(snap_helfer._png_zeilen(datei.read_bytes())[:2], (4, 2))

    def test_snap_bild_holen_ohne_bildflaeche_wirft(self):
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=b"x"):
            with self.assertRaises(snap_helfer.SnapHelferFehler):
                snap_helfer.snap_bild_holen("<hierarchy/>")

    # --- Chat-Bilder (echter Dump vom 29.09.2026: ein Bild von EIKE steht direkt im Chat) ---
    CHATBILD = (Path(__file__).parent / "daten" / "snap_chatbild.xml").read_text(encoding="utf-8")

    def test_chat_bild_flaechen_findet_das_bild_der_gegenseite(self):
        self.assertEqual(snap_helfer.chat_bild_flaechen(self.CHATBILD), [(45, 311, 423, 983)])

    def test_chat_bild_flaechen_ignoriert_eigene_und_angeschnittene(self):
        eigen = self.CHATBILD.replace(">EIKE<", ">ME<").replace('text="EIKE"', 'text="ME"')
        self.assertEqual(snap_helfer.chat_bild_flaechen(eigen), [])
        angeschnitten = self.CHATBILD.replace("[45,311][423,983]", "[45,311][423,2500]")
        self.assertEqual(snap_helfer.chat_bild_flaechen(angeschnitten), [])
        self.assertEqual(snap_helfer.chat_bild_flaechen("<hierarchy/>"), [])
        self.assertEqual(snap_helfer.chat_bild_flaechen("<kaputt"), [])

    def test_platzhalter_des_eingabefelds_ist_keine_nachricht(self):
        # Der echte Dump: Label „EIKE“, dann ein Bild ohne Text, dann der Platzhalter „Send chat“.
        self.assertIn("Send chat", self.CHATBILD)
        self.assertEqual(snap_helfer.fremde_texte(self.CHATBILD), [])
        self.assertEqual(snap_helfer.neue_nachrichten(self.CHATBILD, ""), [])

    def test_chat_bilder_holen_einmal_je_bild(self):
        png = _png(500, 1000, lambda x, y: (x % 256, y % 256, 0))
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=png):
            self.assertEqual(snap_helfer.chat_bilder_holen(self.CHATBILD), 1)
            self.assertEqual(snap_helfer.chat_bilder_holen(self.CHATBILD), 0)  # steht noch im Chat
        eingang = snap_helfer._lesen("eingang.json", [])
        self.assertEqual([e["art"] for e in eingang], ["chatbild"])
        datei = snap_helfer.daten_ordner() / "snapchat" / "bilder" / eingang[0]["bild"]
        self.assertEqual(snap_helfer._png_zeilen(datei.read_bytes())[:2], (378, 672))
        with mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=png):
            snap_helfer.chat_bilder_holen("<hierarchy/>")  # Chat leer -> Merkzeichen zurückgesetzt
            self.assertEqual(snap_helfer.chat_bilder_holen(self.CHATBILD), 1)  # gleiches Bild, neu geschickt

    def test_snaps_abholen_oeffnet_snap_holt_bild_und_geht_weiter(self):
        liste = mit_snap()
        betrachter = ('<hierarchy><node resource-id="com.snapchat.android:id/opera_viewer" bounds="[0,0][1,1]"/>'
                      '<node resource-id="com.snapchat.android:id/base_image_layer_container" '
                      'bounds="[0,1][4,3]"/></hierarchy>')
        leer_liste = '<hierarchy><node text="Received" bounds="[0,0][2,2]"/></hierarchy>'
        dumps = iter([liste, betrachter, leer_liste])
        png = _png(4, 4, lambda x, y: (x, y, 0))
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=lambda: next(dumps)), \
             mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=png), \
             mock.patch.object(snap_helfer, "bildschirmgroesse", return_value=(1080, 2400)), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen, \
             mock.patch.object(snap_helfer, "_auf_snap_warten"), \
             mock.patch.object(snap_helfer, "_pause"):
            anzahl = snap_helfer.snaps_abholen()
        self.assertEqual(anzahl, 1)
        self.assertEqual(m_tippen.call_args_list[0].args, snap_helfer.neuer_snap_knoten(liste, "Eike"))
        eintrag = self._eingang()[0]
        self.assertEqual(eintrag["art"], "snap")
        self.assertTrue(eintrag["bild"].endswith(".png"))
        self.assertFalse(eintrag["gelesen"])

    def test_snaps_abholen_ohne_bild_schreibt_fehlereintrag(self):
        liste = mit_snap()
        betrachter = '<hierarchy><node resource-id="com.snapchat.android:id/opera_viewer" bounds="[0,0][1,1]"/></hierarchy>'
        dumps = iter([liste, betrachter, "<hierarchy/>"])
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=lambda: next(dumps)), \
             mock.patch.object(snap_helfer, "bildschirmgroesse", return_value=(1080, 2400)), \
             mock.patch.object(snap_helfer, "tippen"), mock.patch.object(snap_helfer, "_pause"), \
             mock.patch.object(snap_helfer, "_auf_snap_warten"):
            snap_helfer.snaps_abholen()
        eintrag = self._eingang()[0]
        self.assertIsNone(eintrag["bild"])
        self.assertTrue(eintrag["fehler"])

    def test_auf_snap_warten_endet_sobald_der_betrachter_offen_ist(self):
        offen = '<hierarchy><node resource-id="com.snapchat.android:id/opera_viewer" bounds="[0,0][1,1]"/></hierarchy>'
        dumps = iter(["<hierarchy/>", "<hierarchy/>", offen])
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=lambda: next(dumps)), \
             mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer._auf_snap_warten()  # kommt zurück, ohne alle Versuche zu brauchen

    def test_auf_snap_warten_gibt_nach_der_ladezeit_auf(self):
        uhr = iter([0.0, 0.0, 6.0, 12.0, 12.0])
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), \
             mock.patch.object(snap_helfer.time, "sleep"), \
             mock.patch.object(snap_helfer.time, "time", side_effect=lambda: next(uhr)):
            snap_helfer._auf_snap_warten()

    def test_snap_ohne_erkannten_betrachter_hinterlaesst_einen_fehlereintrag(self):
        liste = mit_snap()
        dumps = iter([liste, "<hierarchy/>", "<hierarchy/>"])  # nach dem Tipp: weder Betrachter noch „New Snap“
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=lambda: next(dumps)), \
             mock.patch.object(snap_helfer, "tippen"), \
             mock.patch.object(snap_helfer, "_auf_snap_warten", return_value=False):
            self.assertEqual(snap_helfer.snaps_abholen(), 1)
        eintrag = self._eingang()[0]
        self.assertIsNone(eintrag["bild"])
        self.assertIn("nicht erkannt", eintrag["fehler"])

    def test_tipp_ohne_wirkung_wird_wiederholt_statt_gemeldet(self):
        liste = mit_snap()
        betrachter = '<hierarchy><node resource-id="com.snapchat.android:id/opera_viewer" bounds="[0,0][1,1]"/></hierarchy>'
        # 1. Tipp greift nicht (Liste bleibt), 2. Tipp öffnet, dann leere Liste.
        dumps = iter([liste, liste, liste, betrachter, "<hierarchy/>"])
        antworten = iter([False, True])
        with mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=lambda: next(dumps)), \
             mock.patch.object(snap_helfer, "bildschirmgroesse", return_value=(1080, 2400)), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen, mock.patch.object(snap_helfer, "_pause"), \
             mock.patch.object(snap_helfer, "_auf_snap_warten", side_effect=lambda: next(antworten)):
            snap_helfer.snaps_abholen()
        self.assertGreaterEqual(len(m_tippen.call_args_list), 2)
        self.assertEqual(self._eingang()[0]["art"], "snap")

    def test_snaps_abholen_ohne_neuen_snap_tut_nichts(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), \
             mock.patch.object(snap_helfer, "tippen") as m_tippen:
            self.assertEqual(snap_helfer.snaps_abholen(), 0)
        m_tippen.assert_not_called()

    def test_eingang_pruefen_ablauf_holt_erst_snaps_dann_texte(self):
        reihenfolge = []
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(snap_helfer, "schritt_ausfuehren", side_effect=lambda n: reihenfolge.append(n)), \
             mock.patch.object(snap_helfer, "snaps_abholen", side_effect=lambda: reihenfolge.append("snaps") or 0), \
             mock.patch.object(snap_helfer, "kontakt_chat_oeffnen", side_effect=lambda a: reihenfolge.append("kontakt_chat")), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"):
            snap_helfer._eingang_pruefen_ablauf("")
        self.assertEqual(reihenfolge, ["chat_tab", "snaps", "kontakt_chat"])

    def test_gespraech_endet_wenn_im_offenen_chat_ein_snap_wartet(self):
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        dump = ('<hierarchy><node text="EIKE" bounds="[0,0][1,1]"/>'
                '<node text="snap_envelop" bounds="[0,0][1,1]"/></hierarchy>')
        uhr = iter([0.0, 1.0, 1.0, 1.0])
        with mock.patch.object(snap_helfer, "_adb"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=dump), \
             mock.patch.object(snap_helfer.time, "sleep") as m_schlaf:
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(uhr))
        m_schlaf.assert_not_called()


class SchlangeTest(unittest.TestCase):
    """Der Helfer arbeitet einen Auftrag nach dem anderen ab; der Dienst legt wartende in `warteschlange/`."""

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()
        self.snap = snap_helfer.daten_ordner() / "snapchat"
        (self.snap / "warteschlange").mkdir(parents=True)
        (self.snap / "konnektoren.json").write_text("{}")
        (snap_helfer.daten_ordner() / "konnektoren.json").write_text(
            json.dumps({"snapchat": {"aktiv": True}}), encoding="utf-8")

    def tearDown(self):
        self.env.stop()

    def _warten_lassen(self, name, text):
        (self.snap / "warteschlange" / name).write_text(
            json.dumps({"art": "text", "text": text, "bild_pfad": None, "empfaenger": "Eike"}), encoding="utf-8")

    def test_aeltester_wartender_ruecken_auf_den_freien_platz(self):
        self._warten_lassen("2.json", "zweiter")
        self._warten_lassen("1.json", "erster")
        snap_helfer._naechsten_nachruecken()
        self.assertEqual(json.loads((self.snap / "auftrag.json").read_text())["text"], "erster")
        self.assertEqual([d.name for d in (self.snap / "warteschlange").glob("*.json")], ["2.json"])

    def test_belegter_platz_wird_nie_ueberschrieben(self):
        (self.snap / "auftrag.json").write_text(json.dumps({"art": "text", "text": "laufend"}))
        self._warten_lassen("1.json", "wartet")
        snap_helfer._naechsten_nachruecken()
        self.assertEqual(json.loads((self.snap / "auftrag.json").read_text())["text"], "laufend")
        self.assertTrue((self.snap / "warteschlange" / "1.json").exists())

    def test_kaputter_wartender_blockiert_nichts(self):
        (self.snap / "warteschlange" / "0.json").write_text("{kaputt")
        self._warten_lassen("1.json", "gut")
        snap_helfer._naechsten_nachruecken()
        self.assertEqual(json.loads((self.snap / "auftrag.json").read_text())["text"], "gut")
        self.assertFalse((self.snap / "warteschlange" / "0.json").exists())

    def test_auftrag_erledigt_gibt_den_platz_frei_und_laesst_den_naechsten_nachruecken(self):
        (self.snap / "auftrag.json").write_text(json.dumps({"art": "text", "text": "fertig"}))
        self._warten_lassen("1.json", "danach")
        snap_helfer._auftrag_erledigt()
        self.assertEqual(json.loads((self.snap / "auftrag.json").read_text())["text"], "danach")

    def test_auftrag_wartet_zaehlt_auch_die_schlange(self):
        self.assertFalse(snap_helfer.auftrag_wartet())
        self._warten_lassen("1.json", "wartet")
        self.assertTrue(snap_helfer.auftrag_wartet())

    def test_einmal_sendet_nacheinander_erst_der_erste_dann_der_zweite(self):
        (self.snap / "auftrag.json").write_text(json.dumps({"art": "text", "text": "eins", "bild_pfad": None, "empfaenger": "Eike"}))
        self._warten_lassen("1.json", "zwei")
        gesendet = []
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=lambda b, t, e=None: gesendet.append(t)), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
            self.assertEqual(gesendet, ["eins"])                    # ein Auftrag je Lauf
            self.assertEqual(json.loads((self.snap / "auftrag.json").read_text())["text"], "zwei")
            snap_helfer.einmal()
        self.assertEqual(gesendet, ["eins", "zwei"])
        self.assertFalse((self.snap / "auftrag.json").exists())


class WachzeitenTest(unittest.TestCase):
    """24/7-Schalter (Freigaben-Seite): aus = Ruhe 23–7 Uhr, an = immer wach."""

    def setUp(self):
        import tempfile
        from unittest import mock
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _berlin(self, stunde, minute=0):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 10, 2, stunde, minute, tzinfo=ZoneInfo("Europe/Berlin"))

    def test_standard_ist_ruhe_nachts(self):
        self.assertFalse(snap_helfer.rund_um_die_uhr())
        self.assertFalse(snap_helfer.ist_wach(self._berlin(23, 30)))
        self.assertTrue(snap_helfer.ist_wach(self._berlin(22)))

    def test_schalter_waeckt_auch_nachts(self):
        snap_helfer._schreiben("wachzeiten.json", {"rund_um_die_uhr": True})
        self.assertTrue(snap_helfer.ist_wach(self._berlin(3)))


DUMP_SUCHE_LEER = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="Search" resource-id="com.snapchat.android:id/search" bounds="[0,100][500,200]" />
</hierarchy>"""

DUMP_SUCHE_EIN_TREFFER = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="Search" resource-id="com.snapchat.android:id/search" bounds="[0,100][500,200]" />
  <node text="mara.v" resource-id="" bounds="[0,300][400,400]" />
  <node text="Add" resource-id="" bounds="[700,310][900,390]" />
</hierarchy>"""

DUMP_SUCHE_ZWEI_TREFFER = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="Search" resource-id="com.snapchat.android:id/search" bounds="[0,100][500,200]" />
  <node text="mara.v" resource-id="" bounds="[0,300][400,400]" />
  <node text="Add" resource-id="" bounds="[700,310][900,390]" />
  <node text="MARA.V" resource-id="" bounds="[0,500][400,600]" />
  <node text="Add" resource-id="" bounds="[700,510][900,590]" />
</hierarchy>"""

DUMP_SUCHE_ANGENOMMEN = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="mara.v" resource-id="" bounds="[0,300][400,400]" />
  <node text="Pending" resource-id="" bounds="[700,310][900,390]" />
</hierarchy>"""

DUMP_SUCHE_ECHO_UND_TREFFER = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="mara.v" resource-id="search-textfield" bounds="[0,100][500,200]" />
  <node text="mara.v" resource-id="" bounds="[0,300][400,400]" />
  <node text="Add" resource-id="" bounds="[700,310][900,390]" />
</hierarchy>"""

DUMP_SUCHE_FREMDER_DANEBEN = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="mara.v" resource-id="" bounds="[0,300][400,400]" />
  <node text="Add" resource-id="" bounds="[700,310][900,390]" />
  <node text="Fremder Name" resource-id="" bounds="[0,330][400,420]" />
</hierarchy>"""

DUMP_SUCHE_UNKLAR = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="mara.v" resource-id="" bounds="[0,300][400,400]" />
</hierarchy>"""

# Echter Suche-Dump vom 04.10.2026 (Namen gekürzt): „From Contacts“ lädt per
# SMS/WhatsApp ein (nie antippen), „Add Friends“ ist die echte Anfrage-Zeile —
# deren Knopf hat keinen Text, nur content-desc „Add“.
DUMP_SUCHE_ECHT = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="tim_kudla" resource-id="search-textfield" bounds="[148,120][751,217]" />
  <node text="From Contacts" resource-id="section-title" bounds="[42,234][349,297]" />
  <node text="Kontakt A" resource-id="result-title" bounds="[221,434][435,497]" />
  <node text="tim_kudla" resource-id="result-subtitle" bounds="[221,500][375,549]" />
  <node text="" resource-id="add-friend-button" content-desc="Add" bounds="[769,469][1006,556]" />
  <node text="Add Friends" resource-id="section-title" bounds="[42,626][300,689]" />
  <node text="Kontakt A" resource-id="result-title" bounds="[221,723][435,786]" />
  <node text="tim_kudla" resource-id="result-subtitle" bounds="[221,789][375,838]" />
  <node text="" resource-id="add-friend-button" content-desc="Add" bounds="[769,758][1006,845]" />
</hierarchy>"""

DUMP_SUCHE_ECHT_ANGENOMMEN = DUMP_SUCHE_ECHT.replace(
    '  <node text="" resource-id="add-friend-button" content-desc="Add" bounds="[769,758][1006,845]" />',
    '  <node text="Pending" resource-id="" bounds="[769,758][1006,845]" />')


class AnfrageTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_ohne_treffer_kein_antippen_verworfen(self):
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            with self.assertRaises(snap_helfer.AuftragVerworfen):
                snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "Niemand Xyz"},
                                               dump_xml=DUMP_SUCHE_LEER)
        tippen.assert_not_called()

    def test_treffer_unter_friends_und_groups_meldet_schon_befreundet(self):
        dump = ("<hierarchy>"
                '<node text="kontakt_m" content-desc="kontakt_m" resource-id="com.snap.android:id/search_input" bounds="[148,120][751,217]"/>'
                '<node text="Friends &amp; Groups" content-desc="Friends &amp; Groups" bounds="[42,234][412,297]"/>'
                '<node text="Kontakt M" content-desc="Kontakt M" resource-id="result-title" bounds="[221,340][576,403]"/>'
                '<node text="kontakt_m" content-desc="kontakt_m" resource-id="result-subtitle" bounds="[270,403][513,470]"/>'
                "</hierarchy>")
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            with self.assertRaises(snap_helfer.AuftragVerworfen) as ctx:
                snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "kontakt_m"}, dump_xml=dump)
        self.assertTrue(str(ctx.exception).startswith(snap_helfer.SCHON_BEFREUNDET))
        tippen.assert_not_called()

    def test_zwei_treffer_verworfen_ohne_antippen(self):
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            with self.assertRaises(snap_helfer.AuftragVerworfen):
                snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "mara.v"},
                                               dump_xml=DUMP_SUCHE_ZWEI_TREFFER)
        tippen.assert_not_called()

    def test_ein_treffer_tippt_hinzufuegen_und_bestaetigt(self):
        dumps = [DUMP_SUCHE_EIN_TREFFER, DUMP_SUCHE_ANGENOMMEN]
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben") as eingabe, \
             mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=dumps), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "mara.v"})
        eingabe.assert_called_once_with("mara.v")
        tippen.assert_called_once_with(800, 350)

    def test_umlaut_name_geht_ueber_adb_tastatur_nicht_input_text(self):
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben") as eingabe, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=DUMP_SUCHE_LEER):
            with self.assertRaises(snap_helfer.AuftragVerworfen):
                snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "Jöhnä"})
        eingabe.assert_called_once_with("Jöhnä")

    def test_treffervergleich_ist_normiert(self):
        self.assertEqual(len(snap_helfer.anfrage_treffer(DUMP_SUCHE_EIN_TREFFER, "MARA.V")), 1)
        self.assertEqual(snap_helfer.anfrage_treffer(DUMP_SUCHE_EIN_TREFFER, "Andre"), [])

    def test_suchecho_zaehlt_nicht_als_zweiter_treffer(self):
        self.assertEqual(len(snap_helfer.anfrage_treffer(DUMP_SUCHE_ECHO_UND_TREFFER, "mara.v")), 1)

    def test_fremde_zeile_im_band_bricht_vor_dem_antippen_ab(self):
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            with self.assertRaises(snap_helfer.AuftragVerworfen):
                snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "mara.v"},
                                               dump_xml=DUMP_SUCHE_FREMDER_DANEBEN)
        tippen.assert_not_called()

    def test_unklarer_zustand_ist_kein_erfolg_sondern_neuversuch(self):
        dumps = [DUMP_SUCHE_EIN_TREFFER, DUMP_SUCHE_UNKLAR]
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=dumps), \
             mock.patch.object(snap_helfer, "tippen"):
            try:
                snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "mara.v"})
            except snap_helfer.AuftragVerworfen:
                self.fail("unklarer Zustand darf nicht still verworfen werden")
            except snap_helfer.SnapHelferFehler:
                pass
            else:
                self.fail("unklarer Zustand darf nicht als Erfolg gelten")

    def test_echter_dump_nur_add_friends_zeile_zaehlt(self):
        # Gleicher Name in beiden Sektionen — nur die unter „Add Friends“ zählt.
        treffer = snap_helfer.anfrage_treffer(DUMP_SUCHE_ECHT, "tim_kudla")
        self.assertEqual(len(treffer), 1)
        self.assertGreater(treffer[0][1], 689)   # unter dem Sektionstitel

    def test_echter_dump_knopf_ohne_text_wird_gefunden(self):
        treffer = snap_helfer.anfrage_treffer(DUMP_SUCHE_ECHT, "tim_kudla")
        knopf = snap_helfer.hinzufuegen_knopf(DUMP_SUCHE_ECHT, treffer[0][1])
        self.assertIsNotNone(knopf)
        self.assertTrue(769 <= knopf[0] <= 1006)   # auf dem Add-Knopf, nicht auf der Zeile

    def test_echter_dump_eigene_reason_zeile_toetet_nichts(self):
        # Gespräch 439: „IN MY CONTACTS“ der eigenen Zeile brach als „fremd“ ab.
        treffer = snap_helfer.anfrage_treffer(DUMP_SUCHE_ECHT, "tim_kudla")
        fremd = snap_helfer._band_fremde_namen(
            DUMP_SUCHE_ECHT, treffer[0][1], snap_helfer._norm("tim_kudla"),
            snap_helfer._eigene_zeile_namen(DUMP_SUCHE_ECHT, treffer[0][1],
                                            snap_helfer._norm("tim_kudla")))
        self.assertEqual(fremd, [])

    def test_echter_dump_komplettlauf_tippt_add_friends_knopf(self):
        dumps = [DUMP_SUCHE_ECHT, DUMP_SUCHE_ECHT_ANGENOMMEN]
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=dumps), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "tim_kudla"})
        tippen.assert_called_once_with(887, 801)   # Add der Add-Friends-Zeile, nie die SMS-Reihe

    def test_nur_from_contacts_ist_kein_treffer_kein_sms_tipp(self):
        nur_sms = DUMP_SUCHE_ECHT.replace(
            '  <node text="Add Friends" resource-id="section-title" bounds="[42,626][300,689]" />\n', "")
        self.assertEqual(snap_helfer.anfrage_treffer(nur_sms, "tim_kudla"), [])
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben"), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            with self.assertRaises(snap_helfer.AuftragVerworfen):
                snap_helfer.anfrage_abarbeiten({"art": "anfrage", "empfaenger": "tim_kudla"},
                                               dump_xml=nur_sms)
        tippen.assert_not_called()

    def test_einmal_arbeitet_anfrage_ab_und_protokolliert_gesendet(self):
        (snap_helfer.daten_ordner() / "konnektoren.json").write_text(
            json.dumps({"snapchat": {"aktiv": True}}), encoding="utf-8")
        auftrag_datei = snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"
        auftrag_datei.parent.mkdir(parents=True, exist_ok=True)
        auftrag_datei.write_text(json.dumps({"art": "anfrage", "empfaenger": "mara.v"}), encoding="utf-8")
        with mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "anfrage_abarbeiten") as abarbeiten, \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        abarbeiten.assert_called_once()
        self.assertFalse(auftrag_datei.exists())

class SucheTest(unittest.TestCase):
    """Handy-Suche ohne Antippen (04.10.2026): sammeln, ablegen, nie Add tippen."""

    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.ordner})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_echter_dump_gibt_genau_einen_kandidaten(self):
        kandidaten, ohne, hinweis = snap_helfer.suchkandidaten(DUMP_SUCHE_ECHT)
        self.assertEqual(len(kandidaten), 1)
        self.assertEqual(kandidaten[0]["name"], "tim_kudla")
        self.assertEqual(kandidaten[0]["titel"], "Kontakt A")
        self.assertEqual(hinweis, "")

    def test_zeile_ohne_knopf_zaehlt_nur(self):
        dump = DUMP_SUCHE_ECHT.replace('content-desc="Add"', 'content-desc=""')
        kandidaten, ohne, _ = snap_helfer.suchkandidaten(dump)
        self.assertEqual(kandidaten, [])
        self.assertGreaterEqual(ohne, 1)

    def test_mehr_als_fuenf_wird_begrenzt(self):
        zeilen = ""
        for i in range(7):
            y = 800 + i * 100
            zeilen += (f'  <node text="Name{i}" resource-id="result-title" bounds="[221,{y}][435,{y + 60}]" />\n'
                       f'  <node text="name{i}" resource-id="result-subtitle" bounds="[221,{y + 65}][375,{y + 110}]" />\n'
                       f'  <node text="" resource-id="add-friend-button" content-desc="Add" bounds="[769,{y}][1006,{y + 85}]" />\n')
        dump = ('<?xml version="1.0" encoding="UTF-8"?>\n<hierarchy>\n'
                '  <node text="Add Friends" resource-id="section-title" bounds="[42,626][300,689]" />\n'
                + zeilen + '</hierarchy>')
        kandidaten, _, _ = snap_helfer.suchkandidaten(dump)
        self.assertEqual(len(kandidaten), snap_helfer.SUCH_TREFFER_MAX)

    def test_nur_from_contacts_gibt_hinweis_statt_kandidaten(self):
        nur_sms = DUMP_SUCHE_ECHT.replace(
            '  <node text="Add Friends" resource-id="section-title" bounds="[42,626][300,689]" />\n', "")
        kandidaten, _, hinweis = snap_helfer.suchkandidaten(nur_sms)
        self.assertEqual(kandidaten, [])
        self.assertIn("SMS", hinweis)

    def test_suchauftrag_legt_ab_ohne_anzutippen(self):
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben") as eingabe, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=DUMP_SUCHE_ECHT), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            anzahl = snap_helfer.suchauftrag_abarbeiten({"art": "suche", "empfaenger": "tim_kudla"})
        self.assertEqual(anzahl, 1)
        eingabe.assert_called_once_with("tim_kudla")
        tippen.assert_not_called()
        abgelegt = snap_helfer._lesen("suchergebnisse.json", {})
        self.assertEqual(abgelegt[snap_helfer._norm("tim_kudla")]["kandidaten"][0]["name"], "tim_kudla")

    HINWEIS = ('<hierarchy><node text="TIM" bounds="[0,0][10,10]" />'
               '<node text="@Kontakt A has not added you as a friend yet. They won’t be able to see your messages '
               'until they do." bounds="[0,10][10,20]" /></hierarchy>')

    def test_einseitig_hinweis_ist_keine_nachricht_sondern_der_beweis(self):
        """Gespräch 448: Snapchats Systemzeile wurde als Tims Text gelesen, Friday antwortete darauf."""
        self.assertTrue(snap_helfer.einseitig(self.HINWEIS))
        self.assertEqual(snap_helfer.fremde_texte(self.HINWEIS), [])
        self.assertFalse(snap_helfer.einseitig('<hierarchy><node text="TIM" /><node text="Moin" /></hierarchy>'))

    LISTE = ('<hierarchy>'
             '<node resource-id="x:id/ff_item" content-desc="Opened" bounds="[0,374][1080,550]">'
             '<node text="Kontakt C" bounds="[0,374][140,441]" />'
             '<node content-desc="Camera Reply" bounds="[909,430][1062,493]" /></node>'
             '<node resource-id="x:id/ff_item" content-desc="Delivered" bounds="[0,553][1080,729]">'
             '<node text="Kontakt A" bounds="[0,553][226,620]" />'
             '<node text="feed_cross_button" resource-id="" bounds="[909,609][1062,672]" /></node>'
             '<node resource-id="x:id/ff_item" content-desc="Tap to view" bounds="[0,732][1080,908]">'
             '<node text="Team Snapchat" bounds="[0,732][346,799]" />'
             '<node text="feed_chat_button" resource-id="" bounds="[909,788][1062,851]" /></node>'
             '</hierarchy>')

    def test_chatliste_zeigt_freunde_und_einseitige(self):
        """Live 04.10.2026: Kamera-Knopf = gegenseitig, Kreuz = hat Friday nicht hinzugefügt."""
        self.assertEqual(snap_helfer.freundschaft_aus_liste(self.LISTE),
                         {snap_helfer._norm("Kontakt C"): "befreundet", snap_helfer._norm("Kontakt A"): "einseitig"})

    def test_freundschaft_wird_vermerkt(self):
        snap_helfer.freundschaft_vermerken("Kontakt A", True)
        e = snap_helfer._lesen("freundschaft.json", {})[snap_helfer._norm("Kontakt A")]
        self.assertEqual(e["stand"], "einseitig")
        snap_helfer.freundschaft_vermerken("Kontakt A", False)
        self.assertEqual(snap_helfer._lesen("freundschaft.json", {})[snap_helfer._norm("Kontakt A")]["stand"], "kein_hinweis")

    def test_suchbegriffe_ganzer_name_dann_woerter(self):
        self.assertEqual(snap_helfer.suchbegriffe("Kontakt A"), ["Kontakt A", "Kontakt"])
        self.assertEqual(snap_helfer.suchbegriffe("kontakt_a_kudla"), ["kontakt_a_kudla", "kontakt", "kudla"])
        self.assertEqual(snap_helfer.suchbegriffe("Kontakt S"), ["Kontakt S", "Kontakt"])

    def test_leere_volle_suche_faellt_auf_vorname_zurueck(self):
        leer = '<hierarchy><node text="Add Friends" resource-id="section-title" bounds="[42,626][300,689]" /></hierarchy>'
        dumps = [leer, DUMP_SUCHE_ECHT]
        with mock.patch.object(snap_helfer, "anfrage_suche_oeffnen"), \
             mock.patch.object(snap_helfer, "text_eingeben") as eingabe, \
             mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=dumps), \
             mock.patch.object(snap_helfer, "tippen") as tippen:
            anzahl = snap_helfer.suchauftrag_abarbeiten({"art": "suche", "empfaenger": "Kontakt A"})
        self.assertEqual(anzahl, 1)
        self.assertEqual([c.args[0] for c in eingabe.call_args_list], ["Kontakt A", "Kontakt"])
        tippen.assert_not_called()
        eintrag = snap_helfer._lesen("suchergebnisse.json", {})[snap_helfer._norm("Kontakt A")]
        self.assertIn("„Kontakt A“", eintrag["hinweis"])

    def test_suche_ohne_name_wird_verworfen(self):
        with self.assertRaises(snap_helfer.AuftragVerworfen):
            snap_helfer.suchauftrag_abarbeiten({"art": "suche", "empfaenger": "  "})


class ZufallTest(unittest.TestCase):
    """07.10.2026, Spec Punkt 4: Tipp-Zufall klein und nie außerhalb des Elements, Pausen kurz aber zufällig."""

    def setUp(self):
        p = mock.patch.object(snap_helfer, "ZUFALL_MAX_PX", 8)
        p.start()
        self.addCleanup(p.stop)

    def test_punkt_bleibt_im_element_und_in_den_grenzen(self):
        import random
        zufall = random.Random(7)
        for box in [(0, 100, 1080, 260), (10, 10, 30, 30), (500, 500, 501, 501), (0, 0, 400, 12)]:
            x1, y1, x2, y2 = box
            mx, my = (x1 + x2) // 2, (y1 + y2) // 2
            for _ in range(500):
                x, y = snap_helfer.zufallspunkt(box, zufall)
                self.assertTrue(x1 <= x <= x2 and y1 <= y <= y2, (box, x, y))
                self.assertLessEqual(abs(x - mx), 8)
                self.assertLessEqual(abs(y - my), 8)
                self.assertLessEqual(abs(x - mx), int((x2 - x1) / 2 * snap_helfer.ZUFALL_ANTEIL))

    def test_punkt_streut_wirklich(self):
        import random
        zufall = random.Random(1)
        punkte = {snap_helfer.zufallspunkt((0, 0, 400, 400), zufall) for _ in range(200)}
        self.assertGreater(len(punkte), 20)

    def test_ohne_zufall_genau_die_mitte(self):
        with mock.patch.object(snap_helfer, "ZUFALL_MAX_PX", 0):
            self.assertEqual(snap_helfer.zufallspunkt((0, 0, 100, 50)), (50, 25))

    def test_bounds_mitte_bleibt_exakt_nur_tipp_ziele_streuen(self):
        """Review 07.10.2026: `bounds_mitte` dient auch als Messwert (Bänder, Sektionen) — dort darf nichts würfeln."""
        self.assertEqual(snap_helfer.bounds_mitte("[0,0][400,400]"), (200, 200))
        self.assertIsNone(snap_helfer.bounds_mitte("kaputt"))
        dump = '<hierarchy><node text="X" bounds="[0,0][400,400]"/></hierarchy>'
        self.assertEqual(snap_helfer.element_finden(dump, text="X"), (200, 200))
        punkte = {snap_helfer.element_finden(dump, text="X", zufall=True) for _ in range(100)}
        self.assertGreater(len(punkte), 5)
        self.assertTrue(all(abs(x - 200) <= 8 and abs(y - 200) <= 8 for x, y in punkte))

    def test_schritt_ausfuehren_tippt_mit_zufall(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<x/>"), \
             mock.patch.object(snap_helfer, "element_finden", return_value=(5, 5)) as ef, \
             mock.patch.object(snap_helfer, "tippen"), mock.patch.object(snap_helfer, "_pause"):
            snap_helfer.schritt_ausfuehren("chat_tab")
        self.assertIs(ef.call_args.kwargs.get("zufall"), True)

    def test_chat_zeilen_nutzen_den_zufall(self):
        dump = ('<hierarchy><node resource-id="com.snapchat.android:id/ff_item" content-desc="Received" bounds="[0,200][1080,360]">'
                '<node text="Kontakt B" bounds="[0,200][400,260]"/></node></hierarchy>')
        with mock.patch.object(snap_helfer, "zufallspunkt", return_value=(7, 8)):
            self.assertEqual(snap_helfer.chat_zeilen(dump)[0]["mitte"], (7, 8))

    def test_verdrehte_grenzen_werfen_keinen_valueerror(self):
        x, y = snap_helfer.zufallspunkt((100, 100, 80, 80))
        self.assertIsInstance(x, int)

    def test_pausen_sind_kurz_und_zufaellig(self):
        self.assertEqual(snap_helfer.PAUSE_S, (0.4, 1.0))
        with mock.patch.object(snap_helfer.time, "sleep") as schlaf:
            for _ in range(50):
                snap_helfer._pause()
        zeiten = [a.args[0] for a in schlaf.call_args_list]
        self.assertTrue(all(0.4 <= t <= 1.0 for t in zeiten))
        self.assertGreater(len(set(zeiten)), 10)


class ScrollenTest(unittest.TestCase):
    """07.10.2026, Spec Punkt 2: bei Rückstau bis zu 3 Seiten der Chatliste, jeder Name höchstens einmal je Durchgang."""

    @staticmethod
    def _zeile(name, status="New Chat"):
        return {"anzeige": name, "status": status, "box": (0, 0, 10, 10), "mitte": (5, 5)}

    def _lesen(self, seiten, heimat=True, kontakte=None, ereignisse=None):
        ereignisse = ereignisse if ereignisse is not None else []
        gelesen, stand = [], {"seite": 0}
        kontakte = kontakte or {f"k{i}": f"p{i}" for i in range(10)}
        with mock.patch.object(snap_helfer, "chat_zeilen", side_effect=lambda _: seiten[min(stand["seite"], len(seiten) - 1)]), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<x/>"), \
             mock.patch.object(snap_helfer, "erlaubte_kontakte", return_value=kontakte), \
             mock.patch.object(snap_helfer, "heimat_aktiv", return_value=heimat), \
             mock.patch.object(snap_helfer, "_zur_chatliste", side_effect=lambda: ereignisse.append("liste")), \
             mock.patch.object(snap_helfer, "_liste_nach_oben", side_effect=lambda: (stand.update(seite=0), ereignisse.append("oben"))), \
             mock.patch.object(snap_helfer, "_liste_wischen", side_effect=lambda: (stand.update(seite=stand["seite"] + 1), ereignisse.append("wischen"))) as wischen, \
             mock.patch.object(snap_helfer, "kontakt_lesen", side_effect=lambda z, p: gelesen.append(z["anzeige"]) or 1), \
             mock.patch.object(snap_helfer, "unbekannte_vermerken"), \
             mock.patch.object(snap_helfer, "_pause"):
            snap_helfer._kontakte_lesen()
        return gelesen, wischen

    def test_weitere_seite_nur_wenn_unterste_zeile_neu(self):
        neu, alt = self._zeile("A"), self._zeile("B", "Received")
        self.assertTrue(snap_helfer.braucht_weitere_seite([alt, neu]))
        self.assertFalse(snap_helfer.braucht_weitere_seite([neu, alt]))
        self.assertFalse(snap_helfer.braucht_weitere_seite([]))

    def test_liest_drei_seiten_und_jeden_namen_einmal(self):
        seiten = [[self._zeile(f"K{i}") for i in range(a, a + 2)] for a in (0, 2, 4)]
        gelesen, wischen = self._lesen(seiten)
        self.assertEqual(sorted(gelesen), [f"K{i}" for i in range(6)])
        self.assertEqual(len(gelesen), len(set(gelesen)))

    def test_ohne_heimat_nur_die_erste_seite(self):
        seiten = [[self._zeile("K0"), self._zeile("K1")], [self._zeile("K2")]]
        gelesen, wischen = self._lesen(seiten, heimat=False)
        self.assertEqual(sorted(gelesen), ["K0", "K1"])
        wischen.assert_not_called()

    def test_nie_endlosschleife_bei_immer_neuer_unterster_zeile(self):
        gelesen, wischen = self._lesen([[self._zeile("K0")]])
        self.assertLessEqual(wischen.call_count, sum(range(snap_helfer.SEITEN_MAX)))     # 0 + 1 + 2 Wischer
        self.assertEqual(gelesen, ["K0"])                                                # derselbe Name nie doppelt

    def test_hoert_auf_wenn_unterste_zeile_nicht_neu_ist(self):
        seiten = [[self._zeile("K0"), self._zeile("K1", "Received")], [self._zeile("K2")]]
        gelesen, wischen = self._lesen(seiten)
        self.assertEqual(gelesen, ["K0"])
        wischen.assert_not_called()

    def test_nach_dem_scrollen_geschlossener_chat_und_liste_oben(self):
        """Review 07.10.2026: Eine gescrollte Liste ließ den nächsten Auftrag „Chat nicht in der Chatliste“ melden (Neustart)."""
        seiten = [[self._zeile(f"K{i}") for i in range(a, a + 2)] for a in (0, 2, 4)]
        ereignisse = []
        self._lesen(seiten, ereignisse=ereignisse)
        self.assertIn("wischen", ereignisse)
        self.assertEqual(ereignisse[-2:], ["liste", "oben"])

    def test_ohne_scrollen_nur_chat_schliessen(self):
        ereignisse = []
        self._lesen([[self._zeile("K0"), self._zeile("K1", "Received")]], ereignisse=ereignisse)
        self.assertEqual(ereignisse[-1], "liste")
        self.assertNotIn("oben", ereignisse[-3:])

    def test_zeilenlimit(self):
        n = snap_helfer.ZEILEN_MAX + 10
        seiten = [[self._zeile(f"K{i}") for i in range(n)]]
        gelesen, _ = self._lesen(seiten, kontakte={f"k{i}": f"p{i}" for i in range(n)})
        self.assertEqual(len(gelesen), snap_helfer.ZEILEN_MAX)


class SchutzschalterTest(unittest.TestCase):
    """07.10.2026, Spec Punkt 6: nach 5 Fehlern in Folge 10 Minuten Pause, danach frischer Zähler."""

    def test_pausiert_nach_fuenf_fehlern_und_erholt_sich(self):
        s = snap_helfer.Schutzschalter()
        for _ in range(snap_helfer.FEHLER_MAX - 1):
            s.fehler(100.0)
        self.assertFalse(s.pausiert(100.0))
        s.fehler(100.0)
        self.assertTrue(s.pausiert(100.0))
        self.assertTrue(s.pausiert(100.0 + snap_helfer.PAUSE_FEHLER_S - 1))
        self.assertFalse(s.pausiert(100.0 + snap_helfer.PAUSE_FEHLER_S + 1))
        s.fehler(800.0)
        self.assertFalse(s.pausiert(800.0))                    # nach der Pause stand der Zähler wieder bei 0

    def test_erfolg_setzt_den_zaehler_zurueck(self):
        s = snap_helfer.Schutzschalter()
        for _ in range(snap_helfer.FEHLER_MAX - 1):
            s.fehler(1.0)
        s.erfolg()
        s.fehler(2.0)
        self.assertFalse(s.pausiert(2.0))

    def test_nach_lauf_schreibt_pausiert_bis_in_den_zustand(self):
        import tempfile, os
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}):
            s = snap_helfer.Schutzschalter()
            for _ in range(snap_helfer.FEHLER_MAX):
                snap_helfer.schutz_nach_lauf(s, 500.0, fehlgeschlagen=True)
            z = snap_helfer._lesen("zustand.json", {})
            self.assertEqual(z["pausiert_bis"], 500.0 + snap_helfer.PAUSE_FEHLER_S)
            snap_helfer.schutz_nach_lauf(s, 500.0 + snap_helfer.PAUSE_FEHLER_S + 1, fehlgeschlagen=False)
            self.assertEqual(snap_helfer._lesen("zustand.json", {})["pausiert_bis"], 0)


class ZeitenTest(unittest.TestCase):
    """07.10.2026: Der Helfer misst je Auftrag, wohin die Zeit geht (adb, Dumps, Pausen) — erst messen, dann beschleunigen."""

    def test_adb_und_pausen_werden_gezaehlt(self):
        fake = mock.Mock(returncode=0, stdout="x", stderr="")
        snap_helfer._zeiten_start()
        with mock.patch.object(snap_helfer.subprocess, "run", return_value=fake), \
             mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer._adb("shell", "input", "tap", "1", "2")
            snap_helfer._adb("shell", "uiautomator", "dump", "/sdcard/x.xml")
            snap_helfer._pause()
        z = snap_helfer._zeiten_ende()
        self.assertEqual(z["adb_aufrufe"], 2)
        self.assertEqual(z["dumps"], 1)
        self.assertGreaterEqual(z["pause_s"], 0.4)
        self.assertLessEqual(z["pause_s"], 1.0)
        self.assertIn("dauer_s", z)

    def test_dumps_werden_nach_aufrufer_gezaehlt(self):
        fake = mock.Mock(returncode=0, stdout="x", stderr="")
        snap_helfer._zeiten_start()

        def holen():
            return snap_helfer._adb("shell", "uiautomator", "dump", "/sdcard/x.xml")
        with mock.patch.object(snap_helfer.subprocess, "run", return_value=fake):
            holen()
            holen()
        z = snap_helfer._zeiten_ende()
        self.assertEqual(sum(z["dump_stellen"].values()), 2)
        self.assertTrue(any("holen" in k for k in z["dump_stellen"]))

    def test_erster_fehler_im_neuversuch_wird_protokolliert(self):
        """07.10.2026: Ein stiller Neuversuch verdoppelt die Dauer eines Auftrags — der Grund muss im Protokoll stehen."""
        calls = []

        def funktion():
            calls.append(1)
            if len(calls) == 1:
                raise snap_helfer.SnapHelferFehler("Element 'texteingabe' nicht gefunden")
            return "ok"
        snap_helfer._zeiten_start()
        with mock.patch.object(snap_helfer, "app_zuruecksetzen"), mock.patch.object(snap_helfer, "app_starten"), \
             mock.patch.object(snap_helfer, "_pause"), mock.patch.object(snap_helfer, "_entwurf_aufraeumen"), \
             mock.patch.object(snap_helfer, "_auf_oberflaeche_warten"), \
             mock.patch("builtins.print") as gedruckt:
            self.assertEqual(snap_helfer.mit_neuversuch(funktion), "ok")
        self.assertIn("texteingabe", snap_helfer._zeiten_ende()["erster_fehler"])
        self.assertTrue(any("Versuch" in str(a) for a in gedruckt.call_args_list))

    def test_ergebnisprotokoll_traegt_die_zeiten(self):
        import tempfile, os
        with mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()}):
            snap_helfer._zeiten_start()
            snap_helfer.ergebnis_protokollieren({"art": "text", "empfaenger": "Eike", "person": None, "text": "Hi"}, "gesendet")
            zeile = json.loads((snap_helfer.daten_ordner() / "snapchat" / "ergebnisse.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        for feld in ("dauer_s", "adb_aufrufe", "dumps", "dump_s", "pause_s", "dump_stellen", "erster_fehler"):
            self.assertIn(feld, zeile)


class ChatTabTest(unittest.TestCase):
    """07.10.2026, am Gerät gesehen: Der Tipp auf den Chat-Tab einer schon offenen Chatliste scrollt sie — Eike und Test verschwanden
    aus dem Bild, „steht nicht in der Chatliste“, Neustart, doppelte Dauer. Ein offene Liste wird nicht noch einmal angetippt."""

    def test_offene_chatliste_wird_nicht_angetippt(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<x/>"), \
             mock.patch.object(snap_helfer, "chat_zeilen", return_value=[{"anzeige": "Eike"}]), \
             mock.patch.object(snap_helfer, "schritt_ausfuehren") as schritt:
            snap_helfer._chat_tab_oeffnen()
        schritt.assert_not_called()

    def test_ohne_chatliste_wird_der_tab_angetippt(self):
        with mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<x/>"), \
             mock.patch.object(snap_helfer, "chat_zeilen", return_value=[]), \
             mock.patch.object(snap_helfer, "schritt_ausfuehren") as schritt:
            snap_helfer._chat_tab_oeffnen()
        schritt.assert_called_once_with("chat_tab")

    def test_die_ablaeufe_nutzen_den_vorsichtigen_weg(self):
        import inspect
        for fn in (snap_helfer._chat_text_ablauf, snap_helfer._eingang_pruefen_ablauf, snap_helfer._bild_snap_ablauf):
            quelle = inspect.getsource(fn)
            self.assertNotIn('schritt_ausfuehren("chat_tab")', quelle, fn.__name__)


class SchnellLaufTest(unittest.TestCase):
    """Review 07.10.2026: Die schnelle Auftragsschleife darf das Lesen nicht aushungern und nicht leer hämmern."""

    def test_schnelle_schleife_nur_wenn_alles_passt(self):
        f = snap_helfer.soll_schnell
        self.assertTrue(f(True, True, True, jetzt=100.0, letzter_voller_lauf=50.0))
        self.assertFalse(f(False, True, True, 100.0, 50.0))        # kein Heimat-Modus
        self.assertFalse(f(True, False, True, 100.0, 50.0))        # kein wartender Auftrag
        self.assertFalse(f(True, True, False, 100.0, 50.0))        # Konnektor aus: nicht im Leerlauf hämmern
        self.assertFalse(f(True, True, True, 100.0, 100.0 - snap_helfer.HEIMAT_LESEN_NACH_S - 1))   # lange nicht gelesen: voller Lauf


class IsolationTest(unittest.TestCase):
    """07.10.2026: Die Tests lasen die echte `data/snapchat/einstellungen.json` (live: Heimat an) und schlugen deshalb fehl — Live-Daten
    dürfen einen Testlauf nie beeinflussen."""

    def test_datenordner_ist_nicht_der_echte(self):
        echt = (Path(snap_helfer.__file__).resolve().parent.parent / "data").resolve()
        self.assertNotEqual(snap_helfer.daten_ordner().resolve(), echt)


# ---------- Ticketbild im Original (09.10.2026) ----------

class TicketOriginalTest(unittest.TestCase):
    MENUE = ('<hierarchy><node text="Unsave in Chat" bounds="[0,100][1080,200]"/>'
             '<node text="Save to Camera Roll" bounds="[0,1400][1080,1500]"/></hierarchy>')
    OHNE = '<hierarchy><node text="Chat Reply" bounds="[0,1400][1080,1500]"/></hierarchy>'

    def test_menue_eintrag_wird_ueber_den_text_gefunden(self):
        self.assertEqual(snap_helfer.menue_speichern_position(self.MENUE), (540, 1450))

    def test_ohne_eintrag_gibt_es_keine_position(self):
        self.assertIsNone(snap_helfer.menue_speichern_position(self.OHNE))
        self.assertIsNone(snap_helfer.menue_speichern_position("kein xml"))

    def test_neue_datei_ist_der_unterschied(self):
        self.assertEqual(snap_helfer.neue_datei({"a.jpg"}, {"a.jpg", "Snapchat-1.jpg"}), "Snapchat-1.jpg")
        self.assertIsNone(snap_helfer.neue_datei({"a.jpg"}, {"a.jpg"}))

    def test_dateien_im_ordner_nimmt_nur_sichere_bildnamen(self):
        ausgabe = "Snapchat-1.jpg\nfoo bar.jpg\n$(rm).jpg\nnotiz.txt\nB_2.png\n"
        with mock.patch.object(snap_helfer, "_adb", return_value=mock.Mock(returncode=0, stdout=ausgabe)):
            self.assertEqual(snap_helfer.dateien_im_ordner(), {"Snapchat-1.jpg", "B_2.png"})

    def test_fehlender_ordner_ist_leer(self):
        with mock.patch.object(snap_helfer, "_adb", return_value=mock.Mock(returncode=1, stdout="")):
            self.assertEqual(snap_helfer.dateien_im_ordner(), set())


class TicketErwartetTest(unittest.TestCase):
    def _setzen(self, einstellungen, liste):
        snap_helfer._schreiben("einstellungen.json", einstellungen)
        snap_helfer._schreiben("ticket_erwartet.json", liste)

    def test_aus_ist_nie_erwartet(self):
        self._setzen({"ticket_annahme": "aus"}, ["p1"])
        self.assertFalse(snap_helfer.ticket_erwartet_fuer("p1"))

    def test_trocken_und_an_erwarten_nur_gelistete_personen(self):
        for modus in ("trocken", "an"):
            self._setzen({"ticket_annahme": modus}, ["p1"])
            self.assertTrue(snap_helfer.ticket_erwartet_fuer("p1"))
            self.assertFalse(snap_helfer.ticket_erwartet_fuer("p2"))
            self.assertFalse(snap_helfer.ticket_erwartet_fuer(None))

    def test_eike_steht_als_eike_in_der_liste(self):
        self._setzen({"ticket_annahme": "trocken"}, ["eike"])
        self.assertTrue(snap_helfer.ticket_erwartet_fuer(None))

    def test_kaputte_dateien_heissen_nicht_erwartet(self):
        self._setzen({"ticket_annahme": "an"}, {"p1": 1})
        self.assertFalse(snap_helfer.ticket_erwartet_fuer("p1"))


class TicketAblaufTest(unittest.TestCase):
    """`chat_bild_original_holen` ohne Handy: `_adb`, Dump und Zeit sind gemockt."""

    def setUp(self):
        self.ruf = []
        self.dateien = [set(), {"Snapchat-9.jpg"}]          # vor / nach dem Tippen
        self.pull_rc = 0

    def adb(self, *args, timeout=20.0):
        self.ruf.append(args)
        if args[:3] == ("shell", "ls", "-1"):
            satz = self.dateien.pop(0) if len(self.dateien) > 1 else self.dateien[0]
            return mock.Mock(returncode=0, stdout="\n".join(satz))
        if args[0] == "pull":
            return mock.Mock(returncode=self.pull_rc, stdout="", stderr="boom")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def laufen_lassen(self, dump):
        with mock.patch.object(snap_helfer, "_adb", side_effect=self.adb), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=dump), \
             mock.patch.object(snap_helfer, "tippen") as tippen, \
             mock.patch.object(snap_helfer, "_pause"), \
             mock.patch.object(snap_helfer.time, "sleep"):
            try:
                return snap_helfer.chat_bild_original_holen((45, 1542, 423, 2214)), tippen
            except snap_helfer.SnapHelferFehler as exc:
                return exc, tippen

    def test_erfolg_haelt_drueckt_speichert_holt_und_loescht(self):
        name, tippen = self.laufen_lassen(TicketOriginalTest.MENUE)
        self.assertRegex(name, r"^[0-9a-f]{32}\.jpg$")
        tippen.assert_called_once_with(540, 1450)
        gedrueckt = [r for r in self.ruf if r[:3] == ("shell", "input", "swipe")]
        self.assertEqual(gedrueckt[0][3:], ("234", "1878", "234", "1878", "900"))
        self.assertIn(("shell", "rm", "-f", "/sdcard/DCIM/Snapchat/Snapchat-9.jpg"), self.ruf)

    def test_kein_menue_kein_tippen_zurueck_taste_und_fehler(self):
        fehler, tippen = self.laufen_lassen(TicketOriginalTest.OHNE)
        self.assertIsInstance(fehler, snap_helfer.SnapHelferFehler)
        tippen.assert_not_called()
        self.assertIn(("shell", "input", "keyevent", "KEYCODE_BACK"), self.ruf)

    def test_fehlgeschlagener_pull_loescht_trotzdem_die_datei_vom_handy(self):
        self.pull_rc = 1
        fehler, _ = self.laufen_lassen(TicketOriginalTest.MENUE)
        self.assertIsInstance(fehler, snap_helfer.SnapHelferFehler)
        self.assertIn(("shell", "rm", "-f", "/sdcard/DCIM/Snapchat/Snapchat-9.jpg"), self.ruf)

    def test_keine_neue_datei_ist_ein_fehler(self):
        self.dateien = [set()]
        fehler, _ = self.laufen_lassen(TicketOriginalTest.MENUE)
        self.assertIsInstance(fehler, snap_helfer.SnapHelferFehler)


class TicketBilderHolenTest(unittest.TestCase):
    """Schleife in `chat_bilder_holen`: je Durchgang ein Original, der Rest bleibt „neu“, Fehler fallen auf Screencap zurück."""

    def setUp(self):
        self.eingang = []
        snap_helfer.kontakt_zustand_schreiben("Lea", {"sichtbar": [], "bilder": []})
        self.patches = [
            mock.patch.object(snap_helfer, "chat_bild_flaechen", return_value=[(0, 0, 10, 10), (0, 20, 10, 30)]),
            mock.patch.object(snap_helfer, "bildschirm_screenshot", return_value=b"x"),
            mock.patch.object(snap_helfer, "png_zuschneiden", side_effect=lambda png, *box: b"png%d" % box[1]),
            mock.patch.object(snap_helfer, "_eingang_anhaengen",
                              side_effect=lambda eintraege, person=None: self.eingang.extend(eintraege)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def holen(self, erwartet, original):
        with mock.patch.object(snap_helfer, "ticket_erwartet_fuer", return_value=erwartet), \
             mock.patch.object(snap_helfer, "chat_bild_original_holen", side_effect=original):
            return snap_helfer.chat_bilder_holen("<x/>", kontakt="Lea", person="p1")

    def test_nicht_erwartet_bleibt_beim_screencap(self):
        self.assertEqual(self.holen(False, lambda box: "a" * 32 + ".jpg"), 2)
        self.assertEqual({e["art"] for e in self.eingang}, {"chatbild"})

    def test_erwartet_holt_ein_original_das_zweite_bild_bleibt_fuer_den_naechsten_durchgang(self):
        n = self.holen(True, lambda box: "a" * 32 + ".jpg")
        self.assertEqual(n, 1)
        self.assertEqual([e["art"] for e in self.eingang], ["ticketbild"])
        self.assertEqual(self.eingang[0]["bild"], "a" * 32 + ".jpg")
        self.assertEqual(len(snap_helfer.kontakt_zustand("Lea")["bilder"]), 1, "das zweite Bild gilt noch nicht als gesehen")

    def test_fehler_beim_original_faellt_auf_screencap_zurueck(self):
        def kaputt(box):
            raise snap_helfer.SnapHelferFehler("kein Menü")
        self.holen(True, kaputt)
        self.assertEqual([e["art"] for e in self.eingang], ["chatbild", "chatbild"])


class AnnehmenTest(unittest.TestCase):
    """Eingegangene Anfragen annehmen (11.10.2026) — Fixture ist der echte Dump „Add friends“ vom Gerät."""
    DUMP = (Path(__file__).parent / "daten" / "snap_addfriends.xml").read_text(encoding="utf-8")

    def test_zeilen_nur_aus_added_me(self):
        z = {r["name"]: r["knopf"] for r in snap_helfer.annehmen_zeilen(self.DUMP)}
        self.assertEqual(set(z), {"Kontakt O", "Kontakt E", "Kontakt M"})   # nichts aus „Find friends“
        self.assertIsNone(z["Kontakt O"])                                              # schon Freund: kein Knopf
        x, y = z["Kontakt E"]
        self.assertTrue(645 <= x <= 934 and 798 <= y <= 885)                                 # im Accept-Knopf

    def _lauf(self, name, dumps):
        auftrag = {"empfaenger": name}
        with mock.patch.multiple(snap_helfer, geraet_verbunden=lambda: True, bildschirm_aufwecken=lambda: None,
                                 app_zuruecksetzen=lambda: None, app_starten=lambda: None, _pause=lambda *a: None,
                                 _auf_oberflaeche_warten=lambda *a: None, _add_friends_oeffnen=lambda: None,
                                 bildschirm_dump=mock.Mock(side_effect=dumps), tippen=mock.Mock()) as m:
            try:
                snap_helfer.annehmen_abarbeiten(auftrag)
            finally:
                self.tipps = snap_helfer.tippen.call_args_list
        return self.tipps

    def test_tippt_genau_den_accept_knopf(self):
        nachher = self.DUMP.replace('content-desc="Accept"', 'content-desc=""')
        tipps = self._lauf("kontakt e", [self.DUMP, nachher])
        self.assertEqual(len(tipps), 1)
        self.assertTrue(798 <= tipps[0].args[1] <= 885)

    def _ohne_accept_tipp(self, name):
        """Verworfen: nur „View 1 More“ darf getippt werden (zweiter Dump), nie ein Accept-Knopf."""
        with self.assertRaises(snap_helfer.AuftragVerworfen) as ctx:
            self._lauf(name, [self.DUMP, self.DUMP])
        for tipp in self.tipps:
            self.assertFalse(798 <= tipp.args[1] <= 885, tipp)
        return str(ctx.exception)

    def test_unbekannter_name_tippt_nichts_und_nennt_offene(self):
        grund = self._ohne_accept_tipp("Kontakt B")
        self.assertIn("Kontakt E", grund)
        self.assertIn("nichts angetippt", grund)

    def test_schon_freund_wird_nicht_getippt(self):
        self.assertIn("Schon befreundet", self._ohne_accept_tipp("Kontakt O"))

    def test_nicht_bestaetigt_ist_fehler_statt_erfolg(self):
        with self.assertRaises(snap_helfer.SnapHelferFehler):
            self._lauf("Kontakt E", [self.DUMP, self.DUMP])
