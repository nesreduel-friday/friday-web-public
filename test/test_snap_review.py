"""Fund-für-Fund-Tests zum Review von Kontakte Teil 4a (30.09.2026): jeder Test würde ohne den Fix rot.
Kein Test hier darf das echte Handy anfassen: `subprocess.run` ist für das ganze Modul gesperrt."""
import copy
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host-helfer"))
import snap_helfer  # noqa: E402
from test.test_snap_kontakte import AUSWAHL, CHAT, LISTE, SENDTO  # noqa: E402

_sperre = None


def setUpModule():
    global _sperre
    _sperre = mock.patch.object(snap_helfer.subprocess, "run",
                                side_effect=AssertionError("Ein Test greift auf das echte Handy (adb) durch"))
    _sperre.start()


def tearDownModule():
    _sperre.stop()


def mit_status(anzeige, status, text=None):
    """Die echte Chatliste, aber die Zeile von `anzeige` trägt den Status `status` (XML-Bearbeitung, kein Textersatz)."""
    wurzel = ET.fromstring(LISTE)
    for zeile in wurzel.iter("node"):
        if (zeile.get("resource-id") or "").endswith("ff_item") and anzeige in [k.get("text") for k in zeile.iter("node")]:
            zeile.set("content-desc", status)
            for k in zeile.iter("node"):
                if k.get("text") and k.get("text").split()[0] in ("New", "Received", "You", "Delivered", "Neuer", "Opened"):
                    k.set("text", text or status)
    return ET.tostring(wurzel, encoding="unicode")


def mit_doppelter_zeile(anzeige):
    wurzel = ET.fromstring(LISTE)
    eltern = {c: p for p in wurzel.iter() for c in p}
    for zeile in list(wurzel.iter("node")):
        if (zeile.get("resource-id") or "").endswith("ff_item") and anzeige in [k.get("text") for k in zeile.iter("node")]:
            eltern[zeile].append(copy.deepcopy(zeile))
            break
    return ET.tostring(wurzel, encoding="unicode")


def mit_doppeltem_sendto_titel(anzeige):
    wurzel = ET.fromstring(SENDTO)
    eltern = {c: p for p in wurzel.iter() for c in p}
    for k in list(wurzel.iter("node")):
        if k.get("text") == anzeige and k.get("bounds", "").startswith("[194,2131"):
            zweite = copy.deepcopy(k)
            zweite.set("bounds", "[194,2200][917,2260]")
            eltern[k].append(zweite)
            break
    return ET.tostring(wurzel, encoding="unicode")


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FRIDAY_DATEN": tempfile.mkdtemp()})
        self.env.start()
        (snap_helfer.daten_ordner() / "snapchat").mkdir(parents=True, exist_ok=True)
        self.tippen = self._patche(snap_helfer, "tippen")
        self._patche(snap_helfer, "_pause")
        self._patche(snap_helfer.time, "sleep")

    def _patche(self, ziel, name, **kw):
        p = mock.patch.object(ziel, name, **kw)       # kein stopall: Es würde auch den Guard gegen adb abschalten
        m = p.start()
        self.addCleanup(p.stop)
        return m

    def tearDown(self):
        self.env.stop()

    def dumps(self, *folge):
        return mock.patch.object(snap_helfer, "bildschirm_dump", side_effect=list(folge))


class GleicheNamen(Basis):
    def test_zwei_chatzeilen_mit_demselben_namen_sind_mehrdeutig(self):
        doppelt = mit_doppelter_zeile("Eike")
        self.assertEqual(len(snap_helfer.chat_zeilen_fuer(doppelt, "Eike")), 2)
        with self.dumps(doppelt):
            with self.assertRaises(snap_helfer.SnapHelferFehler) as f:
                snap_helfer.kontakt_chat_oeffnen("Eike")
        self.assertIn("mehrdeutig", str(f.exception))
        self.tippen.assert_not_called()

    def test_zwei_send_to_titel_mit_demselben_namen_sind_mehrdeutig(self):
        doppelt = mit_doppeltem_sendto_titel("Test")
        self.assertEqual(len(snap_helfer.sendto_zeilen(doppelt, "Test")), 2)
        with self.dumps(doppelt):
            with self.assertRaises(snap_helfer.SnapHelferFehler) as f:
                snap_helfer.sendto_waehlen("Test")
        self.assertIn("mehrdeutig", str(f.exception))
        self.tippen.assert_not_called()

    def test_fremde_absender_im_chat_deuten_auf_eine_gruppe_und_brechen_ab(self):
        gruppe = CHAT.replace('text="EIKE"', 'text="MAX"')          # Kopf sagt „Eike“, die Nachrichten kommen von MAX
        with self.dumps(LISTE, gruppe):
            with self.assertRaises(snap_helfer.SnapHelferFehler) as f:
                snap_helfer.kontakt_chat_oeffnen("Eike")
        self.assertIn("Gruppe", str(f.exception))

    def test_eine_nachricht_in_grossbuchstaben_ist_kein_fremdes_label(self):
        # Ein Label sitzt ganz links (x ≈ 21), ein Nachrichtentext ab x ≈ 42: „OK“ oder „LOL“ als Nachricht darf nicht blockieren.
        mit_ok = CHAT.replace("</hierarchy>", '<node text="OK" bounds="[42,400][120,450]"/></hierarchy>')
        self.assertIn(">OK<", mit_ok.replace('text="OK"', ">OK<"))
        self.assertEqual(snap_helfer.chat_fremde_labels(mit_ok, "Eike"), [])
        fremdes_label = CHAT.replace("</hierarchy>", '<node text="MAX" bounds="[21,400][92,450]"/></hierarchy>')
        self.assertEqual(snap_helfer.chat_fremde_labels(fremdes_label, "Eike"), ["MAX"])

    def test_tages_trenner_ist_kein_fremdes_label(self):
        # „TODAY“ sitzt links wie ein Absender-Label — ohne Ausnahme gälte jeder längere Chat als Gruppe (02.10.2026, Kontakt C).
        mit_today = CHAT.replace('</hierarchy>', '<node text="TODAY" bounds="[21,400][110,450]"/></hierarchy>')
        self.assertEqual(snap_helfer.chat_fremde_labels(mit_today, "Eike"), [])
        fremdes_label = CHAT.replace('</hierarchy>', '<node text="MAX" bounds="[21,400][92,450]"/></hierarchy>')
        self.assertEqual(snap_helfer.chat_fremde_labels(fremdes_label, "Eike"), ["MAX"])

    def test_der_eigene_absender_und_me_sind_in_ordnung(self):
        with self.dumps(LISTE, CHAT):                                 # Label „EIKE“ passt zum Kontakt „Eike“
            snap_helfer.kontakt_chat_oeffnen("Eike")
        self.tippen.assert_called_once()
        self.assertEqual(snap_helfer.chat_fremde_labels(CHAT, "Eike"), [])
        mit_me = CHAT.replace('text="EIKE"', 'text="ME"')
        self.assertEqual(snap_helfer.chat_fremde_labels(mit_me, "Eike"), [])


class StatusSchutz(Basis):
    def test_unbekannte_snap_status_sind_gefaehrlich_bekannte_harmlose_nicht(self):
        for status in ("New Snap", "New Snaps", "Neuer Snap", "New Snap · 5m", "Snap received", "New Story"):
            self.assertFalse(snap_helfer._status_sicher(status), status)
        for status in ("New Chat", "Received", "Delivered", "Opened", "You are now friends 🎉", "Neuer Chat", "Sent", "", "Double-tap to Snap"):
            self.assertTrue(snap_helfer._status_sicher(status), status)

    def test_chat_oeffnen_tippt_nie_auf_eine_zeile_mit_snap_artigem_status(self):
        for status in ("Neuer Snap", "New Snap and Chat", "New Snaps"):
            with self.dumps(mit_status("Eike", status)):
                with self.assertRaises(snap_helfer.SnapHelferFehler, msg=status):
                    snap_helfer.kontakt_chat_oeffnen("Eike")
        self.tippen.assert_not_called()

    def test_harmlose_status_lassen_das_oeffnen_zu(self):
        for status in ("Received", "Delivered", "New Chat"):
            self.tippen.reset_mock()
            with self.dumps(mit_status("Eike", status), CHAT):
                snap_helfer.kontakt_chat_oeffnen("Eike")
            self.tippen.assert_called_once()

    def test_der_snap_knopf_in_der_zeile_von_test_ist_kein_snap_status(self):
        # In Tests Zeile steht ein „Snap“-Knopf (Kamera-Abkürzung): Nur der Status zählt, nicht andere Texte der Zeile.
        with self.dumps(LISTE, CHAT.replace(">Eike<", ">Test<").replace('text="Eike"', 'text="Test"').replace('text="EIKE"', 'text="TEST"')):
            snap_helfer.kontakt_chat_oeffnen("Test")
        self.tippen.assert_called_once()


class AuftraegeUndSchlange(Basis):
    def _einmal(self):
        gesendet = []
        with mock.patch.object(snap_helfer, "_konnektor_aktiv", return_value=True), \
             mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=lambda b, t, e=None: gesendet.append((t, e))), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        return gesendet

    def _schlange(self, name, auftrag):
        d = snap_helfer.daten_ordner() / "snapchat" / "warteschlange"
        d.mkdir(exist_ok=True)
        (d / name).write_text(json.dumps(auftrag), encoding="utf-8")

    def test_ein_verworfener_auftrag_reisst_die_nachrueckenden_nicht_mit(self):
        snap_helfer._schreiben("auftrag.json", {"art": "text", "text": "ALT", "bild_pfad": None})      # ohne Empfänger
        self._schlange("1.json", {"art": "text", "text": "B", "bild_pfad": None, "empfaenger": "Eike"})
        self._schlange("2.json", {"art": "text", "text": "C", "bild_pfad": None, "empfaenger": "Eike"})
        self.assertEqual(self._einmal(), [])                                      # ALT wird verworfen ...
        self.assertEqual(snap_helfer._lesen("auftrag.json", {})["text"], "B")     # ... B rückt auf, unversehrt
        self.assertNotIn("versuche", snap_helfer._lesen("auftrag.json", {}))
        self.assertEqual(self._einmal(), [("B", "Eike")])
        self.assertEqual(self._einmal(), [("C", "Eike")])
        self.assertIsNone(snap_helfer._lesen("auftrag.json", None))

    def test_dasselbe_bei_unbekanntem_empfaenger_und_bei_max_versuchen(self):
        snap_helfer._schreiben("kontakte.json", {"kontakte": [{"anzeige": "Eike", "person": None}]})
        snap_helfer._schreiben("auftrag.json", {"art": "text", "text": "ALT", "bild_pfad": None, "empfaenger": "Fremder"})
        self._schlange("1.json", {"art": "text", "text": "B", "bild_pfad": None, "empfaenger": "Eike"})
        self.assertEqual(self._einmal(), [])
        self.assertEqual(snap_helfer._lesen("auftrag.json", {})["text"], "B")
        # Ein gültiger Auftrag, der wiederholt scheitert, wird nach MAX_VERSUCHEN verworfen — der nächste bleibt erhalten
        self._schlange("2.json", {"art": "text", "text": "C", "bild_pfad": None, "empfaenger": "Eike"})
        snap_helfer._schreiben("auftrag.json", {"art": "text", "text": "B", "bild_pfad": None, "empfaenger": "Eike",
                                                "versuche": snap_helfer.MAX_VERSUCHE - 1})
        with mock.patch.object(snap_helfer, "_konnektor_aktiv", return_value=True), \
             mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=snap_helfer.SnapHelferFehler("kaputt")), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        self.assertEqual(snap_helfer._lesen("auftrag.json", {}).get("text"), "C")


class PersonUndEmpfaenger(Basis):
    def _einmal(self, auftrag):
        snap_helfer._schreiben("kontakte.json", {"kontakte": [{"anzeige": "Eike", "person": None},
                                                              {"anzeige": "Test", "person": "p2"}]})
        snap_helfer._schreiben("auftrag.json", auftrag)
        gesendet = []
        with mock.patch.object(snap_helfer, "_konnektor_aktiv", return_value=True), \
             mock.patch.object(snap_helfer, "geraet_verbunden", return_value=True), \
             mock.patch.object(snap_helfer, "snap_senden", side_effect=lambda b, t, e=None: gesendet.append((t, e))), \
             mock.patch.object(snap_helfer, "eingang_pruefen", return_value=([], "")), \
             mock.patch.object(snap_helfer, "bildschirm_schlafen"):
            snap_helfer.einmal()
        return gesendet

    def test_person_und_empfaenger_muessen_zusammenpassen(self):
        basis = {"art": "text", "text": "Hi", "bild_pfad": None}
        self.assertEqual(self._einmal({**basis, "empfaenger": "Test", "person": "p2"}), [("Hi", "Test")])
        self.assertEqual(self._einmal({**basis, "empfaenger": "Test", "person": "p9"}), [])     # andere Person
        self.assertEqual(self._einmal({**basis, "empfaenger": "Test", "person": None}), [])     # Person fehlt
        self.assertEqual(self._einmal({**basis, "empfaenger": "Eike", "person": "p2"}), [])     # Eike gehört keiner Person
        self.assertEqual(self._einmal({**basis, "empfaenger": "Eike", "person": None}), [("Hi", "Eike")])

    def test_eikes_name_kommt_aus_der_kontaktliste_nicht_aus_der_umgebung_des_helfers(self):
        snap_helfer._schreiben("kontakte.json", {"kontakte": [{"anzeige": "Eike D", "person": None}]})
        self.assertEqual(snap_helfer.eike_name(), "Eike D")


class ZeitzoneUndAdb(Basis):
    def test_aktivzeiten_gelten_in_berliner_zeit_nicht_in_utc(self):
        berlin = ZoneInfo("Europe/Berlin")
        self.assertFalse(snap_helfer.ist_wach(datetime(2026, 9, 30, 6, 30, tzinfo=berlin)))
        self.assertTrue(snap_helfer.ist_wach(datetime(2026, 9, 30, 7, 5, tzinfo=berlin)))
        self.assertTrue(snap_helfer.ist_wach(datetime(2026, 9, 30, 22, 55, tzinfo=berlin)))
        self.assertFalse(snap_helfer.ist_wach(datetime(2026, 9, 30, 23, 30, tzinfo=berlin)))
        # 05:00 UTC ist 07:00 MESZ: wach — in der alten UTC-Rechnung (07:00 UTC = 09:00 MESZ) war es das nicht
        self.assertTrue(snap_helfer.ist_wach(datetime(2026, 9, 30, 5, 0, tzinfo=ZoneInfo("UTC"))))

    def test_kein_test_dieses_moduls_kommt_ans_echte_handy(self):
        with self.assertRaises(AssertionError):
            snap_helfer.subprocess.run(["adb", "devices"])


if __name__ == "__main__":
    unittest.main()
