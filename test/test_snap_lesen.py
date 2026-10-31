"""Der Helfer liest alle Kontakte (Kontakte Teil 4c, 30.09.2026) — an anonymisierten Geräte-Dumps, nie am echten Handy."""
import copy
import json
import os
import re
import tempfile
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host-helfer"))
import snap_helfer  # noqa: E402

DATEN = Path(__file__).resolve().parent / "daten"
_sperre = None
_daten_env = None


def setUpModule():
    """Kein Test dieses Moduls darf das echte Handy (adb) anfassen."""
    global _sperre, _daten_env
    _sperre = mock.patch.object(snap_helfer.subprocess, "run",
                                side_effect=AssertionError("Ein Test greift auf das echte Handy (adb) durch"))
    _sperre.start()
    import os, tempfile
    _daten_env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})   # nie die echten Live-Daten (07.10.2026)
    _daten_env.start()


def tearDownModule():
    _sperre.stop()
    _daten_env.stop()


def dump(name: str) -> str:
    return (DATEN / name).read_text(encoding="utf-8")


def _mit_zweiter_zeile(name: str) -> str:
    """Der Dump, in dem die Zeile `name` ein zweites Mal steht (Zeile samt Unterbaum kopiert, 1000 px tiefer)."""
    wurzel = ET.fromstring(dump("snap_chatliste_mehrere.xml"))
    for eltern in wurzel.iter("node"):
        for kind in list(eltern):
            if (kind.get("resource-id") or "").endswith("ff_item") and any(k.get("text") == name for k in kind.iter("node")):
                kopie = copy.deepcopy(kind)
                for k in kopie.iter("node"):
                    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", k.get("bounds", ""))
                    if m:
                        x1, y1, x2, y2 = (int(g) for g in m.groups())
                        k.set("bounds", f"[{x1},{y1 + 1000}][{x2},{y2 + 1000}]")
                eltern.append(kopie)
                return ET.tostring(wurzel, encoding="unicode")
    raise AssertionError(f"Zeile {name} nicht im Dump")


class NeueZeilen(unittest.TestCase):
    BEKANNT = {"eike": None, "test": "p2", "paula": "p3"}

    def test_status_neu(self):
        for s in ("New Chat", "New Snap", "Neuer Snap", "Snap received", "2 new Chats", "2 new Chats · 9m", "3 neue Chats"):
            self.assertTrue(snap_helfer.zeile_ist_neu(s), s)
        for s in ("Received", "Delivered", "Opened", "You are now friends 🎉", "", "6 new Chats · 2d", "New Chat · 1w"):
            self.assertFalse(snap_helfer.zeile_ist_neu(s), s)

    def test_bekannte_und_unbekannte_getrennt_eike_nie_dabei(self):
        bekannte, unbekannte = snap_helfer.neue_kontakte(dump("snap_chatliste_mehrere.xml"), self.BEKANNT)
        self.assertEqual([(z["anzeige"], p) for z, p in bekannte], [("Test", "p2")])   # Eike hat seinen eigenen Weg, Kontakt Fa ist nicht neu
        self.assertEqual(unbekannte, ["Fremder Nutzer"])                                 # nie „My AI“/„Team Snapchat“

    def test_mehrdeutiger_name_wird_uebersprungen(self):
        bekannte, _ = snap_helfer.neue_kontakte(_mit_zweiter_zeile("Test"), self.BEKANNT)
        self.assertEqual(bekannte, [])                                                   # zwei Zeilen „Test“ → nie lesen


class DatenTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.env.stop()


class EingangUndZustand(DatenTest):
    def test_eintrag_traegt_person_nur_wenn_angegeben(self):
        snap_helfer._eingang_anhaengen([{"text": "Hi", "art": "text"}], person="p2")
        snap_helfer._eingang_anhaengen([{"text": "Hey", "art": "text"}])
        e = snap_helfer._lesen("eingang.json", [])
        self.assertEqual(e[0]["person"], "p2")
        self.assertNotIn("person", e[1])

    def test_zustand_je_kontakt_ist_getrennt(self):
        snap_helfer.kontakt_zustand_schreiben("Test", {"sichtbar": ["a"], "bilder": []})
        self.assertEqual(snap_helfer.kontakt_zustand("Test")["sichtbar"], ["a"])
        self.assertEqual(snap_helfer.kontakt_zustand("  test ")["sichtbar"], ["a"])      # normalisierter Name ist der Schlüssel
        self.assertEqual(snap_helfer.kontakt_zustand("Kontakt Fa"), {"sichtbar": None, "bilder": []})

    def test_unbekannte_werden_einmal_und_ohne_inhalt_vermerkt(self):
        snap_helfer.unbekannte_vermerken(["Fremder Nutzer"])
        snap_helfer.unbekannte_vermerken(["fremder  nutzer"])
        self.assertEqual(snap_helfer._lesen("unbekannt_eingang.json", []), ["Fremder Nutzer"])

    def test_unbekannte_haben_eine_obergrenze_und_nichts_faellt_weg(self):
        snap_helfer.unbekannte_vermerken([f"Name {i}" for i in range(250)])
        namen = snap_helfer._lesen("unbekannt_eingang.json", [])
        self.assertEqual(len(namen), 200)
        self.assertEqual(namen[0], "Name 0")

    def test_neue_texte_zuwachs_oder_alles(self):
        self.assertEqual(snap_helfer.neue_texte(["a", "b", "c"], ["a", "b"]), ["c"])
        self.assertEqual(snap_helfer.neue_texte(["x"], ["a", "b"]), ["x"])      # Snapchat löscht Gesehenes: was jetzt steht, ist neu
        self.assertEqual(snap_helfer.neue_texte(["x"], None), ["x"])
        self.assertEqual(snap_helfer.neue_texte([], ["a"]), [])


def _chat(*paare) -> str:
    """Ein Chat-Dump aus (Absender-Label, Text)-Paaren — so liest der Helfer den Chat (`fremde_texte`)."""
    knoten = "".join(f'<node text="{a}" bounds="[0,0][1,1]"/><node text="{t}" bounds="[0,0][1,1]"/>' for a, t in paare)
    return f'<?xml version="1.0"?><hierarchy>{knoten}</hierarchy>'


LISTE = dump("snap_chatliste_mehrere.xml")


def _liste_mit_neu(name: str) -> str:
    """Die Chatliste, in der auch `name` „New Chat“ zeigt."""
    wurzel = ET.fromstring(LISTE)
    for k in wurzel.iter("node"):
        if (k.get("resource-id") or "").endswith("ff_item") and any(n.get("text") == name for n in k.iter("node")):
            k.set("content-desc", "New Chat")
    return ET.tostring(wurzel, encoding="unicode")


class KontakteLesen(DatenTest):
    """Ein Telefon aus Attrappen: `bildschirm_dump` zeigt, was zuletzt geöffnet wurde."""

    def setUp(self):
        super().setUp()
        self.liste = LISTE
        self.schirm = self.liste
        self.chats = {}
        self.aufrufe = []
        self.fehler_bei = set()

        def oeffnen(anzeige):
            self.aufrufe.append(("chat", anzeige))
            if anzeige in self.fehler_bei:
                raise snap_helfer.SnapHelferFehler("kaputt")
            self.schirm = self.chats[anzeige]

        def zur_liste():
            self.schirm = self.liste

        def snaps(anzeige=None, max_snaps=5, person=None):
            self.aufrufe.append(("snaps", anzeige, person))
            return 0
        self.tippen = self._patche("tippen")
        self._patche("_chat_hochscrollen")
        self.tippen = self._patche("tippen")
        for name, ersatz in (("bildschirm_dump", lambda: self.schirm), ("kontakt_chat_oeffnen", oeffnen),
                             ("_zur_chatliste", zur_liste), ("snaps_abholen", snaps),
                             ("chat_bilder_holen", lambda *a, **k: 0), ("symbole_vermerken", lambda d: None),
                             ("gespraech_halten", lambda *a, **k: self.aufrufe.append(("gespraech", k))),
                             ("erlaubte_kontakte", lambda: {"eike": None, "test": "p2", "kontakt fa": "p3"})):
            self._patche(name, side_effect=ersatz)

    def _patche(self, name, **kw):
        p = mock.patch.object(snap_helfer, name, **kw)
        m = p.start()
        self.addCleanup(p.stop)
        return m

    def _eingang(self):
        return snap_helfer._lesen("eingang.json", [])

    def test_text_eines_kontakts_geht_mit_person_in_den_eingang(self):
        self.chats["Test"] = _chat(("TEST", "Hallo Friday"))
        snap_helfer._kontakte_lesen()
        e = self._eingang()
        self.assertEqual([(x["text"], x["person"], x["art"]) for x in e], [("Hallo Friday", "p2", "text")])
        self.assertIsNone(snap_helfer._lesen("sichtbar.json", None))                      # Eikes Zustand bleibt unberührt
        self.assertEqual(snap_helfer.kontakt_zustand("Test")["sichtbar"], ["Hallo Friday"])

    def test_nur_eigene_zeilen_ergeben_keinen_eintrag(self):
        self.chats["Test"] = _chat(("ME", "Hi, ich bin Friday"))
        snap_helfer._kontakte_lesen()
        self.assertEqual(self._eingang(), [])

    def _beide_neu(self):
        self.liste = self.schirm = _liste_mit_neu("Kontakt Fa")

    def test_zwei_kontakte_je_eigener_eintrag(self):
        self.chats["Test"] = _chat(("TEST", "Hallo von Test"))
        self.chats["Kontakt Fa"] = _chat(("PAULA", "Hallo von Kontakt Fa"))
        self._beide_neu()
        snap_helfer._kontakte_lesen()
        self.assertEqual([(e["text"], e["person"]) for e in self._eingang()],
                         [("Hallo von Test", "p2"), ("Hallo von Kontakt Fa", "p3")])

    def test_fehler_bei_einem_kontakt_stoppt_die_uebrigen_nicht(self):
        self.fehler_bei.add("Test")
        self.chats["Test"] = _chat(("TEST", "x"))
        self.chats["Kontakt Fa"] = _chat(("PAULA", "Hallo von Kontakt Fa"))
        self._beide_neu()
        snap_helfer._kontakte_lesen()
        self.assertEqual([(e["text"], e["person"]) for e in self._eingang()], [("Hallo von Kontakt Fa", "p3")])

    def test_unbekannter_name_wird_nur_vermerkt_und_nie_angetippt(self):
        self.chats["Test"] = _chat(("TEST", "Hi"))
        snap_helfer._kontakte_lesen()
        self.assertEqual(snap_helfer._lesen("unbekannt_eingang.json", []), ["Fremder Nutzer"])
        self.assertNotIn(("chat", "Fremder Nutzer"), self.aufrufe)
        for c in self.tippen.call_args_list:
            self.assertFalse(1090 <= c.args[1] <= 1266, "Die Zeile des Unbekannten wurde angetippt")
        self.assertNotIn("Fremder Nutzer", str(self._eingang()))                          # nie Inhalt

    def test_nach_neuem_text_haelt_der_helfer_das_gespraech_mit_diesem_kontakt(self):
        self.chats["Test"] = _chat(("TEST", "Hallo Friday"))
        snap_helfer._kontakte_lesen()
        self.assertIn(("gespraech", {"kontakt": "Test", "person": "p2"}), self.aufrufe)

    def test_ohne_neuen_text_kein_gespraechsmodus(self):
        self.chats["Test"] = _chat(("ME", "Hi"))
        snap_helfer._kontakte_lesen()
        self.assertFalse([a for a in self.aufrufe if a[0] == "gespraech"])

    def test_snap_wird_vor_dem_chat_aus_seiner_zeile_geholt(self):
        self.chats["Test"] = _chat(("TEST", "Hi"))
        snap_helfer._kontakte_lesen()
        self.assertEqual(self.aufrufe[:2], [("snaps", "Test", "p2"), ("chat", "Test")])


def _liste_mit_status(name: str, status: str) -> str:
    wurzel = ET.fromstring(LISTE)
    for k in wurzel.iter("node"):
        if (k.get("resource-id") or "").endswith("ff_item") and any(n.get("text") == name for n in k.iter("node")):
            k.set("content-desc", status)
    return ET.tostring(wurzel, encoding="unicode")


def _chat_mit_kopf(kopf: str, *paare) -> str:
    """Ein Chat-Dump mit Kopfzeile (`chat_kopfname` erkennt ihn an `chat_message_list` und dem Namen oben)."""
    knoten = "".join(f'<node text="{a}" bounds="[0,0][1,1]"/><node text="{t}" bounds="[0,0][1,1]"/>' for a, t in paare)
    return ('<?xml version="1.0"?><hierarchy><node resource-id="com.snapchat.android:id/chat_message_list" bounds="[0,300][1080,2000]"/>'
            f'<node text="{kopf}" bounds="[300,100][600,170]"/>{knoten}</hierarchy>')


class ReviewFunde(DatenTest):
    """Fixes zum Review von Teil 4c (30.09.2026); jeder Test war vorher rot."""

    def _einmal(self, **patches):
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(snap_helfer, "_konnektor_aktiv", return_value=True), \
             mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"):
            snap_helfer.einmal()

    def test_andere_kontakte_kommen_erst_nach_eikes_eingang_und_gespraech(self):
        """C1: Sonst bleibt der Chat eines Kontakts offen, Eikes Gesprächsmodus sendet dort — und Eikes Antwort geht an ihn."""
        reihenfolge = []

        def kontakte():
            reihenfolge.append(("kontakte", [e["text"] for e in snap_helfer._lesen("eingang.json", [])]))

        with mock.patch.object(snap_helfer, "eingang_pruefen", return_value=(["Hi"], "Hi")), \
             mock.patch.object(snap_helfer, "gespraech_halten", side_effect=lambda *a, **k: reihenfolge.append(("gespraech",))), \
             mock.patch.object(snap_helfer, "_kontakte_lesen", side_effect=kontakte):
            self._einmal()
        self.assertEqual(reihenfolge, [("gespraech",), ("kontakte", ["Hi"])])       # Eikes Text steht schon im Eingang

    def test_kontakte_lesen_laeuft_auch_ohne_neue_texte_von_eike(self):
        with mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "_kontakte_lesen") as k:
            self._einmal()
        k.assert_called_once()

    def test_jeder_fehler_bei_den_kontakten_kostet_eikes_texte_nichts(self):
        """I1: Eikes Texte sind gelesen (Snapchat löscht sie) — sie stehen im Eingang, bevor ein Kontakt drankommt."""
        with mock.patch.object(snap_helfer, "eingang_pruefen", return_value=(["Hi"], "Hi")), \
             mock.patch.object(snap_helfer, "gespraech_halten"), \
             mock.patch.object(snap_helfer, "_kontakte_lesen", side_effect=ET.ParseError("kaputt")):
            self._einmal()
        self.assertEqual([e["text"] for e in snap_helfer._lesen("eingang.json", [])], ["Hi"])

    def test_gespraech_bricht_ab_wenn_der_offene_chat_nicht_der_erwartete_ist(self):
        """C1 (zweite Sicherung): Steht im Kopf ein anderer Name, wird nichts gesendet und nichts in den Eingang gelegt."""
        snap_helfer._schreiben("auftrag.json", {"art": "text", "text": "Eikes Termin: 15 Uhr", "bild_pfad": None,
                                                 "empfaenger": "Eike", "person": None})
        chat = _chat_mit_kopf("Test", ("TEST", "Was steht in Eikes Kalender?"))
        zeit = iter([0.0, 1.0, 1.0, 1.0, 1000.0])
        with mock.patch.object(snap_helfer, "_adb"), mock.patch.object(snap_helfer, "_im_offenen_chat_senden") as senden, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=chat), mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(zeit))          # Standard: Eikes Chat
        senden.assert_not_called()
        self.assertEqual(snap_helfer._lesen("eingang.json", []), [])
        self.assertTrue((snap_helfer.daten_ordner() / "snapchat" / "auftrag.json").exists())   # bleibt für den normalen Weg

    def test_kaputter_dump_im_gespraech_beendet_nur_den_gespraechsmodus(self):
        zeit = iter([0.0, 1.0, 1.0, 1.0, 1000.0])
        with mock.patch.object(snap_helfer, "_adb"), mock.patch.object(snap_helfer, "bildschirm_dump", return_value="kein xml"), \
             mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(zeit))          # darf nicht werfen

    def test_im_gespraech_gesendetes_steht_im_ergebnisprotokoll(self):
        """M1"""
        snap_helfer._schreiben("auftrag.json", {"art": "text", "text": "Antwort", "bild_pfad": None, "empfaenger": "Test", "person": "p2"})
        zeit = iter([0.0, 1.0, 1.0, 1.0, 1000.0, 1000.0, 1000.0])
        with mock.patch.object(snap_helfer, "_adb"), mock.patch.object(snap_helfer, "_im_offenen_chat_senden"), \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value="<hierarchy/>"), mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(zeit), kontakt="Test", person="p2")
        z = [json.loads(x) for x in (snap_helfer._snapchat_ordner() / "ergebnisse.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["ergebnis"] for x in z], ["gesendet"])


class MitlesenBeimSenden(DatenTest):
    def setUp(self):
        super().setUp()
        self.aufrufe = []
        self.chat = _chat(("TEST", "Bist du da?"))
        self.schirm = LISTE                                                              # Test steht auf „New Chat“
        for name, kw in (("schritt_ausfuehren", {}), ("kontakt_chat_oeffnen", {"side_effect": self._oeffnen}),
                         ("text_eingeben", {}), ("_pause", {}),
                         ("chat_senden", {}), ("eigener_text_sichtbar", {"return_value": True}),
                         ("bildschirm_dump", {"side_effect": lambda: self.schirm}),
                         ("snaps_abholen", {"side_effect": lambda *a, **k: self.aufrufe.append((a, k)) or 0}),
                         ("erlaubte_kontakte", {"return_value": {"eike": None, "test": "p2"}})):
            p = mock.patch.object(snap_helfer, name, **kw)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(snap_helfer.time, "sleep")
        p.start()
        self.addCleanup(p.stop)
        snap_helfer._beim_senden_gesehen.clear()

    def _oeffnen(self, anzeige):
        self.schirm = self.chat

    def test_ein_gespeicherter_text_ohne_neuen_status_wird_nicht_immer_wieder_gelesen(self):
        """I2: Der Freund speichert eine Nachricht im Chat — sie steht bei jedem Öffnen da. Nicht bei jedem Senden neu einlesen
        (das löste eine Antwortschleife aus)."""
        self.schirm = _liste_mit_status("Test", "Received")
        for _ in range(3):
            self.schirm = _liste_mit_status("Test", "Received")
            snap_helfer._chat_text_ablauf("Ja, hier", "Test")
        self.assertEqual(snap_helfer._lesen("eingang.json", []), [])

    def test_beim_senden_an_test_wird_sein_text_mitgelesen(self):
        snap_helfer._chat_text_ablauf("Ja, hier", "Test")
        e = snap_helfer._lesen("eingang.json", [])
        self.assertEqual([(x["text"], x["person"]) for x in e], [("Bist du da?", "p2")])
        self.assertEqual(snap_helfer.kontakt_zustand("Test")["sichtbar"], ["Bist du da?"])
        self.assertEqual(snap_helfer._beim_senden_gesehen, [])                          # nichts wird Eike zugeschrieben

    def test_ein_wartender_snap_des_kontakts_wird_vor_dem_chat_geholt(self):
        snap_helfer._chat_text_ablauf("Ja, hier", "Test")
        self.assertIn((("Test",), {"person": "p2"}), self.aufrufe)

    def test_beim_senden_an_eike_bleibt_es_wie_bisher(self):
        self.chat = _chat(("EIKE", "Na?"))
        snap_helfer._chat_text_ablauf("Hi", "Eike")
        self.assertEqual(snap_helfer._beim_senden_gesehen, ["Na?"])
        self.assertEqual(snap_helfer._lesen("eingang.json", []), [])

    def test_wiederholter_versuch_liest_nichts_doppelt(self):
        snap_helfer._chat_text_ablauf("Ja, hier", "Test")
        self.chat = _chat()                                                             # Snapchat hat das Gesehene gelöscht
        self.schirm = LISTE
        snap_helfer._chat_text_ablauf("Ja, hier", "Test")                               # `mit_neuversuch` läuft den Ablauf erneut
        self.assertEqual(len(snap_helfer._lesen("eingang.json", [])), 1)
        self.assertEqual(snap_helfer.kontakt_zustand("Test")["sichtbar"], [])


class GespraechJeKontakt(DatenTest):
    def _auftrag(self, **felder):
        auftrag = {"art": "text", "text": "Antwort", "bild_pfad": None, "empfaenger": "Test", "person": "p2", **felder}
        (snap_helfer.daten_ordner() / "snapchat" / "auftrag.json").write_text(json.dumps(auftrag), encoding="utf-8")

    def _halten(self, dump="<hierarchy/>", uhr=(0.0, 1.0, 1.0, 1.0, 1000.0, 1000.0, 1000.0)):
        zeit = iter(uhr)
        with mock.patch.object(snap_helfer, "_adb"), \
             mock.patch.object(snap_helfer, "_im_offenen_chat_senden") as senden, \
             mock.patch.object(snap_helfer, "bildschirm_dump", return_value=dump), \
             mock.patch.object(snap_helfer.time, "sleep"):
            snap_helfer.gespraech_halten([], [], jetzt_fn=lambda: next(zeit), kontakt="Test", person="p2")
        return senden

    def _platz(self):
        return snap_helfer.daten_ordner() / "snapchat" / "auftrag.json"

    def test_auftrag_an_den_offenen_kontakt_wird_gesendet(self):
        self._auftrag()
        self._halten().assert_called_once_with("Antwort")
        self.assertFalse(self._platz().exists())

    def test_auftrag_an_eike_wird_im_chat_mit_test_nicht_gesendet(self):
        self._auftrag(empfaenger="Eike", person=None)
        self._halten().assert_not_called()
        self.assertTrue(self._platz().exists())                                         # bleibt für den normalen Weg

    def test_richtiger_name_aber_falsche_person_wird_nicht_gesendet(self):
        self._auftrag(person="p3")
        self._halten().assert_not_called()
        self.assertTrue(self._platz().exists())

    def test_folgenachricht_geht_mit_person_in_den_eingang(self):
        self._halten(dump=_chat(("TEST", "Und noch was")), uhr=(0.0, 1.0, 1.0, 1.0, 1.0, 1000.0, 1000.0))
        e = snap_helfer._lesen("eingang.json", [])
        self.assertEqual([(x["text"], x["person"]) for x in e], [("Und noch was", "p2")])
        self.assertEqual(snap_helfer.kontakt_zustand("Test")["sichtbar"], ["Und noch was"])
        self.assertIsNone(snap_helfer._lesen("sichtbar.json", None))                    # Eikes Zustand bleibt unberührt


class ErgebnisProtokoll(DatenTest):
    AUFTRAG = {"art": "text", "text": "Geheimer Wortlaut", "bild_pfad": None, "empfaenger": "Test", "person": "p2"}

    def _zeilen(self):
        d = snap_helfer._snapchat_ordner() / "ergebnisse.jsonl"
        return [json.loads(z) for z in d.read_text(encoding="utf-8").splitlines()] if d.exists() else []

    def test_ergebnis_ohne_auftragstext(self):
        snap_helfer.ergebnis_protokollieren(self.AUFTRAG, "verworfen", "Empfänger unbekannt")
        (z,) = self._zeilen()
        self.assertEqual((z["ergebnis"], z["empfaenger"], z["person"], z["art"], z["grund"]),
                         ("verworfen", "Test", "p2", "text", "Empfänger unbekannt"))
        self.assertNotIn("Geheimer", json.dumps(z))
        self.assertEqual(z["laenge"], len("Geheimer Wortlaut"))

    def test_datei_ist_auf_500_zeilen_begrenzt(self):
        for i in range(520):
            snap_helfer.ergebnis_protokollieren(self.AUFTRAG, "gesendet", str(i))
        z = self._zeilen()
        self.assertEqual(len(z), 500)
        self.assertEqual(z[-1]["grund"], "519")

    def test_einmal_protokolliert_gesendet_verworfen_und_aufgegeben(self):
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        snap_helfer._schreiben("kontakte.json", {"kontakte": [{"anzeige": "Eike", "person": None}, {"anzeige": "Test", "person": "p2"}]})
        for auftrag, effekt, erwartet in (
                (self.AUFTRAG, None, "gesendet"),
                ({**self.AUFTRAG, "empfaenger": "Unbekannt"}, None, "verworfen"),
                ({**self.AUFTRAG, "versuche": snap_helfer.MAX_VERSUCHE - 1}, snap_helfer.SnapHelferFehler("kaputt"), "aufgegeben")):
            snap_helfer._schreiben("auftrag.json", auftrag)
            with mock.patch.object(snap_helfer, "_konnektor_aktiv", return_value=True), \
                 mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
                 mock.patch.object(snap_helfer, "snap_senden", side_effect=effekt), \
                 mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
                 mock.patch.object(snap_helfer, "bildschirm_schlafen"):
                snap_helfer.einmal()
            self.assertEqual(self._zeilen()[-1]["ergebnis"], erwartet, erwartet)


def _liste_aggregiert(name: str, status: str = "3 Snaps and Chats") -> str:
    """Die Chatliste, in der `name` eine gebündelte Zeile zeigt (Snaps+Chats, kein „New Snap“-Knoten) —
    so stand Kontakt C am 02.10.2026 in der Liste („3 Snaps and Chats     1d“)."""
    wurzel = ET.fromstring(LISTE)
    for k in wurzel.iter("node"):
        if (k.get("resource-id") or "").endswith("ff_item") and any(n.get("text") == name for n in k.iter("node")):
            k.set("content-desc", status)
            for n in k.iter("node"):
                if (n.get("text") or "").startswith(("New Chat", "New Snap", "Received", "Opened", "Delivered")):
                    n.set("text", f"{status}     1d")
    return ET.tostring(wurzel, encoding="unicode")


def _betrachter() -> str:
    """Ein offener Snap-Betrachter (das Bild selbst holt der Test nicht — `snap_bild_holen` ist Attrappe)."""
    return ('<?xml version="1.0"?><hierarchy>'
            '<node resource-id="com.snapchat.android:id/opera_viewer" bounds="[0,0][1080,2400]"/>'
            '<node resource-id="com.snapchat.android:id/base_image_layer_container" bounds="[0,100][1080,2000]"/>'
            '</hierarchy>')


class AggregierteZeilen(DatenTest):
    """Gebündelte Zeilen („3 Snaps and Chats“): Der Status nennt Snaps, aber kein „New Snap“-Knoten ist
    tippbar — vorher Deadlock (der Tipp wurde verweigert, die Person bekam nie eine Antwort), jetzt ein
    Tipp mit Verzweigung (Betrachter → abholen, Chat → Kopf und Gruppe geprüft lesen)."""

    def _lesen(self, schirme: list[str], **ersatz):
        schirme = list(schirme)
        self.tippen_aufrufe = []
        patches = {"bildschirm_dump": lambda: schirme.pop(0) if len(schirme) > 1 else schirme[0],
                   "tippen": lambda *a: self.tippen_aufrufe.append(a), "_pause": lambda: None,
                   "gespraech_halten": lambda *a, **k: None,
                   "_zur_chatliste": lambda: None,
                   "bildschirmgroesse": lambda: (1080, 2400),
                   "snap_bild_holen": lambda dump: "bild.png", **ersatz}
        for name, se in patches.items():
            p = mock.patch.object(snap_helfer, name, side_effect=se)
            p.start()
            self.addCleanup(p.stop)

    def _eingang(self):
        return snap_helfer._lesen("eingang.json", [])

    def test_aggregierte_zeile_wird_angetippt_und_chat_gelesen(self):
        self._lesen([_liste_aggregiert("Test"), _liste_aggregiert("Test"),
                     _chat_mit_kopf("Test", ("TEST", "Hallo Friday"))])
        anzahl = snap_helfer.kontakt_lesen({"anzeige": "Test", "status": "x", "box": (0, 0, 1, 1), "mitte": (540, 641)}, "p2")
        self.assertGreaterEqual(anzahl, 1)
        self.assertTrue(self.tippen_aufrufe, "die aggregierte Zeile wurde nie angetippt")
        self.assertEqual([(x["text"], x["person"]) for x in self._eingang()], [("Hallo Friday", "p2")])

    def test_falscher_kopf_wird_nicht_gelesen(self):
        self._lesen([_liste_aggregiert("Test"), _liste_aggregiert("Test"),
                     _chat_mit_kopf("Fremder", ("FREMDE", "Hey"))])
        anzahl = snap_helfer.kontakt_lesen({"anzeige": "Test", "status": "x", "box": (0, 0, 1, 1), "mitte": (540, 641)}, "p2")
        self.assertEqual(anzahl, 0)
        self.assertEqual(self._eingang(), [])

    def test_gruppe_wird_nicht_als_kontakt_gelesen(self):
        self._lesen([_liste_aggregiert("Test"), _liste_aggregiert("Test"),
                     _chat_mit_kopf("Test", ("TEST", "Hi"), ("FREMDE", "Hey"))])
        with self.assertRaises(snap_helfer.SnapHelferFehler):
            snap_helfer.kontakt_lesen({"anzeige": "Test", "status": "x", "box": (0, 0, 1, 1), "mitte": (540, 641)}, "p2")
        self.assertEqual(self._eingang(), [])

    def test_betrachter_hinter_aggregierter_zeile_wird_abgeholt(self):
        self._lesen([_liste_aggregiert("Test"), _liste_aggregiert("Test"),
                     _betrachter(), _betrachter(), _liste_mit_status("Test", "Received")])
        anzahl = snap_helfer.kontakt_lesen({"anzeige": "Test", "status": "x", "box": (0, 0, 1, 1), "mitte": (540, 641)}, "p2")
        self.assertEqual(anzahl, 1)
        e = self._eingang()
        self.assertEqual([(x["art"], x["person"]) for x in e], [("snap", "p2")])


class EikeAggregierteZeile(DatenTest):
    """Dieselbe Lücke wie bei `AggregierteZeilen`, aber auf Eikes eigenem Leseweg (07.10.2026): Vorher
    verweigerte `kontakt_chat_oeffnen(eike_name())` bei einer gebündelten Zeile für immer — `einmal()`
    meldete dann „verbunden: False“ und erreichte `_kontakte_lesen()` nie mehr. Solange Eikes Zeile so
    stand, bekam NIEMAND mehr eine Antwort auf Snapchat, nicht nur Eike."""

    def _lesen(self, schirme: list[str], **ersatz):
        schirme = list(schirme)
        self.tippen_aufrufe = []
        patches = {"bildschirm_dump": lambda: schirme.pop(0) if len(schirme) > 1 else schirme[0],
                   "tippen": lambda *a: self.tippen_aufrufe.append(a), "_pause": lambda: None,
                   "_zur_chatliste": lambda: None,
                   "bildschirmgroesse": lambda: (1080, 2400),
                   "snap_bild_holen": lambda dump: "bild.png", **ersatz}
        for name, se in patches.items():
            p = mock.patch.object(snap_helfer, name, side_effect=se)
            p.start()
            self.addCleanup(p.stop)

    def test_gebuendelte_eike_zeile_wird_angetippt_und_chat_geoeffnet(self):
        self._lesen([_liste_aggregiert("Eike"), _liste_aggregiert("Eike"),
                     _chat_mit_kopf("Eike", ("EIKE", "Hallo Friday"))])
        dump = snap_helfer._eike_chat_oeffnen()
        self.assertIsNotNone(dump)
        self.assertTrue(self.tippen_aufrufe, "die gebündelte Zeile wurde nie angetippt")

    def test_bleibt_es_gebuendelt_gibt_es_kein_dump_und_keinen_fehler(self):
        self._lesen([_liste_aggregiert("Eike"), _liste_aggregiert("Eike"), _liste_aggregiert("Eike")])
        self.assertIsNone(snap_helfer._eike_chat_oeffnen())

    def test_gruppe_mit_eikes_namen_bleibt_ein_fehler(self):
        self._lesen([_liste_aggregiert("Eike"), _liste_aggregiert("Eike"),
                     _chat_mit_kopf("Eike", ("EIKE", "Hi"), ("FREMDE", "Hey"))])
        with self.assertRaises(snap_helfer.SnapHelferFehler):
            snap_helfer._eike_chat_oeffnen()

    def test_eingang_pruefen_ablauf_liest_trotz_gebuendelter_eike_zeile(self):
        """Regressionstest fürs Ganze: Ohne den Fix wirft `kontakt_chat_oeffnen(eike_name())` hier für
        immer — die Gegenprobe steht in `test_gebuendelte_eike_zeile_wird_angetippt_und_chat_geoeffnet`.
        Drei gebündelte Zeilen, weil vor dem Tipp dreimal gelesen wird: `snaps_abholen()`, der erste
        (verweigerte) Versuch und die Prüfung danach, ob die Zeile wirklich gebündelt ist."""
        self._lesen([_liste_aggregiert("Eike"), _liste_aggregiert("Eike"), _liste_aggregiert("Eike"),
                     _chat_mit_kopf("Eike", ("EIKE", "Hallo Friday"))], schritt_ausfuehren=lambda name: None)
        with mock.patch.object(snap_helfer, "_chat_tab_oeffnen"):      # der Tab-Schritt hat eigene Tests (`ChatTabTest`)
            neue, _ = snap_helfer._eingang_pruefen_ablauf("")
        self.assertIn("Hallo Friday", neue)

    def test_bleibt_es_gebuendelt_meldet_eingang_pruefen_ablauf_nichts_neues_statt_zu_scheitern(self):
        with mock.patch.object(snap_helfer, "_chat_tab_oeffnen"), \
             mock.patch.object(snap_helfer, "snaps_abholen", return_value=0), \
             mock.patch.object(snap_helfer, "_eike_chat_oeffnen", return_value=None):
            neue, zuletzt = snap_helfer._eingang_pruefen_ablauf("Hey")
        self.assertEqual((neue, zuletzt), ([], "Hey"))


if __name__ == "__main__":
    unittest.main()
